import os
import json
from pathlib import Path

LOG_COLORS = {
    "CORE": "#4FC3F7",
    "GUI": "#81C784",
    "AUDIO": "#FFB74D",
    "BOT": "#BA68C8",
    "CALL": "#26C6DA",
    "WATCHDOG": "#EF5350",
    "SIP": "#FFD54F",
}

PROJECT_DIR = Path(__file__).resolve().parent.parent

ICON_DIR = PROJECT_DIR / "icons"
CFG_PATH = PROJECT_DIR / "data/config.json"
THEME_PATH = PROJECT_DIR / "data/dark.qss"
BOT_ICON = PROJECT_DIR / "icons/bot.png"
SONG_DIR = PROJECT_DIR / "song"

def load_cfg():
    default = {
        "sip_domain": "10.10.2.4",
        "sip_user": "316",
        "sip_pass": "316316",
        "sip_port": 0,
        "sip_realm": "*",
        "max_calls": 32,
        "wav_dir": os.path.join(PROJECT_DIR, "song"),
        "media_source": "media",
        "call_mode": "single",
        "playback_mode": "loop",
        "transmit_volume_pct": 100,
        "loopback_capture_dev": -1,
        "monitor_call_audio": False,
        "audio_playback_dev": -1,
        "audio_capture_dev": -1,
        "audio_clock_rate": 16000,
        "audio_snd_clock_rate": 48000,
        "audio_frame_ptime": 20,
        "audio_snd_rec_latency": 200,
        "audio_snd_play_latency": 200,
        "audio_snd_auto_close_time": -1,
        "audio_ec_tail_len": 0,
        "loopback_auto_create_sink": True,
        "loopback_sink_name": "sipbot_sink",
        "loopback_sink_description": "SIPBotSink",
        "loopback_auto_select_playback": True,
        "loopback_auto_select_capture": True,
    }
    if not os.path.exists(CFG_PATH):
        return default
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        default.update(data)
    except Exception:
        pass
    return default

def save_cfg(data):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)