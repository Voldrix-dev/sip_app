import threading
import traceback
from .call import BotCall
from pjsua2 import Account, CallOpParam

class BotAccount(Account):
    def __init__(self, ep, cfg, bot):
        super().__init__()
        self.ep = ep
        self.bot = bot
        self.calls = {}
        self.active_call_id = None
        self.state_lock = threading.Lock()
        self.create(cfg)

    def get_active_call_id(self):
        with self.state_lock:
            return self.active_call_id

    def set_active_call_id(self, call_id):
        with self.state_lock:
            self.active_call_id = call_id

    def remove_call(self, call_id):
        with self.state_lock:
            self.calls.pop(call_id, None)
            remaining_calls = list(self.calls.values())
        self.notify_calls_changed()
        for call in remaining_calls:
            try:
                call.on_media_settings_changed()
                call.sync_local_monitor_from_settings(force_reconnect=True)
                if self.bot.get_runtime_settings().get("media_source") in ("media", "loopback"):
                    call.start_loopback_to_call(force_reconnect=True)
            except Exception as e:
                self.bot.log(f"[ACCOUNT] re-sync call {call.call_id} failed: {e}")

    def get_calls_snapshot(self):
        with self.state_lock:
            return list(self.calls.items())

    def get_call_by_id(self, call_id):
        with self.state_lock:
            return self.calls.get(call_id)

    def notify_calls_changed(self):
        self.bot.register_current_thread("notify-calls")
        calls_list = []
        with self.state_lock:
            calls_copy = list(self.calls.values())
        for c in calls_copy:
            try:
                calls_list.append(c.get_info_dict())
            except Exception:
                pass
        self.bot.emit_calls(calls_list)

    def make_outgoing_call(self, target_uri: str) -> bool:
        self.bot.register_current_thread("make-outgoing-call")
        raw_uri = str(target_uri or "").strip()
        if not raw_uri:
            return False

        uri = raw_uri
        if not uri.lower().startswith(("sip:", "sips:")):
            if "@" not in uri:
                domain = str(getattr(getattr(self.bot, "config", None), "sip_domain", "") or "").strip()
                if domain:
                    uri = f"{uri}@{domain}"
            uri = f"sip:{uri}"
        else:
            if "@" not in uri.split(":", 1)[1]:
                domain = str(getattr(getattr(self.bot, "config", None), "sip_domain", "") or "").strip()
                if domain:
                    uri = uri.rstrip("/")
                    uri = uri + f"@{domain}"

        try:
            cop = CallOpParam()
            call = BotCall(self, self.bot)
            call.makeCall(uri, cop)
            call_id = call.getId()
            if call_id is not None and call_id >= 0:
                call.call_id = call_id
                with self.state_lock:
                    self.calls[call_id] = call
                    self.active_call_id = call_id
                self.notify_calls_changed()
            else:
                call.call_id = None
                self.bot.log(f"[BOT] outgoing call created but id is not ready yet: {uri}")
            self.bot.log(f"[BOT] outgoing call started: {uri}")
            return True
        except Exception as e:
            self.bot.log(f"[BOT] outgoing call failed: {uri}, {e}")
            return False

    def hangup_call(self, call_id) -> bool:
        self.bot.register_current_thread("hangup-call")
        call = self.get_call_by_id(call_id)
        if not call:
            return False
        try:
            call.stop_flag.set()
            wt = getattr(call, "worker_thread", None)
            if wt and wt.is_alive():
                wt.join(timeout=1)
            cop = CallOpParam()
            cop.statusCode = 486
            call.hangup(cop)
            self.bot.log(f"[BOT] hung up call {call_id}")
            self.remove_call(call_id)
            return True
        except Exception as e:
            self.bot.log(f"[BOT] hangup call {call_id} failed: {e}")
            return False

    def hangup_all_calls(self):
        self.bot.register_current_thread("hangup-all")
        for call_id, call in self.get_calls_snapshot():
            try:
                call.stop_flag.set()
                wt = getattr(call, "worker_thread", None)
                if wt and wt.is_alive():
                    wt.join(timeout=1)
                cop = CallOpParam()
                cop.statusCode = 486
                call.hangup(cop)
            except Exception as e:
                self.bot.log(f"[BOT] hangup_all call {call_id} failed: {e}")
        with self.state_lock:
            self.calls.clear()
            self.active_call_id = None
        self.bot.log("[BOT] hung up all calls")
        self.notify_calls_changed()

    def onIncomingCall(self, prm):
        try:
            if self.bot._shutting_down.is_set() or self.bot.stop_event.is_set():
                self.bot.log(f"[BOT] ignoring incoming call during shutdown: {prm.callId}")
                return
            self.bot.register_current_thread("incoming-call")
            call_id = prm.callId
            max_calls = int(getattr(self.bot.config, "max_calls", 32))
            if call_id < 0 or call_id >= max_calls:
                self.bot.log(f"[BOT] ignoring invalid call id: {call_id}")
                return
            self.bot.log(f"[BOT] incoming call: {call_id}")
            self.bot.emit_state("call", "incoming")
            call_mode = self.bot.get_runtime_settings().get("call_mode", "single")
            if call_mode == "single":
                for old_id, old_call in self.get_calls_snapshot():
                    old_call.stop_flag.set()
                    wt = getattr(old_call, "worker_thread", None)
                    if wt and wt.is_alive():
                        wt.join(timeout=2)
                    try:
                        cop = CallOpParam()
                        cop.statusCode = 486
                        old_call.hangup(cop)
                    except Exception:
                        pass
                    self.remove_call(old_id)
            call = BotCall(self, self.bot, call_id)
            with self.state_lock:
                self.calls[call_id] = call
                self.active_call_id = call_id
            cop = CallOpParam()
            cop.statusCode = 200
            call.answer(cop)
            self.bot.log(f"[BOT] answered 200 (callId={call_id}, mode={call_mode})")
            self.notify_calls_changed()
        except Exception:
            self.bot.log("[BOT] onIncomingCall exception:\n" + traceback.format_exc())
