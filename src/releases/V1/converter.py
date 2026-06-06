#!/usr/bin/env python3
"""
Unified Teensy / Ground Station pressure converter.

Modes:

1) Teensy SD binary log:
   python teensy_pressure_converter.py --mode bin --input data.bin

2) Ground-station raw CSV folder:
   python teensy_pressure_converter.py --mode csv --input .

3) Auto:
   python teensy_pressure_converter.py --mode auto --input .
   - Uses data.bin if found.
   - Otherwise converts all CSVs in the folder.

Outputs:
  converted/
    session_000_converted.csv
    session_001_converted.csv
    ...
or:
    original_name_converted.csv

Teensy binary format:
  Marker: 0xA5A5A5A5 little-endian
  Session prefix: 2 bytes after marker
  Frame format: <I I B B H 4I 4H

Frame fields:
  timestamp_us      uint32
  seq               uint32
  valid_mask        uint8
  status_bits       uint8
  solenoid_state    uint16
  payload[4]        uint32
  raw_adc[4]        uint16
"""

from __future__ import annotations

import argparse
import csv
import struct
from pathlib import Path
from typing import Iterable


# ============================================================
# USER CONFIG
# ============================================================

# One entry per pressure sensor.
# "high" = 0-5000 psig
# "low"  = 0-2000 psig
PRESSURE_SENSORS = [
    "high",
    "high",
    "high",
    "high",
]

# Ground-station CSV mode config.
# 1-based columns to skip from the input CSV.
# For GSC-style CSV:
#   column 1 = timestamp
#   column 2 = seq
#   column 3 = solenoids
#   column 4+ = ADC values
SKIP_COLUMNS = [2, 3]
TIMESTAMP_COLUMN = 1  # 1-based

# CSV mode: if the CSV has named raw ADC columns, these are preferred.
# If missing, script falls back to the SKIP_COLUMNS method.
CSV_RAW_ADC_COLUMNS = ["raw_adc0", "raw_adc1", "raw_adc2", "raw_adc3"]

# Teensy SD binary mode config.
MARKER = 0xA5A5A5A5
SESSION_PREFIX_BYTES = 2
FRAME_FMT = "<I I B B H 4I 4H"
SENSOR_COUNT = 4

# ADS / sensor conversion.
# ADS1115 with GAIN_TWOTHIRDS:
ADC_MAX_COUNT = 32767
ADC_FULL_SCALE_VOLTAGE = 6.144

# Pressure transducer voltage range.
SENSOR_VOLTAGE_MIN = 0.5
SENSOR_VOLTAGE_MAX = 4.5

HIGH_PRESSURE_MAX_PSIG = 5000.0
LOW_PRESSURE_MAX_PSIG = 2000.0

# Timestamp sanity for CSV mode.
# Prevents fake 2^32 us jumps from corrupt timestamps.
MAX_REASONABLE_DT_US = 5_000_000

DEFAULT_INPUT = Path(".")
DEFAULT_OUTPUT_FOLDER = Path("converted")


# ============================================================
# PRESSURE CONVERSION
# ============================================================

def raw_adc_to_psi(raw_adc: float, sensor_kind: str) -> float:
    voltage = raw_adc / ADC_MAX_COUNT * ADC_FULL_SCALE_VOLTAGE

    kind = sensor_kind.lower().strip()
    if kind == "high":
        pressure_max = HIGH_PRESSURE_MAX_PSIG
    elif kind == "low":
        pressure_max = LOW_PRESSURE_MAX_PSIG
    else:
        raise ValueError(f"Bad sensor type: {sensor_kind!r}. Use 'high' or 'low'.")

    return (
        (voltage - SENSOR_VOLTAGE_MIN)
        / (SENSOR_VOLTAGE_MAX - SENSOR_VOLTAGE_MIN)
        * pressure_max
    )


def format_psi_header(num_sensors: int) -> list[str]:
    header = ["time_s"]
    for i, sensor_kind in enumerate(PRESSURE_SENSORS[:num_sensors], start=1):
        header.append(f"P{i}_{sensor_kind}_psi")
    return header


def write_pressure_rows(rows: Iterable[list[str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="") as outfile:
        writer = csv.writer(outfile)
        for row in rows:
            writer.writerow(row)


# ============================================================
# TIMESTAMP HELPERS
# ============================================================

def unwrap_uint32_timestamp_safe(
    current: int,
    previous_raw: int | None,
    previous_unwrapped: int | None,
) -> int | None:
    """
    Returns unwrapped timestamp_us if sane, otherwise None.

    This avoids fake 2^32-us jumps when a timestamp is corrupt.
    """
    current = int(current) & 0xFFFFFFFF

    if previous_raw is None or previous_unwrapped is None:
        return current

    previous_raw = int(previous_raw) & 0xFFFFFFFF

    if current >= previous_raw:
        dt = current - previous_raw
        if 0 <= dt <= MAX_REASONABLE_DT_US:
            return previous_unwrapped + dt
        return None

    rollover_dt = current + (2**32) - previous_raw
    if 0 <= rollover_dt <= MAX_REASONABLE_DT_US:
        return previous_unwrapped + rollover_dt

    return None


# ============================================================
# CSV MODE
# ============================================================

def filter_row(row: list[str], skip_columns_1_based: list[int]) -> list[str]:
    skip_indices = {col - 1 for col in skip_columns_1_based}
    return [value for idx, value in enumerate(row) if idx not in skip_indices]


def get_adc_values_from_csv_row(
    row: list[str],
    header: list[str] | None,
    num_sensors: int,
) -> list[str]:
    """
    Prefer named raw_adc columns if present.
    Otherwise, use the original skip-column method:
      filtered row = timestamp, ADC1, ADC2, ...
    """
    if header:
        header_map = {name.strip(): idx for idx, name in enumerate(header)}
        if all(col in header_map for col in CSV_RAW_ADC_COLUMNS[:num_sensors]):
            return [row[header_map[col]] for col in CSV_RAW_ADC_COLUMNS[:num_sensors]]

    filtered_row = filter_row(row, SKIP_COLUMNS)
    return filtered_row[1:1 + num_sensors]


def convert_one_csv(input_path: Path, output_path: Path) -> None:
    timestamp_idx = TIMESTAMP_COLUMN - 1
    num_sensors = len(PRESSURE_SENSORS)

    first_timestamp_unwrapped: int | None = None
    previous_timestamp_raw: int | None = None
    previous_timestamp_unwrapped: int | None = None

    output_rows: list[list[str]] = [format_psi_header(num_sensors)]

    with input_path.open("r", newline="") as infile:
        reader = csv.reader(infile)
        header = next(reader, None)

        first_data_row = None
        if header:
            try:
                int(float(header[timestamp_idx]))
                first_data_row = header
                header = None
            except Exception:
                pass

        row_iter = []
        if first_data_row is not None:
            row_iter.append(first_data_row)
        row_iter.extend(reader)

        row_num = 1 if first_data_row is not None else 2

        for row in row_iter:
            if not row:
                row_num += 1
                continue

            if len(row) <= timestamp_idx:
                raise ValueError(
                    f"{input_path.name}, row {row_num}: missing timestamp column {TIMESTAMP_COLUMN}."
                )

            timestamp_raw = int(float(row[timestamp_idx]))

            timestamp_unwrapped = unwrap_uint32_timestamp_safe(
                timestamp_raw,
                previous_timestamp_raw,
                previous_timestamp_unwrapped,
            )

            if timestamp_unwrapped is None:
                if previous_timestamp_unwrapped is None:
                    timestamp_unwrapped = 0
                else:
                    timestamp_unwrapped = previous_timestamp_unwrapped

            previous_timestamp_raw = timestamp_raw
            previous_timestamp_unwrapped = timestamp_unwrapped

            if first_timestamp_unwrapped is None:
                first_timestamp_unwrapped = timestamp_unwrapped

            time_s = (timestamp_unwrapped - first_timestamp_unwrapped) / 1_000_000.0

            adc_values = get_adc_values_from_csv_row(row, header, num_sensors)

            if len(adc_values) < num_sensors:
                raise ValueError(
                    f"{input_path.name}, row {row_num}: has {len(adc_values)} ADC values, "
                    f"but expected {num_sensors}."
                )

            psi_values = []
            for raw_adc, sensor_kind in zip(adc_values, PRESSURE_SENSORS):
                psi = raw_adc_to_psi(float(raw_adc), sensor_kind)
                psi_values.append(f"{psi:.3f}")

            output_rows.append([f"{time_s:.6f}"] + psi_values)
            row_num += 1

    write_pressure_rows(output_rows, output_path)


def convert_csv_folder(input_folder: Path, output_folder: Path) -> None:
    output_folder.mkdir(exist_ok=True)

    csv_files = [
        path for path in input_folder.glob("*.csv")
        if path.parent.resolve() != output_folder.resolve()
        and not path.name.endswith("_converted.csv")
    ]

    if not csv_files:
        print("No CSV files found.")
        return

    for input_path in sorted(csv_files):
        output_path = output_folder / f"{input_path.stem}_converted.csv"

        try:
            convert_one_csv(input_path, output_path)
            print(f"Converted CSV: {input_path.name} -> {output_path}")
        except Exception as exc:
            print(f"FAILED CSV: {input_path.name}: {exc}")


# ============================================================
# BINARY SD MODE
# ============================================================

def find_all_markers(data: bytes) -> list[int]:
    marker_bytes = struct.pack("<I", MARKER)
    offsets = []
    start = 0

    while True:
        idx = data.find(marker_bytes, start)
        if idx == -1:
            break

        offsets.append(idx)
        start = idx + 4

    return offsets


def decode_binary_frames(region: bytes) -> list[dict[str, int]]:
    frame_size = struct.calcsize(FRAME_FMT)
    usable_len = len(region) - (len(region) % frame_size)

    frames = []

    for off in range(0, usable_len, frame_size):
        chunk = region[off:off + frame_size]

        try:
            vals = struct.unpack(FRAME_FMT, chunk)
        except struct.error:
            break

        ts_us, seq, valid_mask, status_bits, sol_state, *rest = vals
        payload = rest[:SENSOR_COUNT]
        raw_adc = rest[SENSOR_COUNT:SENSOR_COUNT + SENSOR_COUNT]

        frames.append({
            "timestamp_us": int(ts_us),
            "seq": int(seq),
            "valid_mask": int(valid_mask),
            "status_bits": int(status_bits),
            "solenoid_state": int(sol_state),
            "payload0_centi_psi": int(payload[0]),
            "payload1_centi_psi": int(payload[1]),
            "payload2_centi_psi": int(payload[2]),
            "payload3_centi_psi": int(payload[3]),
            "raw_adc0": int(raw_adc[0]),
            "raw_adc1": int(raw_adc[1]),
            "raw_adc2": int(raw_adc[2]),
            "raw_adc3": int(raw_adc[3]),
        })

    return frames


def estimate_hz_from_frames(frames: list[dict[str, int]]) -> tuple[float | None, float | None]:
    """
    Returns:
      overall_hz, steady_hz

    steady_hz ignores startup burst by using frames with seq >= 150 when available.
    """
    if len(frames) < 2:
        return None, None

    def hz_for(sub: list[dict[str, int]]) -> float | None:
        if len(sub) < 2:
            return None

        dt_us = int(sub[-1]["timestamp_us"]) - int(sub[0]["timestamp_us"])
        dseq = int(sub[-1]["seq"]) - int(sub[0]["seq"])

        if dt_us <= 0 or dseq <= 0:
            return None

        return dseq / (dt_us / 1_000_000.0)

    overall = hz_for(frames)

    steady_subset = [f for f in frames if int(f["seq"]) >= 150]
    steady = hz_for(steady_subset) if len(steady_subset) >= 2 else overall

    return overall, steady


def binary_frames_to_pressure_rows(frames: list[dict[str, int]]) -> list[list[str]]:
    num_sensors = min(len(PRESSURE_SENSORS), SENSOR_COUNT)

    output_rows = [format_psi_header(num_sensors)]

    if not frames:
        return output_rows

    first_ts = int(frames[0]["timestamp_us"])

    for frame in frames:
        time_s = (int(frame["timestamp_us"]) - first_ts) / 1_000_000.0

        psi_values = []
        for i, sensor_kind in enumerate(PRESSURE_SENSORS[:num_sensors]):
            raw_adc = float(frame[f"raw_adc{i}"])
            psi = raw_adc_to_psi(raw_adc, sensor_kind)
            psi_values.append(f"{psi:.3f}")

        output_rows.append([f"{time_s:.6f}"] + psi_values)

    return output_rows


def write_decoded_raw_csv(frames: list[dict[str, int]], output_path: Path) -> None:
    if not frames:
        return

    output_path.parent.mkdir(exist_ok=True)
    headers = list(frames[0].keys())

    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(frames)


def convert_binary_file(
    input_file: Path,
    output_folder: Path,
    write_decoded_raw: bool = False,
) -> None:
    if not input_file.exists():
        print(f"ERROR: binary input not found: {input_file}")
        return

    output_folder.mkdir(exist_ok=True)

    data = input_file.read_bytes()
    markers = find_all_markers(data)

    if not markers:
        print(f"ERROR: no 0x{MARKER:08X} session marker found in {input_file}")
        return

    print(f"Found {len(markers)} session marker(s) in {input_file.name}.")

    frame_size = struct.calcsize(FRAME_FMT)
    session_index = 0

    for marker_i, marker_pos in enumerate(markers):
        start = marker_pos + 4 + SESSION_PREFIX_BYTES
        end = markers[marker_i + 1] if marker_i + 1 < len(markers) else len(data)

        region = data[start:end]

        if len(region) < frame_size:
            print(f"Skipping tiny/empty region after marker {marker_i}.")
            continue

        frames = decode_binary_frames(region)

        if not frames:
            print(f"Skipping region {marker_i}; no frames decoded.")
            continue

        output_path = output_folder / f"session_{session_index:03d}_converted.csv"
        rows = binary_frames_to_pressure_rows(frames)
        write_pressure_rows(rows, output_path)

        overall_hz, steady_hz = estimate_hz_from_frames(frames)

        hz_text = []
        if overall_hz is not None:
            hz_text.append(f"overall {overall_hz:.3f} Hz")
        if steady_hz is not None:
            hz_text.append(f"steady {steady_hz:.3f} Hz")

        hz_summary = " | ".join(hz_text) if hz_text else "Hz unknown"

        print(
            f"Decoded BIN session {session_index:03d}: "
            f"{len(frames)} frames -> {output_path} | {hz_summary}"
        )

        if write_decoded_raw:
            raw_path = output_folder / f"session_{session_index:03d}_decoded_raw.csv"
            write_decoded_raw_csv(frames, raw_path)

        session_index += 1


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert Teensy SD binary logs or ground-station CSV logs into pressure-vs-time CSVs."
    )

    parser.add_argument(
        "--mode",
        choices=["auto", "bin", "csv"],
        default="auto",
        help="Conversion mode. Default: auto.",
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=(
            "Input file/folder. "
            "For bin mode, pass data.bin or a folder containing data.bin. "
            "For csv mode, pass a folder."
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FOLDER,
        help="Output folder. Default: converted.",
    )

    parser.add_argument(
        "--write-decoded-raw",
        action="store_true",
        help="In bin mode, also write fully decoded raw frame CSVs.",
    )

    return parser.parse_args()


def resolve_binary_input(path: Path) -> Path:
    if path.is_file():
        return path
    return path / "data.bin"


def main() -> None:
    args = parse_args()

    input_path = args.input
    output_folder = args.output

    if args.mode == "csv":
        if not input_path.is_dir():
            raise SystemExit("CSV mode requires --input to be a folder.")
        convert_csv_folder(input_path, output_folder)
        return

    if args.mode == "bin":
        binary_file = resolve_binary_input(input_path)
        convert_binary_file(
            binary_file,
            output_folder,
            write_decoded_raw=args.write_decoded_raw,
        )
        return

    # auto mode
    binary_file = resolve_binary_input(input_path)

    if binary_file.exists():
        convert_binary_file(
            binary_file,
            output_folder,
            write_decoded_raw=args.write_decoded_raw,
        )
    elif input_path.is_dir():
        convert_csv_folder(input_path, output_folder)
    else:
        raise SystemExit(f"Auto mode found neither data.bin nor CSV folder at {input_path}")


if __name__ == "__main__":
    main()