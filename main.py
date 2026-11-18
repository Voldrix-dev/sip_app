import sys
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from gui.config import THEME_PATH, PROJECT_DIR
from gui.config import load_cfg
from gui.window import MainWindow
from sipbot.audio import prepare_loopback_sink
from sipbot.config import SipBotConfig

import qtawesome as qta

def main():
    cfg = load_cfg()
    audio_config = SipBotConfig(
        media_source="media" if cfg.get("media_source", "media") == "wav" else cfg.get("media_source", "media"),
        loopback_auto_create_sink=bool(cfg.get("loopback_auto_create_sink", True)),
        loopback_sink_name=str(cfg.get("loopback_sink_name", "sipbot_sink")),
        loopback_sink_description=str(cfg.get("loopback_sink_description", "SIPBotSink")),
    )
    if not prepare_loopback_sink(audio_config):
        print("Warning: loopback audio preparation failed")
    app = QApplication(sys.argv)
    app_icon = qta.icon("fa5s.robot", color="#4FC3F7")
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon)
    app.setApplicationName("SIP_BOT")
    app.setApplicationDisplayName("SIP Bot")
    app.setDesktopFileName("SIP_BOT")
    app.setOrganizationName("pcuser")
    if os.path.exists(THEME_PATH):
        try:
            with open(THEME_PATH, "r", encoding="utf-8") as f:
                app.setStyleSheet(f.read())
        except Exception:
            pass
    win = MainWindow()
    win.setWindowIcon(app_icon)
    win.setWindowFlags(win.windowFlags() & ~Qt.WindowMinimizeButtonHint & ~Qt.WindowMaximizeButtonHint & ~Qt.WindowMinMaxButtonsHint)
    win.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())