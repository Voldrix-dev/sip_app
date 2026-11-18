import threading
from unittest.mock import Mock, patch

from sipbot.account import BotAccount


def test_make_outgoing_call_uses_bot_call_instance_api():
    acc = BotAccount.__new__(BotAccount)
    acc.bot = Mock()
    acc.bot.register_current_thread = Mock()
    acc.bot.log = Mock()
    acc.bot.config = Mock(sip_domain="pbx.local")
    acc.calls = {}
    acc.active_call_id = None
    acc.state_lock = threading.Lock()
    acc.notify_calls_changed = Mock()

    call = Mock()
    call.getId.return_value = 42
    call.call_id = None
    call.makeCall = Mock()
    prm = Mock()

    with patch("sipbot.account.BotCall", return_value=call), \
         patch("sipbot.account.CallOpParam", return_value=prm):
        assert acc.make_outgoing_call("112") is True

    call.makeCall.assert_called_once_with("sip:112@pbx.local", prm)
    assert call.call_id == 42
    assert acc.calls[42] is call
    assert acc.active_call_id == 42
    acc.notify_calls_changed.assert_called_once()
    acc.bot.log.assert_any_call("[BOT] outgoing call started: sip:112@pbx.local")
