import subprocess
import time
import glob
import os
import re
import shutil
import threading
from evdev import UInput, ecodes
from typing import Callable, Optional
from pjsua2 import Endpoint, EpConfig
from .config import SipBotConfig

LogFn = Callable[[str], None]
RegisterThreadFn = Callable[[str], None]

MEDIA_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus", ".wav", ".webm", ".wma"}

def configure_pulse_source(sink_name: str):
	os.environ["PULSE_SOURCE"] = f"{sink_name}.monitor"

def list_media_files(dir_path: str) -> list[str]:
	dir_path = dir_path or "."
	files = []
	for path in glob.glob(os.path.join(dir_path, "*")):
		if os.path.isfile(path) and os.path.splitext(path)[1].lower() in MEDIA_EXTENSIONS:
			if os.path.basename(path).lower() != "x.wav":
				files.append(path)

	def sort_key(path: str):
		basename = os.path.basename(path)
		match = re.match(r"^(\d+)", basename)
		number = int(match.group(1)) if match else 10**9
		return number, basename.lower()

	return sorted(files, key=sort_key)

def get_media_duration_seconds(path: str) -> Optional[float]:
	try:
		result = subprocess.run(
			["ffprobe", "-v", "error", "-show_entries", "format=duration",
			 "-of", "default=noprint_wrappers=1:nokey=1", path],
			text=True, capture_output=True, check=False, timeout=5)
		if result.returncode == 0:
			return float(result.stdout.strip())
	except (ValueError, OSError, subprocess.SubprocessError):
		return None
	return None

class MediaPlayerProcess:
	def __init__(self, process: subprocess.Popen, path: str, sink_name: str):
		self.process = process
		self.path = path
		self.sink_name = sink_name
		self._move_to_sink()

	def _move_to_sink(self):
		deadline = time.monotonic() + 2.0
		while time.monotonic() < deadline and self.process.poll() is None:
			try:
				result = subprocess.run(
					["pactl", "list", "sink-inputs"],
					text=True, capture_output=True, check=False, timeout=1)
				input_id = self._find_sink_input_id(result.stdout)
				if input_id is not None:
					moved = subprocess.run(
						["pactl", "move-sink-input", str(input_id), self.sink_name],
						text=True, capture_output=True, check=False, timeout=1)
					if moved.returncode == 0:
						return
			except (OSError, subprocess.SubprocessError):
				pass
			time.sleep(0.05)

	def _find_sink_input_id(self, output: str):
		input_id = None
		matches_process = False
		for line in output.splitlines():
			stripped = line.strip()
			if stripped.startswith("Sink Input #"):
				input_id = stripped.split("#", 1)[1]
				matches_process = False
			elif "application.process.id" in stripped:
				matches_process = f'"{self.process.pid}"' in stripped
			elif matches_process and stripped.startswith("media.name"):
				if self.sink_name in stripped:
					return input_id
		return None

	def stop(self):
		if self.process.poll() is not None:
			return
		self.process.terminate()
		try:
			self.process.wait(timeout=1)
		except subprocess.TimeoutExpired:
			self.process.kill()
			self.process.wait()

	def is_running(self) -> bool:
		return self.process.poll() is None


def prepare_loopback_sink(config: SipBotConfig) -> bool:
	return AudioManager(config).ensure_loopback_sink()

def start_sink_monitor(config: SipBotConfig, log_fn: Optional[LogFn] = None, interval: float = 10.0) -> threading.Thread:
	"""Запускает фоновый мониторинг loopback sink, пересоздаёт при пропаже."""
	def monitor():
		mgr = AudioManager(config, log_fn=log_fn)
		while True:
			time.sleep(interval)
			if config.loopback_auto_create_sink:
				try:
					mgr.ensure_loopback_sink()
				except Exception as e:
					if log_fn:
						log_fn(f"[SINK_MONITOR] check failed: {e}")

	thread = threading.Thread(target=monitor, daemon=True, name="sink-monitor")
	thread.start()
	return thread

class AudioManager:
	def __init__(self, config: SipBotConfig, log_fn: Optional[LogFn] = None,
				 register_thread_fn: Optional[RegisterThreadFn] = None):
		self.config = config
		self.log_fn = log_fn
		self.register_thread_fn = register_thread_fn

	def log(self, text: str):
		if self.log_fn:
			self.log_fn(text)

	def start_media_file(self, path: str, level: float = 1.0) -> MediaPlayerProcess:
		if not path or not os.path.isfile(path):
			raise FileNotFoundError(path)
		if shutil.which("ffmpeg") is None:
			raise RuntimeError("ffmpeg is not installed")
		sink_name = self.config.loopback_sink_name or "sipbot_sink"
		process = subprocess.Popen(
			["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-re",
			 "-i", path, "-vn", "-af", f"volume={max(0.0, float(level))}",
			 "-f", "pulse", sink_name],
			stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
			env={**os.environ, "PULSE_SINK": sink_name})
		return MediaPlayerProcess(process, path, sink_name)

	def register_thread(self, name: str):
		if self.register_thread_fn:
			self.register_thread_fn(name)

	def list_devices(self, ep: Optional[Endpoint] = None):
		temporary = ep is None
		if temporary:
			ep = Endpoint()
		try:
			if temporary:
				ep.libCreate()
				ep.libInit(EpConfig())
			else:
				self.register_thread("gui-audio-list")
			devices = ep.audDevManager().enumDev2()
			return [(index, str(getattr(device, "name", "Audio device")))
					for index, device in enumerate(devices or [])]
		except Exception as e:
			self.log(f"[AUDIO] list devices failed: {e}")
			return []
		finally:
			if temporary:
				try:
					ep.libDestroy()
				except Exception:
					pass

	def set_devices(self, ep: Optional[Endpoint], playback_id: int, capture_id: int) -> bool:
		if ep is None:
			return False
		self.register_thread("gui-audio-devices")
		try:
			adm = ep.audDevManager()
			adm.setPlaybackDev(int(playback_id))
			adm.setCaptureDev(int(capture_id))
			self.config.audio_playback_dev = int(playback_id)
			self.config.audio_capture_dev = int(capture_id)
			self.log(f"[AUDIO] devices set: playback={playback_id}, capture={capture_id}")
			return True
		except Exception as e:
			self.log(f"[AUDIO] set devices failed: {e}")
			return False

	def set_capture_device(self, ep: Optional[Endpoint], device_id: int) -> bool:
		if ep is None:
			return False
		self.register_thread("gui-audio-dev")
		try:
			resolved_id = int(device_id)
			if self.config.media_source == "loopback":
				devices = ep.audDevManager().enumDev2()
				pipewire_id = self._find_device_id(devices, "pipewire")
				if pipewire_id is not None:
					resolved_id = pipewire_id
			ep.audDevManager().setCaptureDev(resolved_id)
			self.config.audio_capture_dev = resolved_id
			self.log(f"[AUDIO] capture dev set to {resolved_id}")
			return True
		except Exception as e:
			self.log(f"[AUDIO] set capture dev failed: {e}")
			return False

	def configure_media_clock(self, ep_cfg: EpConfig):
		media_cfg = getattr(ep_cfg, "medConfig", None)
		if media_cfg is None:
			media_cfg = getattr(ep_cfg, "mediaConfig", None)
		if media_cfg is None:
			self.log("[AUDIO] media config not available")
			return
		values = {
			"clockRate": int(self.config.audio_clock_rate or 16000),
			"sndClockRate": int(self.config.audio_snd_clock_rate or 48000),
			"audioFramePtime": int(self.config.audio_frame_ptime or 20),
			"ptime": int(self.config.audio_frame_ptime or 20),
			"sndRecLatency": int(self.config.audio_snd_rec_latency or 200),
			"sndPlayLatency": int(self.config.audio_snd_play_latency or 200),
			"sndAutoCloseTime": int(self.config.audio_snd_auto_close_time),
			"ecTailLen": int(self.config.audio_ec_tail_len or 0),
			"noVad": True,
		}
		for name, value in values.items():
			self._set_media_config_value(media_cfg, name, value)
		self.log("[AUDIO] media config")

	def configure_devices(self, ep: Endpoint):
		try:
			adm = ep.audDevManager()
			devices = adm.enumDev2()
		except Exception as e:
			self.log(f"[AUDIO] audio devices init failed: {e}")
			return

		try:
			pulse_device_id = self._find_device_id(devices, "pulse")
			pipewire_device_id = self._find_device_id(devices, "pipewire")
			if (self.config.media_source in ("media", "loopback")
					and pipewire_device_id is not None):
				configure_pulse_source(self.config.loopback_sink_name or "sipbot_sink")
				self.config.audio_capture_dev = pipewire_device_id
				self.log(
					f"[AUDIO] loopback capture routed through pipewire: {pipewire_device_id}, "
					f"source={os.environ['PULSE_SOURCE']}")
				if pulse_device_id is not None:
					self.config.audio_playback_dev = pulse_device_id
					self.log(f"[AUDIO] playback routed through pulse: {pulse_device_id}")
			if (self.config.media_source in ("media", "loopback")
					and self.config.loopback_auto_select_capture
					and self.config.audio_capture_dev < 0):
				device_id = self._find_loopback_capture_device(devices)
				if device_id is not None:
					self.config.audio_capture_dev = device_id
					self.log(f"[AUDIO] loopback capture auto-selected: {device_id}")
			if self.config.audio_playback_dev >= 0:
				adm.setPlaybackDev(int(self.config.audio_playback_dev))
				self.log(f"[AUDIO] playback dev set to {self.config.audio_playback_dev}")
			if self.config.audio_capture_dev >= 0:
				adm.setCaptureDev(int(self.config.audio_capture_dev))
				self.log(f"[AUDIO] capture dev set to {self.config.audio_capture_dev}")
		except Exception as e:
			self.log(f"[AUDIO] set device failed: {e}")

	def _find_device_id(self, devices, name: str):
		for index, device in enumerate(devices or []):
			if str(getattr(device, "name", "") or "").lower() == name.lower():
				return index
		return None

	def ensure_loopback_sink(self):
		if not self.config.loopback_auto_create_sink:
			return False
		sink_name = self.config.loopback_sink_name or "sipbot_sink"
		description = self.config.loopback_sink_description or "SIPBotSink"
		try:
			existing = subprocess.run(
				["pactl", "list", "short", "sinks"],
				text=True, capture_output=True, check=False, timeout=3)
		except FileNotFoundError:
			self.log("[AUDIO] pactl not found, cannot auto-create loopback sink")
			return False
		except Exception as e:
			self.log(f"[AUDIO] pactl list sinks failed: {e}")
			return False

		if existing.returncode == 0 and any(
				line.split()[1] == sink_name
				for line in existing.stdout.splitlines()
				if len(line.split()) > 1):
			self.log(f"[AUDIO] loopback sink already exists: {sink_name}")
			if not self._wait_loopback_sink_ready(sink_name):
				return False
			return self._set_default_source(sink_name)

		try:
			created = subprocess.run(
				["pactl", "load-module", "module-null-sink",
				 f"sink_name={sink_name}",
				 f"sink_properties=device.description={description}"],
				text=True, capture_output=True, check=False, timeout=5)
		except Exception as e:
			self.log(f"[AUDIO] loopback sink create failed: {e}")
			return False
		if created.returncode != 0:
			err = (created.stderr or created.stdout or "").strip()
			self.log(f"[AUDIO] loopback sink create failed: {err}")
			return False
		self.log(f"[AUDIO] loopback sink created: {sink_name} module={created.stdout.strip()}")
		if not self._wait_loopback_sink_ready(sink_name):
			return False
		return self._set_default_source(sink_name)

	def _set_default_source(self, sink_name: str) -> bool:
		source_name = f"{sink_name}.monitor"
		try:
			result = subprocess.run(
				["pactl", "set-default-source", source_name],
				text=True, capture_output=True, check=False, timeout=3)
		except Exception as e:
			self.log(f"[AUDIO] set default source failed: {e}")
			return False
		if result.returncode != 0:
			error = (result.stderr or result.stdout or "").strip()
			self.log(f"[AUDIO] set default source failed: {error}")
			return False
		self.log(f"[AUDIO] default source set: {source_name}")
		return True

	def _find_loopback_capture_device(self, devices):
		sink_name = (self.config.loopback_sink_name or "").lower()
		description = (self.config.loopback_sink_description or "").lower()
		needles = [n for n in (sink_name, description, f"{sink_name}.monitor") if n]
		for index, device in enumerate(devices or []):
			try:
				if int(getattr(device, "inputCount", 0)) <= 0:
					continue
			except Exception:
				continue
			name = str(getattr(device, "name", "") or "").lower()
			if any(needle in name for needle in needles):
				return index
		return None

	def _wait_loopback_sink_ready(self, sink_name: str, timeout: float = 3.0) -> bool:
		deadline = time.monotonic() + timeout
		while time.monotonic() < deadline:
			try:
				result = subprocess.run(
					["pactl", "list", "short", "sources"],
					text=True, capture_output=True, check=False, timeout=2)
				if result.returncode == 0:
					for line in result.stdout.splitlines():
						parts = line.split()
						if len(parts) > 1 and (
								parts[1] == f"{sink_name}.monitor"
								or parts[1].startswith(f"{sink_name}.monitor")):
							self.log(f"[AUDIO] loopback monitor ready: {parts[1]}")
							return True
			except Exception as e:
				self.log(f"[AUDIO] loopback monitor check failed: {e}")
			time.sleep(0.1)
		self.log(f"[AUDIO] loopback monitor readiness timeout: {sink_name}.monitor")
		return False

	def _set_media_config_value(self, media_cfg, name: str, value) -> bool:
		if not hasattr(media_cfg, name):
			return False
		try:
			setattr(media_cfg, name, value)
			return True
		except Exception as e:
			self.log(f"[AUDIO] media config {name}={value} failed: {e}")
			return False

class MediaController:
	KEYS = {"play": ecodes.KEY_PLAYPAUSE, "next": ecodes.KEY_NEXTSONG, "prev": ecodes.KEY_PREVIOUSSONG,
		"stop": ecodes.KEY_STOPCD, "vol_up": ecodes.KEY_VOLUMEUP, "vol_down": ecodes.KEY_VOLUMEDOWN, "mute": ecodes.KEY_MUTE}

	def __init__(self, backend="evdev"):
		self.backend = backend
		self.ui = None
		if backend == "evdev":
			self.ui = UInput({ecodes.EV_KEY: list(self.KEYS.values())}, name="Python Media Controller")

	def key(self, name):
		code = self.KEYS[name]
		if self.backend == "evdev":
			self.ui.write(ecodes.EV_KEY, code, 1)
			self.ui.write(ecodes.EV_KEY, code, 0)
			self.ui.syn()
		elif self.backend == "ydotool":
			env = os.environ.copy()
			env["YDOTOOL_SOCKET"] = os.path.expanduser("~/.ydotool_socket")
			subprocess.run(["ydotool", "key", f"{code}:1", f"{code}:0"], env=env, check=True)

	def control(self, name):
		if name not in self.KEYS:
			raise ValueError(f"Unknown media key: {name}")
		self.key(name)
		return True
