"""
PyQt replacement for the DearPyGUI ground station.

Preserves the original serial protocol from main_windows_v4a(1).py:
- Incoming packets: AA 55 sync + struct payload <IIBBH{N}H
- Outgoing commands: 0xXXXX,2\n
Keyboard controls:
- Shift + 1..9 toggles Valve 1..9
- Shift + A performs E-stop
- Shift + configured macro letter runs that macro
- Valve buttons are display-only; valve commands require Shift+number

Optional macro file: macros.json in the same folder as this script.
Use the built-in Macro Editor to create/edit it.
"""

from __future__ import annotations

import csv
import json
import queue
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import serial
import serial.tools.list_ports
from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg


BAUD = 115200
HISTORY_LENGTH = 150
PLOT_WINDOW_SECONDS = 20.0

V_MAX = 4.5
V_MIN = 0.5
V_DIFF = V_MAX - V_MIN
HIGH_PRESSURE_MAX = 5000
LOW_PRESSURE_MAX = 2000

ORIGINAL_ADS_VOLTAGE_RANGE = 4.096
GAIN = 2 / 3
ADS_VOLTAGE_RANGE = ORIGINAL_ADS_VOLTAGE_RANGE / GAIN
ADS_COUNT_RANGE = 2**15 - 1
ADS_UNIT_VOLTAGE = ADS_VOLTAGE_RANGE / ADS_COUNT_RANGE

SYNC = b"\xAA\x55"
MACRO_FILE = Path(__file__).with_name("macros.json")


@dataclass
class AppConfig:
    port: str = "COM6"
    num_p: int = 4
    num_t: int = 0
    num_lc: int = 0
    num_sol: int = 6
    sensor_type: list[str] = field(default_factory=lambda: ["low", "low", "low", "low"])

    @property
    def total_sensors(self) -> int:
        return self.num_p + self.num_t + self.num_lc

    @property
    def packet_format(self) -> str:
        return f"<IIBBH{self.total_sensors}H"

    @property
    def packet_size(self) -> int:
        return 12 + (self.total_sensors * 2)


@dataclass
class Packet:
    timestamp: int
    seq: int
    mask: int
    status: int
    solenoids: int
    adc_values: tuple[int, ...]
    elapsed: float


class SerialWorker(QtCore.QThread):
    packet_received = QtCore.pyqtSignal(object)
    log_message = QtCore.pyqtSignal(str)
    connection_changed = QtCore.pyqtSignal(bool)

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.command_queue: queue.Queue[int] = queue.Queue()
        self._running = threading.Event()
        self._running.set()
        self._ser: serial.Serial | None = None

    def queue_command(self, cmd_bits: int) -> None:
        self.command_queue.put(cmd_bits & 0xFFFF)

    def clear_command_queue(self) -> None:
        while not self.command_queue.empty():
            try:
                self.command_queue.get_nowait()
            except queue.Empty:
                break

    def stop(self) -> None:
        self._running.clear()
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
        except Exception:
            pass

    def run(self) -> None:
        log_file = None
        try:
            self._ser = serial.Serial(self.config.port, BAUD, timeout=0)
            self.connection_changed.emit(True)
            self.log_message.emit(f"[SERIAL] Connected to {self.config.port}")
            self.log_message.emit(f"[SERIAL] Expecting {self.config.packet_size} payload bytes")

            log_file = open(f"telemetry_{int(time.time())}.csv", "w", newline="")
            writer = csv.writer(log_file)
            headers = ["timestamp", "seq", "solenoids"]
            headers += [f"P_{i}" for i in range(self.config.num_p)]
            headers += [f"T_{i}" for i in range(self.config.num_t)]
            headers += [f"LC_{i}" for i in range(self.config.num_lc)]
            writer.writerow(headers)

            buffer = bytearray()
            start_time = time.time()
            total_packet_size = 2 + self.config.packet_size

            while self._running.is_set():
                while not self.command_queue.empty():
                    cmd_bits = self.command_queue.get_nowait()
                    message = f"0x{cmd_bits:04X},2\n"
                    self._ser.write(message.encode())
                    self.log_message.emit(f"[GUI] Sent: {message.strip()}")

                incoming = self._ser.read(self._ser.in_waiting or 1)
                if incoming:
                    buffer.extend(incoming)

                while True:
                    if len(buffer) < 2:
                        break

                    sync_index = buffer.find(SYNC)
                    if sync_index == -1:
                        buffer.clear()
                        break

                    if sync_index > 0:
                        del buffer[:sync_index]

                    if len(buffer) < total_packet_size:
                        break

                    raw = buffer[2:total_packet_size]
                    del buffer[:total_packet_size]

                    try:
                        unpacked = struct.unpack(self.config.packet_format, raw)
                    except Exception as exc:
                        self.log_message.emit(f"[SERIAL] Unpack failed: {exc}")
                        continue

                    ts, seq, mask, status, solenoids = unpacked[:5]
                    adc_values = tuple(int(v) for v in unpacked[5:])
                    elapsed = time.time() - start_time

                    packet = Packet(ts, seq, mask, status, solenoids, adc_values, elapsed)
                    self.packet_received.emit(packet)
                    writer.writerow([ts, seq, f"{solenoids:016b}"] + list(adc_values))
                    log_file.flush()

                self.msleep(1)

        except Exception as exc:
            self.log_message.emit(f"[SERIAL ERROR] {exc}")
        finally:
            try:
                if log_file:
                    log_file.close()
            finally:
                self.connection_changed.emit(False)


class MacroEditorDialog(QtWidgets.QDialog):
    """Table-based macro editor. Macros only contain SET snapshots and WAIT delays."""

    ACTIONS = ["set", "wait"]
    STATES = ["low", "high"]
    VALVE_COUNT = 9

    DEFAULT_MACROS = {
        "macros": [
            {
                "key": "B",
                "name": "Example Sequence",
                "steps": [
                    {
                        "action": "set",
                        "states": {
                            "1": "high", "2": "low", "3": "low", "4": "low", "5": "low",
                            "6": "low", "7": "low", "8": "low", "9": "low",
                        },
                    },
                    {"action": "wait", "seconds": 0.5},
                    {
                        "action": "set",
                        "states": {
                            "1": "low", "2": "high", "3": "low", "4": "low", "5": "low",
                            "6": "low", "7": "low", "8": "low", "9": "low",
                        },
                    },
                    {"action": "wait", "seconds": 0.5},
                    {
                        "action": "set",
                        "states": {
                            "1": "low", "2": "low", "3": "low", "4": "low", "5": "low",
                            "6": "low", "7": "low", "8": "low", "9": "low",
                        },
                    },
                ],
            }
        ]
    }

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Macro Editor")
        self.resize(1120, 660)
        self._macros: list[dict[str, Any]] = []
        self._loading = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        help_text = QtWidgets.QLabel(
            "Rules: macros run with Shift+letter; Shift+A is reserved for E-stop; steps run top-to-bottom.\n"
            "Actions: set = choose high/low for every valve; wait = pause for the specified seconds."
        )
        help_text.setObjectName("macroHelp")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        layout.addWidget(splitter, stretch=1)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.addWidget(QtWidgets.QLabel("Macros"))
        self.macro_table = QtWidgets.QTableWidget(0, 2)
        self.macro_table.setHorizontalHeaderLabels(["Key", "Name"])
        self.macro_table.horizontalHeader().setStretchLastSection(True)
        self.macro_table.verticalHeader().setVisible(False)
        self.macro_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.macro_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        left_layout.addWidget(self.macro_table, stretch=1)

        macro_buttons = QtWidgets.QHBoxLayout()
        add_macro = QtWidgets.QPushButton("Add Macro")
        delete_macro = QtWidgets.QPushButton("Delete Macro")
        macro_buttons.addWidget(add_macro)
        macro_buttons.addWidget(delete_macro)
        left_layout.addLayout(macro_buttons)
        splitter.addWidget(left)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.addWidget(QtWidgets.QLabel("Selected Macro Steps"))
        self.step_table = QtWidgets.QTableWidget(0, 11)
        self.step_table.setHorizontalHeaderLabels(
            ["Action", "Seconds"] + [f"V{i}" for i in range(1, self.VALVE_COUNT + 1)]
        )
        self.step_table.horizontalHeader().setStretchLastSection(False)
        self.step_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.step_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        for col in range(2, 11):
            self.step_table.horizontalHeader().setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.step_table.verticalHeader().setVisible(False)
        self.step_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.step_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        right_layout.addWidget(self.step_table, stretch=1)

        step_buttons = QtWidgets.QHBoxLayout()
        add_set_step = QtWidgets.QPushButton("Add Set Step")
        add_wait_step = QtWidgets.QPushButton("Add Wait Step")
        delete_step = QtWidgets.QPushButton("Delete Step")
        move_up = QtWidgets.QPushButton("Move Up")
        move_down = QtWidgets.QPushButton("Move Down")
        step_buttons.addWidget(add_set_step)
        step_buttons.addWidget(add_wait_step)
        step_buttons.addWidget(delete_step)
        step_buttons.addWidget(move_up)
        step_buttons.addWidget(move_down)
        step_buttons.addStretch(1)
        right_layout.addLayout(step_buttons)
        splitter.addWidget(right)
        splitter.setSizes([300, 820])

        bottom = QtWidgets.QHBoxLayout()
        load_example = QtWidgets.QPushButton("Load Example")
        save_btn = QtWidgets.QPushButton("Save")
        cancel_btn = QtWidgets.QPushButton("Cancel")
        bottom.addWidget(load_example)
        bottom.addStretch(1)
        bottom.addWidget(save_btn)
        bottom.addWidget(cancel_btn)
        layout.addLayout(bottom)

        self.macro_table.itemChanged.connect(self._macro_item_changed)
        self.macro_table.currentCellChanged.connect(lambda *_: self._load_steps_for_selected_macro())
        self.step_table.itemChanged.connect(self._step_item_changed)
        self.step_table.cellChanged.connect(self._step_cell_changed)
        add_macro.clicked.connect(self.add_macro)
        delete_macro.clicked.connect(self.delete_macro)
        add_set_step.clicked.connect(lambda: self.add_step("set"))
        add_wait_step.clicked.connect(lambda: self.add_step("wait"))
        delete_step.clicked.connect(self.delete_step)
        move_up.clicked.connect(lambda: self.move_step(-1))
        move_down.clicked.connect(lambda: self.move_step(1))
        load_example.clicked.connect(self.load_example)
        save_btn.clicked.connect(self.save_file)
        cancel_btn.clicked.connect(self.reject)

        self._load_from_file()

    def _default_states(self) -> dict[str, str]:
        return {str(i): "low" for i in range(1, self.VALVE_COUNT + 1)}

    def _normalize_states(self, step: dict[str, Any]) -> dict[str, str]:
        states = self._default_states()
        raw_states = step.get("states")
        if isinstance(raw_states, dict):
            for valve_num in range(1, self.VALVE_COUNT + 1):
                raw_state = str(raw_states.get(str(valve_num), raw_states.get(valve_num, "low"))).lower().strip()
                states[str(valve_num)] = "high" if raw_state in {"high", "on", "1", "true", "open"} else "low"
            return states

        # Migration path for older macros that had one valve per set/high/low step.
        action = str(step.get("action", "set")).lower().strip()
        valve = int(step.get("valve", 0) or 0)
        state = str(step.get("state", "high" if action in {"high", "on"} else "low")).lower().strip()
        if 1 <= valve <= self.VALVE_COUNT:
            states[str(valve)] = "high" if state in {"high", "on", "1", "true", "open"} or action in {"high", "on"} else "low"
        return states

    def _normalize_step(self, step: dict[str, Any]) -> dict[str, Any]:
        action = str(step.get("action", "set")).lower().strip().replace("_", "-")
        if action in {"wait", "delay", "pause"}:
            try:
                seconds = float(step.get("seconds", step.get("duration", 0.0)) or 0.0)
            except (TypeError, ValueError):
                seconds = 0.0
            return {"action": "wait", "seconds": max(0.0, seconds)}
        return {"action": "set", "states": self._normalize_states(step)}

    def _load_from_file(self) -> None:
        if MACRO_FILE.exists():
            try:
                parsed = json.loads(MACRO_FILE.read_text())
            except Exception:
                parsed = self.DEFAULT_MACROS
        else:
            parsed = self.DEFAULT_MACROS
        self._macros = [self._normalize_macro(m) for m in parsed.get("macros", [])]
        if not self._macros:
            self._macros = [self._normalize_macro(self.DEFAULT_MACROS["macros"][0])]
        self._refresh_macro_table()

    def _normalize_macro(self, macro: dict[str, Any]) -> dict[str, Any]:
        key = str(macro.get("key", "B")).upper().strip()[:1] or "B"
        name = str(macro.get("name", "Macro")).strip() or "Macro"
        steps = macro.get("steps", [])
        if not isinstance(steps, list):
            steps = []
        return {"key": key, "name": name, "steps": [self._normalize_step(dict(s)) for s in steps if isinstance(s, dict)]}

    def _refresh_macro_table(self) -> None:
        self._loading = True
        self.macro_table.setRowCount(len(self._macros))
        for row, macro in enumerate(self._macros):
            self.macro_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(macro.get("key", ""))))
            self.macro_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(macro.get("name", ""))))
        self._loading = False
        if self._macros:
            current = self.macro_table.currentRow()
            self.macro_table.selectRow(min(current, len(self._macros) - 1) if current >= 0 else 0)
        self._load_steps_for_selected_macro()

    def _selected_macro_index(self) -> int:
        row = self.macro_table.currentRow()
        return row if 0 <= row < len(self._macros) else -1

    def _macro_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._loading:
            return
        row = item.row()
        if not (0 <= row < len(self._macros)):
            return
        if item.column() == 0:
            self._macros[row]["key"] = item.text().upper().strip()[:1]
        elif item.column() == 1:
            self._macros[row]["name"] = item.text().strip()

    def _load_steps_for_selected_macro(self) -> None:
        idx = self._selected_macro_index()
        self._loading = True
        self.step_table.setRowCount(0)
        if idx >= 0:
            steps = self._macros[idx].setdefault("steps", [])
            self.step_table.setRowCount(len(steps))
            for row, step in enumerate(steps):
                self._set_step_row_widgets(row, step)
        self._loading = False

    def _set_step_row_widgets(self, row: int, step: dict[str, Any]) -> None:
        action = str(step.get("action", "set")).lower().strip()
        if action not in self.ACTIONS:
            action = "set"

        action_combo = QtWidgets.QComboBox()
        action_combo.addItems(self.ACTIONS)
        action_combo.setCurrentText(action)
        action_combo.currentTextChanged.connect(lambda _=None, r=row: self._on_action_changed(r))
        self.step_table.setCellWidget(row, 0, action_combo)

        seconds_item = QtWidgets.QTableWidgetItem(str(step.get("seconds", "") if action == "wait" else ""))
        self.step_table.setItem(row, 1, seconds_item)

        states = self._normalize_states(step)
        for valve_num in range(1, self.VALVE_COUNT + 1):
            state_combo = QtWidgets.QComboBox()
            state_combo.addItems(self.STATES)
            state_combo.setCurrentText(states.get(str(valve_num), "low"))
            state_combo.currentTextChanged.connect(lambda _=None, r=row: self._write_step_from_row(r))
            self.step_table.setCellWidget(row, valve_num + 1, state_combo)

        self._update_row_enabled(row)

    def _on_action_changed(self, row: int) -> None:
        self._update_row_enabled(row)
        self._write_step_from_row(row)

    def _update_row_enabled(self, row: int) -> None:
        action = self._row_action(row)
        seconds_item = self.step_table.item(row, 1)
        if seconds_item:
            seconds_item.setFlags(
                seconds_item.flags() | QtCore.Qt.ItemFlag.ItemIsEditable if action == "wait"
                else seconds_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable
            )
            seconds_item.setBackground(QtGui.QColor("#ffffff" if action == "wait" else "#cfcfcf"))
            if action == "set":
                seconds_item.setText("")
        for col in range(2, 11):
            widget = self.step_table.cellWidget(row, col)
            if widget:
                widget.setEnabled(action == "set")

    def _row_action(self, row: int) -> str:
        widget = self.step_table.cellWidget(row, 0)
        if isinstance(widget, QtWidgets.QComboBox):
            return widget.currentText()
        return "set"

    def _step_cell_changed(self, row: int, column: int) -> None:
        if not self._loading and column == 1:
            self._write_step_from_row(row)

    def _step_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if not self._loading:
            self._write_step_from_row(item.row())

    def _write_step_from_row(self, row: int) -> None:
        if self._loading:
            return
        idx = self._selected_macro_index()
        if idx < 0 or row < 0 or row >= self.step_table.rowCount():
            return

        action = self._row_action(row)
        if action == "wait":
            seconds_text = self.step_table.item(row, 1).text().strip() if self.step_table.item(row, 1) else ""
            try:
                seconds = float(seconds_text) if seconds_text else 0.0
            except ValueError:
                seconds = 0.0
            step = {"action": "wait", "seconds": max(0.0, seconds)}
        else:
            states = self._default_states()
            for valve_num in range(1, self.VALVE_COUNT + 1):
                widget = self.step_table.cellWidget(row, valve_num + 1)
                if isinstance(widget, QtWidgets.QComboBox):
                    states[str(valve_num)] = widget.currentText()
            step = {"action": "set", "states": states}

        self._macros[idx].setdefault("steps", [])[row] = step

    def add_macro(self) -> None:
        self._macros.append({"key": "B", "name": "New Macro", "steps": [{"action": "set", "states": self._default_states()}]})
        self._refresh_macro_table()
        self.macro_table.selectRow(len(self._macros) - 1)

    def delete_macro(self) -> None:
        idx = self._selected_macro_index()
        if idx >= 0:
            del self._macros[idx]
            self._refresh_macro_table()

    def add_step(self, action: str = "set") -> None:
        idx = self._selected_macro_index()
        if idx < 0:
            return
        self._write_all_steps_from_table()
        if action == "wait":
            self._macros[idx].setdefault("steps", []).append({"action": "wait", "seconds": 0.0})
        else:
            self._macros[idx].setdefault("steps", []).append({"action": "set", "states": self._default_states()})
        self._load_steps_for_selected_macro()
        self.step_table.selectRow(self.step_table.rowCount() - 1)

    def delete_step(self) -> None:
        idx = self._selected_macro_index()
        row = self.step_table.currentRow()
        if idx >= 0 and 0 <= row < len(self._macros[idx].setdefault("steps", [])):
            self._write_all_steps_from_table()
            del self._macros[idx]["steps"][row]
            self._load_steps_for_selected_macro()

    def move_step(self, direction: int) -> None:
        idx = self._selected_macro_index()
        row = self.step_table.currentRow()
        if idx < 0:
            return
        self._write_all_steps_from_table()
        steps = self._macros[idx].setdefault("steps", [])
        new_row = row + direction
        if 0 <= row < len(steps) and 0 <= new_row < len(steps):
            steps[row], steps[new_row] = steps[new_row], steps[row]
            self._load_steps_for_selected_macro()
            self.step_table.selectRow(new_row)

    def _write_all_steps_from_table(self) -> None:
        for row in range(self.step_table.rowCount()):
            self._write_step_from_row(row)

    def load_example(self) -> None:
        self._macros = [self._normalize_macro(m) for m in self.DEFAULT_MACROS["macros"]]
        self._refresh_macro_table()

    def _validate(self) -> tuple[bool, str]:
        keys: set[str] = set()
        for macro in self._macros:
            key = str(macro.get("key", "")).upper().strip()
            name = str(macro.get("name", "")).strip()
            if len(key) != 1 or not key.isalpha():
                return False, "Each macro key must be one letter."
            if key == "A":
                return False, "Shift+A is reserved for E-stop. Choose another macro key."
            if key in keys:
                return False, f"Duplicate macro key: {key}"
            keys.add(key)
            if not name:
                return False, f"Macro {key} needs a name."
            steps = macro.get("steps", [])
            if not steps:
                return False, f"Macro {key} needs at least one step."
            for step in steps:
                action = step.get("action")
                if action not in self.ACTIONS:
                    return False, f"Macro {key} has an invalid action: {action}"
                if action == "wait":
                    try:
                        if float(step.get("seconds", 0)) < 0:
                            return False, "Wait seconds cannot be negative."
                    except (TypeError, ValueError):
                        return False, f"Macro {key} has an invalid wait time."
                if action == "set":
                    states = step.get("states")
                    if not isinstance(states, dict):
                        return False, f"Macro {key} set steps must define all valve states."
                    for valve_num in range(1, self.VALVE_COUNT + 1):
                        if str(valve_num) not in states:
                            return False, f"Macro {key} set step is missing V{valve_num}."
                        if states[str(valve_num)] not in self.STATES:
                            return False, f"Macro {key} V{valve_num} must be high or low."
        return True, ""

    def save_file(self) -> None:
        self._write_all_steps_from_table()
        ok, message = self._validate()
        if not ok:
            QtWidgets.QMessageBox.critical(self, "Invalid Macro", message)
            return
        payload = {"macros": self._macros}
        MACRO_FILE.write_text(json.dumps(payload, indent=2))
        self.accept()


class GroundStationWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Liquid Propulsion Ground Station - guiv5")
        self.resize(1280, 820)

        self.config = AppConfig()
        self.worker: SerialWorker | None = None
        self.connected = False

        self.history_x: dict[int, list[float]] = {}
        self.history_y: dict[int, list[int]] = {}
        self.pressure_zero_offsets: dict[int, float] = {}
        self.current_tick = 0.0
        self.cmd_solenoid_bits = 0
        self.solenoid_feedback_bits = 0
        self.cmd_lock = threading.Lock()
        self.macros: dict[str, dict[str, Any]] = {}
        self.macro_running = threading.Event()
        self._active_macro_steps: list[dict[str, Any]] = []
        self._active_macro_index = 0
        self._macro_step_timer = QtCore.QTimer(self)
        self._macro_step_timer.setSingleShot(True)
        self._macro_step_timer.timeout.connect(self._run_next_macro_step)

        self._build_ui()
        self.reload_ports()
        self.reload_macros()

        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self.gui_timer = QtCore.QTimer(self)
        self.gui_timer.timeout.connect(self.refresh_gui)
        self.gui_timer.start(50)

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        top_panel = QtWidgets.QFrame()
        top_panel.setObjectName("topPanel")
        top_panel.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        top_layout = QtWidgets.QVBoxLayout(top_panel)
        root.addWidget(top_panel, stretch=0)

        cfg_row = QtWidgets.QHBoxLayout()
        top_layout.addLayout(cfg_row)

        self.port_combo = QtWidgets.QComboBox()
        self.refresh_ports_button = QtWidgets.QPushButton("Refresh Ports")
        self.p_spin = self._spin(0, 10, 4)
        self.t_spin = self._spin(0, 10, 0)
        self.lc_spin = self._spin(0, 4, 0)
        self.sol_spin = self._spin(1, 9, 5)
        self.connect_button = QtWidgets.QPushButton("Connect")
        self.connect_button.setObjectName("connectButton")
        self.disconnect_button = QtWidgets.QPushButton("Disconnect")
        self.disconnect_button.setObjectName("disconnectButton")
        self.disconnect_button.setEnabled(False)

        cfg_row.addWidget(QtWidgets.QLabel("Port"))
        cfg_row.addWidget(self.port_combo, stretch=1)
        cfg_row.addWidget(self.refresh_ports_button)
        cfg_row.addSpacing(10)
        cfg_row.addWidget(QtWidgets.QLabel("P"))
        cfg_row.addWidget(self.p_spin)
        cfg_row.addWidget(QtWidgets.QLabel("T"))
        cfg_row.addWidget(self.t_spin)
        cfg_row.addWidget(QtWidgets.QLabel("LC"))
        cfg_row.addWidget(self.lc_spin)
        cfg_row.addWidget(QtWidgets.QLabel("Valves"))
        cfg_row.addWidget(self.sol_spin)
        cfg_row.addSpacing(10)
        cfg_row.addWidget(self.connect_button)
        cfg_row.addWidget(self.disconnect_button)

        valve_row = QtWidgets.QHBoxLayout()
        top_layout.addLayout(valve_row)
        valve_title = QtWidgets.QLabel("Valves:")
        valve_title.setObjectName("sectionTitle")
        valve_row.addWidget(valve_title)
        self.valve_buttons: list[QtWidgets.QLabel] = []
        for i in range(9):
            indicator = QtWidgets.QLabel(f"V{i + 1}: OFF")
            indicator.setProperty("role", "valve")
            indicator.setProperty("state", "off")
            indicator.setMinimumWidth(92)
            indicator.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            indicator.setToolTip("Display only. Hold Shift and press the valve number to toggle.")
            self.valve_buttons.append(indicator)
            valve_row.addWidget(indicator)
        valve_row.addStretch(1)
        self.zero_button = QtWidgets.QPushButton("Shift+Z Zero")
        self.zero_button.setObjectName("zeroButton")
        self.estop_button = QtWidgets.QPushButton("Shift+A E-STOP")
        self.estop_button.setObjectName("estopButton")
        self.estop_button.setMinimumWidth(110)
        valve_row.addWidget(self.zero_button)
        valve_row.addWidget(self.estop_button)

        macro_row = QtWidgets.QHBoxLayout()
        top_layout.addLayout(macro_row)
        self.macro_label = QtWidgets.QLabel("Macros: none loaded")
        self.macro_label.setObjectName("macroLabel")
        self.macro_editor_button = QtWidgets.QPushButton("Edit Macros")
        self.reload_macro_button = QtWidgets.QPushButton("Reload Macros")
        macro_row.addWidget(self.macro_label, stretch=1)
        macro_row.addWidget(self.macro_editor_button)
        macro_row.addWidget(self.reload_macro_button)

        readout_row = QtWidgets.QHBoxLayout()
        top_layout.addLayout(readout_row)
        self.pressure_labels: list[QtWidgets.QLabel] = []
        for i in range(10):
            label = QtWidgets.QLabel(f"P-{i + 1}: --")
            label.setProperty("role", "readout")
            label.setMinimumWidth(118)
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.pressure_labels.append(label)
            readout_row.addWidget(label)
        readout_row.addStretch(1)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground("w")
        self.plot_widget.setLabel("left", "Pressure", units="PSI")
        self.plot_widget.setLabel("bottom", "Time", units="s")
        self.plot_widget.getAxis("left").setPen(pg.mkPen("k", width=1))
        self.plot_widget.getAxis("bottom").setPen(pg.mkPen("k", width=1))
        self.plot_widget.getAxis("left").setTextPen(pg.mkPen("k"))
        self.plot_widget.getAxis("bottom").setTextPen(pg.mkPen("k"))
        self.plot_widget.addLegend(labelTextColor="k")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.35)
        root.addWidget(self.plot_widget, stretch=1)
        self.pressure_curves: list[Any] = []

        bottom = QtWidgets.QFrame()
        bottom.setObjectName("bottomPanel")
        bottom.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        bottom_layout = QtWidgets.QHBoxLayout(bottom)
        self.status_label = QtWidgets.QLabel("Disconnected")
        self.status_label.setObjectName("statusLabel")
        self.log_box = QtWidgets.QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(110)
        bottom_layout.addWidget(self.status_label)
        bottom_layout.addWidget(self.log_box, stretch=1)
        root.addWidget(bottom, stretch=0)

        self.refresh_ports_button.clicked.connect(self.reload_ports)
        self.connect_button.clicked.connect(self.connect_serial)
        self.disconnect_button.clicked.connect(self.disconnect_serial)
        self.zero_button.clicked.connect(self.zero_pressures_from_button)
        self.estop_button.clicked.connect(self.emergency_stop_from_button)
        self.macro_editor_button.clicked.connect(self.open_macro_editor)
        self.reload_macro_button.clicked.connect(self.reload_macros)

    def _spin(self, low: int, high: int, value: int) -> QtWidgets.QSpinBox:
        spin = QtWidgets.QSpinBox()
        spin.setRange(low, high)
        spin.setValue(value)
        spin.setMaximumWidth(60)
        return spin

    def reload_ports(self) -> None:
        current = self.port_combo.currentText() or self.config.port
        ports = [port.device for port in serial.tools.list_ports.comports()]
        if not ports:
            ports = ["No Ports Found"]
        self.port_combo.clear()
        self.port_combo.addItems(ports)
        if current in ports:
            self.port_combo.setCurrentText(current)
        elif self.config.port in ports:
            self.port_combo.setCurrentText(self.config.port)

    def connect_serial(self) -> None:
        port = self.port_combo.currentText()
        if not port or port == "No Ports Found":
            self.append_log("Invalid COM port selected.")
            return

        self.config = AppConfig(
            port=port,
            num_p=self.p_spin.value(),
            num_t=self.t_spin.value(),
            num_lc=self.lc_spin.value(),
            num_sol=self.sol_spin.value(),
            sensor_type=["low"] * max(self.p_spin.value(), 1),
        )

        self.history_x = {i: [] for i in range(self.config.total_sensors)}
        self.history_y = {i: [] for i in range(self.config.total_sensors)}
        self.pressure_zero_offsets = {i: 0.0 for i in range(self.config.num_p)}
        self.current_tick = 0.0
        self.cmd_solenoid_bits = 0
        self.solenoid_feedback_bits = 0
        self.rebuild_plot_curves()
        self.refresh_gui()

        self.worker = SerialWorker(self.config)
        self.worker.packet_received.connect(self.handle_packet)
        self.worker.log_message.connect(self.append_log)
        self.worker.connection_changed.connect(self.handle_connection_changed)
        self.worker.start()

        self.set_controls_enabled(False)

    def disconnect_serial(self) -> None:
        if self.worker:
            self.worker.stop()
            self.worker.wait(1000)
            self.worker = None
        self.handle_connection_changed(False)

    def set_controls_enabled(self, enabled: bool) -> None:
        self.port_combo.setEnabled(enabled)
        self.refresh_ports_button.setEnabled(enabled)
        self.p_spin.setEnabled(enabled)
        self.t_spin.setEnabled(enabled)
        self.lc_spin.setEnabled(enabled)
        self.sol_spin.setEnabled(enabled)
        self.connect_button.setEnabled(enabled)
        self.disconnect_button.setEnabled(not enabled)

    def handle_connection_changed(self, connected: bool) -> None:
        self.connected = connected
        self.status_label.setText("Connected" if connected else "Disconnected")
        self.status_label.setProperty("state", "connected" if connected else "disconnected")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.set_controls_enabled(not connected)

    def handle_packet(self, packet: Packet) -> None:
        self.current_tick = packet.elapsed
        self.solenoid_feedback_bits = packet.solenoids

        for idx, val in enumerate(packet.adc_values):
            if idx not in self.history_x:
                continue
            self.history_x[idx].append(packet.elapsed)
            self.history_y[idx].append(int(val))
            if len(self.history_x[idx]) > HISTORY_LENGTH:
                self.history_x[idx].pop(0)
                self.history_y[idx].pop(0)

    def append_log(self, message: str) -> None:
        self.log_box.appendPlainText(message)
        sb = self.log_box.verticalScrollBar()
        sb.setValue(sb.maximum())

    def rebuild_plot_curves(self) -> None:
        self.plot_widget.clear()
        self.plot_widget.addLegend(labelTextColor="k")
        self.pressure_curves.clear()
        grayscale_pens = [
            pg.mkPen((0, 0, 0), width=3),
            pg.mkPen((70, 70, 70), width=3),
            pg.mkPen((120, 120, 120), width=3),
            pg.mkPen((0, 0, 0), width=2, style=QtCore.Qt.PenStyle.DashLine),
            pg.mkPen((70, 70, 70), width=2, style=QtCore.Qt.PenStyle.DashLine),
            pg.mkPen((120, 120, 120), width=2, style=QtCore.Qt.PenStyle.DashLine),
            pg.mkPen((0, 0, 0), width=2, style=QtCore.Qt.PenStyle.DotLine),
            pg.mkPen((70, 70, 70), width=2, style=QtCore.Qt.PenStyle.DotLine),
            pg.mkPen((120, 120, 120), width=2, style=QtCore.Qt.PenStyle.DotLine),
            pg.mkPen((40, 40, 40), width=2),
        ]
        for i in range(self.config.num_p):
            curve = self.plot_widget.plot([], [], name=f"P-{i + 1}", pen=grayscale_pens[i % len(grayscale_pens)])
            self.pressure_curves.append(curve)

    def refresh_gui(self) -> None:
        for i, btn in enumerate(self.valve_buttons):
            visible = i < self.config.num_sol
            btn.setVisible(visible)
            if not visible:
                continue
            bit_position = 14 - i
            is_on = (self.cmd_solenoid_bits & (1 << bit_position)) != 0
            btn.setText(f"V{i + 1}: {'ON' if is_on else 'OFF'}")
            btn.setProperty("state", "on" if is_on else "off")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        for i, label in enumerate(self.pressure_labels):
            label.setVisible(i < self.config.num_p)
            if i >= self.config.num_p:
                continue
            y = self.history_y.get(i, [])
            if y:
                psi = self.convert_pressure(y[-1], i)
                label.setText(f"P-{i + 1}: {psi:.1f} psi")
            else:
                label.setText(f"P-{i + 1}: --")

        all_y: list[float] = []
        for i, curve in enumerate(self.pressure_curves):
            x = self.history_x.get(i, [])
            y_raw = self.history_y.get(i, [])
            y = [self.convert_pressure(v, i) for v in y_raw]
            curve.setData(x, y)
            all_y.extend(y)

        if self.current_tick > 0:
            self.plot_widget.setXRange(
                max(0.0, self.current_tick - PLOT_WINDOW_SECONDS),
                max(PLOT_WINDOW_SECONDS, self.current_tick),
                padding=0,
            )
        if all_y:
            ymin = min(all_y)
            ymax = max(all_y)
            pad = 1.0 if ymin == ymax else (ymax - ymin) * 0.10
            self.plot_widget.setYRange(ymin - pad, ymax + pad, padding=0)

    def raw_adc_to_pressure_no_zero(self, raw_adc: int, sensor_index: int) -> float:
        voltage = raw_adc * ADS_UNIT_VOLTAGE
        sensor_type = self.config.sensor_type[sensor_index] if sensor_index < len(self.config.sensor_type) else "low"
        if sensor_type == "low":
            return (voltage - V_MIN) / V_DIFF * LOW_PRESSURE_MAX
        if sensor_type == "high":
            return (voltage - V_MIN) / V_DIFF * HIGH_PRESSURE_MAX
        return 0.0

    def convert_pressure(self, raw_adc: int, sensor_index: int) -> float:
        psi = self.raw_adc_to_pressure_no_zero(raw_adc, sensor_index)
        return psi - self.pressure_zero_offsets.get(sensor_index, 0.0)

    def _shift_is_down(self) -> bool:
        return bool(QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier)

    def zero_pressures_from_button(self) -> None:
        if not self._shift_is_down():
            self.append_log("[SAFETY] Hold Shift while pressing Zero Pressures.")
            return
        self.zero_pressures()

    def emergency_stop_from_button(self) -> None:
        if not self._shift_is_down():
            self.append_log("[SAFETY] Hold Shift while pressing E-STOP, or use Shift+A.")
            return
        self.emergency_stop()

    def zero_pressures(self) -> None:
        for i in range(self.config.num_p):
            y = self.history_y.get(i, [])
            if y:
                self.pressure_zero_offsets[i] = self.raw_adc_to_pressure_no_zero(y[-1], i)
        self.append_log("Pressures zeroed.")

    def set_valve_state(self, solenoid_idx: int, on: bool) -> None:
        if solenoid_idx < 0 or solenoid_idx >= self.config.num_sol:
            return
        with self.cmd_lock:
            bit_position = 14 - solenoid_idx
            if on:
                self.cmd_solenoid_bits |= 1 << bit_position
            else:
                self.cmd_solenoid_bits &= ~(1 << bit_position)
            self.cmd_solenoid_bits |= 0x8000
            bits_to_send = self.cmd_solenoid_bits
        self.queue_command(bits_to_send)
        self.append_log(f"Command bits: {bits_to_send:016b}")

    def toggle_solenoid(self, solenoid_idx: int) -> None:
        if solenoid_idx < 0 or solenoid_idx >= self.config.num_sol:
            return
        with self.cmd_lock:
            bit_position = 14 - solenoid_idx
            self.cmd_solenoid_bits ^= 1 << bit_position
            self.cmd_solenoid_bits |= 0x8000
            bits_to_send = self.cmd_solenoid_bits
        self.queue_command(bits_to_send)
        self.append_log(f"Command bits: {bits_to_send:016b}")

    def emergency_stop(self) -> None:
        with self.cmd_lock:
            self.cmd_solenoid_bits = 0x8000
            bits_to_send = self.cmd_solenoid_bits
        if self.worker:
            self.worker.clear_command_queue()
        self.queue_command(bits_to_send)
        self.append_log("!!! EMERGENCY STOP ACTIVATED !!!")
        self.append_log(f"Command bits: {bits_to_send:016b}")

    def queue_command(self, bits_to_send: int) -> None:
        if self.worker and self.connected:
            self.worker.queue_command(bits_to_send)
        else:
            self.append_log("[WARN] Not connected; command not sent.")

    def _default_macro_states(self) -> dict[str, str]:
        return {str(i): "low" for i in range(1, 10)}

    def _normalize_macro_for_runtime(self, macro: dict[str, Any]) -> dict[str, Any]:
        """Accept the new set/wait format and gently migrate older macro files."""
        key = str(macro.get("key", "")).upper().strip()[:1]
        name = str(macro.get("name", "Macro")).strip() or "Macro"
        normalized_steps: list[dict[str, Any]] = []

        for raw_step in macro.get("steps", []):
            if not isinstance(raw_step, dict):
                continue
            action = str(raw_step.get("action", "set")).lower().strip().replace("_", " ").replace("-", " ")
            if action in {"wait", "delay", "pause"}:
                try:
                    seconds = float(raw_step.get("seconds", raw_step.get("duration", 0.0)) or 0.0)
                except (TypeError, ValueError):
                    seconds = 0.0
                normalized_steps.append({"action": "wait", "seconds": max(0.0, seconds)})
                continue

            states = self._default_macro_states()
            raw_states = raw_step.get("states")
            if isinstance(raw_states, dict):
                for valve_num in range(1, 10):
                    raw_state = str(raw_states.get(str(valve_num), raw_states.get(valve_num, "low"))).lower().strip()
                    states[str(valve_num)] = "high" if raw_state in {"high", "on", "1", "true", "open"} else "low"
            else:
                valve = int(raw_step.get("valve", 0) or 0)
                state = str(raw_step.get("state", "high" if action in {"high", "on"} else "low")).lower().strip()
                if 1 <= valve <= 9:
                    states[str(valve)] = "high" if state in {"high", "on", "1", "true", "open"} or action in {"high", "on"} else "low"
            normalized_steps.append({"action": "set", "states": states})

        return {"key": key, "name": name, "steps": normalized_steps}

    def reload_macros(self) -> None:
        self.macros.clear()
        if not MACRO_FILE.exists():
            self.macro_label.setText("Macros | none loaded")
            return
        try:
            parsed = json.loads(MACRO_FILE.read_text())
            for macro in parsed.get("macros", []):
                macro = self._normalize_macro_for_runtime(macro)
                key = str(macro.get("key", "")).upper().strip()
                if len(key) != 1 or not key.isalpha() or key == "A":
                    continue
                self.macros[key] = macro
            if self.macros:
                summary = " | ".join(f"{k}: {v.get('name', 'Macro')}" for k, v in sorted(self.macros.items()))
                self.macro_label.setText(f"Macros | {summary}")
            else:
                self.macro_label.setText("Macros | none loaded")
        except Exception as exc:
            self.macro_label.setText("Macros | failed to load")
            self.append_log(f"[MACRO ERROR] {exc}")

    def open_macro_editor(self) -> None:
        dialog = MacroEditorDialog(self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.reload_macros()

    def _apply_macro_step(self, step: dict[str, Any]) -> float:
        action = str(step.get("action", "")).lower().strip()

        if action == "wait":
            return max(0.0, float(step.get("seconds", step.get("duration", 0))))

        if action == "set":
            raw_states = step.get("states")
            if not isinstance(raw_states, dict):
                raise ValueError("Set steps must include a states object for all valves.")

            with self.cmd_lock:
                bits = 0x8000
                for solenoid_idx in range(self.config.num_sol):
                    valve_num = solenoid_idx + 1
                    state = str(raw_states.get(str(valve_num), raw_states.get(valve_num, "low"))).lower().strip()
                    if state in {"high", "on", "1", "true", "open"}:
                        bit_position = 14 - solenoid_idx
                        bits |= 1 << bit_position
                    elif state not in {"low", "off", "0", "false", "closed", "close"}:
                        raise ValueError(f"Invalid state for valve {valve_num}: {state!r}")
                self.cmd_solenoid_bits = bits & 0xFFFF
                bits_to_send = self.cmd_solenoid_bits

            self.queue_command(bits_to_send)
            self.append_log(f"Command bits: {bits_to_send:016b}")
            return 0.0

        raise ValueError(f"Unknown macro action: {action}. Macros only support set and wait.")

    def run_macro(self, key: str) -> None:
        macro = self.macros.get(key.upper())
        if not macro:
            return
        if self.macro_running.is_set():
            self.append_log("[MACRO] Another macro is already running.")
            return

        steps = [dict(step) for step in macro.get("steps", []) if isinstance(step, dict)]
        if not steps:
            self.append_log("[MACRO] No steps to run.")
            return

        self._active_macro_steps = steps
        self._active_macro_index = 0
        self.macro_running.set()
        name = macro.get("name", f"Shift+{key.upper()}")
        self.append_log(f"[MACRO] Running {name}")
        self._run_next_macro_step()

    def _run_next_macro_step(self) -> None:
        if not self.macro_running.is_set():
            return
        if self._active_macro_index >= len(self._active_macro_steps):
            self.append_log("[MACRO] Done")
            self._active_macro_steps = []
            self._active_macro_index = 0
            self.macro_running.clear()
            return

        step = self._active_macro_steps[self._active_macro_index]
        self._active_macro_index += 1
        try:
            delay_seconds = self._apply_macro_step(step)
        except Exception as exc:
            self.append_log(f"[MACRO ERROR] {exc}")
            self._active_macro_steps = []
            self._active_macro_index = 0
            self.macro_running.clear()
            return

        self._macro_step_timer.start(max(0, int(delay_seconds * 1000)))

    def _handle_shift_keypress(self, event: QtGui.QKeyEvent) -> bool:
        """Handle the same shortcut idea as the original DearPyGUI code.

        DearPyGUI was watching raw key codes 537-545 while Shift was down.
        In PyQt, Shift+number can arrive as either the physical number key
        (Key_1..Key_9) OR as the shifted printable character (!, @, #, ...).
        This accepts both forms so Shift+1 through Shift+9 work regardless of
        keyboard layout/focus handling.
        """
        if event.type() != QtCore.QEvent.Type.KeyPress:
            return False

        # Only commands require Shift. Plain number/letter presses pass through.
        shift_down = bool(event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier)
        if not shift_down:
            return False

        # Swallow auto-repeat while Shift is held so a held key does not spam valves.
        if event.isAutoRepeat():
            return True

        key = event.key()
        text = event.text()

        # Physical/top-row number key path.
        number_key_map = {
            QtCore.Qt.Key.Key_1: 0,
            QtCore.Qt.Key.Key_2: 1,
            QtCore.Qt.Key.Key_3: 2,
            QtCore.Qt.Key.Key_4: 3,
            QtCore.Qt.Key.Key_5: 4,
            QtCore.Qt.Key.Key_6: 5,
            QtCore.Qt.Key.Key_7: 6,
            QtCore.Qt.Key.Key_8: 7,
            QtCore.Qt.Key.Key_9: 8,
        }

        # Shifted printable symbol path. On many systems, Shift+1 reports
        # key=Key_Exclam and text='!' rather than key=Key_1.
        shifted_number_text_map = {
            "!": 0,
            "@": 1,
            "#": 2,
            "$": 3,
            "%": 4,
            "^": 5,
            "&": 6,
            "*": 7,
            "(": 8,
        }

        # Shifted Qt key enum path. Some platforms preserve these as special keys.
        shifted_number_key_map = {
            QtCore.Qt.Key.Key_Exclam: 0,
            QtCore.Qt.Key.Key_At: 1,
            QtCore.Qt.Key.Key_NumberSign: 2,
            QtCore.Qt.Key.Key_Dollar: 3,
            QtCore.Qt.Key.Key_Percent: 4,
            QtCore.Qt.Key.Key_AsciiCircum: 5,
            QtCore.Qt.Key.Key_Ampersand: 6,
            QtCore.Qt.Key.Key_Asterisk: 7,
            QtCore.Qt.Key.Key_ParenLeft: 8,
        }

        solenoid_idx = None
        if key in number_key_map:
            solenoid_idx = number_key_map[key]
        elif text in shifted_number_text_map:
            solenoid_idx = shifted_number_text_map[text]
        elif key in shifted_number_key_map:
            solenoid_idx = shifted_number_key_map[key]

        if solenoid_idx is not None:
            self.toggle_solenoid(solenoid_idx)
            return True

        if key == QtCore.Qt.Key.Key_A or text.upper() == "A":
            self.emergency_stop()
            return True

        if key == QtCore.Qt.Key.Key_Z or text.upper() == "Z":
            self.zero_pressures()
            return True

        macro_key = text.upper()
        if len(macro_key) == 1 and macro_key.isalpha() and macro_key in self.macros:
            self.run_macro(macro_key)
            return True

        return False

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        # Only capture shortcuts while the main ground-station window is active.
        # This prevents Shift+letters from firing while editing the macro JSON dialog.
        if QtWidgets.QApplication.activeWindow() is self and isinstance(event, QtGui.QKeyEvent):
            if self._handle_shift_keypress(event):
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if self._handle_shift_keypress(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.disconnect_serial()
        event.accept()


STYLE = """
QMainWindow, QWidget {
    font-family: Segoe UI, Arial;
    font-size: 10.5pt;
    color: #000000;
    background: #ffffff;
}

QFrame#topPanel, QFrame#bottomPanel {
    background: #d9d9d9;
    border: 3px solid #000000;
    border-radius: 0px;
}

QLabel {
    color: #000000;
    background: transparent;
    font-weight: 600;
}

QLabel#sectionTitle {
    color: #ffffff;
    background: #000000;
    border: 2px solid #000000;
    padding: 6px 10px;
    font-weight: 800;
}

QLabel#macroLabel {
    color: #000000;
    background: #ffffff;
    border: 2px solid #000000;
    padding: 6px 8px;
    font-weight: 700;
}

QLabel#statusLabel {
    color: #ffffff;
    background: #000000;
    border: 2px solid #000000;
    padding: 8px 12px;
    min-width: 120px;
    font-weight: 900;
}

QLabel#statusLabel[state="connected"] {
    color: #000000;
    background: #ffffff;
}

QLabel[role="readout"] {
    color: #ffffff;
    background: #000000;
    border: 2px solid #000000;
    padding: 7px 8px;
    font-weight: 900;
}

QComboBox, QSpinBox {
    color: #000000;
    background: #ffffff;
    border: 2px solid #000000;
    padding: 5px 8px;
    selection-background-color: #000000;
    selection-color: #ffffff;
    font-weight: 700;
}

QComboBox QAbstractItemView {
    color: #000000;
    background: #ffffff;
    border: 2px solid #000000;
    selection-background-color: #000000;
    selection-color: #ffffff;
}

QPushButton {
    color: #ffffff;
    background: #333333;
    border: 2px solid #000000;
    border-radius: 0px;
    padding: 7px 11px;
    font-weight: 800;
}

QPushButton:hover {
    color: #000000;
    background: #ffffff;
}

QPushButton:pressed {
    color: #ffffff;
    background: #000000;
}

QPushButton:disabled {
    color: #666666;
    background: #bfbfbf;
    border: 2px solid #666666;
}

QPushButton#connectButton {
    color: #ffffff;
    background: #000000;
    border: 3px solid #000000;
}

QPushButton#disconnectButton {
    color: #000000;
    background: #ffffff;
    border: 3px solid #000000;
}

QPushButton#zeroButton {
    color: #000000;
    background: #ffffff;
    border: 3px solid #000000;
}

QPushButton#estopButton {
    color: #ffffff;
    background: #000000;
    border: 4px solid #000000;
    font-size: 12pt;
    font-weight: 900;
}

QLabel[role="valve"] {
    color: #ffffff;
    background: #000000;
    border: 3px solid #000000;
    padding: 8px 10px;
    font-weight: 900;
}

QLabel[role="valve"][state="on"] {
    color: #000000;
    background: #ffffff;
    border: 3px solid #000000;
}

QLabel[role="valve"][state="off"] {
    color: #ffffff;
    background: #000000;
    border: 3px solid #000000;
}

QPlainTextEdit {
    font-family: Consolas, monospace;
    font-size: 9.5pt;
    color: #ffffff;
    background: #000000;
    border: 3px solid #000000;
    selection-background-color: #ffffff;
    selection-color: #000000;
}

QLabel#macroHelp {
    color: #000000;
    background: #ffffff;
    border: 3px solid #000000;
    padding: 8px 10px;
    font-weight: 800;
}

QTableWidget {
    color: #000000;
    background: #ffffff;
    gridline-color: #000000;
    border: 3px solid #000000;
    selection-background-color: #000000;
    selection-color: #ffffff;
}

QHeaderView::section {
    color: #ffffff;
    background: #000000;
    border: 1px solid #ffffff;
    padding: 5px;
    font-weight: 900;
}

QDialog, QDialog QWidget {
    background: #d9d9d9;
    color: #000000;
}

QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QPushButton:focus {
    border: 3px solid #000000;
}
"""


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = GroundStationWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
