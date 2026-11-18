
import os
import time
import re
import subprocess
import threading
import qtawesome as qta
from PySide6.QtCore import QObject, Signal, QTimer, Qt, QSize
from PySide6.QtWidgets import (QDialog, QMessageBox, QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMainWindow, QPushButton, QSlider, QTextEdit, QVBoxLayout,
                               QWidget, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView)
from sipbot.config import SipBotConfig
from sipbot.core import SipBotCore
from sipbot.audio import MediaController

from .config import load_cfg, save_cfg, LOG_COLORS, PROJECT_DIR
from .bridge import Bridge


class _OperationWorker(QObject):
    finished = Signal(bool)


class _AudioRouterDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Audio Router")
        self.resize(850, 450)

        layout = QVBoxLayout(self)

        self.status = QLabel("Searching audio streams...")
        layout.addWidget(self.status)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["ID", "Application", "PID", "Binary", "Media", "Sink"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.sipbot_button = QPushButton("→ SIPBotSink")
        self.default_button = QPushButton("→ System default")

        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.sipbot_button)
        buttons.addWidget(self.default_button)
        buttons.addStretch()

        layout.addLayout(buttons)

        self.refresh_button.clicked.connect(self.refresh)
        self.sipbot_button.clicked.connect(lambda: self.move_selected("sipbot_sink"))
        self.default_button.clicked.connect(self.move_to_default)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)

        self.refresh()

    @staticmethod
    def _pactl(*args):
        try:
            return subprocess.run(["pactl", *args], text=True, capture_output=True, check=False, timeout=2)
        except FileNotFoundError:
            return None

    def get_sink_inputs(self):
        result = self._pactl("list", "sink-inputs")
        if result is None or result.returncode != 0:
            return []

        streams = []
        current = None
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("Sink Input #"):
                if current:
                    streams.append(current)
                current = {
                    "id": stripped.split("#", 1)[1],
                    "application": "",
                    "pid": "",
                    "binary": "",
                    "media": "",
                    "sink": "",
                }
            elif stripped.startswith("Вход аудиоприёмника №"):
                if current:
                    streams.append(current)
                match = re.search(r"№(\d+)", stripped)
                current = {
                    "id": match.group(1) if match else stripped.split("№", 1)[1],
                    "application": "",
                    "pid": "",
                    "binary": "",
                    "media": "",
                    "sink": "",
                }
            elif current:
                if stripped.startswith("Sink:") or stripped.startswith("Аудиоприёмник:"):
                    current["sink"] = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("application.name"):
                    current["application"] = self.get_value(stripped)
                elif stripped.startswith("application.process.id"):
                    current["pid"] = self.get_value(stripped)
                elif stripped.startswith("application.process.binary"):
                    current["binary"] = self.get_value(stripped)
                elif stripped.startswith("media.name"):
                    current["media"] = self.get_value(stripped)

        if current:
            streams.append(current)
        return streams

    @staticmethod
    def get_value(line):
        value = line.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        return value

    def get_selected_id(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        item = self.table.item(row, 0)
        return item.text() if item else None

    def refresh(self):
        selected_id = self.get_selected_id()
        streams = self.get_sink_inputs()
        self.table.setRowCount(0)

        for stream in streams:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                stream["id"],
                stream["application"],
                stream["pid"],
                stream["binary"],
                stream["media"],
                stream["sink"],
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))

        if selected_id:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).text() == selected_id:
                    self.table.selectRow(row)
                    break

        self.status.setText(f"Active audio streams: {len(streams)}")

    def move_selected(self, sink):
        input_id = self.get_selected_id()
        if not input_id:
            self.status.setText("Select an audio stream first")
            return

        result = self._pactl("move-sink-input", input_id, sink)
        if result is not None and result.returncode == 0:
            self.status.setText(f"Stream {input_id} → {sink}")
        else:
            error = (result.stderr or result.stdout).strip() if result else "pactl not available"
            self.status.setText(f"Error: {error}")
        self.refresh()

    def move_to_default(self):
        result = self._pactl("get-default-sink")
        if result is None or result.returncode != 0:
            self.status.setText("Could not determine default sink")
            return

        sink = result.stdout.strip()
        if not sink:
            self.status.setText("Default sink not found")
            return

        self.move_selected(sink)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = load_cfg()
        self.bot = None
        self.media_controller = MediaController()
        self.bridge = Bridge()
        self._op_thread = None
        self._op_worker = None
        self.current_calls = []
        self.bridge.log_signal.connect(self.append_log)
        self.bridge.state_signal.connect(self.on_state)
        self.bridge.calls_signal.connect(self.on_calls_updated)
        self.call_started_at = None
        self.call_timer = QTimer(self)
        self.call_timer.setInterval(1000)
        self.call_timer.timeout.connect(self._tick_call_time)
        self._build_ui()
        self._update_transmit_volume_label()

    def _build_ui(self):
        self.setWindowTitle("SIP Bot")
        self.setMinimumSize(1020, 640)
        self.resize(1020, 680)

        root = QWidget()
        self.setCentralWidget(root)

        main = QVBoxLayout(root)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(10)

        basic_layout = QVBoxLayout()
        basic_layout.setSpacing(10)
        main.addLayout(basic_layout)

        top = QHBoxLayout()
        top.setSpacing(8)

        title = QLabel("Control Center")
        title.setObjectName("titleLabel")

        self.btn_start = QPushButton()
        self.btn_stop = QPushButton()
        self.btn_restart = QPushButton()
        self.btn_clear = QPushButton()

        self.btn_start.setIcon(qta.icon("fa5s.play", color="#66BB6A"))
        self.btn_stop.setIcon(qta.icon("fa5s.stop", color="#EF5350"))
        self.btn_restart.setIcon(qta.icon("fa5s.redo-alt", color="#FFB74D"))
        self.btn_clear.setIcon(qta.icon("fa5s.trash-alt", color="#B0BEC5"))

        for btn in (self.btn_start, self.btn_stop, self.btn_restart, self.btn_clear):
            btn.setFixedSize(40, 40)
            btn.setIconSize(QSize(28, 28))

        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.btn_start)
        top.addWidget(self.btn_stop)
        top.addWidget(self.btn_restart)
        top.addWidget(self.btn_clear)
        basic_layout.addLayout(top)

        self.e_domain = QLineEdit(str(self.cfg.get("sip_domain", "")))
        self.e_domain.setMinimumWidth(80)
        self.e_user = QLineEdit(str(self.cfg.get("sip_user", "")))
        self.e_pass = QLineEdit(str(self.cfg.get("sip_pass", "")))
        self.e_realm = QLineEdit(str(self.cfg.get("sip_realm", "mxone")))
        self.e_port = QLineEdit(str(self.cfg.get("sip_port", 0)))
        self.e_wav = QLineEdit(str(self.cfg.get("wav_dir", "")))
        self.e_capture_dev = QLineEdit(str(self.cfg.get("loopback_capture_dev", -1)))

        self.e_pass.setEchoMode(QLineEdit.Password)
        self.e_capture_dev.setFixedWidth(90)
        self.e_port.setPlaceholderText("5060")
        self.e_capture_dev.setPlaceholderText("-1")
        self.e_domain.setPlaceholderText("10.10.2.4")
        self.e_user.setPlaceholderText("316")
        self.e_realm.setPlaceholderText("mxone")

        top_content = QHBoxLayout()
        top_content.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(10)

        group_sip = QGroupBox("SIP Account")
        sip = QGridLayout(group_sip)

        sip.addWidget(QLabel("Domain"), 0, 0)
        sip.addWidget(self.e_domain, 0, 1)
        sip.addWidget(QLabel("Port"), 0, 2)
        sip.addWidget(self.e_port, 0, 3)
        sip.addWidget(QLabel("Realm"), 0, 4)
        sip.addWidget(self.e_realm, 0, 5)
        sip.addWidget(QLabel("User"), 1, 0)
        sip.addWidget(self.e_user, 1, 1)
        sip.addWidget(QLabel("Password"), 1, 2)
        sip.addWidget(self.e_pass, 1, 3)
        sip.addWidget(QLabel("Status"), 1, 4)

        self.lbl_reg_status = QLabel("● Offline")
        self.lbl_reg_status.setStyleSheet("color: #EF5350; font-weight: bold; font-size: 13px;")
        sip.addWidget(self.lbl_reg_status, 1, 5)

        left.addWidget(group_sip)

        group_media = QGroupBox("Media")
        media = QGridLayout(group_media)

        media.setHorizontalSpacing(8)
        media.setVerticalSpacing(8)

        self.cb_source = QComboBox()
        self.cb_source.addItem("Media files", "media")
        self.cb_source.addItem("Loopback device", "loopback")

        source_from_cfg = str(self.cfg.get("media_source", "media"))
        if source_from_cfg == "wav":
            source_from_cfg = "media"
        source_idx = max(0, self.cb_source.findData(source_from_cfg))
        self.cb_source.setCurrentIndex(source_idx)

        self.cb_call_mode = QComboBox()
        self.cb_call_mode.addItem("Single", "single")
        self.cb_call_mode.addItem("Broadcast", "broadcast")

        call_mode_from_cfg = str(self.cfg.get("call_mode", "single"))
        call_mode_idx = max(0, self.cb_call_mode.findData(call_mode_from_cfg))
        self.cb_call_mode.setCurrentIndex(call_mode_idx)

        self.cb_mode = QComboBox()
        self.cb_mode.addItem("Loop", "loop")
        self.cb_mode.addItem("Shuffle", "shuffle")
        self.cb_mode.addItem("Repeat One", "repeat_one")

        mode_from_cfg = str(self.cfg.get("playback_mode", "loop"))
        mode_idx = max(0, self.cb_mode.findData(mode_from_cfg))
        self.cb_mode.setCurrentIndex(mode_idx)

        self.transmit_volume = int(self.cfg.get("transmit_volume_pct", 100))
        
        self.btn_media = QPushButton()
        self.btn_apply_media = QPushButton()
        self.btn_audio_router = QPushButton()
        self.btn_prev = QPushButton()
        self.btn_next = QPushButton()
        self.btn_play_media = QPushButton()
        self.btn_vol_up = QPushButton()
        self.btn_vol_down = QPushButton()
        self.btn_mute = QPushButton()
        self.btn_transmit_vol_down = QPushButton()
        self.btn_transmit_vol_up = QPushButton()

        self.btn_media.setIcon(qta.icon("fa5s.folder-open", color="#E0E0E0"))
        self.btn_apply_media.setIcon(qta.icon("fa5s.check", color="#66BB6A"))
        self.btn_audio_router.setIcon(qta.icon("fa5s.route", color="#E0E0E0"))
        self.btn_prev.setIcon(qta.icon("fa5s.step-backward", color="#E0E0E0"))
        self.btn_next.setIcon(qta.icon("fa5s.step-forward", color="#E0E0E0"))
        self.btn_play_media.setIcon(qta.icon("fa5s.play", color="#E0E0E0"))
        self.btn_vol_up.setIcon(qta.icon("fa5s.volume-up", color="#E0E0E0"))
        self.btn_vol_down.setIcon(qta.icon("fa5s.volume-down", color="#E0E0E0"))
        self.btn_mute.setIcon(qta.icon("fa5s.volume-mute", color="#E0E0E0"))
        self.btn_transmit_vol_up.setIcon(qta.icon("fa5s.volume-up", color="#E0E0E0"))
        self.btn_transmit_vol_down.setIcon(qta.icon("fa5s.volume-down", color="#E0E0E0"))

        for btn in (self.btn_media, self.btn_apply_media, self.btn_prev, self.btn_next, self.btn_play_media, self.btn_audio_router,
                    self.btn_vol_down, self.btn_vol_up, self.btn_mute, self.btn_transmit_vol_down, self.btn_transmit_vol_up):
            btn.setFixedSize(28, 28)
            btn.setIconSize(QSize(20, 20))


        source_box = QGroupBox("Source")
        source = QHBoxLayout(source_box)
        source.addWidget(self.cb_source)

        mode_box = QGroupBox("Play Mode")
        mode = QHBoxLayout(mode_box)
        mode.addWidget(self.cb_mode)

        media_dir_box = QGroupBox("Media Directory")
        media_dir = QHBoxLayout(media_dir_box)
        media_dir.addWidget(self.e_wav)
        media_dir.addWidget(self.btn_media)
        
        capture_dev_box = QGroupBox("Capture Device")
        capture_dev = QHBoxLayout(capture_dev_box)
        capture_dev.addWidget(self.e_capture_dev)

        call_mode_box = QGroupBox("Call Mode")
        call_mode = QHBoxLayout(call_mode_box)
        call_mode.addWidget(self.cb_call_mode)

        self.transmit_vol_box = QGroupBox("TX Vol")
        transmit_vol = QHBoxLayout(self.transmit_vol_box)
        transmit_vol.addWidget(self.btn_transmit_vol_down)
        transmit_vol.addWidget(self.btn_transmit_vol_up)

        sys_vol_box = QGroupBox("Sys Vol")
        sys_vol = QHBoxLayout(sys_vol_box)
        sys_vol.addWidget(self.btn_vol_down)
        sys_vol.addWidget(self.btn_vol_up)
        sys_vol.addWidget(self.btn_mute)

        self.media_control_box = QGroupBox("Media Controls")
        media_control = QHBoxLayout(self.media_control_box)
        media_control.addWidget(self.btn_prev)
        media_control.addWidget(self.btn_play_media)
        media_control.addWidget(self.btn_next)

        controls = QHBoxLayout()
        controls.addWidget(self.btn_apply_media)
        controls.addStretch()
        controls.addWidget(self.btn_audio_router)

        media.addWidget(source_box, 0, 0)
        media.addWidget(capture_dev_box, 0, 1)
        media.addWidget(mode_box, 0, 2)
        media.addWidget(media_dir_box, 1, 0, 1, 2)
        media.addWidget(call_mode_box, 1, 2)
        media.addWidget(self.transmit_vol_box, 3, 1)
        media.addWidget(sys_vol_box, 3, 0)
        media.addWidget(self.media_control_box, 3, 2)
        media.addLayout(controls, 6, 0, 1, 3)

        left.addWidget(group_media)
        left.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(10)

        group_callers = QGroupBox("Active Callers")
        callers_layout = QVBoxLayout(group_callers)
        callers_layout.setContentsMargins(8, 8, 8, 8)
        callers_layout.setSpacing(6)

        callers_top = QHBoxLayout()

        self.lbl_callers_count = QLabel("0 active")

        self.e_outgoing_number = QLineEdit()
        self.e_outgoing_number.setPlaceholderText("Number or sip:user@domain")
        self.e_outgoing_number.setMinimumWidth(180)
        self.e_outgoing_number.returnPressed.connect(self.make_outgoing_call)

        self.btn_hangup_all = QPushButton()
        self.btn_hangup_all.setIcon(qta.icon("fa5s.phone-slash", color="#EF5350"))
        self.btn_hangup_all.clicked.connect(self.hangup_all_calls)

        self.btn_make_call = QPushButton()
        self.btn_make_call.setIcon(qta.icon("fa5s.phone", color="#66BB6A"))
        self.btn_make_call.setToolTip("Make call")
        self.btn_make_call.clicked.connect(self.make_outgoing_call)

        callers_top.addWidget(self.lbl_callers_count)
        callers_top.addStretch(1)
        callers_top.addWidget(self.e_outgoing_number)
        callers_top.addWidget(self.btn_make_call)
        callers_top.addWidget(self.btn_hangup_all)

        callers_layout.addLayout(callers_top)

        self.table_callers = QTableWidget()
        self.table_callers.setColumnCount(5)
        self.table_callers.setHorizontalHeaderLabels(["ID", "Caller", "Status", "Duration", "Action"])

        self.table_callers.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_callers.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_callers.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table_callers.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table_callers.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)

        self.table_callers.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_callers.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_callers.setMinimumHeight(150)

        callers_layout.addWidget(self.table_callers)
        right.addWidget(group_callers)

        top_content.addLayout(left, 1)
        top_content.addLayout(right, 1)

        basic_layout.addLayout(top_content)

        log_group = QGroupBox("Log")
        log_layout = QVBoxLayout(log_group)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QTextEdit.NoWrap)
        self.log.setObjectName("logView")

        log_layout.addWidget(self.log)
        basic_layout.addWidget(log_group, 1)

        self.btn_media.clicked.connect(self.pick_wav_dir)
        self.btn_start.clicked.connect(self.start_bot)
        self.btn_stop.clicked.connect(self.stop_bot)
        self.btn_restart.clicked.connect(self.restart_bot)
        self.btn_clear.clicked.connect(self.log.clear)
        self.btn_apply_media.clicked.connect(self.apply_media_settings)
        self.btn_audio_router.clicked.connect(self.open_audio_router_dialog)
        self.btn_prev.clicked.connect(self.prev_track)
        self.btn_next.clicked.connect(self.next_track)
        self.btn_play_media.clicked.connect(self.play_media)
        self.btn_vol_up.clicked.connect(self.volume_up)
        self.btn_vol_down.clicked.connect(self.volume_down)
        self.btn_mute.clicked.connect(self.mute)
        self.cb_source.currentIndexChanged.connect(self._refresh_media_controls)
        self.btn_transmit_vol_up.clicked.connect(self.transmit_volume_up)
        self.btn_transmit_vol_down.clicked.connect(self.transmit_volume_down)

        self.e_domain.setToolTip("SIP server domain or IP")
        self.e_port.setToolTip("SIP server port (default 5060)")
        self.e_realm.setToolTip("SIP realm (default * or mxone)")
        self.e_user.setToolTip("SIP username")
        self.e_pass.setToolTip("SIP password")
        self.e_wav.setToolTip("Directory containing media files")
        self.e_capture_dev.setToolTip("Loopback capture device ID (-1 for default)")
        self.cb_source.setToolTip("Select media source")
        self.cb_mode.setToolTip("Select playback mode for media files")
        self.cb_call_mode.setToolTip("Select call mode (single or broadcast)")
        self.btn_start.setToolTip("Start SIP Bot")
        self.btn_stop.setToolTip("Stop SIP Bot")
        self.btn_restart.setToolTip("Restart SIP Bot")
        self.btn_clear.setToolTip("Clear log")
        self.btn_hangup_all.setToolTip("Hang up all active calls")
        self.btn_transmit_vol_up.setToolTip("Increase transmit volume")
        self.btn_transmit_vol_down.setToolTip("Decrease transmit volume")
        self.btn_media.setToolTip("Select media directory")
        self.btn_apply_media.setToolTip("Apply media settings")
        self.btn_prev.setToolTip("Previous track")
        self.btn_next.setToolTip("Next track")
        self.btn_play_media.setToolTip("Play/Pause")
        self.btn_vol_up.setToolTip("System volume up")
        self.btn_vol_down.setToolTip("System volume down")
        self.btn_mute.setToolTip("Mute")
        self.btn_transmit_vol_up.setToolTip("Transmit volume up")
        self.btn_transmit_vol_down.setToolTip("Transmit volume down")
        self.e_wav.setPlaceholderText("Media directory")
        self.btn_audio_router.setToolTip("Open sink input routing dialog")

        self._refresh_media_controls()

    def append_log(self, text: str):
        def repl(match):
            tag = match.group(1)
            key = tag.split()[0]          # "CALL 0" -> "CALL"
            color = LOG_COLORS.get(key, "#FFFFFF")
            return f"<span style='color:{color}; font-weight:bold'>[{tag}]</span>"
        self.log.append(re.sub(r"\[([^\]]+)\]", repl, text))

    def on_state(self, key: str, value: str):
        if key == "process":
            if value in ("starting", "connecting"):
                self.lbl_reg_status.setText("● Connecting")
                self.lbl_reg_status.setStyleSheet("color: #FFB74D; font-weight: bold; font-size: 13px;")
            elif value in ("stopped", "stopping"):
                self.lbl_reg_status.setText("● Offline")
                self.lbl_reg_status.setStyleSheet("color: #EF5350; font-weight: bold; font-size: 13px;")
                self._reset_call_time()
        elif key == "registration":
            if value == "ok":
                self.lbl_reg_status.setText("● Online")
                self.lbl_reg_status.setStyleSheet("color: #66BB6A; font-weight: bold; font-size: 13px;")
            elif value in ("lost", "failed", "unregistered"):
                self.lbl_reg_status.setText("● Reg Lost")
                self.lbl_reg_status.setStyleSheet("color: #EF5350; font-weight: bold; font-size: 13px;")
        elif key == "call":
            if value == "confirmed":
                if self.call_started_at is None:
                    self.call_started_at = time.monotonic()
                if not self.call_timer.isActive():
                    self.call_timer.start()
                self._tick_call_time()
            elif value in ("idle", "disconnected", "terminated"):
                self._reset_call_time()

    def pick_wav_dir(self):
        current = self.e_wav.text().strip() or PROJECT_DIR
        path = QFileDialog.getExistingDirectory(self, "Choose media directory", current)
        if path:
            self.e_wav.setText(path)
            self.apply_media_settings(show_message=False)

    def _update_transmit_volume_label(self):
        self.transmit_vol_box.setTitle(f"TX Vol {self.transmit_volume}%")

    def transmit_volume_up(self):
        self.transmit_volume = min(200, int(self.transmit_volume) + 5)
        self._update_transmit_volume_label()
        self.cfg["transmit_volume_pct"] = int(self.transmit_volume)
        save_cfg(self.cfg)
        if self.bot and self.bot.is_running():
            self.bot.update_runtime_settings(transmit_volume_pct=int(self.transmit_volume))

    def transmit_volume_down(self):
        self.transmit_volume = max(0, int(self.transmit_volume) - 5)
        self._update_transmit_volume_label()
        self.cfg["transmit_volume_pct"] = int(self.transmit_volume)
        save_cfg(self.cfg)
        if self.bot and self.bot.is_running():
            self.bot.update_runtime_settings(transmit_volume_pct=int(self.transmit_volume))

    def _refresh_media_controls(self):
        source = self.cb_source.currentData()
        is_media = source == "media"
        self.e_wav.setEnabled(is_media)
        self.cb_mode.setEnabled(is_media)
        self.btn_prev.setEnabled(True)
        self.btn_next.setEnabled(True)
        self.e_capture_dev.setEnabled(source in ("media", "loopback"))

    def _read_config_from_form(self):
        try:
            port = int(self.e_port.text().strip())
        except ValueError:
            raise ValueError("SIP port must be integer")
        wav_dir = self.e_wav.text().strip()
        media_source = self.cb_source.currentData()
        if media_source == "media" and not os.path.isdir(wav_dir):
            raise ValueError(f"Media directory not found: {wav_dir}")
        try:
            capture_dev = int(self.e_capture_dev.text().strip() or "-1")
        except ValueError:
            raise ValueError("Capture device id must be integer")
        audio_data = {
            key: self.cfg.get(key)
            for key in (
                "audio_playback_dev", "audio_capture_dev", "loopback_auto_select_playback",
                "loopback_auto_select_capture", "loopback_auto_create_sink",
                "loopback_sink_name", "loopback_sink_description", "audio_clock_rate",
                "audio_snd_clock_rate", "audio_frame_ptime", "audio_snd_rec_latency",
                "audio_snd_play_latency", "audio_snd_auto_close_time", "audio_ec_tail_len",
            )
        }
        data = {
            "sip_domain": self.e_domain.text().strip(),
            "sip_user": self.e_user.text().strip(),
            "sip_pass": self.e_pass.text().strip(),
            "sip_realm": self.e_realm.text().strip() or "*",
            "sip_port": port,
            "max_calls": int(self.cfg.get("max_calls", 32)),
            "wav_dir": wav_dir,
            "media_source": media_source,
            "call_mode": self.cb_call_mode.currentData(),
            "playback_mode": self.cb_mode.currentData(),
            "transmit_volume_pct": int(self.transmit_volume),
            "loopback_capture_dev": capture_dev,
            "monitor_call_audio": bool(self.cfg.get("monitor_call_audio", False)),
            **audio_data,
        }
        data["audio_capture_dev"] = capture_dev
        return data

    def _create_bot_config(self, data):
        return SipBotConfig(
            sip_domain=data["sip_domain"],
            sip_user=data["sip_user"],
            sip_pass=data["sip_pass"],
            sip_realm=data["sip_realm"],
            sip_port=data["sip_port"],
            max_calls=int(data.get("max_calls", 32)),
            wav_dir=data["wav_dir"],
            media_source=data["media_source"],
            call_mode=data["call_mode"],
            playback_mode=data["playback_mode"],
            transmit_volume_pct=data["transmit_volume_pct"],
            audio_playback_dev=data["audio_playback_dev"],
            audio_capture_dev=data["audio_capture_dev"],
            monitor_call_audio=data["monitor_call_audio"],
            audio_clock_rate=data["audio_clock_rate"],
            audio_snd_clock_rate=data["audio_snd_clock_rate"],
            audio_frame_ptime=data["audio_frame_ptime"],
            audio_snd_rec_latency=data["audio_snd_rec_latency"],
            audio_snd_play_latency=data["audio_snd_play_latency"],
            audio_snd_auto_close_time=data["audio_snd_auto_close_time"],
            audio_ec_tail_len=data["audio_ec_tail_len"],
            loopback_auto_create_sink=data["loopback_auto_create_sink"],
            loopback_sink_name=data["loopback_sink_name"],
            loopback_sink_description=data["loopback_sink_description"],
            loopback_auto_select_capture=data["loopback_auto_select_capture"],
            loopback_auto_select_playback=data["loopback_auto_select_playback"],
        )

    def start_bot(self):
        if self.bot and self.bot.is_running():
            self.append_log("[GUI] Bot already running")
            return
        try:
            data = self._read_config_from_form()
        except ValueError as e:
            QMessageBox.critical(self, "Invalid config", str(e))
            return
        self.cfg.update(data)
        save_cfg(self.cfg)
        self.cfg = load_cfg()
        cfg = self._create_bot_config(self.cfg)
        self.bot = SipBotCore(
            config=cfg,
            log_fn=self.bridge.log_signal.emit,
            state_fn=self.bridge.state_signal.emit,
            calls_fn=self.bridge.calls_signal.emit,
        )
        ok = self.bot.start()
        if ok:
            self.append_log("[GUI] Start requested")
        else:
            self.append_log("[GUI] Start failed")

    def stop_bot(self):
        if not self.bot:
            self.append_log("[GUI] Bot is not initialized")
            return
        if self._op_thread is not None:
            return
        self.append_log("[GUI] Stop requested")
        self._start_operation("stop", self.bot.stop)

    def restart_bot(self):
        if not self.bot or not self.bot.is_running():
            self.append_log("[GUI] Bot is not running")
            return
        if self._op_thread is not None:
            return
        self.append_log("[GUI] Restart requested")
        self._start_operation("restart", self.bot.restart)

    def _set_control_buttons_enabled(self, enabled: bool):
        self.btn_start.setEnabled(enabled)
        self.btn_stop.setEnabled(enabled)
        self.btn_restart.setEnabled(enabled)

    def _start_operation(self, name: str, func, *args, **kwargs):
        if self._op_thread is not None:
            return
        self._set_control_buttons_enabled(False)
        self._op_worker = _OperationWorker()
        self._op_worker.finished.connect(lambda ok: self._on_operation_finished(name, ok))
        def _run():
            try:
                result = func(*args, **kwargs)
                success = bool(result)
            except Exception:
                success = False
            self._op_worker.finished.emit(success)
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        self._op_thread = thread

    def _on_operation_finished(self, name: str, success: bool):
        self._op_thread = None
        self._op_worker = None
        self._set_control_buttons_enabled(True)
        if name == "stop":
            self.on_calls_updated([])
        self.append_log(f"[GUI] {name.capitalize()} {'succeeded' if success else 'failed'}")

    def open_audio_router_dialog(self):
        dialog = _AudioRouterDialog(self)
        dialog.exec()

    def apply_media_settings(self, show_message=True):
        wav_dir = self.e_wav.text().strip()
        source = self.cb_source.currentData()
        if source == "media" and not os.path.isdir(wav_dir):
            if show_message:
                QMessageBox.critical(self, "Invalid media", f"Media directory not found:\n{wav_dir}")
            return False
        try:
            capture_dev = int(self.e_capture_dev.text().strip() or "-1")
        except ValueError:
            if show_message:
                QMessageBox.critical(self, "Invalid media", "Capture device id must be integer")
            return False

        mode = self.cb_mode.currentData()
        call_mode = self.cb_call_mode.currentData()
        volume_pct = int(self.transmit_volume)
        monitor_call_audio = bool(self.cfg.get("monitor_call_audio", False))
        self.cfg["wav_dir"] = wav_dir
        self.cfg["media_source"] = source
        self.cfg["call_mode"] = call_mode
        self.cfg["playback_mode"] = mode
        self.cfg["transmit_volume_pct"] = volume_pct
        self.cfg["loopback_capture_dev"] = capture_dev
        self.cfg["audio_capture_dev"] = capture_dev
        self.cfg["monitor_call_audio"] = monitor_call_audio
        save_cfg(self.cfg)
        if self.bot and self.bot.is_running():
            self.bot.update_runtime_settings(
                wav_dir=wav_dir,
                media_source=source,
                call_mode=call_mode,
                playback_mode=mode,
                transmit_volume_pct=volume_pct,
                loopback_capture_dev=capture_dev,
                monitor_call_audio=monitor_call_audio,
            )
            action = "Applied"
        else:
            action = "Saved"
        self.append_log(f"[GUI] {action} media settings")
        return True

    def _request_track_skip(self, direction: int):
        if not self.bot or not self.bot.is_running():
            self.append_log("[GUI] Bot is not running")
            return
        if not self.bot.request_track_skip(direction):
            self.append_log("[GUI] No active call to control")

    def prev_track(self):
        source = self.cb_source.currentData()
        if source == 'media': # for media files use existing skip logic
            self._request_track_skip(-1)
            return
        try: # for loopback mode, send global media prev to controller
            if getattr(self, 'media_controller', None):
                self.media_controller.control('prev')
        except Exception as e:
            self.append_log(f'[GUI] Media prev failed: {e}')

    def next_track(self):
        source = self.cb_source.currentData()
        if source == 'media': # for media files use existing skip logic
            self._request_track_skip(1)
            return
        try: # for loopback mode, send global media next to controller
            if getattr(self, 'media_controller', None):
                self.media_controller.control('next')
        except Exception as e:
            self.append_log(f'[GUI] Media next failed: {e}')

    def play_media(self):
        try:
            if getattr(self, 'media_controller', None):
                self.media_controller.control('play')
        except Exception as e:
            self.append_log(f'[GUI] Media play failed: {e}')

    def volume_up(self):
        try:
            if getattr(self, 'media_controller', None):
                self.media_controller.control('vol_up')
        except Exception:
            pass

    def volume_down(self):
        try:
            if getattr(self, 'media_controller', None):
                self.media_controller.control('vol_down')
        except Exception:
            pass

    def mute(self):
        try:
            if getattr(self, 'media_controller', None):
                self.media_controller.control('mute')
        except Exception:
            pass

    def on_calls_updated(self, calls_data: list):
        self.current_calls = calls_data or []
        count = len(self.current_calls)
        self.lbl_callers_count.setText(f"{count} active call{'s' if count != 1 else ''}")
        self.table_callers.setRowCount(count)
        for row, cinfo in enumerate(self.current_calls):
            call_id = cinfo.get("id")
            uri = cinfo.get("uri", "")
            state = cinfo.get("state", "")
            duration_sec = cinfo.get("duration", 0)
            mm, ss = divmod(duration_sec, 60)
            hh, mm = divmod(mm, 60)
            dur_str = f"{hh:02d}:{mm:02d}:{ss:02d}" if hh > 0 else f"{mm:02d}:{ss:02d}"
            item_id = QTableWidgetItem(str(call_id))
            item_uri = QTableWidgetItem(str(uri))
            item_state = QTableWidgetItem(str(state))
            item_dur = QTableWidgetItem(dur_str)
            item_id.setTextAlignment(Qt.AlignCenter)
            item_state.setTextAlignment(Qt.AlignCenter)
            item_dur.setTextAlignment(Qt.AlignCenter)
            self.table_callers.setItem(row, 0, item_id)
            self.table_callers.setItem(row, 1, item_uri)
            self.table_callers.setItem(row, 2, item_state)
            self.table_callers.setItem(row, 3, item_dur)
            btn_drop = QPushButton()
            btn_drop.setIcon(qta.icon("fa5s.phone-slash", color="#EF5350"))
            btn_drop.setStyleSheet("color: #FF5252;")
            btn_drop.clicked.connect(lambda _, cid=call_id: self.hangup_single_call(cid))
            self.table_callers.setCellWidget(row, 4, btn_drop)

    def hangup_single_call(self, call_id):
        if self.bot and self.bot.is_running():
            ok = self.bot.hangup_call(call_id)
            if ok:
                self.append_log(f"[GUI] Hangup requested for call {call_id}")
            else:
                self.append_log(f"[GUI] Failed to hangup call {call_id}")

    def make_outgoing_call(self):
        if not self.bot or not self.bot.is_running():
            self.append_log("[GUI] Bot is not running")
            return
        target = self.e_outgoing_number.text().strip()
        if not target:
            self.append_log("[GUI] Enter a number or SIP URI")
            return
        ok = self.bot.make_outgoing_call(target)
        if ok:
            self.append_log(f"[GUI] Outgoing call started: {target}")
        else:
            self.append_log(f"[GUI] Failed to start outgoing call: {target}")

    def hangup_all_calls(self):
        if self.bot and self.bot.is_running():
            self.bot.hangup_all_calls()
            self.append_log("[GUI] Hangup requested for ALL calls")

    def _tick_call_time(self):
        if self.current_calls:
            for row, cinfo in enumerate(self.current_calls):
                if cinfo.get("connected_at") or cinfo.get("state") == "CONFIRMED":
                    cinfo["duration"] = cinfo.get("duration", 0) + 1
                    dur_sec = cinfo["duration"]
                    mm, ss = divmod(dur_sec, 60)
                    hh, mm = divmod(mm, 60)
                    dur_str = f"{hh:02d}:{mm:02d}:{ss:02d}" if hh > 0 else f"{mm:02d}:{ss:02d}"
                    item = self.table_callers.item(row, 3)
                    if item:
                        item.setText(dur_str)

    def _reset_call_time(self):
        self.call_started_at = None
        if self.call_timer.isActive():
            self.call_timer.stop()

    def closeEvent(self, event):
        if self._op_thread is not None and self._op_thread.is_alive():
            if self.bot and self.bot.is_running():
                self.append_log("[GUI] Waiting for operation to finish...")
            self._op_thread.join(timeout=5)
        if self.bot and self.bot.is_running():
            reply = QMessageBox.question(self, "Exit", "Bot is running. Stop and close?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.No:
                event.ignore()
                return
            self.bot.stop()
        event.accept()