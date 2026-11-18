from dataclasses import dataclass


@dataclass
class SipBotConfig:
    sip_domain: str = "10.10.2.4"
    sip_user: str = "316"
    sip_pass: str = "316316"
    sip_port: int = 0
    sip_realm: str = "*"
    user_agent: str = "Mitel 6865i/5.0.0.1016"
    #user_agent: str = "Aastra MX-ONE SN/17.4.1.0.17"
    #user_agent: str = "801/1.6.0.79"
    reg_timeout_sec: int = 600
    keep_alive_sec: int = 25
    max_calls: int = 32
    wav_dir: str = "/home/pcuser/sip_bot/song"
    media_source: str = "media"  # media | loopback
    call_mode: str = "single"  # single | broadcast
    playback_mode: str = "loop"  # loop | shuffle | repeat_one
    transmit_volume_pct: int = 100
    audio_playback_dev: int = -1
    audio_capture_dev: int = -1
    monitor_call_audio: bool = False
    audio_clock_rate: int = 16000
    audio_snd_clock_rate: int = 48000
    audio_frame_ptime: int = 20
    audio_snd_rec_latency: int = 200
    audio_snd_play_latency: int = 200
    audio_snd_auto_close_time: int = -1
    audio_ec_tail_len: int = 0
    loopback_auto_create_sink: bool = True
    loopback_sink_name: str = "sipbot_sink"
    loopback_sink_description: str = "SIPBotSink"
    loopback_auto_select_playback: bool = True
    loopback_auto_select_capture: bool = True