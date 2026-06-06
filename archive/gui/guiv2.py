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
    DEFAULT_TEXT = {
        "macros": [
            {
                "key": "B",
                "name": "Example Valve Sequence",
                "steps": [
                    {"action": "set", "valve": 1, "state": "high"},
                    {"action": "wait", "seconds": 0.5},
                    {"action": "set", "valve": 1, "state": "low"},
                ],
            }
        ]
    }

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Macro Editor")
        self.resize(760, 520)

        layout = QtWidgets.QVBoxLayout(self)
        help_text = QtWidgets.QLabel(
            "Macros are triggered with Shift+letter. Shift+A is reserved for E-stop.\n"
            "Supported actions: set/high/low/on/off, wait, estop, send_bits. Valve numbers are 1-based."
        )
        layout.addWidget(help_text)

        self.editor = QtWidgets.QPlainTextEdit()
        self.editor.setTabStopDistance(24)
        layout.addWidget(self.editor, stretch=1)

        buttons = QtWidgets.QHBoxLayout()
        load_example = QtWidgets.QPushButton("Load Example")
        save_btn = QtWidgets.QPushButton("Save")
        cancel_btn = QtWidgets.QPushButton("Cancel")
        buttons.addWidget(load_example)
        buttons.addStretch(1)
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        load_example.clicked.connect(self.load_example)
        save_btn.clicked.connect(self.save_file)
        cancel_btn.clicked.connect(self.reject)

        if MACRO_FILE.exists():
            self.editor.setPlainText(MACRO_FILE.read_text())
        else:
            self.editor.setPlainText(json.dumps(self.DEFAULT_TEXT, indent=2))

    def load_example(self) -> None:
        self.editor.setPlainText(json.dumps(self.DEFAULT_TEXT, indent=2))

    def save_file(self) -> None:
        text = self.editor.toPlainText()
        try:
            parsed = json.loads(text)
            if not isinstance(parsed.get("macros", []), list):
                raise ValueError("Top-level object must contain a 'macros' list")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Invalid JSON", str(exc))
            return
        MACRO_FILE.write_text(json.dumps(parsed, indent=2))
        self.accept()


class GroundStationWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Liquid Propulsion Ground Station - PyQt")
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

    def reload_macros(self) -> None:
        self.macros.clear()
        if not MACRO_FILE.exists():
            self.macro_label.setText("Macros: none loaded")
            return
        try:
            parsed = json.loads(MACRO_FILE.read_text())
            for macro in parsed.get("macros", []):
                key = str(macro.get("key", "")).upper().strip()
                if len(key) != 1 or not key.isalpha() or key == "A":
                    continue
                self.macros[key] = macro
            if self.macros:
                summary = ", ".join(f"Shift+{k}: {v.get('name', 'Macro')}" for k, v in sorted(self.macros.items()))
                self.macro_label.setText(f"Macros: {summary}")
            else:
                self.macro_label.setText("Macros: none loaded")
        except Exception as exc:
            self.macro_label.setText("Macros: failed to load")
            self.append_log(f"[MACRO ERROR] {exc}")

    def open_macro_editor(self) -> None:
        dialog = MacroEditorDialog(self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.reload_macros()

    def _apply_macro_step(self, step: dict[str, Any]) -> float:
        action = str(step.get("action", "")).lower().strip().replace("_", " ").replace("-", " ")

        if action == "wait":
            return max(0.0, float(step.get("seconds", step.get("duration", 0))))

        if action in {"set", "set valve", "valve"}:
            state = str(step.get("state", step.get("value", ""))).lower().strip()
            if state in {"high", "on", "1", "true", "open"}:
                self.set_valve_state(int(step.get("valve", 0)) - 1, True)
                return 0.0
            if state in {"low", "off", "0", "false", "closed", "close"}:
                self.set_valve_state(int(step.get("valve", 0)) - 1, False)
                return 0.0
            raise ValueError(f"Invalid macro set state: {state!r}")

        if action in {"high", "set high", "on", "open"}:
            self.set_valve_state(int(step.get("valve", 0)) - 1, True)
            return 0.0

        if action in {"low", "set low", "off", "close", "closed"}:
            self.set_valve_state(int(step.get("valve", 0)) - 1, False)
            return 0.0

        if action == "toggle":
            raise ValueError("Macros must use explicit high/low or on/off states; toggle is only allowed for Shift+number.")

        if action == "estop":
            self.emergency_stop()
            return 0.0

        if action == "send bits":
            raw_bits = step.get("bits", "0x8000")
            bits = int(str(raw_bits), 0) | 0x8000
            with self.cmd_lock:
                self.cmd_solenoid_bits = bits & 0xFFFF
            self.queue_command(self.cmd_solenoid_bits)
            self.append_log(f"Command bits: {self.cmd_solenoid_bits:016b}")
            return 0.0

        raise ValueError(f"Unknown macro action: {action}")

    def run_macro(self, key: str) -> None:
        macro = self.macros.get(key.upper())
        if not macro:
            return
        if self.macro_running.is_set():
            self.append_log("[MACRO] Another macro is already running.")
            return

        steps = list(macro.get("steps", []))
        name = macro.get("name", f"Shift+{key.upper()}")
        self.macro_running.set()
        self.append_log(f"[MACRO] Running {name}")

        def run_step(index: int = 0) -> None:
            if index >= len(steps):
                self.append_log("[MACRO] Done")
                self.macro_running.clear()
                return
            try:
                delay_seconds = self._apply_macro_step(steps[index])
            except Exception as exc:
                self.append_log(f"[MACRO ERROR] {exc}")
                self.macro_running.clear()
                return
            QtCore.QTimer.singleShot(int(delay_seconds * 1000), lambda: run_step(index + 1))

        run_step(0)

    def _handle_shift_keypress(self, event: QtGui.QKeyEvent) -> bool:
        if event.type() != QtCore.QEvent.Type.KeyPress:
            return False
        if event.isAutoRepeat():
            return True
        if not (event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier):
            return False

        key = event.key()
        if QtCore.Qt.Key.Key_1 <= key <= QtCore.Qt.Key.Key_9:
            self.toggle_solenoid(key - QtCore.Qt.Key.Key_1)
            return True

        if key == QtCore.Qt.Key.Key_A:
            self.emergency_stop()
            return True

        if key == QtCore.Qt.Key.Key_Z:
            self.zero_pressures()
            return True

        text = event.text().upper()
        if len(text) == 1 and text.isalpha() and text in self.macros:
            self.run_macro(text)
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
