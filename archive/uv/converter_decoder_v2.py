import csv
import os
import pandas as pd
from datetime import datetime

# -------- SAME CONSTANTS AS GUI --------

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


# -------- PRESSURE CONVERSION --------

def convert_pressure(raw_adc, sensor_index):

    voltage = raw_adc * ADS_UNIT_VOLTAGE

    if sensor_type[sensor_index] == "high":
        psi = (voltage - V_MIN) / V_DIFF * HIGH_PRESSURE_MAX
    else:
        psi = (voltage - V_MIN) / V_DIFF * LOW_PRESSURE_MAX

    return psi


# -------- SINGLE FILE PROCESSOR --------

def convert_csv(filepath):

    output_path = filepath.replace(".csv", "_converted.csv")

    df = pd.read_csv(filepath)

    if df.empty:
        print(f"Skipping empty dataframe: {filepath}")
        return

    # ----- elapsed time -----
    if "timestamp" in df.columns:
        t0 = df["timestamp"].iloc[0]
        df["t_sec"] = (df["timestamp"] - t0) / 1_000_000.0

    # ----- pressure conversion -----
    for i in range(len(sensor_type)):

        col = f"P_{i}"

        if col in df.columns:

            raw_adc = df[col]

            voltage = raw_adc * ADS_UNIT_VOLTAGE

            if sensor_type[i] == "high":
                psi = (voltage - V_MIN) / V_DIFF * HIGH_PRESSURE_MAX
            else:
                psi = (voltage - V_MIN) / V_DIFF * LOW_PRESSURE_MAX

            df[col] = psi.round(2)

    # ----- reorder columns so time is first -----
    if "t_sec" in df.columns:
        cols = ["t_sec"] + [c for c in df.columns if c != "t_sec"]
        df = df[cols]
    
    if os.path.exists(output_path):
        os.remove(output_path)

    df.to_csv(output_path, index=False)

    print(f"Converted: {os.path.basename(filepath)}")


# -------- FOLDER PROCESSOR --------

def convert_folder(directory):

    for file in os.listdir(directory):

        # only real csv files
        if not file.endswith(".csv"):
            continue

        full_path = os.path.join(directory, file)

        # skip empty files
        if os.path.getsize(full_path) == 0:
            print(f"Skipping empty file: {file}")
            continue

        try:
            convert_csv(full_path)
        except Exception as e:
            print(f"Skipping {file}: {e}")


# -------- RUN --------

data_directory = r"[DATA_DIRECTORY_PATH]"  # Replace with your actual data directory path

convert_folder(data_directory)