import threading
import time
import traceback
from typing import Callable, Optional
from .config import SipBotConfig
from .account import BotAccount
from .audio import AudioManager, configure_pulse_source
from pjsua2 import (AccountConfig, AuthCredInfo, CallOpParam, Endpoint,
                    EpConfig, TransportConfig, PJSIP_TRANSPORT_UDP)

LogFn = Callable[[str], None]
StateFn = Callable[[str, str], None]

CallsFn = Callable[[list], None]

class SipBotCore:
    def __init__(self, config: SipBotConfig, log_fn: Optional[LogFn] = None, state_fn: Optional[StateFn] = None, calls_fn: Optional[CallsFn] = None):
        self.config = config
        self.config.media_source = self._normalize_media_source(self.config.media_source)
        self.log_fn = log_fn
        self.state_fn = state_fn
        self.calls_fn = calls_fn
        self.stop_event = threading.Event()
        self._shutting_down = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._runtime_lock = threading.Lock()
        self._ep = None
        self._acc = None
        self.audio = AudioManager(config, self.log, self.register_current_thread)
        self._runtime_settings = {
            "wav_dir": self.config.wav_dir,
            "media_source": self._normalize_media_source(self.config.media_source),
            "call_mode": self._normalize_call_mode(self.config.call_mode),
            "playback_mode": self._normalize_playback_mode(self.config.playback_mode),
            "transmit_volume_pct": self._normalize_volume_pct(self.config.transmit_volume_pct),
            "loopback_capture_dev": int(self.config.audio_capture_dev or -1),
            "monitor_call_audio": bool(self.config.monitor_call_audio),
        }

    def log(self, text: str):
        if self.log_fn:
            self.log_fn(text)

    def emit_state(self, key: str, value: str):
        if self.state_fn:
            self.state_fn(key, value)

    def register_current_thread(self, name="external-thread"):
        ep = self._ep
        if ep is not None:
            try:
                if hasattr(ep, "libIsThreadRegistered") and hasattr(ep, "libRegisterThread"):
                    if not ep.libIsThreadRegistered():
                        ep.libRegisterThread(name)
            except Exception:
                pass

    def emit_calls(self, calls_list: list):
        if self.calls_fn:
            self.calls_fn(calls_list)

    def make_outgoing_call(self, target_uri: str) -> bool:
        self.register_current_thread("gui-make-call")
        acc = self._acc
        if not acc:
            return False
        return acc.make_outgoing_call(target_uri)

    def hangup_call(self, call_id) -> bool:
        self.register_current_thread("gui-hangup")
        acc = self._acc
        if not acc:
            return False
        return acc.hangup_call(call_id)

    def hangup_all_calls(self) -> bool:
        self.register_current_thread("gui-hangup-all")
        acc = self._acc
        if not acc:
            return False
        acc.hangup_all_calls()
        return True

    def is_running(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive())

    def _normalize_playback_mode(self, mode: str) -> str:
        if mode in ("loop", "shuffle", "repeat_one"):
            return mode
        return "loop"

    def _normalize_media_source(self, source: str) -> str:
        if source == "wav":
            return "media"
        if source in ("media", "loopback"):
            return source
        return "media"

    def _normalize_call_mode(self, mode: str) -> str:
        if mode in ("single", "broadcast"):
            return mode
        return "single"

    def _normalize_volume_pct(self, value) -> int:
        try:
            pct = int(value)
        except Exception:
            pct = 100
        return max(0, min(200, pct))

    def get_runtime_settings(self):
        with self._runtime_lock:
            return dict(self._runtime_settings)

    def update_runtime_settings(self, wav_dir=None, media_source=None, call_mode=None, playback_mode=None,
        transmit_volume_pct=None, loopback_capture_dev=None, monitor_call_audio=None):
        with self._runtime_lock:
            if wav_dir is not None:
                self._runtime_settings["wav_dir"] = wav_dir
                self.config.wav_dir = wav_dir
            if media_source is not None:
                normalized_source = self._normalize_media_source(media_source)
                self._runtime_settings["media_source"] = normalized_source
                self.config.media_source = normalized_source
            if call_mode is not None:
                normalized_call_mode = self._normalize_call_mode(call_mode)
                self._runtime_settings["call_mode"] = normalized_call_mode
                self.config.call_mode = normalized_call_mode
            if playback_mode is not None:
                normalized = self._normalize_playback_mode(playback_mode)
                self._runtime_settings["playback_mode"] = normalized
                self.config.playback_mode = normalized
            if transmit_volume_pct is not None:
                volume = self._normalize_volume_pct(transmit_volume_pct)
                self._runtime_settings["transmit_volume_pct"] = volume
                self.config.transmit_volume_pct = volume
            if loopback_capture_dev is not None:
                dev = int(loopback_capture_dev)
                self._runtime_settings["loopback_capture_dev"] = dev
                self.config.audio_capture_dev = dev
            if monitor_call_audio is not None:
                enabled = bool(monitor_call_audio)
                self._runtime_settings["monitor_call_audio"] = enabled
                self.config.monitor_call_audio = enabled
            snapshot = dict(self._runtime_settings)
        if snapshot["call_mode"] == "single":
            self._enforce_single_call_mode()
        self._notify_active_call_settings_changed()
        return snapshot

    def _enforce_single_call_mode(self):
        acc = self._acc
        if not acc:
            return
        active_id = acc.get_active_call_id()
        for call_id, call in acc.get_calls_snapshot():
            if call_id == active_id:
                continue
            call.stop_flag.set()

    def _notify_active_call_settings_changed(self):
        acc = self._acc
        if not acc:
            return
        for _, call in acc.get_calls_snapshot():
            call.on_media_settings_changed()

    def request_track_skip(self, direction: int) -> bool:
        acc = self._acc
        if not acc:
            return False
        if self.get_runtime_settings().get("call_mode") == "broadcast":
            ok = False
            for _, call in acc.get_calls_snapshot():
                try:
                    call.request_skip(direction)
                    ok = True
                except Exception as e:
                    self.log(f"[CORE] skip request failed: {e}")
            return ok
        call_id = acc.get_active_call_id()
        if call_id is None:
            return False
        call = acc.get_call_by_id(call_id)
        if not call:
            return False
        try:
            call.request_skip(direction)
            return True
        except Exception as e:
            self.log(f"[CORE] skip request failed: {e}")
            return False

    def set_capture_device(self, device_id: int) -> bool:
        return self.audio.set_capture_device(self._ep, device_id)

    def list_audio_devices(self):
        return self.audio.list_devices(self._ep)

    def set_audio_devices(self, playback_id: int, capture_id: int) -> bool:
        return self.audio.set_devices(self._ep, playback_id, capture_id)

    def start(self) -> bool:
        with self._lock:
            if self.is_running():
                self.log("[CORE] already running")
                return False
            self.stop_event.clear()
            self._shutting_down.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            self.log("[CORE] start requested")
            return True

    def stop(self, timeout: float = 10.0) -> bool:
        self._shutting_down.set()
        self.register_current_thread("gui-stop")
        with self._lock:
            if not self.is_running():
                self._thread = None
                return True
            self.stop_event.set()
            thread = self._thread
        thread.join(timeout)
        with self._lock:
            if thread.is_alive():
                self.log("[CORE] stop timeout")
                return False
            self._thread = None
            return True
        
    def restart(self, timeout: float = 10.0) -> bool:
        self.log("[CORE] restarting...")
        if not self.stop(timeout):
            self.log("[CORE] restart failed: stop timeout")
            return False
        return self.start()

    def _run(self):
        ep = Endpoint()
        acc = None
        self._ep = ep
        try:
            self.log("[CORE] start")
            self.emit_state("process", "starting")
            ep.libCreate()
            ep_cfg = EpConfig()
            ep_cfg.uaConfig.maxCalls = max(1, int(self.config.max_calls))
            self.log(f"[CORE] configuring max calls: {ep_cfg.uaConfig.maxCalls}")
            ep_cfg.uaConfig.userAgent = self.config.user_agent
            ep_cfg.uaConfig.natTypeInSdp = 1
            ep_cfg.uaConfig.allowContactRewrite = True
            self.audio.configure_media_clock(ep_cfg)
            if self.config.media_source in ("media", "loopback"):
                configure_pulse_source(self.config.loopback_sink_name or "sipbot_sink")
            ep.libInit(ep_cfg)
            self.log(f"[CORE] max calls active: {ep_cfg.uaConfig.maxCalls}")
            tcfg = TransportConfig()
            tcfg.port = self.config.sip_port
            ep.transportCreate(PJSIP_TRANSPORT_UDP, tcfg)
            ep.libStart()
            self.audio.configure_devices(ep)
            with self._runtime_lock:
                self._runtime_settings["loopback_capture_dev"] = int(
                    self.config.audio_capture_dev or -1)
            acc_cfg = AccountConfig()
            acc_cfg.idUri = f"sip:{self.config.sip_user}@{self.config.sip_domain}"
            acc_cfg.regConfig.registrarUri = f"sip:{self.config.sip_domain}"
            acc_cfg.regConfig.timeoutSec = self.config.reg_timeout_sec
            acc_cfg.sipConfig.keepAliveIntervalSec = self.config.keep_alive_sec
            acc_cfg.sipConfig.allowContactRewrite = True
            acc_cfg.regConfig.registerOnAdd = True
            cred = AuthCredInfo("digest", self.config.sip_realm, self.config.sip_user, 0, self.config.sip_pass)
            acc_cfg.sipConfig.authCreds.append(cred)
            acc = BotAccount(ep, acc_cfg, self)
            self._acc = acc
            self.emit_state("process", "running")
            while not self.stop_event.is_set():
                time.sleep(5)
                try:
                    info = acc.getInfo()
                except Exception as e:
                    self.log(f"[WATCHDOG] getInfo failed: {e}")
                    continue
                if info.regStatus in (200, 202):
                    self.emit_state("registration", "ok")
                else:
                    self.emit_state("registration", "lost")
                    self.log(f"[WATCHDOG] registration lost: {info.regStatus}")
                    try:
                        acc.setRegistration(True)
                    except Exception as e:
                        self.log(f"[WATCHDOG] setRegistration failed: {e}")
                    continue
                if info.regExpiresSec and info.regExpiresSec < 40:
                    self.log(f"[WATCHDOG] expires in {info.regExpiresSec}, refreshing")
                    try:
                        acc.setRegistration(True)
                    except Exception as e:
                        self.log(f"[WATCHDOG] refresh failed: {e}")
        except Exception:
            self.log("[CORE] fatal:\n" + traceback.format_exc())
        finally:
            self._shutting_down.set()
            self.emit_state("process", "stopping")
            try:
                if acc is not None:
                    if acc.get_calls_snapshot():
                        acc.hangup_all_calls()
                    workers = []
                    for _, c in acc.get_calls_snapshot():
                        try:
                            c.stop_flag.set()
                            wt = getattr(c, "worker_thread", None)
                            if wt:
                                workers.append(wt)
                            cop = CallOpParam()
                            cop.statusCode = 486
                            c.hangup(cop)
                        except Exception:
                            pass
                    for t in workers:
                        if t and t.is_alive():
                            t.join(1)
                    with acc.state_lock:
                        acc.calls.clear()
                        acc.active_call_id = None
            except Exception:
                pass
            self._acc = None
            del acc
            import gc
            gc.collect()
            try:
                ep.libDestroy()
            except Exception:
                pass
            self._ep = None
            self._thread = None
            self.stop_event.clear()
            self.emit_state("registration", "lost")
            self.emit_state("process", "stopped")
            self.log("[CORE] stopped")