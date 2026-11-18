import threading
import random
import traceback
import os
import time
from .audio import list_media_files, get_media_duration_seconds

from pjsua2 import (AudioMediaTransmitParam,
                    Call, Error, PJSIP_INV_STATE_DISCONNECTED,
                    PJMEDIA_TYPE_AUDIO, PJSUA_CALL_MEDIA_ACTIVE)


class BotCall(Call):
    def __init__(self, acc, bot, call_id=None):
        if call_id is None:
            super().__init__(acc)
        else:
            super().__init__(acc, call_id)
        self.acc_ref = acc
        self.bot = bot
        self.ep = getattr(acc, "ep", None)
        self.call_id = call_id
        self.player = None
        self.monitor_source = None
        self.monitor_target = None
        self.monitor_target_idx = None
        self.loopback_source = None
        self.loopback_target = None
        self.loopback_target_idx = None
        self.loopback_level = None
        self.loopback_capture_dev = None
        self.player_lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.worker_thread = None
        self._playlist_started = False
        self._skip_lock = threading.Lock()
        self._skip_request = 0
        self._skip_event = threading.Event()
        self._settings_event = threading.Event()
        self.remote_uri = "Unknown"
        self.connected_at = None
        self.state_str = "INCOMING"

    def get_info_dict(self):
        duration = 0
        if self.connected_at:
            duration = int(time.time() - self.connected_at)
        uri = self.remote_uri or f"Call #{self.call_id}"
        if uri.startswith("<") and uri.endswith(">"):
            uri = uri[1:-1]
        return {
            "id": self.call_id,
            "uri": uri,
            "state": self.state_str,
            "duration": duration,
        }

    def _call_is_allowed_to_run(self):
        if self.bot.get_runtime_settings().get("call_mode") == "broadcast":
            return self.acc_ref.get_call_by_id(self.call_id) is self
        return self.acc_ref.get_active_call_id() == self.call_id

    def _transmit_level(self) -> float:
        try:
            pct = int(self.bot.get_runtime_settings().get("transmit_volume_pct", 100))
        except Exception:
            pct = 100
        pct = max(0, min(200, pct))
        return pct / 100.0

    def onCallState(self, prm):
        try:
            if self.bot._shutting_down.is_set():
                return
            self.bot.register_current_thread(f"callstate-{self.call_id}")
            try:
                ci = self.getInfo()
                if ci.remoteUri:
                    self.remote_uri = ci.remoteUri
                self.state_str = ci.stateText or self.state_str
            except Exception as e:
                self.bot.log(f"[CALL {self.call_id}] getInfo failed: {e}")
                self.stop_flag.set()
                self.acc_ref.notify_calls_changed()
                return

            state_text = (ci.stateText or "").upper()
            self.bot.log(f"[CALL {self.call_id}] {ci.stateText} ({ci.lastStatusCode})")

            if "CONFIRMED" in state_text:
                if self.connected_at is None:
                    self.connected_at = time.time()
                self.state_str = "CONFIRMED"
                if not self._playlist_started:
                    self._playlist_started = True
                    self.bot.emit_state("call", "confirmed")
                    self.worker_thread = threading.Thread(target=self._playlist_thread, daemon=True)
                    self.worker_thread.start()

            if "DISCONNECTED" in state_text or "TERMINATED" in state_text:
                self.state_str = "DISCONNECTED"
                self.stop_flag.set()
                if self.acc_ref.get_active_call_id() == self.call_id:
                    self.acc_ref.set_active_call_id(None)
                self._cleanup_media()
                self.acc_ref.remove_call(self.call_id)
                if self.acc_ref.get_calls_snapshot():
                    self.bot.emit_state("call", "confirmed")
                else:
                    self.bot.emit_state("call", "idle")

            self.acc_ref.notify_calls_changed()
        except Exception:
            self.bot.log("[CALL] onCallState exception:\n" + traceback.format_exc())

    def onCallMediaState(self, prm):
        try:
            if self.bot._shutting_down.is_set() or self.stop_flag.is_set():
                return
            self.bot.register_current_thread(f"mediastate-{self.call_id}")
            settings = self.bot.get_runtime_settings()
            call_media, media_idx = self._get_active_audio_media()
            if call_media is None:
                for idx in (0, -1):
                    try:
                        call_media = self.getAudioMedia(idx)
                        media_idx = idx
                        break
                    except Exception:
                        call_media = None
            if call_media is None:
                self.bot.log(f"[CALL {self.call_id}] no AudioMedia")
                return

            self.sync_local_monitor(call_media, media_idx)

            if settings.get("media_source") == "loopback":
                self.start_loopback_to_call(force_reconnect=True)
        except Exception:
            self.bot.log("[CALL] onCallMediaState exception:\n" + traceback.format_exc())

    def _playlist_thread(self):
        try:
            self.bot.register_current_thread(f"worker-{self.call_id}")
        except Exception:
            self.bot.log("[CALL] register_current_thread failed")
        try:
            self.run_playlist()
        finally:
            try:
                if self.ep is not None and hasattr(self.ep, "libUnregisterThread"):
                    self.ep.libUnregisterThread()
            except Exception:
                self.bot.log("[CALL] libUnregisterThread failed")

    def run_playlist(self):
        mode = None
        wav_dir = None
        media_files = []
        order = []
        order_index = 0
        loop_index = 0
        repeat_one_track = None
        play_idx = 0
        missing_dir_logged = None
        history = []
        history_index = -1
        pending_skip = 0
        while True:
            if self.stop_flag.is_set() or self.bot.stop_event.is_set():
                break
            if not self._call_is_allowed_to_run():
                self.stop_flag.set()
                break
            try:
                ci = self.getInfo()
                if ci.state == PJSIP_INV_STATE_DISCONNECTED:
                    break
            except Exception:
                break

            settings = self.bot.get_runtime_settings()
            media_source = settings["media_source"]
            next_mode = settings["playback_mode"]
            next_media_dir = settings["wav_dir"]
            self.sync_local_monitor_from_settings()
            if media_source == "loopback":
                self._cleanup_player()
                self.start_loopback_to_call(force_reconnect=True)
                self._wait_for_source_change()
                continue

            self.start_loopback_to_call(force_reconnect=True)
            if next_mode != mode or next_media_dir != wav_dir or not media_files:
                mode = next_mode
                wav_dir = next_media_dir
                media_files = list_media_files(wav_dir)
                order = []
                loop_index = 0
                repeat_one_track = None
                if media_files:
                    if mode == "shuffle":
                        order = media_files[:]
                        random.shuffle(order)
                        order_index = 0
                    elif mode == "repeat_one":
                        repeat_one_track = random.choice(media_files)
                    else:
                        loop_index = random.randint(0, len(media_files) - 1)
                    self.bot.log(
                        f"[CALL {self.call_id}] playlist reload: mode={mode}, dir={wav_dir}, tracks={len(media_files)}"
                    )
                    missing_dir_logged = None
                else:
                    if missing_dir_logged != wav_dir:
                        self.bot.log(f"[CALL {self.call_id}] no media files in {wav_dir}")
                        missing_dir_logged = wav_dir
                    time.sleep(1)
                    continue

            wav_path = None
            force_play = False
            if pending_skip != 0:
                if pending_skip == -1 and history_index > 0:
                    history_index -= 1
                    wav_path = history[history_index]
                    force_play = True
                elif pending_skip == 1 and history_index < len(history) - 1:
                    history_index += 1
                    wav_path = history[history_index]
                    force_play = True
                pending_skip = 0

            if not force_play:
                if mode == "repeat_one":
                    wav_path = repeat_one_track
                elif mode == "shuffle":
                    if not order or order_index >= len(order):
                        order = media_files[:]
                        random.shuffle(order)
                        order_index = 0
                    wav_path = order[order_index]
                    order_index += 1
                else:
                    wav_path = media_files[loop_index]
                    loop_index = (loop_index + 1) % len(media_files)

            if not wav_path or not os.path.isfile(wav_path):
                self.bot.log(f"[CALL {self.call_id}] missing track, skipping: {wav_path}")
                if wav_path in history:
                    history = [p for p in history if p != wav_path]
                    history_index = min(history_index, len(history) - 1)
                media_files = []
                order = []
                order_index = 0
                repeat_one_track = None
                continue
            if force_play:
                if mode == "repeat_one":
                    repeat_one_track = wav_path
                elif mode == "shuffle":
                    try:
                        order_index = order.index(wav_path) + 1
                    except ValueError:
                        pass
                else:
                    try:
                        idx = media_files.index(wav_path)
                        loop_index = (idx + 1) % len(media_files)
                    except ValueError:
                        pass

            play_idx += 1
            self.bot.log(f"[CALL {self.call_id}] PLAY #{play_idx} {os.path.basename(wav_path)}")
            if not force_play:
                if history_index < len(history) - 1:
                    history = history[: history_index + 1]
                history.append(wav_path)
                history_index = len(history) - 1
                if len(history) > 50:
                    history.pop(0)
                    history_index = max(0, history_index - 1)
            self.play_media_file(wav_path)
            pending_skip = self._wait_playback(get_media_duration_seconds(wav_path) or 0)

    def _wait_playback(self, duration_sec: float) -> int:
        if duration_sec <= 0:
            while self.player and self.player.is_running():
                if self.stop_flag.is_set() or self.bot.stop_event.is_set():
                    break
                if self._skip_event.is_set() or self._settings_event.is_set():
                    break
                time.sleep(0.2)
            self._settings_event.clear()
            skip_dir = self._consume_skip_request()
            if skip_dir != 0 or (self.player and self.player.is_running()):
                self._cleanup_player()
            return skip_dir
        deadline = time.monotonic() + duration_sec
        while time.monotonic() < deadline:
            if self.stop_flag.is_set() or self.bot.stop_event.is_set():
                break
            if not self._call_is_allowed_to_run():
                break
            if self.player and not self.player.is_running():
                break
            if self._skip_event.is_set():
                break
            if self._settings_event.is_set():
                break
            time.sleep(0.2)
        self._settings_event.clear()
        skip_dir = self._consume_skip_request()
        if skip_dir != 0:
            self._cleanup_player()
        return skip_dir

    def _wait_for_source_change(self):
        while True:
            if self.stop_flag.is_set() or self.bot.stop_event.is_set():
                break
            if not self._call_is_allowed_to_run():
                break
            if self._settings_event.wait(0.5):
                self._settings_event.clear()
                break
            if self.bot.get_runtime_settings().get("media_source") != "loopback":
                break

    def request_skip(self, direction: int):
        direction = 1 if direction > 0 else -1
        with self._skip_lock:
            self._skip_request = direction
            self._skip_event.set()

    def _consume_skip_request(self) -> int:
        with self._skip_lock:
            direction = self._skip_request
            self._skip_request = 0
            self._skip_event.clear()
            return direction

    def _cleanup_player(self):
        with self.player_lock:
            self._cleanup_player_unlocked()

    def _cleanup_media(self):
        with self.player_lock:
            self._cleanup_player_unlocked()
            self._cleanup_loopback_unlocked()
            self._cleanup_monitor_unlocked()

    def _cleanup_player_unlocked(self):
        if not self.player:
            return
        try:
            self.player.stop()
        except Exception:
            pass
        self.player = None

    def _cleanup_loopback(self):
        with self.player_lock:
            self._cleanup_loopback_unlocked()

    def _cleanup_loopback_unlocked(self):
        if not self.loopback_source:
            return
        try:
            if self.loopback_target:
                self.loopback_source.stopTransmit(self.loopback_target)
        except Exception:
            pass
        self.loopback_source = None
        self.loopback_target = None
        self.loopback_target_idx = None
        self.loopback_level = None
        self.loopback_capture_dev = None

    def _cleanup_monitor(self):
        with self.player_lock:
            self._cleanup_monitor_unlocked()

    def _cleanup_monitor_unlocked(self):
        if not self.monitor_source:
            return
        try:
            if self.monitor_target:
                self.monitor_source.stopTransmit(self.monitor_target)
        except Exception:
            pass
        self.monitor_source = None
        self.monitor_target = None
        self.monitor_target_idx = None

    def _get_active_audio_media(self):
        try:
            ci = self.getInfo()
        except Error:
            return None, None

        for m in ci.media:
            if m.type == PJMEDIA_TYPE_AUDIO and m.status == PJSUA_CALL_MEDIA_ACTIVE:
                try:
                    return self.getAudioMedia(m.index), m.index
                except Exception:
                    pass
        return None, None

    def sync_local_monitor_from_settings(self, force_reconnect: bool = False):
        target_am, target_idx = self._get_active_audio_media()
        self.sync_local_monitor(target_am, target_idx, force_reconnect)

    def sync_local_monitor(self, target_am=None, target_idx=None, force_reconnect: bool = False):
        settings = self.bot.get_runtime_settings()
        enabled = bool(settings.get("monitor_call_audio"))
        if not enabled:
            self._cleanup_monitor()
            return
        if target_am is None:
            target_am, target_idx = self._get_active_audio_media()
        if target_am is None:
            return

        with self.player_lock:
            if (not force_reconnect and self.monitor_source
                    and self.monitor_target_idx == target_idx):
                return
            self._cleanup_monitor_unlocked()
            try:
                playback = self.ep.audDevManager().getPlaybackDevMedia()
                target_am.startTransmit(playback)
                self.monitor_source = target_am
                self.monitor_target = playback
                self.monitor_target_idx = target_idx
                self.bot.log(f"[CALL {self.call_id}] remote audio -> local playback media {target_idx}")
            except Error as e:
                self.bot.log(f"[CALL] local playback connect failed: status={getattr(e, 'status', '?')} reason={getattr(e, 'reason', '?')}")
                try:
                    self.bot.log(f"[CALL] pjsua2 info: {e.info()}")
                except Exception:
                    pass
                traceback.print_exc()
            except Exception as e:
                self.bot.log(f"[CALL] local playback connect failed: {type(e).__name__}: {e}")
                traceback.print_exc()

    def start_loopback_to_call(self, force_reconnect: bool = False):
        self.bot.register_current_thread(f"loopback-{self.call_id}")
        settings = self.bot.get_runtime_settings()
        capture_dev = int(settings.get("loopback_capture_dev", -1))
        if capture_dev >= 0 and capture_dev != self.loopback_capture_dev:
            self.bot.set_capture_device(capture_dev)
            with self.player_lock:
                self._cleanup_loopback_unlocked()
        target_am, target_idx = self._get_active_audio_media()
        if target_am is None:
            self.bot.log(f"[CALL {self.call_id}] no active audio media for loopback")
            return
        desired_level = float(self._transmit_level())
        if self.loopback_level is None or abs(float(self.loopback_level) - desired_level) > 1e-6:
            force_reconnect = True
        with self.player_lock:
            if (not force_reconnect and self.loopback_source and self.loopback_target
                and self.loopback_target_idx == target_idx and self.loopback_capture_dev == capture_dev):
                return
            self._cleanup_loopback_unlocked()
            self._cleanup_player_unlocked()
            try:
                source = self.ep.audDevManager().getCaptureDevMedia()
                prm = AudioMediaTransmitParam()
                prm.level = desired_level
                source.startTransmit2(target_am, prm)
                self.loopback_source = source
                self.loopback_target = target_am
                self.loopback_target_idx = target_idx
                self.loopback_capture_dev = capture_dev
                self.loopback_level = desired_level
                #self.bot.log(f"[CALL {self.call_id}] loopback capture dev {capture_dev} -> media {target_idx} level={desired_level:.2f}")
            except Error as e:
                self.bot.log(f"[CALL {self.call_id}] loopback start failed: status={getattr(e, 'status', '?')} reason={getattr(e, 'reason', '?')}")
                try:
                    self.bot.log(f"[CALL {self.call_id}] pjsua2 info: {e.info()}")
                except Exception:
                    pass
                traceback.print_exc()
            except Exception as e:
                self.bot.log(f"[CALL {self.call_id}] loopback start failed: {type(e).__name__}: {e}")
                traceback.print_exc()

    def on_media_settings_changed(self):
        self._settings_event.set()

    def play_media_file(self, media_path: str):
        if not media_path or not os.path.isfile(media_path):
            self.bot.log(f"[CALL {self.call_id}] media file not found: {media_path}")
            return
        with self.player_lock:
            self._cleanup_player_unlocked()
            try:
                self.player = self.bot.audio.start_media_file(
                    media_path, self._transmit_level())
                self.bot.log(
                    f"[CALL {self.call_id}] media -> {self.bot.audio.config.loopback_sink_name}: "
                    f"{os.path.basename(media_path)} level={self._transmit_level():.2f}")
            except Exception:
                self.bot.log(f"[CALL {self.call_id}] failed to play {media_path}")