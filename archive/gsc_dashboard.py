"""
GSC Dashboard: live pressure, thrust, and temperature graphs plus 6 solenoid valve switches.
Uses tkinter + matplotlib. For static or animated-only demos see plottingShit.py and gscPlots.py.

Hooking real data:
  - Sensor data: set app.data_source in main() to a callable(t) -> (pressure_Pa, thrust_N, temp_C).
  - Relay commands: set app.on_relay_change to a callable(hex_str) that sends the 4-char hex to hardware.
"""
import tkinter as tk
from tkinter import ttk
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import numpy as np

# Relay bit layout (matches LoRa_Library/lora_config.h)
RELAY_MSB_BIT = 0x8000   # Bit 15 = valid command
RELAY_BIT_START = 9      # Relays 1-6 = bits 9-14

# Rolling window: show last N seconds; once time > this, left edge moves right and 0 scrolls off the x-axis.
ROLLING_WINDOW_SEC = 30
# Sim seconds per 100 ms frame (0.1 = realtime; 0.5 = 5 sim sec per real sec, so 0 disappears after ~6 s)
SIM_SEC_PER_FRAME = 0.5
MAX_POINTS = max(10, int(ROLLING_WINDOW_SEC / SIM_SEC_PER_FRAME) + 20)

# Test data generator (simulates a short run: pressure drop, thrust spike, temp rise)
def make_test_data(t: float) -> tuple[float, float, float]:
    """Return (pressure_Pa, thrust_N, temp_C) for time t (seconds)."""
    # Pressure: high then decay (e.g. tank emptying), floor at ~50 Pa
    pressure = max(50, 900 * np.exp(-t / 15) + 30 * np.sin(t * 0.5))
    # Thrust: initial spike then oscillating decay
    thrust = 80 + 120 * np.exp(-t / 8) * abs(np.sin(t * 0.8)) + 20 * np.sin(t * 0.3)
    thrust = max(0, min(300, thrust))
    # Temp: ramp up then level off with small variation
    temp = 28 + min(60, t * 1.2) + 3 * np.sin(t * 0.4)
    temp = max(20, min(95, temp))
    return (float(pressure), float(thrust), float(temp))


def build_relay_state(switches: list[bool]) -> int:
    """Build 16-bit relay state (MSB set, bits 9-14 = relays 1-6)."""
    state = RELAY_MSB_BIT
    for i, on in enumerate(switches):
        if on:
            state |= 1 << (RELAY_BIT_START + i)
    return state


class GSCDashboard:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("GSC Dashboard")
        self.root.geometry("800x800")  # Square so 2x2 grid cells are equal

        # Data buffers (replace with CSV/serial later)
        self.t_data = np.array([], dtype=float)
        self.pressure_data = np.array([], dtype=float)
        self.thrust_data = np.array([], dtype=float)
        self.temp_data = np.array([], dtype=float)

        # Solenoid states (relays 1-6)
        self.solenoid_vars: list[tk.BooleanVar] = [
            tk.BooleanVar(value=False) for _ in range(6)
        ]

        # Data source: callable that returns (pressure_Pa, thrust_N, temp_C) for current time/sample.
        # Default = test data. For real data, set e.g. app.data_source = your_read_function
        self.data_source = make_test_data
        self._tick = 0.0  # used by test data; replace with your time/sample index if needed

        self._build_layout()
        self._start_update_loop()

    def _build_layout(self):
        # 2x2 equal grid: pressure top-left, thrust top-right, temp bottom-left, buttons bottom-right
        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)
        pad = 4

        # Top-left: Pressure
        fig_p = Figure(figsize=(4, 3), dpi=100)
        self.ax_p = fig_p.add_subplot(111)
        self.ax_p.set_title("Pressure (Pa)")
        self.ax_p.set_ylim(0, 1000)
        self.ax_p.set_xlim(0, ROLLING_WINDOW_SEC)
        self.ax_p.set_autoscalex_on(False)
        self.ax_p.grid(True, alpha=0.3)
        self.line_p, = self.ax_p.plot([], [], "b-", lw=1.5)
        self.canvas_p = FigureCanvasTkAgg(fig_p, master=self.root)
        self.canvas_p.get_tk_widget().grid(row=0, column=0, sticky="nsew", padx=pad, pady=pad)

        # Top-right: Thrust
        fig_th = Figure(figsize=(4, 3), dpi=100)
        self.ax_th = fig_th.add_subplot(111)
        self.ax_th.set_title("Thrust (N)")
        self.ax_th.set_ylim(0, 300)
        self.ax_th.set_xlim(0, ROLLING_WINDOW_SEC)
        self.ax_th.set_autoscalex_on(False)
        self.ax_th.grid(True, alpha=0.3)
        self.line_thrust, = self.ax_th.plot([], [], color=(1, 0.65, 0), lw=1.5)
        self.canvas_th = FigureCanvasTkAgg(fig_th, master=self.root)
        self.canvas_th.get_tk_widget().grid(row=0, column=1, sticky="nsew", padx=pad, pady=pad)

        # Bottom-left: Temperature
        fig_t = Figure(figsize=(4, 3), dpi=100)
        self.ax_t = fig_t.add_subplot(111)
        self.ax_t.set_title("Temperature (°C)")
        self.ax_t.set_ylim(0, 100)
        self.ax_t.set_xlim(0, ROLLING_WINDOW_SEC)
        self.ax_t.set_autoscalex_on(False)
        self.ax_t.grid(True, alpha=0.3)
        self.line_temp, = self.ax_t.plot([], [], "r-", lw=1.5)
        self.canvas_t = FigureCanvasTkAgg(fig_t, master=self.root)
        self.canvas_t.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=pad, pady=pad)

        # Bottom-right: Solenoid valves (bigger buttons)
        btn_frame = ttk.LabelFrame(self.root, text="Solenoid valves", padding=16)
        btn_frame.grid(row=1, column=1, sticky="nsew", padx=pad, pady=pad)
        btn_frame.grid_rowconfigure(0, weight=1)
        btn_frame.grid_rowconfigure(1, weight=1)
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)
        btn_frame.grid_columnconfigure(2, weight=1)
        big_font = ("", 14, "bold")
        for i in range(6):
            var = self.solenoid_vars[i]
            cb = tk.Checkbutton(
                btn_frame,
                text=f"Valve {i + 1}",
                variable=var,
                command=lambda idx=i: self._on_solenoid_toggle(idx),
                font=big_font,
                indicatoron=True,
                width=10,
                height=2,
                anchor="center",
            )
            cb.grid(row=i // 3, column=i % 3, padx=8, pady=8, sticky="nsew")
        self.on_relay_change = None

    def _on_solenoid_toggle(self, _index: int):
        states = [v.get() for v in self.solenoid_vars]
        state_hex = f"{build_relay_state(states):04X}"
        if self.on_relay_change:
            self.on_relay_change(state_hex)

    def _start_update_loop(self):
        self._update_plots()

    def _update_plots(self):
        # --- REAL DATA: replace self.data_source with a function that returns (pressure_Pa, thrust_N, temp_C) ---
        # Examples:
        #   CSV:  app.data_source = lambda: read_latest_row_from_csv("path/to/data.csv")
        #   Serial: app.data_source = lambda: parse_serial_line(ser.readline())
        # Your function can set self._tick to a timestamp if you have one (else elapsed time is used).
        self._tick += SIM_SEC_PER_FRAME
        t = self._tick
        pressure, thrust, temp = self.data_source(t)

        # Append one sample; cap at MAX_POINTS so we always show a rolling window
        def append(buf: np.ndarray, val: float) -> np.ndarray:
            new = np.append(buf, val)
            if len(new) > MAX_POINTS:
                return new[-MAX_POINTS:]
            return new

        self.t_data = append(self.t_data, t)
        self.pressure_data = append(self.pressure_data, pressure)
        self.thrust_data = append(self.thrust_data, thrust)
        self.temp_data = append(self.temp_data, temp)

        # Rolling window: fixed width, right edge = now
        t_max = float(self.t_data[-1])
        x_min = max(0.0, t_max - ROLLING_WINDOW_SEC)
        x_max = t_max + 0.5

        # Plot only data inside the visible window so the trace clearly rolls
        mask = (self.t_data >= x_min) & (self.t_data <= x_max)
        t_vis = self.t_data[mask]
        p_vis = self.pressure_data[mask]
        th_vis = self.thrust_data[mask]
        temp_vis = self.temp_data[mask]

        self.line_p.set_data(t_vis, p_vis)
        self.line_temp.set_data(t_vis, temp_vis)
        self.line_thrust.set_data(t_vis, th_vis)

        self.ax_p.set_xlim(x_min, x_max)
        self.ax_t.set_xlim(x_min, x_max)
        self.ax_th.set_xlim(x_min, x_max)

        for canvas in (self.canvas_p, self.canvas_th, self.canvas_t):
            canvas.draw()
        self.root.after(100, self._update_plots)


def main():
    root = tk.Tk()
    app = GSCDashboard(root)

    # Optional: override data source (default is make_test_data)
    # app.data_source = lambda t: read_from_csv("path/to/data.csv", t)
    # app.data_source = lambda t: read_from_serial(ser)

    # Optional: send relay state when user toggles valves (4-char hex, e.g. "E200")
    # app.on_relay_change = lambda h: ser.write(f"h{h}\n".encode())
    # app.on_relay_change = lambda h: print("Relay:", h)

    root.mainloop()


if __name__ == "__main__":
    main()
