import os
from os import path
import pandas as pd
import json

# Constants from GUI

V_MAX = 4.5
V_MIN = 0.5
V_DIFF = V_MAX - V_MIN

HIGH_PRESSURE_MAX = 5000
LOW_PRESSURE_MAX = 2000

ORIGINAL_ADS_VOLTAGE_RANGE = 4.096
GAIN = 2/3
ADS_VOLTAGE_RANGE = ORIGINAL_ADS_VOLTAGE_RANGE / GAIN

ADS_COUNT_RANGE = 2**15 - 1
ADS_UNIT_VOLTAGE = ADS_VOLTAGE_RANGE / ADS_COUNT_RANGE

sensor_type = ["high", "low", "low", "low"]

# Split Parser

def split_resets(df):
    resets = df["timestamp"].diff() < 0

    segments = []
    start = 0

    for i, r in enumerate(resets):

        if r:
            segments.append(df.iloc[start:i])
            start = i

    segments.append(df.iloc[start:])

    return segments

# Pressure Conversion

def convert_pressure(raw_adc, sensor_index):
    voltage = raw_adc * ADS_UNIT_VOLTAGE

    if sensor_type[sensor_index] == "high":
        psi = (voltage - V_MIN) / V_DIFF * HIGH_PRESSURE_MAX
    else:
        psi = (voltage - V_MIN) / V_DIFF * LOW_PRESSURE_MAX

    return psi

# -------- FOLDER PROCESSOR --------

def convert_sessions(data_directory):

    master_rows = []

    for file in os.listdir(data_directory):

        if not file.endswith(".csv") or file == config["output_file"]:
            continue

        path = os.path.join(data_directory, file)

        try:
            df = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            print(f"Skipping empty file: {file}")
            continue

        if df.empty:
            continue

        # detect timestamp resets
        segments = split_resets(df)

        file_time = os.path.getmtime(path)

        for seg in segments:

            if seg.empty:
                continue

            rel_time = seg["timestamp"] / 1_000_000.0

            # estimate boot time
            last_timestamp = seg["timestamp"].iloc[-1] / 1_000_000.0
            boot_time = file_time - last_timestamp

            # reconstruct absolute time
            absolute_seconds = boot_time + rel_time
            seg["time"] = pd.to_datetime(absolute_seconds, unit="s", utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)

            # pressure conversion
            for i in range(len(sensor_type)):

                col = f"P_{i}"

                if col in seg.columns:
                    seg[col] = seg[col].apply(
                        lambda x: convert_pressure(x, i)
                    ).round(2)

            seg["source_file"] = file

            master_rows.append(seg)

    master_df = pd.concat(master_rows).reset_index(drop=True)

    return master_df


# -- RUN --

config_path = r"C:\Users\highp\all\repos\Torito-25-26\src\uv\2.28CF\old_session_config.json"

# Read config & set directories
with open(config_path) as f:
    config = json.load(f)

test_directory = os.path.dirname(config_path)
data_directory = os.path.join(test_directory, "data")

# Process sessions
master_df = convert_sessions(data_directory)

# Sort chronologically & reset index
master_df = master_df.sort_values("time").reset_index(drop=True)

# Write output CSV
output_file = config["output_file"]
output_path = os.path.join(test_directory, output_file)

master_df.to_csv(output_path, index=False)

print(f"Successful: {output_file}")