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

Shared configuration file: ground_station_config.json in the same folder as this script.
It stores sensor/valve counts, relay mapping, macros, and packet configuration for external editing/sharing.
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

DEFAULT_SENSOR_V_MIN = 0.5
DEFAULT_SENSOR_V_MAX = 4.5
DEFAULT_PRESSURE_MAX = 5000.0
DEFAULT_ADC_FULL_SCALE_VOLTAGE = 6.144  # ADS gain=2/3 full-scale range

ADC_PRESETS = {
    "ADS1115_16BIT": {"display": "ADS1115 / 16-bit", "bits": 16, "max_count": 32767, "full_scale_voltage": 6.144},
    "ADS1015_12BIT": {"display": "ADS1015 / 12-bit", "bits": 12, "max_count": 2047, "full_scale_voltage": 6.144},
}
DEFAULT_ADC_MODEL = "ADS1115_16BIT"

SYNC = b"\xAA\x55"
CONFIG_FILE = Path(__file__).with_name("ground_station_config.json")
MACRO_FILE = Path(__file__).with_name("macros.json")  # legacy fallback/migration only


def default_relay_map() -> dict[str, int]:
    # Valve 1..9 map to command bits 14..6 by default.
    # Edit ground_station_config.json to change the backend relay mapping.
    return {str(valve): 15 - valve for valve in range(1, 10)}


def default_pressure_sensor_config(index: int) -> dict[str, Any]:
    preset = ADC_PRESETS[DEFAULT_ADC_MODEL]
    return {
        "name": f"P-{index + 1}",
        "adc_model": DEFAULT_ADC_MODEL,
        "adc_bits": preset["bits"],
        "adc_max_count": preset["max_count"],
        "adc_full_scale_voltage": preset["full_scale_voltage"],
        "voltage_min": DEFAULT_SENSOR_V_MIN,
        "voltage_max": DEFAULT_SENSOR_V_MAX,
        "pressure_max_psig": DEFAULT_PRESSURE_MAX,
    }


def normalize_pressure_sensor_config(raw: Any, index: int) -> dict[str, Any]:
    cfg = default_pressure_sensor_config(index)
    if isinstance(raw, dict):
        cfg.update(raw)

    adc_model = str(cfg.get("adc_model", DEFAULT_ADC_MODEL)).strip()
    if adc_model not in ADC_PRESETS:
        adc_model = DEFAULT_ADC_MODEL
    preset = ADC_PRESETS[adc_model]
    cfg["adc_model"] = adc_model

    def as_float(key: str, default: float) -> float:
        try:
            return float(cfg.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def as_int(key: str, default: int) -> int:
        try:
            return int(float(cfg.get(key, default)))
        except (TypeError, ValueError):
            return int(default)

    cfg["name"] = str(cfg.get("name") or f"P-{index + 1}").strip() or f"P-{index + 1}"
    cfg["adc_bits"] = as_int("adc_bits", int(preset["bits"]))
    cfg["adc_max_count"] = max(1, as_int("adc_max_count", int(preset["max_count"])))
    cfg["adc_full_scale_voltage"] = max(0.0001, as_float("adc_full_scale_voltage", float(preset["full_scale_voltage"])))
    cfg["voltage_min"] = as_float("voltage_min", DEFAULT_SENSOR_V_MIN)
    cfg["voltage_max"] = as_float("voltage_max", DEFAULT_SENSOR_V_MAX)
    if cfg["voltage_max"] == cfg["voltage_min"]:
        cfg["voltage_max"] = cfg["voltage_min"] + 1.0
    cfg["pressure_max_psig"] = max(0.0, as_float("pressure_max_psig", DEFAULT_PRESSURE_MAX))
    return cfg


def normalize_pressure_sensor_configs(raw_configs: Any, count: int) -> list[dict[str, Any]]:
    raw_list = raw_configs if isinstance(raw_configs, list) else []
    return [normalize_pressure_sensor_config(raw_list[i] if i < len(raw_list) else None, i) for i in range(max(0, count))]


def default_packet_config() -> dict[str, Any]:
    return {
        "baud": BAUD,
        "sync_hex": "AA55",
        "endian": "<",
        "base_format": "IIBBH",
        "adc_format": "H",
        "command_template": "0x{bits:04X},2\n",
    }


def normalize_packet_config(raw: Any) -> dict[str, Any]:
    cfg = default_packet_config()
    if isinstance(raw, dict):
        cfg.update(raw)
    try:
        cfg["baud"] = max(1, int(float(cfg.get("baud", BAUD))))
    except (TypeError, ValueError):
        cfg["baud"] = BAUD
    sync_hex = "".join(ch for ch in str(cfg.get("sync_hex", "AA55")) if ch in "0123456789abcdefABCDEF")
    cfg["sync_hex"] = sync_hex.upper() if sync_hex else "AA55"
    endian = str(cfg.get("endian", "<")).strip()[:1]
    cfg["endian"] = endian if endian in {"<", ">", "=", "!"} else "<"
    cfg["base_format"] = str(cfg.get("base_format", "IIBBH") or "IIBBH").replace(" ", "")
    cfg["adc_format"] = str(cfg.get("adc_format", "H") or "H").replace(" ", "")
    tmpl = str(cfg.get("command_template", "0x{bits:04X},2\n"))
    cfg["command_template"] = tmpl if "{bits" in tmpl else "0x{bits:04X},2\n"
    return cfg


@dataclass
class AppConfig:
    port: str = "COM6"
    num_p: int = 4
    num_t: int = 0
    num_lc: int = 0
    num_sol: int = 6
    relay_map: dict[str, int] = field(default_factory=default_relay_map)
    pressure_sensor_configs: list[dict[str, Any]] = field(default_factory=lambda: normalize_pressure_sensor_configs([], 4))
    sensor_type: list[str] = field(default_factory=lambda: ["low", "low", "low", "low"])
    baud: int = BAUD
    sync_hex: str = "AA55"
    packet_endian: str = "<"
    packet_base_format: str = "IIBBH"
    packet_adc_format: str = "H"
    command_template: str = "0x{bits:04X},2\n"

    @property
    def total_sensors(self) -> int:
        return self.num_p + self.num_t + self.num_lc

    @property
    def sync_bytes(self) -> bytes:
        cleaned = "".join(ch for ch in str(self.sync_hex) if ch in "0123456789abcdefABCDEF")
        if len(cleaned) % 2:
            cleaned = "0" + cleaned
        try:
            return bytes.fromhex(cleaned) or SYNC
        except ValueError:
            return SYNC

    @property
    def packet_format(self) -> str:
        endian = self.packet_endian if self.packet_endian in {"<", ">", "=", "!"} else "<"
        base = str(self.packet_base_format or "IIBBH").replace(" ", "")
        adc = str(self.packet_adc_format or "H").replace(" ", "")
        return f"{endian}{base}{self.total_sensors}{adc}"

    @property
    def packet_size(self) -> int:
        try:
            return struct.calcsize(self.packet_format)
        except struct.error:
            return 12 + (self.total_sensors * 2)

    def relay_bit_for_valve(self, valve_index_zero_based: int) -> int:
        valve_num = valve_index_zero_based + 1
        raw = self.relay_map.get(str(valve_num), 14 - valve_index_zero_based)
        try:
            bit = int(raw)
        except (TypeError, ValueError):
            bit = 14 - valve_index_zero_based
        return max(0, min(15, bit))


def _read_shared_config() -> dict[str, Any]:
    """Read the externally editable/shared GUI configuration file."""
    if CONFIG_FILE.exists():
        try:
            parsed = json.loads(CONFIG_FILE.read_text())
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    # Legacy macro fallback so old macros.json is not lost.
    macros = []
    if MACRO_FILE.exists():
        try:
            legacy = json.loads(MACRO_FILE.read_text())
            macros = legacy.get("macros", []) if isinstance(legacy, dict) else []
        except Exception:
            macros = []
    return {
        "port": "COM6",
        "pressure_sensors": 4,
        "pressure_sensor_configs": normalize_pressure_sensor_configs([], 4),
        "temperature_sensors": 0,
        "load_cells": 0,
        "valves": 5,
        "relay_map": default_relay_map(),
        "macros": macros,
        "packet_config": default_packet_config(),
    }


def _write_shared_config(config_payload: dict[str, Any]) -> None:
    CONFIG_FILE.write_text(json.dumps(config_payload, indent=2))



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
            self._ser = serial.Serial(self.config.port, self.config.baud, timeout=0)
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
            sync_bytes = self.config.sync_bytes
            sync_len = len(sync_bytes)
            total_packet_size = sync_len + self.config.packet_size

            while self._running.is_set():
                while not self.command_queue.empty():
                    cmd_bits = self.command_queue.get_nowait()
                    try:
                        message = self.config.command_template.format(bits=cmd_bits)
                    except Exception:
                        message = f"0x{cmd_bits:04X},2\n"
                    self._ser.write(message.encode())
                    self.log_message.emit(f"[GUI] Sent: {message.strip()}")

                incoming = self._ser.read(self._ser.in_waiting or 1)
                if incoming:
                    buffer.extend(incoming)

                while True:
                    if len(buffer) < sync_len:
                        break

                    sync_index = buffer.find(sync_bytes)
                    if sync_index == -1:
                        buffer.clear()
                        break

                    if sync_index > 0:
                        del buffer[:sync_index]

                    if len(buffer) < total_packet_size:
                        break

                    raw = buffer[sync_len:total_packet_size]
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

    def __init__(self, parent: QtWidgets.QWidget | None = None, valve_count: int = 9):
        super().__init__(parent)
        self.VALVE_COUNT = max(1, min(9, int(valve_count)))
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
        self.step_table = QtWidgets.QTableWidget(0, 2 + self.VALVE_COUNT)
        self.step_table.setHorizontalHeaderLabels(
            ["Action", "Seconds"] + [f"V{i}" for i in range(1, self.VALVE_COUNT + 1)]
        )
        self.step_table.horizontalHeader().setStretchLastSection(False)
        self.step_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.step_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        for col in range(2, 2 + self.VALVE_COUNT):
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
        parsed = _read_shared_config()
        macros = parsed.get("macros", [])
        if not isinstance(macros, list):
            macros = []
        self._macros = [self._normalize_macro(m) for m in macros if isinstance(m, dict)]
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
        for col in range(2, 2 + self.VALVE_COUNT):
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
        payload = _read_shared_config()
        payload["macros"] = self._macros
        _write_shared_config(payload)
        self.accept()


class SensorConfigDialog(QtWidgets.QDialog):
    """Editor for pressure sensor conversion parameters stored in ground_station_config.json."""

    COL_NAME = 0
    COL_ADC = 1
    COL_BITS = 2
    COL_MAX_COUNT = 3
    COL_FS_VOLT = 4
    COL_V_MIN = 5
    COL_V_MAX = 6
    COL_P_MAX = 7

    def __init__(self, parent: QtWidgets.QWidget | None = None, pressure_count: int = 4):
        super().__init__(parent)
        self.pressure_count = max(0, min(10, int(pressure_count)))
        self.setWindowTitle("Sensor Configuration")
        self.resize(1080, 520)
        self._loading = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        help_text = QtWidgets.QLabel(
            "Pressure conversion is stored per sensor. Default transducer span is 0.5–4.5 V. "
            "Choose ADS1115 for 16-bit raw counts or ADS1015 for native 12-bit raw counts. "
            "Use 5000 psig or 2000 psig full-scale as needed."
        )
        help_text.setObjectName("macroHelp")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        self.table = QtWidgets.QTableWidget(self.pressure_count, 8)
        self.table.setHorizontalHeaderLabels([
            "Name", "ADC", "Bits", "ADC Max", "ADC FS V", "V Min", "V Max", "PSI Max"
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, stretch=1)

        button_row = QtWidgets.QHBoxLayout()
        defaults_5k = QtWidgets.QPushButton("Default 5k / ADS1115")
        defaults_2k = QtWidgets.QPushButton("Default 2k / ADS1115")
        save_btn = QtWidgets.QPushButton("Save")
        cancel_btn = QtWidgets.QPushButton("Cancel")
        button_row.addWidget(defaults_5k)
        button_row.addWidget(defaults_2k)
        button_row.addStretch(1)
        button_row.addWidget(save_btn)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)

        defaults_5k.clicked.connect(lambda: self.apply_defaults(5000.0, DEFAULT_ADC_MODEL))
        defaults_2k.clicked.connect(lambda: self.apply_defaults(2000.0, DEFAULT_ADC_MODEL))
        save_btn.clicked.connect(self.save_file)
        cancel_btn.clicked.connect(self.reject)

        self.table.cellChanged.connect(self._cell_changed)
        self._load_from_file()

    def _load_from_file(self) -> None:
        payload = _read_shared_config()
        configs = normalize_pressure_sensor_configs(payload.get("pressure_sensor_configs"), self.pressure_count)
        self._loading = True
        for row, cfg in enumerate(configs):
            self._set_row(row, cfg)
        self._loading = False

    def _set_row(self, row: int, cfg: dict[str, Any]) -> None:
        self.table.setItem(row, self.COL_NAME, QtWidgets.QTableWidgetItem(str(cfg.get("name", f"P-{row + 1}"))))

        adc_combo = QtWidgets.QComboBox()
        for key, preset in ADC_PRESETS.items():
            adc_combo.addItem(preset["display"], key)
        idx = adc_combo.findData(str(cfg.get("adc_model", DEFAULT_ADC_MODEL)))
        adc_combo.setCurrentIndex(max(0, idx))
        adc_combo.currentIndexChanged.connect(lambda _=None, r=row: self._adc_changed(r))
        self.table.setCellWidget(row, self.COL_ADC, adc_combo)

        self.table.setItem(row, self.COL_BITS, QtWidgets.QTableWidgetItem(str(cfg.get("adc_bits", 16))))
        self.table.setItem(row, self.COL_MAX_COUNT, QtWidgets.QTableWidgetItem(str(cfg.get("adc_max_count", 32767))))
        self.table.setItem(row, self.COL_FS_VOLT, QtWidgets.QTableWidgetItem(str(cfg.get("adc_full_scale_voltage", 6.144))))
        self.table.setItem(row, self.COL_V_MIN, QtWidgets.QTableWidgetItem(str(cfg.get("voltage_min", 0.5))))
        self.table.setItem(row, self.COL_V_MAX, QtWidgets.QTableWidgetItem(str(cfg.get("voltage_max", 4.5))))
        self.table.setItem(row, self.COL_P_MAX, QtWidgets.QTableWidgetItem(str(cfg.get("pressure_max_psig", 5000))))

    def _adc_model_for_row(self, row: int) -> str:
        widget = self.table.cellWidget(row, self.COL_ADC)
        if isinstance(widget, QtWidgets.QComboBox):
            return str(widget.currentData() or DEFAULT_ADC_MODEL)
        return DEFAULT_ADC_MODEL

    def _adc_changed(self, row: int) -> None:
        if self._loading:
            return
        model = self._adc_model_for_row(row)
        preset = ADC_PRESETS.get(model, ADC_PRESETS[DEFAULT_ADC_MODEL])
        self._loading = True
        self.table.item(row, self.COL_BITS).setText(str(preset["bits"]))
        self.table.item(row, self.COL_MAX_COUNT).setText(str(preset["max_count"]))
        self.table.item(row, self.COL_FS_VOLT).setText(str(preset["full_scale_voltage"]))
        self._loading = False

    def _cell_changed(self, row: int, column: int) -> None:
        if self._loading:
            return
        # Keep names from becoming blank.
        if column == self.COL_NAME:
            item = self.table.item(row, column)
            if item is not None and not item.text().strip():
                item.setText(f"P-{row + 1}")

    def _float_cell(self, row: int, col: int, default: float) -> float:
        item = self.table.item(row, col)
        try:
            return float(item.text().strip()) if item else default
        except (TypeError, ValueError):
            return default

    def _int_cell(self, row: int, col: int, default: int) -> int:
        item = self.table.item(row, col)
        try:
            return int(float(item.text().strip())) if item else default
        except (TypeError, ValueError):
            return default

    def _row_config(self, row: int) -> dict[str, Any]:
        model = self._adc_model_for_row(row)
        preset = ADC_PRESETS.get(model, ADC_PRESETS[DEFAULT_ADC_MODEL])
        name_item = self.table.item(row, self.COL_NAME)
        cfg = {
            "name": name_item.text().strip() if name_item and name_item.text().strip() else f"P-{row + 1}",
            "adc_model": model,
            "adc_bits": self._int_cell(row, self.COL_BITS, int(preset["bits"])),
            "adc_max_count": self._int_cell(row, self.COL_MAX_COUNT, int(preset["max_count"])),
            "adc_full_scale_voltage": self._float_cell(row, self.COL_FS_VOLT, float(preset["full_scale_voltage"])),
            "voltage_min": self._float_cell(row, self.COL_V_MIN, DEFAULT_SENSOR_V_MIN),
            "voltage_max": self._float_cell(row, self.COL_V_MAX, DEFAULT_SENSOR_V_MAX),
            "pressure_max_psig": self._float_cell(row, self.COL_P_MAX, DEFAULT_PRESSURE_MAX),
        }
        return normalize_pressure_sensor_config(cfg, row)

    def apply_defaults(self, pressure_max: float, adc_model: str) -> None:
        self._loading = True
        preset = ADC_PRESETS.get(adc_model, ADC_PRESETS[DEFAULT_ADC_MODEL])
        for row in range(self.pressure_count):
            cfg = default_pressure_sensor_config(row)
            cfg.update({
                "adc_model": adc_model,
                "adc_bits": preset["bits"],
                "adc_max_count": preset["max_count"],
                "adc_full_scale_voltage": preset["full_scale_voltage"],
                "voltage_min": 0.5,
                "voltage_max": 4.5,
                "pressure_max_psig": pressure_max,
            })
            self._set_row(row, cfg)
        self._loading = False

    def save_file(self) -> None:
        configs = [self._row_config(row) for row in range(self.pressure_count)]
        for idx, cfg in enumerate(configs):
            if cfg["voltage_max"] == cfg["voltage_min"]:
                QtWidgets.QMessageBox.critical(self, "Invalid Sensor", f"P-{idx + 1} V Min and V Max cannot match.")
                return
            if cfg["adc_max_count"] <= 0 or cfg["adc_full_scale_voltage"] <= 0:
                QtWidgets.QMessageBox.critical(self, "Invalid Sensor", f"P-{idx + 1} ADC settings must be positive.")
                return
        payload = _read_shared_config()
        payload["pressure_sensor_configs"] = configs
        payload["pressure_sensors"] = self.pressure_count
        _write_shared_config(payload)
        self.accept()


class ConfigEditorDialog(QtWidgets.QDialog):
    """Tabbed editor for sensors, relay mapping, macros, and packets stored in ground_station_config.json."""

    def __init__(self, parent: QtWidgets.QWidget | None = None, config: AppConfig | None = None):
        super().__init__(parent)
        self.config = config or AppConfig()
        self.valve_count = max(1, min(9, int(self.config.num_sol)))
        self.pressure_count = max(0, min(10, int(self.config.num_p)))
        self._loading = False
        self._macros: list[dict[str, Any]] = []
        self._current_macro_row = -1

        self.setWindowTitle("Configuration")
        self.resize(1180, 720)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.tabs = QtWidgets.QTabWidget()
        root.addWidget(self.tabs, stretch=1)

        self._build_sensors_tab()
        self._build_relays_tab()
        self._build_macros_tab()
        self._build_packet_tab()

        buttons = QtWidgets.QHBoxLayout()
        self.reload_button = QtWidgets.QPushButton("Reload From File")
        self.save_button = QtWidgets.QPushButton("Save Configuration")
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        buttons.addWidget(self.reload_button)
        buttons.addStretch(1)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.cancel_button)
        root.addLayout(buttons)

        self.reload_button.clicked.connect(self.reload_from_file)
        self.save_button.clicked.connect(self.save_all)
        self.cancel_button.clicked.connect(self.reject)

        self.reload_from_file()

    # ---------------- Sensors tab ----------------
    def _build_sensors_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        help_text = QtWidgets.QLabel(
            "Pressure sensors: edit name, ADC model/count scaling, transducer voltage span, and pressure full-scale. "
            "Defaults use 0.5-4.5 V and ADS1115/16-bit scaling."
        )
        help_text.setObjectName("macroHelp")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        self.sensor_table = QtWidgets.QTableWidget(self.pressure_count, 8)
        self.sensor_table.setHorizontalHeaderLabels([
            "Name", "ADC", "Bits", "ADC Max", "ADC FS V", "V Min", "V Max", "PSI Max"
        ])
        self.sensor_table.verticalHeader().setVisible(False)
        self.sensor_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.sensor_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.sensor_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.sensor_table, stretch=1)

        row = QtWidgets.QHBoxLayout()
        default_5k = QtWidgets.QPushButton("Set All: 5k / ADS1115")
        default_2k = QtWidgets.QPushButton("Set All: 2k / ADS1115")
        row.addWidget(default_5k)
        row.addWidget(default_2k)
        row.addStretch(1)
        layout.addLayout(row)

        default_5k.clicked.connect(lambda: self.apply_sensor_defaults(5000.0, DEFAULT_ADC_MODEL))
        default_2k.clicked.connect(lambda: self.apply_sensor_defaults(2000.0, DEFAULT_ADC_MODEL))
        self.sensor_table.cellChanged.connect(self._sensor_cell_changed)
        self.tabs.addTab(tab, "Sensors")

    def _set_sensor_row(self, row: int, cfg: dict[str, Any]) -> None:
        self.sensor_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(cfg.get("name", f"P-{row + 1}"))))
        adc_combo = QtWidgets.QComboBox()
        for key, preset in ADC_PRESETS.items():
            adc_combo.addItem(preset["display"], key)
        idx = adc_combo.findData(str(cfg.get("adc_model", DEFAULT_ADC_MODEL)))
        adc_combo.setCurrentIndex(max(0, idx))
        adc_combo.currentIndexChanged.connect(lambda _=None, r=row: self._sensor_adc_changed(r))
        self.sensor_table.setCellWidget(row, 1, adc_combo)
        values = [
            cfg.get("adc_bits", 16), cfg.get("adc_max_count", 32767),
            cfg.get("adc_full_scale_voltage", 6.144), cfg.get("voltage_min", 0.5),
            cfg.get("voltage_max", 4.5), cfg.get("pressure_max_psig", 5000),
        ]
        for col, val in enumerate(values, start=2):
            self.sensor_table.setItem(row, col, QtWidgets.QTableWidgetItem(str(val)))

    def _sensor_adc_model_for_row(self, row: int) -> str:
        widget = self.sensor_table.cellWidget(row, 1)
        if isinstance(widget, QtWidgets.QComboBox):
            return str(widget.currentData() or DEFAULT_ADC_MODEL)
        return DEFAULT_ADC_MODEL

    def _sensor_adc_changed(self, row: int) -> None:
        if self._loading:
            return
        preset = ADC_PRESETS.get(self._sensor_adc_model_for_row(row), ADC_PRESETS[DEFAULT_ADC_MODEL])
        self._loading = True
        for col, val in [(2, preset["bits"]), (3, preset["max_count"]), (4, preset["full_scale_voltage"])]:
            item = self.sensor_table.item(row, col)
            if item is None:
                item = QtWidgets.QTableWidgetItem()
                self.sensor_table.setItem(row, col, item)
            item.setText(str(val))
        self._loading = False

    def _sensor_cell_changed(self, row: int, column: int) -> None:
        if self._loading:
            return
        if column == 0:
            item = self.sensor_table.item(row, column)
            if item is not None and not item.text().strip():
                item.setText(f"P-{row + 1}")

    def _sensor_float_cell(self, row: int, col: int, default: float) -> float:
        item = self.sensor_table.item(row, col)
        try:
            return float(item.text().strip()) if item else default
        except (TypeError, ValueError):
            return default

    def _sensor_int_cell(self, row: int, col: int, default: int) -> int:
        item = self.sensor_table.item(row, col)
        try:
            return int(float(item.text().strip())) if item else default
        except (TypeError, ValueError):
            return default

    def _sensor_row_config(self, row: int) -> dict[str, Any]:
        model = self._sensor_adc_model_for_row(row)
        preset = ADC_PRESETS.get(model, ADC_PRESETS[DEFAULT_ADC_MODEL])
        name_item = self.sensor_table.item(row, 0)
        cfg = {
            "name": name_item.text().strip() if name_item and name_item.text().strip() else f"P-{row + 1}",
            "adc_model": model,
            "adc_bits": self._sensor_int_cell(row, 2, int(preset["bits"])),
            "adc_max_count": self._sensor_int_cell(row, 3, int(preset["max_count"])),
            "adc_full_scale_voltage": self._sensor_float_cell(row, 4, float(preset["full_scale_voltage"])),
            "voltage_min": self._sensor_float_cell(row, 5, DEFAULT_SENSOR_V_MIN),
            "voltage_max": self._sensor_float_cell(row, 6, DEFAULT_SENSOR_V_MAX),
            "pressure_max_psig": self._sensor_float_cell(row, 7, DEFAULT_PRESSURE_MAX),
        }
        return normalize_pressure_sensor_config(cfg, row)

    def apply_sensor_defaults(self, pressure_max: float, adc_model: str) -> None:
        preset = ADC_PRESETS.get(adc_model, ADC_PRESETS[DEFAULT_ADC_MODEL])
        self._loading = True
        for row in range(self.pressure_count):
            cfg = default_pressure_sensor_config(row)
            cfg.update({
                "adc_model": adc_model,
                "adc_bits": preset["bits"],
                "adc_max_count": preset["max_count"],
                "adc_full_scale_voltage": preset["full_scale_voltage"],
                "voltage_min": 0.5,
                "voltage_max": 4.5,
                "pressure_max_psig": pressure_max,
            })
            self._set_sensor_row(row, cfg)
        self._loading = False

    # ---------------- Relay tab ----------------
    def _build_relays_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        help_text = QtWidgets.QLabel(
            "Relay mapping defines which command bit each valve controls. Default: V1->bit14, V2->bit13, etc."
        )
        help_text.setObjectName("macroHelp")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        self.relay_table = QtWidgets.QTableWidget(9, 2)
        self.relay_table.setHorizontalHeaderLabels(["Valve", "Command Bit"])
        self.relay_table.verticalHeader().setVisible(False)
        self.relay_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.relay_table, stretch=1)

        row = QtWidgets.QHBoxLayout()
        default_btn = QtWidgets.QPushButton("Restore Default Mapping")
        row.addWidget(default_btn)
        row.addStretch(1)
        layout.addLayout(row)
        default_btn.clicked.connect(self.apply_default_relay_mapping)
        self.tabs.addTab(tab, "Relay Mapping")

    def _set_relay_row(self, valve_num: int, bit: int) -> None:
        row = valve_num - 1
        valve_item = QtWidgets.QTableWidgetItem(f"V{valve_num}")
        valve_item.setFlags(valve_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
        self.relay_table.setItem(row, 0, valve_item)
        spin = QtWidgets.QSpinBox()
        spin.setRange(0, 15)
        spin.setValue(max(0, min(15, int(bit))))
        spin.setEnabled(valve_num <= self.valve_count)
        self.relay_table.setCellWidget(row, 1, spin)

    def apply_default_relay_mapping(self) -> None:
        mapping = default_relay_map()
        for valve_num in range(1, 10):
            self._set_relay_row(valve_num, int(mapping[str(valve_num)]))

    # ---------------- Macro tab ----------------
    def _build_macros_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        help_text = QtWidgets.QLabel(
            "Rules: macros run with Shift+letter; Shift+A is reserved for E-stop; steps run top-to-bottom. "
            "Actions: set = choose high/low for every valve; wait = pause for seconds."
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
        self.step_table = QtWidgets.QTableWidget(0, 2 + self.valve_count)
        self.step_table.setHorizontalHeaderLabels(["Action", "Seconds"] + [f"V{i}" for i in range(1, self.valve_count + 1)])
        self.step_table.verticalHeader().setVisible(False)
        self.step_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.step_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.step_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        right_layout.addWidget(self.step_table, stretch=1)
        step_buttons = QtWidgets.QHBoxLayout()
        add_set = QtWidgets.QPushButton("Add Set Step")
        add_wait = QtWidgets.QPushButton("Add Wait Step")
        del_step = QtWidgets.QPushButton("Delete Step")
        move_up = QtWidgets.QPushButton("Move Up")
        move_down = QtWidgets.QPushButton("Move Down")
        for b in (add_set, add_wait, del_step, move_up, move_down):
            step_buttons.addWidget(b)
        step_buttons.addStretch(1)
        right_layout.addLayout(step_buttons)
        splitter.addWidget(right)
        splitter.setSizes([300, 820])
        self.tabs.addTab(tab, "Macros")

        self.macro_table.itemChanged.connect(self._macro_item_changed)
        self.macro_table.currentCellChanged.connect(self._macro_selection_changed)
        add_macro.clicked.connect(self.add_macro)
        delete_macro.clicked.connect(self.delete_macro)
        add_set.clicked.connect(lambda: self.add_macro_step("set"))
        add_wait.clicked.connect(lambda: self.add_macro_step("wait"))
        del_step.clicked.connect(self.delete_macro_step)
        move_up.clicked.connect(lambda: self.move_macro_step(-1))
        move_down.clicked.connect(lambda: self.move_macro_step(1))

    def _default_macro_states(self) -> dict[str, str]:
        return {str(i): "low" for i in range(1, self.valve_count + 1)}

    def _normalize_macro_states(self, step: dict[str, Any]) -> dict[str, str]:
        states = self._default_macro_states()
        raw_states = step.get("states")
        if isinstance(raw_states, dict):
            for valve_num in range(1, self.valve_count + 1):
                raw = str(raw_states.get(str(valve_num), raw_states.get(valve_num, "low"))).lower().strip()
                states[str(valve_num)] = "high" if raw in {"high", "on", "1", "true", "open"} else "low"
            return states
        action = str(step.get("action", "set")).lower().strip()
        try:
            valve = int(step.get("valve", 0) or 0)
        except (TypeError, ValueError):
            valve = 0
        state = str(step.get("state", "high" if action in {"high", "on"} else "low")).lower().strip()
        if 1 <= valve <= self.valve_count:
            states[str(valve)] = "high" if state in {"high", "on", "1", "true", "open"} or action in {"high", "on"} else "low"
        return states

    def _normalize_macro_step(self, step: dict[str, Any]) -> dict[str, Any]:
        action = str(step.get("action", "set")).lower().strip().replace("_", "-")
        if action in {"wait", "delay", "pause"}:
            try:
                seconds = float(step.get("seconds", step.get("duration", 0.0)) or 0.0)
            except (TypeError, ValueError):
                seconds = 0.0
            return {"action": "wait", "seconds": max(0.0, seconds)}
        return {"action": "set", "states": self._normalize_macro_states(step)}

    def _normalize_macro(self, macro: dict[str, Any]) -> dict[str, Any]:
        key = str(macro.get("key", "")).upper().strip()[:1]
        name = str(macro.get("name", "Macro")).strip() or "Macro"
        steps = [self._normalize_macro_step(s) for s in macro.get("steps", []) if isinstance(s, dict)]
        if not steps:
            steps = [{"action": "set", "states": self._default_macro_states()}]
        return {"key": key, "name": name, "steps": steps}

    def _load_macro_table(self) -> None:
        self._loading = True
        self.macro_table.setRowCount(0)
        for macro in self._macros:
            row = self.macro_table.rowCount()
            self.macro_table.insertRow(row)
            self.macro_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(macro.get("key", ""))))
            self.macro_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(macro.get("name", "Macro"))))
        self._loading = False
        if self._macros:
            self.macro_table.selectRow(0)
            self._current_macro_row = 0
            self._load_steps_for_macro(0)
        else:
            self._current_macro_row = -1
            self.step_table.setRowCount(0)

    def _macro_selection_changed(self, current_row: int, _current_col: int, previous_row: int, _previous_col: int) -> None:
        if self._loading:
            return
        if previous_row >= 0 and previous_row < len(self._macros):
            self._save_steps_for_macro(previous_row)
        self._current_macro_row = current_row
        self._load_steps_for_macro(current_row)

    def _macro_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._loading:
            return
        row = item.row()
        if not (0 <= row < len(self._macros)):
            return
        if item.column() == 0:
            self._macros[row]["key"] = item.text().upper().strip()[:1]
            if item.text() != self._macros[row]["key"]:
                self._loading = True
                item.setText(self._macros[row]["key"])
                self._loading = False
        elif item.column() == 1:
            self._macros[row]["name"] = item.text().strip() or "Macro"

    def _set_step_row(self, row: int, step: dict[str, Any]) -> None:
        action = str(step.get("action", "set")).lower().strip()
        action_item = QtWidgets.QTableWidgetItem(action)
        action_item.setFlags(action_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
        self.step_table.setItem(row, 0, action_item)
        if action == "wait":
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(0.0, 3600.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setValue(float(step.get("seconds", 0.0)))
            self.step_table.setCellWidget(row, 1, spin)
            for col in range(2, 2 + self.valve_count):
                item = QtWidgets.QTableWidgetItem("—")
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self.step_table.setItem(row, col, item)
            return
        item = QtWidgets.QTableWidgetItem("—")
        item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
        self.step_table.setItem(row, 1, item)
        states = self._normalize_macro_states(step)
        for valve_num in range(1, self.valve_count + 1):
            combo = QtWidgets.QComboBox()
            combo.addItems(["low", "high"])
            combo.setCurrentText(states.get(str(valve_num), "low"))
            self.step_table.setCellWidget(row, 1 + valve_num, combo)

    def _load_steps_for_macro(self, macro_row: int) -> None:
        self._loading = True
        self.step_table.setRowCount(0)
        if 0 <= macro_row < len(self._macros):
            for step in self._macros[macro_row].get("steps", []):
                row = self.step_table.rowCount()
                self.step_table.insertRow(row)
                self._set_step_row(row, step)
        self._loading = False

    def _row_step(self, row: int) -> dict[str, Any]:
        action_item = self.step_table.item(row, 0)
        action = action_item.text().strip().lower() if action_item else "set"
        if action == "wait":
            widget = self.step_table.cellWidget(row, 1)
            seconds = float(widget.value()) if isinstance(widget, QtWidgets.QDoubleSpinBox) else 0.0
            return {"action": "wait", "seconds": max(0.0, seconds)}
        states = self._default_macro_states()
        for valve_num in range(1, self.valve_count + 1):
            widget = self.step_table.cellWidget(row, 1 + valve_num)
            if isinstance(widget, QtWidgets.QComboBox):
                states[str(valve_num)] = widget.currentText().strip().lower()
        return {"action": "set", "states": states}

    def _save_steps_for_macro(self, macro_row: int) -> None:
        if not (0 <= macro_row < len(self._macros)):
            return
        self._macros[macro_row]["steps"] = [self._row_step(row) for row in range(self.step_table.rowCount())]

    def add_macro(self) -> None:
        self._save_steps_for_macro(self._current_macro_row)
        used = {str(m.get("key", "")).upper() for m in self._macros}
        key = next((chr(c) for c in range(ord("B"), ord("Z") + 1) if chr(c) not in used and chr(c) != "A"), "B")
        self._macros.append({"key": key, "name": "New Macro", "steps": [{"action": "set", "states": self._default_macro_states()}]})
        self._load_macro_table()
        self.macro_table.selectRow(len(self._macros) - 1)

    def delete_macro(self) -> None:
        row = self.macro_table.currentRow()
        if 0 <= row < len(self._macros):
            self._macros.pop(row)
            self._load_macro_table()

    def add_macro_step(self, action: str) -> None:
        if not (0 <= self._current_macro_row < len(self._macros)):
            return
        row = self.step_table.rowCount()
        self.step_table.insertRow(row)
        step = {"action": "wait", "seconds": 0.5} if action == "wait" else {"action": "set", "states": self._default_macro_states()}
        self._set_step_row(row, step)
        self.step_table.selectRow(row)

    def delete_macro_step(self) -> None:
        row = self.step_table.currentRow()
        if row >= 0:
            self.step_table.removeRow(row)

    def move_macro_step(self, direction: int) -> None:
        row = self.step_table.currentRow()
        target = row + direction
        if row < 0 or target < 0 or target >= self.step_table.rowCount():
            return
        steps = [self._row_step(r) for r in range(self.step_table.rowCount())]
        steps[row], steps[target] = steps[target], steps[row]
        self.step_table.setRowCount(0)
        for step in steps:
            r = self.step_table.rowCount()
            self.step_table.insertRow(r)
            self._set_step_row(r, step)
        self.step_table.selectRow(target)

    # ---------------- Packet tab ----------------
    def _build_packet_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        help_text = QtWidgets.QLabel(
            "Packet settings control serial baud, sync bytes, struct format, and command formatting. "
            "Default payload is <IIBBH{N}H where N = P + T + LC."
        )
        help_text.setObjectName("macroHelp")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        form_box = QtWidgets.QFrame()
        form = QtWidgets.QFormLayout(form_box)
        form.setContentsMargins(10, 10, 10, 10)
        form.setSpacing(8)

        self.packet_baud_spin = QtWidgets.QSpinBox()
        self.packet_baud_spin.setRange(1200, 2000000)
        self.packet_baud_spin.setValue(BAUD)

        self.packet_sync_edit = QtWidgets.QLineEdit("AA55")
        self.packet_sync_edit.setPlaceholderText("AA55")

        self.packet_endian_combo = QtWidgets.QComboBox()
        self.packet_endian_combo.addItem("Little endian (<)", "<")
        self.packet_endian_combo.addItem("Big endian (>)", ">")
        self.packet_endian_combo.addItem("Native standard (=)", "=")
        self.packet_endian_combo.addItem("Network (!)", "!")

        self.packet_base_edit = QtWidgets.QLineEdit("IIBBH")
        self.packet_adc_edit = QtWidgets.QLineEdit("H")
        self.packet_command_edit = QtWidgets.QLineEdit("0x{bits:04X},2\\n")
        self.packet_derived_label = QtWidgets.QLabel("Derived: <IIBBH{N}H | payload 20 bytes for 4 sensors")
        self.packet_derived_label.setObjectName("macroLabel")
        self.packet_derived_label.setWordWrap(True)

        form.addRow("Baud", self.packet_baud_spin)
        form.addRow("Sync bytes hex", self.packet_sync_edit)
        form.addRow("Endian", self.packet_endian_combo)
        form.addRow("Base payload fields", self.packet_base_edit)
        form.addRow("ADC field type", self.packet_adc_edit)
        form.addRow("Command template", self.packet_command_edit)
        form.addRow("Preview", self.packet_derived_label)
        layout.addWidget(form_box, stretch=0)

        notes = QtWidgets.QLabel(
            "Base payload fields must unpack to timestamp, sequence, valid mask, status, and solenoid bits. "
            "ADC field type is repeated once per configured sensor. Use H for uint16 ADC values."
        )
        notes.setWordWrap(True)
        notes.setObjectName("macroHelp")
        layout.addWidget(notes)
        layout.addStretch(1)

        for widget in (self.packet_baud_spin, self.packet_sync_edit, self.packet_endian_combo,
                       self.packet_base_edit, self.packet_adc_edit, self.packet_command_edit):
            if isinstance(widget, QtWidgets.QSpinBox):
                widget.valueChanged.connect(self._update_packet_preview)
            elif isinstance(widget, QtWidgets.QComboBox):
                widget.currentIndexChanged.connect(self._update_packet_preview)
            else:
                widget.textChanged.connect(self._update_packet_preview)

        self.tabs.addTab(tab, "Packet Config")

    def _set_packet_config(self, cfg: dict[str, Any]) -> None:
        cfg = normalize_packet_config(cfg)
        self.packet_baud_spin.setValue(int(cfg["baud"]))
        self.packet_sync_edit.setText(str(cfg["sync_hex"]))
        idx = self.packet_endian_combo.findData(cfg["endian"])
        self.packet_endian_combo.setCurrentIndex(max(0, idx))
        self.packet_base_edit.setText(str(cfg["base_format"]))
        self.packet_adc_edit.setText(str(cfg["adc_format"]))
        self.packet_command_edit.setText(str(cfg["command_template"]))
        self._update_packet_preview()

    def _collect_packet_config(self) -> dict[str, Any] | None:
        cfg = normalize_packet_config({
            "baud": self.packet_baud_spin.value(),
            "sync_hex": self.packet_sync_edit.text(),
            "endian": self.packet_endian_combo.currentData(),
            "base_format": self.packet_base_edit.text(),
            "adc_format": self.packet_adc_edit.text(),
            "command_template": self.packet_command_edit.text(),
        })
        try:
            struct.calcsize(f"{cfg['endian']}{cfg['base_format']}{self.config.total_sensors}{cfg['adc_format']}")
        except struct.error as exc:
            QtWidgets.QMessageBox.critical(self, "Invalid Packet Config", f"Struct format is invalid: {exc}")
            return None
        try:
            cfg["command_template"].format(bits=0x8000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Invalid Packet Config", f"Command template is invalid: {exc}")
            return None
        return cfg

    def _update_packet_preview(self) -> None:
        if not hasattr(self, "packet_derived_label"):
            return
        cfg = normalize_packet_config({
            "baud": self.packet_baud_spin.value(),
            "sync_hex": self.packet_sync_edit.text(),
            "endian": self.packet_endian_combo.currentData(),
            "base_format": self.packet_base_edit.text(),
            "adc_format": self.packet_adc_edit.text(),
            "command_template": self.packet_command_edit.text(),
        })
        fmt = f"{cfg['endian']}{cfg['base_format']}{self.config.total_sensors}{cfg['adc_format']}"
        try:
            size = struct.calcsize(fmt)
            sync_len = len(bytes.fromhex(cfg["sync_hex"])) if cfg["sync_hex"] else 0
            self.packet_derived_label.setText(
                f"Derived payload: {fmt} | payload {size} bytes | sync {sync_len} bytes | total {size + sync_len} bytes"
            )
        except Exception as exc:
            self.packet_derived_label.setText(f"Invalid derived packet format: {exc}")

    # ---------------- Load/save ----------------
    def reload_from_file(self) -> None:
        payload = _read_shared_config()
        self._loading = True

        # Sensors
        self.sensor_table.setRowCount(self.pressure_count)
        sensor_configs = normalize_pressure_sensor_configs(payload.get("pressure_sensor_configs"), self.pressure_count)
        for row, cfg in enumerate(sensor_configs):
            self._set_sensor_row(row, cfg)

        # Relays
        relay_map = payload.get("relay_map", default_relay_map())
        if not isinstance(relay_map, dict):
            relay_map = default_relay_map()
        for valve_num in range(1, 10):
            bit = relay_map.get(str(valve_num), default_relay_map()[str(valve_num)])
            self._set_relay_row(valve_num, int(bit))

        # Packet
        self._set_packet_config(payload.get("packet_config", default_packet_config()))

        # Macros
        macros = payload.get("macros", [])
        if not isinstance(macros, list):
            macros = []
        self._macros = [self._normalize_macro(m) for m in macros if isinstance(m, dict)]
        self._loading = False
        self._load_macro_table()

    def _collect_sensors(self) -> list[dict[str, Any]] | None:
        configs = [self._sensor_row_config(row) for row in range(self.pressure_count)]
        for idx, cfg in enumerate(configs):
            if cfg["voltage_max"] == cfg["voltage_min"]:
                QtWidgets.QMessageBox.critical(self, "Invalid Sensor", f"P-{idx + 1} V Min and V Max cannot match.")
                return None
            if cfg["adc_max_count"] <= 0 or cfg["adc_full_scale_voltage"] <= 0:
                QtWidgets.QMessageBox.critical(self, "Invalid Sensor", f"P-{idx + 1} ADC settings must be positive.")
                return None
        return configs

    def _collect_relay_map(self) -> dict[str, int]:
        mapping: dict[str, int] = {}
        used: dict[int, int] = {}
        for valve_num in range(1, 10):
            widget = self.relay_table.cellWidget(valve_num - 1, 1)
            bit = int(widget.value()) if isinstance(widget, QtWidgets.QSpinBox) else int(default_relay_map()[str(valve_num)])
            mapping[str(valve_num)] = bit
            if valve_num <= self.valve_count:
                used.setdefault(bit, valve_num)
        return mapping

    def _collect_macros(self) -> list[dict[str, Any]] | None:
        self._save_steps_for_macro(self._current_macro_row)
        cleaned: list[dict[str, Any]] = []
        seen: set[str] = set()
        for macro in self._macros:
            key = str(macro.get("key", "")).upper().strip()[:1]
            name = str(macro.get("name", "Macro")).strip() or "Macro"
            if len(key) != 1 or not key.isalpha() or key == "A":
                QtWidgets.QMessageBox.critical(self, "Invalid Macro", "Every macro needs one letter key, and A is reserved for E-stop.")
                return None
            if key in seen:
                QtWidgets.QMessageBox.critical(self, "Invalid Macro", f"Duplicate macro key: {key}")
                return None
            seen.add(key)
            steps = [self._normalize_macro_step(s) for s in macro.get("steps", []) if isinstance(s, dict)]
            if not steps:
                QtWidgets.QMessageBox.critical(self, "Invalid Macro", f"Macro {key} needs at least one step.")
                return None
            cleaned.append({"key": key, "name": name, "steps": steps})
        return cleaned

    def save_all(self) -> None:
        sensors = self._collect_sensors()
        if sensors is None:
            return
        macros = self._collect_macros()
        if macros is None:
            return
        packet_config = self._collect_packet_config()
        if packet_config is None:
            return
        payload = _read_shared_config()
        payload["pressure_sensor_configs"] = sensors
        payload["pressure_sensors"] = self.pressure_count
        payload["temperature_sensors"] = int(self.config.num_t)
        payload["load_cells"] = int(self.config.num_lc)
        payload["valves"] = int(self.valve_count)
        payload["relay_map"] = self._collect_relay_map()
        payload["macros"] = macros
        payload["packet_config"] = packet_config
        _write_shared_config(payload)
        self.accept()


class GroundStationWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Liquid Propulsion Ground Station - guiv10")
        self.resize(1280, 820)

        self.config = self.load_shared_app_config()
        self.worker: SerialWorker | None = None
        self.connected = False

        self.history_x: dict[int, list[float]] = {}
        self.history_y: dict[int, list[int]] = {}
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
        self._ensure_history_arrays(reset=True)
        self.rebuild_plot_curves()
        self.reload_macros()
        self.refresh_gui()
        self.save_shared_app_config()

        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self.gui_timer = QtCore.QTimer(self)
        self.gui_timer.timeout.connect(self.refresh_gui)
        self.gui_timer.start(50)

    def load_shared_app_config(self) -> AppConfig:
        payload = _read_shared_config()
        relay_map = payload.get("relay_map", default_relay_map())
        if not isinstance(relay_map, dict):
            relay_map = default_relay_map()
        num_p = max(0, min(10, int(payload.get("pressure_sensors", payload.get("num_p", 4)) or 0)))
        packet_cfg = normalize_packet_config(payload.get("packet_config"))
        return AppConfig(
            port=str(payload.get("port", "COM6")),
            num_p=num_p,
            num_t=max(0, min(10, int(payload.get("temperature_sensors", payload.get("num_t", 0)) or 0))),
            num_lc=max(0, min(4, int(payload.get("load_cells", payload.get("num_lc", 0)) or 0))),
            num_sol=max(1, min(9, int(payload.get("valves", payload.get("num_sol", 5)) or 1))),
            relay_map={str(k): int(v) for k, v in relay_map.items() if str(k).isdigit()},
            pressure_sensor_configs=normalize_pressure_sensor_configs(payload.get("pressure_sensor_configs"), num_p),
            sensor_type=["low"] * max(1, num_p),
            baud=int(packet_cfg["baud"]),
            sync_hex=str(packet_cfg["sync_hex"]),
            packet_endian=str(packet_cfg["endian"]),
            packet_base_format=str(packet_cfg["base_format"]),
            packet_adc_format=str(packet_cfg["adc_format"]),
            command_template=str(packet_cfg["command_template"]),
        )

    def save_shared_app_config(self) -> None:
        payload = _read_shared_config()
        payload.update({
            "port": self.port_combo.currentText() or self.config.port,
            "pressure_sensors": int(self.p_spin.value()),
            "pressure_sensor_configs": normalize_pressure_sensor_configs(self.config.pressure_sensor_configs, int(self.p_spin.value())),
            "temperature_sensors": int(self.t_spin.value()),
            "load_cells": int(self.lc_spin.value()),
            "valves": int(self.sol_spin.value()),
            "relay_map": self.config.relay_map or default_relay_map(),
            "packet_config": {
                "baud": self.config.baud,
                "sync_hex": self.config.sync_hex,
                "endian": self.config.packet_endian,
                "base_format": self.config.packet_base_format,
                "adc_format": self.config.packet_adc_format,
                "command_template": self.config.command_template,
            },
        })
        if "macros" not in payload or not isinstance(payload.get("macros"), list):
            payload["macros"] = []
        _write_shared_config(payload)

    def apply_config_from_controls(self) -> None:
        if self.connected:
            return
        self.config.port = self.port_combo.currentText() or self.config.port
        self.config.num_p = self.p_spin.value()
        self.config.num_t = self.t_spin.value()
        self.config.num_lc = self.lc_spin.value()
        self.config.num_sol = self.sol_spin.value()
        self.config.pressure_sensor_configs = normalize_pressure_sensor_configs(self.config.pressure_sensor_configs, self.config.num_p)
        self.config.sensor_type = ["low"] * max(self.config.num_p, 1)
        self._ensure_history_arrays(reset=True)
        self.rebuild_plot_curves()
        self.refresh_gui()
        self.save_shared_app_config()

    def _ensure_history_arrays(self, reset: bool = False) -> None:
        if reset or not self.history_x or not self.history_y:
            self.history_x = {i: [] for i in range(self.config.total_sensors)}
            self.history_y = {i: [] for i in range(self.config.total_sensors)}
            return
        for i in range(self.config.total_sensors):
            self.history_x.setdefault(i, [])
            self.history_y.setdefault(i, [])
        for key in list(self.history_x.keys()):
            if key >= self.config.total_sensors:
                self.history_x.pop(key, None)
                self.history_y.pop(key, None)

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
        self.p_spin = self._spin(0, 10, self.config.num_p)
        self.t_spin = self._spin(0, 10, self.config.num_t)
        self.lc_spin = self._spin(0, 4, self.config.num_lc)
        self.sol_spin = self._spin(1, 9, self.config.num_sol)
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
        self.estop_indicator = QtWidgets.QLabel("E-STOP")
        self.estop_indicator.setObjectName("estopIndicator")
        self.estop_indicator.setMinimumWidth(92)
        self.estop_indicator.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.estop_indicator.setToolTip("Keyboard only. Hold Shift and press A.")
        valve_row.addWidget(self.estop_indicator)

        macro_row = QtWidgets.QHBoxLayout()
        top_layout.addLayout(macro_row)
        self.macro_label = QtWidgets.QLabel("Macros: none loaded")
        self.macro_label.setObjectName("macroLabel")
        self.config_editor_button = QtWidgets.QPushButton("Edit Configuration")
        self.reload_config_button = QtWidgets.QPushButton("Reload Config")
        macro_row.addWidget(self.macro_label, stretch=1)
        macro_row.addWidget(self.config_editor_button)
        macro_row.addWidget(self.reload_config_button)

        self.pressure_labels: list[QtWidgets.QLabel] = []
        self.temp_labels: list[QtWidgets.QLabel] = []
        self.lc_labels: list[QtWidgets.QLabel] = []

        pressure_row = QtWidgets.QHBoxLayout()
        top_layout.addLayout(pressure_row)
        pressure_title = QtWidgets.QLabel("Pressure")
        pressure_title.setObjectName("sectionTitle")
        pressure_row.addWidget(pressure_title)
        for i in range(10):
            label = QtWidgets.QLabel(f"P-{i + 1}: --")
            label.setProperty("role", "readout")
            label.setProperty("sensor", str(i + 1))
            label.setObjectName(f"pressureReadout{i + 1}")
            label.setMinimumWidth(128)
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.pressure_labels.append(label)
            pressure_row.addWidget(label)
        pressure_row.addStretch(1)

        aux_row = QtWidgets.QHBoxLayout()
        top_layout.addLayout(aux_row)
        self.temp_title = QtWidgets.QLabel("Temp")
        self.temp_title.setObjectName("sectionTitle")
        aux_row.addWidget(self.temp_title)
        for i in range(10):
            label = QtWidgets.QLabel(f"T-{i + 1}: --")
            label.setProperty("role", "readout")
            label.setProperty("kind", "temp")
            label.setMinimumWidth(110)
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.temp_labels.append(label)
            aux_row.addWidget(label)

        self.lc_title = QtWidgets.QLabel("Load Cell")
        self.lc_title.setObjectName("sectionTitle")
        aux_row.addWidget(self.lc_title)
        for i in range(4):
            label = QtWidgets.QLabel(f"LC-{i + 1}: --")
            label.setProperty("role", "readout")
            label.setProperty("kind", "lc")
            label.setMinimumWidth(118)
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.lc_labels.append(label)
            aux_row.addWidget(label)
        aux_row.addStretch(1)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground((18, 22, 28))
        self.plot_widget.setLabel("left", "Pressure", units="PSI")
        self.plot_widget.setLabel("bottom", "Time", units="s")
        self.plot_widget.getAxis("left").setPen(pg.mkPen((220, 226, 235), width=1))
        self.plot_widget.getAxis("bottom").setPen(pg.mkPen((220, 226, 235), width=1))
        self.plot_widget.getAxis("left").setTextPen(pg.mkPen((220, 226, 235)))
        self.plot_widget.getAxis("bottom").setTextPen(pg.mkPen((220, 226, 235)))
        self.plot_widget.addLegend(labelTextColor=(235, 238, 243))
        self.plot_widget.showGrid(x=True, y=True, alpha=0.22)
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
        self.config_editor_button.clicked.connect(self.open_config_editor)
        self.reload_config_button.clicked.connect(self.reload_config_from_file)
        self.port_combo.currentTextChanged.connect(lambda _=None: self.apply_config_from_controls())
        self.p_spin.valueChanged.connect(lambda _=None: self.apply_config_from_controls())
        self.t_spin.valueChanged.connect(lambda _=None: self.apply_config_from_controls())
        self.lc_spin.valueChanged.connect(lambda _=None: self.apply_config_from_controls())
        self.sol_spin.valueChanged.connect(lambda _=None: self.apply_config_from_controls())

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
            relay_map=self.config.relay_map or default_relay_map(),
            pressure_sensor_configs=normalize_pressure_sensor_configs(self.config.pressure_sensor_configs, self.p_spin.value()),
            sensor_type=["low"] * max(self.p_spin.value(), 1),
        )
        self.save_shared_app_config()

        self._ensure_history_arrays(reset=True)
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
        if hasattr(self, "config_editor_button"):
            self.config_editor_button.setEnabled(enabled)
        if hasattr(self, "reload_config_button"):
            self.reload_config_button.setEnabled(enabled)
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

    def _sensor_color(self, index: int) -> tuple[int, int, int]:
        colors = [
            (86, 156, 214),   # blue
            (78, 201, 176),   # teal
            (220, 220, 120),  # soft yellow
            (197, 134, 192),  # purple
            (244, 168, 96),   # orange
            (106, 153, 85),   # green
            (214, 112, 112),  # muted red
            (156, 220, 254),  # light cyan
            (181, 206, 168),  # sage
            (206, 145, 120),  # copper
        ]
        return colors[index % len(colors)]

    def _style_readout(self, label: QtWidgets.QLabel, color: tuple[int, int, int], dark_text: bool = True) -> None:
        text_color = "#101318" if dark_text else "#f4f7fb"
        label.setStyleSheet(
            f"background-color: rgb({color[0]}, {color[1]}, {color[2]}); "
            f"color: {text_color}; border: 2px solid #0b0d10; "
            "border-radius: 6px; padding: 7px 8px; font-weight: 900;"
        )

    def _pressure_sensor_config(self, sensor_index: int) -> dict[str, Any]:
        configs = normalize_pressure_sensor_configs(self.config.pressure_sensor_configs, self.config.num_p)
        if sensor_index < len(configs):
            return configs[sensor_index]
        return default_pressure_sensor_config(sensor_index)

    def _pressure_sensor_name(self, sensor_index: int) -> str:
        return str(self._pressure_sensor_config(sensor_index).get("name", f"P-{sensor_index + 1}"))

    def rebuild_plot_curves(self) -> None:
        self.plot_widget.clear()
        self.plot_widget.addLegend(labelTextColor=(235, 238, 243))
        self.pressure_curves.clear()
        for i in range(self.config.num_p):
            color = self._sensor_color(i)
            curve = self.plot_widget.plot([], [], name=self._pressure_sensor_name(i), pen=pg.mkPen(color, width=3))
            self.pressure_curves.append(curve)

    def refresh_gui(self) -> None:
        for i, btn in enumerate(self.valve_buttons):
            visible = i < self.config.num_sol
            btn.setVisible(visible)
            if not visible:
                continue
            bit_position = self.config.relay_bit_for_valve(i)
            is_on = (self.cmd_solenoid_bits & (1 << bit_position)) != 0
            btn.setText(f"V{i + 1}: {'ON' if is_on else 'OFF'}")
            btn.setProperty("state", "on" if is_on else "off")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        for i, label in enumerate(self.pressure_labels):
            label.setVisible(i < self.config.num_p)
            if i >= self.config.num_p:
                continue
            color = self._sensor_color(i)
            self._style_readout(label, color, dark_text=True)
            y = self.history_y.get(i, [])
            if y:
                psi = self.convert_pressure(y[-1], i)
                name = self._pressure_sensor_name(i)
                label.setText(f"{name}: {psi:.1f} psi")
            else:
                label.setText(f"{self._pressure_sensor_name(i)}: --")

        self.temp_title.setVisible(self.config.num_t > 0)
        self.lc_title.setVisible(self.config.num_lc > 0)

        for i, label in enumerate(self.temp_labels):
            label.setVisible(i < self.config.num_t)
            if i >= self.config.num_t:
                continue
            self._style_readout(label, (150, 164, 180), dark_text=True)
            sensor_idx = self.config.num_p + i
            y = self.history_y.get(sensor_idx, [])
            label.setText(f"T-{i + 1}: {y[-1]}" if y else f"T-{i + 1}: --")

        for i, label in enumerate(self.lc_labels):
            label.setVisible(i < self.config.num_lc)
            if i >= self.config.num_lc:
                continue
            self._style_readout(label, (188, 170, 210), dark_text=True)
            sensor_idx = self.config.num_p + self.config.num_t + i
            y = self.history_y.get(sensor_idx, [])
            label.setText(f"LC-{i + 1}: {y[-1]}" if y else f"LC-{i + 1}: --")

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

    def raw_adc_to_voltage(self, raw_adc: int, sensor_index: int = 0) -> float:
        cfg = self._pressure_sensor_config(sensor_index)
        adc_max_count = max(1.0, float(cfg.get("adc_max_count", 32767)))
        adc_full_scale = max(0.0001, float(cfg.get("adc_full_scale_voltage", 6.144)))
        return float(raw_adc) / adc_max_count * adc_full_scale

    def convert_pressure(self, raw_adc: int, sensor_index: int) -> float:
        # Direct conversion only. Per-sensor config maps ADC counts -> voltage -> psig.
        # Default: ADS1115 gain=2/3, 0.5-4.5 V transducer, 0-5000 psig.
        cfg = self._pressure_sensor_config(sensor_index)
        voltage = self.raw_adc_to_voltage(raw_adc, sensor_index)
        v_min = float(cfg.get("voltage_min", DEFAULT_SENSOR_V_MIN))
        v_max = float(cfg.get("voltage_max", DEFAULT_SENSOR_V_MAX))
        pressure_max = float(cfg.get("pressure_max_psig", DEFAULT_PRESSURE_MAX))
        if v_max == v_min:
            return 0.0
        return (voltage - v_min) / (v_max - v_min) * pressure_max

    def set_valve_state(self, solenoid_idx: int, on: bool) -> None:
        if solenoid_idx < 0 or solenoid_idx >= self.config.num_sol:
            return
        with self.cmd_lock:
            bit_position = self.config.relay_bit_for_valve(solenoid_idx)
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
            bit_position = self.config.relay_bit_for_valve(solenoid_idx)
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
        try:
            parsed = _read_shared_config()
            macros = parsed.get("macros", [])
            if not isinstance(macros, list):
                macros = []
            for macro in macros:
                if not isinstance(macro, dict):
                    continue
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

    def reload_config_from_file(self) -> None:
        if self.connected:
            QtWidgets.QMessageBox.information(self, "Configuration", "Disconnect before reloading configuration.")
            return
        payload = _read_shared_config()
        relay_map = payload.get("relay_map", default_relay_map())
        if not isinstance(relay_map, dict):
            relay_map = default_relay_map()
        self.config.relay_map = {str(k): int(v) for k, v in relay_map.items() if str(k).isdigit()}
        self.config.pressure_sensor_configs = normalize_pressure_sensor_configs(
            payload.get("pressure_sensor_configs"), self.config.num_p
        )
        packet_cfg = normalize_packet_config(payload.get("packet_config"))
        self.config.baud = int(packet_cfg["baud"])
        self.config.sync_hex = str(packet_cfg["sync_hex"])
        self.config.packet_endian = str(packet_cfg["endian"])
        self.config.packet_base_format = str(packet_cfg["base_format"])
        self.config.packet_adc_format = str(packet_cfg["adc_format"])
        self.config.command_template = str(packet_cfg["command_template"])
        self.reload_macros()
        self.rebuild_plot_curves()
        self.refresh_gui()
        self.append_log("[CONFIG] Reloaded configuration file.")

    def open_config_editor(self) -> None:
        if self.connected:
            QtWidgets.QMessageBox.information(self, "Configuration", "Disconnect before changing configuration.")
            return
        self.apply_config_from_controls()
        dialog = ConfigEditorDialog(self, config=self.config)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            payload = _read_shared_config()
            relay_map = payload.get("relay_map", default_relay_map())
            if not isinstance(relay_map, dict):
                relay_map = default_relay_map()
            self.config.relay_map = {str(k): int(v) for k, v in relay_map.items() if str(k).isdigit()}
            self.config.pressure_sensor_configs = normalize_pressure_sensor_configs(
                payload.get("pressure_sensor_configs"), self.config.num_p
            )
            self.reload_macros()
            self.rebuild_plot_curves()
            self.refresh_gui()
            self.append_log("[CONFIG] Configuration reloaded.")

    def open_macro_editor(self) -> None:
        dialog = MacroEditorDialog(self, valve_count=self.config.num_sol)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.reload_macros()

    def open_sensor_editor(self) -> None:
        if self.connected:
            QtWidgets.QMessageBox.information(self, "Sensor Configuration", "Disconnect before changing sensor conversion settings.")
            return
        self.apply_config_from_controls()
        dialog = SensorConfigDialog(self, pressure_count=self.config.num_p)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            payload = _read_shared_config()
            self.config.pressure_sensor_configs = normalize_pressure_sensor_configs(
                payload.get("pressure_sensor_configs"), self.config.num_p
            )
            self.rebuild_plot_curves()
            self.refresh_gui()
            self.append_log("[CONFIG] Sensor conversion settings reloaded.")

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
                        bit_position = self.config.relay_bit_for_valve(solenoid_idx)
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
    color: #e8edf2;
    background: #12161c;
}

QFrame#topPanel, QFrame#bottomPanel {
    background: #1b2129;
    border: 1px solid #303946;
    border-radius: 10px;
}

QLabel {
    color: #e8edf2;
    background: transparent;
    font-weight: 600;
}

QLabel#sectionTitle {
    color: #f6f8fa;
    background: #2a313c;
    border: 1px solid #3c4655;
    border-radius: 6px;
    padding: 6px 10px;
    font-weight: 900;
}

QLabel#macroLabel {
    color: #e8edf2;
    background: #11151b;
    border: 1px solid #3c4655;
    border-radius: 6px;
    padding: 7px 10px;
    font-weight: 750;
}

QLabel#statusLabel {
    color: #f1f5f9;
    background: #353b45;
    border: 1px solid #596270;
    border-radius: 6px;
    padding: 8px 12px;
    min-width: 120px;
    font-weight: 900;
}

QLabel#statusLabel[state="connected"] {
    color: #07110c;
    background: #78d69c;
    border: 1px solid #9be7b8;
}

QComboBox, QSpinBox {
    color: #f5f7fa;
    background: #0f1318;
    border: 1px solid #596270;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: #5d84bf;
    selection-color: #ffffff;
    font-weight: 700;
}

QComboBox QAbstractItemView {
    color: #f5f7fa;
    background: #11151b;
    border: 1px solid #596270;
    selection-background-color: #5d84bf;
    selection-color: #ffffff;
}

QPushButton {
    color: #f4f7fb;
    background: #2c3440;
    border: 1px solid #4c596a;
    border-radius: 7px;
    padding: 7px 12px;
    font-weight: 800;
}

QPushButton:hover {
    background: #3a4554;
    border: 1px solid #708198;
}

QPushButton:pressed {
    background: #202733;
}

QPushButton:disabled {
    color: #8c96a3;
    background: #20262e;
    border: 1px solid #343d49;
}

QPushButton#connectButton {
    color: #07110c;
    background: #78d69c;
    border: 1px solid #9be7b8;
}

QPushButton#disconnectButton {
    color: #18110b;
    background: #e8b86d;
    border: 1px solid #f0c98f;
}

QLabel#estopIndicator {
    color: #ffffff;
    background: #a93232;
    border: 2px solid #d26464;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 12pt;
    font-weight: 950;
}

QLabel[role="valve"] {
    color: #d9e2ec;
    background: #232a34;
    border: 1px solid #475363;
    border-radius: 7px;
    padding: 8px 10px;
    font-weight: 900;
}

QLabel[role="valve"][state="on"] {
    color: #08100d;
    background: #78d69c;
    border: 1px solid #a5edbd;
}

QLabel[role="valve"][state="off"] {
    color: #d9e2ec;
    background: #232a34;
    border: 1px solid #475363;
}

QPlainTextEdit {
    font-family: Consolas, monospace;
    font-size: 9.5pt;
    color: #dfe7ef;
    background: #0b0e12;
    border: 1px solid #303946;
    border-radius: 7px;
    selection-background-color: #5d84bf;
    selection-color: #ffffff;
}

QLabel#macroHelp {
    color: #e8edf2;
    background: #11151b;
    border: 1px solid #3c4655;
    border-radius: 8px;
    padding: 8px 10px;
    font-weight: 800;
}

QTableWidget {
    color: #e8edf2;
    background: #10141a;
    alternate-background-color: #151b23;
    gridline-color: #303946;
    border: 1px solid #303946;
    border-radius: 6px;
    selection-background-color: #5d84bf;
    selection-color: #ffffff;
}

QHeaderView::section {
    color: #f4f7fb;
    background: #2a313c;
    border: 1px solid #3c4655;
    padding: 5px;
    font-weight: 900;
}

QDialog, QDialog QWidget {
    background: #151a21;
    color: #e8edf2;
}

QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QPushButton:focus {
    border: 2px solid #7aa2d6;
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
