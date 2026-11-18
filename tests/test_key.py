from evdev import UInput, ecodes

KEYS = {
    "pl": ecodes.KEY_PLAYPAUSE,
    "nx": ecodes.KEY_NEXTSONG,
    "pv": ecodes.KEY_PREVIOUSSONG,
    "st": ecodes.KEY_STOPCD,
    "vup": ecodes.KEY_VOLUMEUP,
    "vdw": ecodes.KEY_VOLUMEDOWN,
    "mt": ecodes.KEY_MUTE,
}

ui = UInput({ecodes.EV_KEY: list(KEYS.values())}, name="Python Media Controller")

def media_key(name):
    code = KEYS[name]
    ui.write(ecodes.EV_KEY, code, 1)
    ui.write(ecodes.EV_KEY, code, 0)
    ui.syn()

try:
    while True:
        cmd = input("media> ").strip().lower()
        if cmd in KEYS:
            media_key(cmd)
        elif cmd in ("q", "quit", "exit"):
            break
        elif cmd:
            print("Доступно:", ", ".join(KEYS))

finally:
    ui.close()