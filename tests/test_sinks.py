import sys
import subprocess
import re
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem, QLabel


class AudioRouterTest(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SIP Bot Audio Router Test")
        self.resize(850, 450)

        layout = QVBoxLayout(self)

        self.status = QLabel("Поиск аудиопотоков...")
        layout.addWidget(self.status)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["ID", "Application", "PID", "Binary", "Media", "Sink"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()

        self.refresh_button = QPushButton("Обновить")
        self.sipbot_button = QPushButton("→ SIPBotSink")
        self.default_button = QPushButton("→ Системный вывод")

        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.sipbot_button)
        buttons.addWidget(self.default_button)
        buttons.addStretch()

        layout.addLayout(buttons)

        self.refresh_button.clicked.connect(self.refresh)
        self.sipbot_button.clicked.connect(self.move_to_sipbot)
        self.default_button.clicked.connect(self.move_to_default)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)

        self.refresh()

    def get_sink_inputs(self):
        result = subprocess.run(
            ["pactl", "list", "sink-inputs"],
            text=True,
            capture_output=True,
            check=False,
            timeout=2
        )

        if result.returncode != 0:
            return []

        streams = []
        current = None

        for line in result.stdout.splitlines():
            line = line.strip()

            if line.startswith("Sink Input #"):
                if current:
                    streams.append(current)

                current = {
                    "id": line.split("#", 1)[1],
                    "application": "",
                    "pid": "",
                    "binary": "",
                    "media": "",
                    "sink": ""
                }

            elif current:
                if line.startswith("Sink:"):
                    current["sink"] = line.split(":", 1)[1].strip()

                elif line.startswith("application.name"):
                    current["application"] = self.get_value(line)

                elif line.startswith("application.process.id"):
                    current["pid"] = self.get_value(line)

                elif line.startswith("application.process.binary"):
                    current["binary"] = self.get_value(line)

                elif line.startswith("media.name"):
                    current["media"] = self.get_value(line)

        if current:
            streams.append(current)

        return streams

    @staticmethod
    def get_value(line):
        value = line.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        return value

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
                stream["sink"]
            ]

            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))

        if selected_id:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).text() == selected_id:
                    self.table.selectRow(row)
                    break

        self.status.setText(f"Активных аудиопотоков: {len(streams)}")

    def get_selected_id(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None

        row = rows[0].row()
        item = self.table.item(row, 0)

        return item.text() if item else None

    def move_selected(self, sink):
        input_id = self.get_selected_id()

        if not input_id:
            self.status.setText("Сначала выбери аудиопоток")
            return

        result = subprocess.run(
            ["pactl", "move-sink-input", input_id, sink],
            text=True,
            capture_output=True,
            check=False,
            timeout=2
        )

        if result.returncode == 0:
            self.status.setText(f"Поток {input_id} → {sink}")
        else:
            error = (result.stderr or result.stdout).strip()
            self.status.setText(f"Ошибка: {error}")

        self.refresh()

    def move_to_sipbot(self):
        self.move_selected("sipbot_sink")

    def move_to_default(self):
        result = subprocess.run(
            ["pactl", "get-default-sink"],
            text=True,
            capture_output=True,
            check=False,
            timeout=2
        )

        if result.returncode != 0:
            self.status.setText("Не удалось определить default sink")
            return

        sink = result.stdout.strip()

        if not sink:
            self.status.setText("Default sink не найден")
            return

        self.move_selected(sink)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AudioRouterTest()
    window.show()
    sys.exit(app.exec())