#!/usr/bin/env python3
"""
Batch Pressure Plotter for Ground Station telemetry CSV files.

This intentionally copies the pressure conversion behavior from main_windows_v4.py:

    voltage = raw_adc * ADS_UNIT_VOLTAGE

    if sensor_type == "low":
        psi = (voltage - V_MIN) / V_DIFF * LOW_PRESSURE_MAX
    elif sensor_type == "high":
        psi = (voltage - V_MIN) / V_DIFF * HIGH_PRESSURE_MAX

    zero = pressure_zero_offsets.get(sensor_index, 0.0)
    return psi - zero

For offline CSV plotting, GUI zeroing is emulated by ZERO_MODE:
    "none"       -> exactly like GUI before pressing Zero Pressures
    "first"      -> like pressing Zero Pressures using the first sample
    "mean_start" -> like pressing Zero Pressures using the mean of first N samples

Expected CSV format:
    timestamp,seq,solenoids,P_0,P_1,P_2,P_3,...

Usage:
    python batch_plot_pressure_gui_exact.py

Optional:
    python batch_plot_pressure_gui_exact.py --folder .
    python batch_plot_pressure_gui_exact.py --folder logs --out plots
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


# ============================================================
# USER CONFIG
# ============================================================

# Folder scanned when --folder is not provided.
INPUT_FOLDER = "."

# Folder where PNGs are saved when --out is not provided.
OUTPUT_FOLDER = "pressure_plots"

# CSV filename pattern.
CSV_PATTERN = "*.csv"

# Configure pressure sensors here.
#
# This mirrors config["sensor_type"] in main_windows_v4.py.
# Each entry corresponds to CSV pressure columns P_0, P_1, P_2, ...
#
# type:
#   "high" -> uses HIGH_PRESSURE_MAX = 5000
#   "low"  -> uses LOW_PRESSURE_MAX = 2000
#
PRESSURE_SENSORS = [
    {"column": "P_0", "type": "low", "label": "P_0"},
    {"column": "P_1", "type": "low",  "label": "P_1"},
    {"column": "P_2", "type": "low",  "label": "P_2"},
    {"column": "P_3", "type": "low",  "label": "P_3"},
]

# GUI-zero emulation:
#   "none"       = exactly GUI math before pressing Zero Pressures
#   "first"      = subtract pressure at first sample
#   "mean_start" = subtract average pressure of first ZERO_START_SAMPLES samples
ZERO_MODE = "none"

ZERO_START_SAMPLES = 25

# If True, missing configured P_* columns raise an error.
# If False, missing columns are skipped.
REQUIRE_ALL_CONFIGURED_SENSORS = True

# Plot settings.
FIGSIZE = (11, 6)
DPI = 200
# ============================================================


# ============================================================
# CONSTANTS COPIED FROM main_windows_v4.py
# ============================================================

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
# ============================================================


def convert_pressure_reference(raw_adc, psi_max):
    V_MIN = 0.5
    V_MAX = 4.5

    voltage = raw_adc * 0.1875 / 1000.0
    psi = (voltage - V_MIN) * (psi_max / (V_MAX - V_MIN))
    return psi


def validate_sensors(df: pd.DataFrame) -> list[dict]:
    sensors = []

    for i, sensor in enumerate(PRESSURE_SENSORS):
        col = sensor.get("column", f"P_{i}")
        typ = sensor.get("type", "").strip().lower()
        label = sensor.get("label", col)

        if typ not in {"high", "low"}:
            raise ValueError(f"{col}: invalid sensor type {typ!r}. Use 'high' or 'low'.")

        if col not in df.columns:
            if REQUIRE_ALL_CONFIGURED_SENSORS:
                raise ValueError(f"Configured sensor column {col!r} not found in CSV.")
            continue

        sensors.append({"index": i, "column": col, "type": typ, "label": label})

    if not sensors:
        raise ValueError("No configured pressure sensors found in CSV.")

    return sensors


def compute_zero_offset_psi(df: pd.DataFrame, sensor_index: int, column: str) -> float:
    """
    Emulates data_store.pressure_zero_offsets from the GUI.

    In the GUI:
        zero_pressures() stores the currently displayed PSI value.
        convert_pressure() then returns psi - zero.

    Offline:
        ZERO_MODE decides what sample(s) represent the "current displayed PSI".
    """
    if ZERO_MODE == "none":
        return 0.0

    if ZERO_MODE == "first":
        return float(convert_pressure_reference(float(df[column].iloc[0]), sensor_index, 0.0))

    if ZERO_MODE == "mean_start":
        n = min(ZERO_START_SAMPLES, len(df))
        raw_mean = float(df[column].iloc[:n].mean())
        return float(convert_pressure_reference(raw_mean, sensor_index, 0.0))

    raise ValueError(f"Invalid ZERO_MODE {ZERO_MODE!r}. Use 'none', 'first', or 'mean_start'.")


def plot_one_csv(csv_path: Path, out_dir: Path) -> Path:
    df = pd.read_csv(csv_path)

    if "timestamp" not in df.columns:
        raise ValueError(f"{csv_path.name}: missing required 'timestamp' column")

    sensors = validate_sensors(df)

    # GUI logs timestamp from Teensy in microseconds.
    time_s = (df["timestamp"].astype(float) - float(df["timestamp"].iloc[0])) / 1_000_000.0

    plt.figure(figsize=FIGSIZE)

    for sensor in sensors:
        idx = sensor["index"]
        col = sensor["column"]

        zero_offset = compute_zero_offset_psi(df, idx, col)
        pressure = convert_pressure_reference(df[col].astype(float), LOW_PRESSURE_MAX if sensor["type"] == "low" else HIGH_PRESSURE_MAX) - zero_offset

        print(
            f"{csv_path.name} | {col} | type={sensor['type']} | "
            f"zero_offset={zero_offset:.3f} PSI"
        )

        plt.plot(time_s, pressure, label=f"{sensor['label']} ({sensor['type']})")

    plt.xlabel("Time (s)")
    plt.ylabel("Pressure (PSI)")
    plt.title(csv_path.stem)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{csv_path.stem}_pressure.png"
    plt.savefig(out_path, dpi=DPI)
    plt.close()

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch plot pressure PNGs from telemetry CSV files.")
    parser.add_argument("--folder", default=INPUT_FOLDER, help="Folder containing telemetry CSV files")
    parser.add_argument("--out", default=OUTPUT_FOLDER, help="Folder to save PNG plots")
    args = parser.parse_args()

    input_dir = Path(args.folder)
    out_dir = Path(args.out)

    if not input_dir.exists():
        raise FileNotFoundError(f"Input folder not found: {input_dir}")

    csv_files = sorted(input_dir.glob(CSV_PATTERN))

    if not csv_files:
        raise FileNotFoundError(f"No CSV files matching {CSV_PATTERN!r} found in {input_dir}")

    print(f"Found {len(csv_files)} CSV file(s) in {input_dir}")
    print(f"Saving PNGs to {out_dir}")
    print(f"ZERO_MODE = {ZERO_MODE!r}")

    for csv_path in csv_files:
        try:
            out_path = plot_one_csv(csv_path, out_dir)
            print(f"Saved: {out_path}")
        except Exception as e:
            print(f"SKIPPED {csv_path.name}: {e}")


if __name__ == "__main__":
    main()
