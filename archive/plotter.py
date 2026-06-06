import os
import pandas as pd
import json
import matplotlib.pyplot as plt

# Configuration

config_path = r"C:\Users\highp\all\repos\Torito-25-26\src\uv\2.28CF\session_config.json"

with open(config_path) as f:
    config = json.load(f)

adc_cfg = config["adc"]
sensor_cfg = config["sensors"]
output_cfg = config["output"]
plot_cfg = config["plot"]
output_file = output_cfg["csv"]

test_directory = os.path.dirname(config_path)
output_path = os.path.join(test_directory, output_file)
data_directory = os.path.join(test_directory, "data")

voltage_range = adc_cfg["voltage_range"] / adc_cfg["gain"]
count_range = adc_cfg["count_range"]

ADS_UNIT_VOLTAGE = voltage_range / count_range

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

# Sensor Conversion

def convert_sensor(raw_adc, sensor_class, sensor_index):

    voltage = raw_adc * ADS_UNIT_VOLTAGE

    if sensor_class == "pressure":

        calib = sensor_cfg["pressure"]["calibration"]
        types = sensor_cfg["pressure"]["types"]

        v_min = calib["v_min"]
        v_max = calib["v_max"]

        if types[sensor_index] == "high":
            p_max = calib["high_pressure_max"]
        else:
            p_max = calib["low_pressure_max"]

        return (voltage - v_min) / (v_max - v_min) * p_max

    elif sensor_class == "temperature":

        calib = sensor_cfg["temperature"]["calibration"]

        offset = calib["v_offset"]
        slope = calib["v_per_degree"]

        return (voltage - offset) / slope

    return voltage

# -------- FOLDER PROCESSOR --------

def convert_sessions(data_directory):

    master_rows = []

    for file in os.listdir(data_directory):

        if not file.endswith(".csv") or file == output_file:
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
            for sensor_class, info in sensor_cfg.items():

                prefix = info["prefix"]
                channels = info["channels"]

                for i, ch in enumerate(channels):

                    col = f"{prefix}_{i}"

                    if col in seg.columns:

                        seg[col] = convert_sensor(seg[col], sensor_class, i).round(2)

            seg["source_file"] = file

            master_rows.append(seg)

    master_df = pd.concat(master_rows).reset_index(drop=True)

    return master_df

def plot_sensors(df):

    plot_dir = os.path.join(test_directory, output_cfg["plot_directory"])
    os.makedirs(plot_dir, exist_ok=True)

    time_col = plot_cfg["time_column"]

    # Ensure chronological order
    df = df.sort_values(time_col)

    # Create hourly groups
    df["hour_group"] = df[time_col].dt.floor("h")

    for hour, hour_df in df.groupby("hour_group"):

        date_str = hour.strftime("%Y-%m-%d")
        hour_str = hour.strftime("%H")

        for sensor_class, info in sensor_cfg.items():

            prefix = info["prefix"]
            channels = info["channels"]

            cols = [f"{prefix}_{i}" for i in range(len(channels)) if f"{prefix}_{i}" in hour_df.columns]

            if not cols:
                continue

            plt.figure(figsize=(10,6))

            for col in cols:
                plt.plot(hour_df[time_col], hour_df[col], label=col)

            title = plot_cfg[sensor_class]["title"]
            ylabel = plot_cfg[sensor_class]["ylabel"]

            plt.title(f"{title} — {date_str} Hour {hour_str}")
            plt.xlabel("Time")
            plt.ylabel(ylabel)

            plt.legend()
            plt.grid(True)

            ymin = hour_df[cols].min().min()
            ymax = hour_df[cols].max().max()

            if sensor_class == "pressure":
                if ymin >= -10 and ymax <= 100:
                    ymin_plot = -10
                    ymax_plot = 100
                else:
                    ymin_plot = ymin - 10
                    ymax_plot = ymax + 10
                plt.ylim(ymin_plot, ymax_plot)
            else:
                plt.ylim(ymin, ymax)

            filename = f"{sensor_class}_{date_str}_{hour_str}.png"
            path = os.path.join(plot_dir, filename)

            plt.savefig(path, dpi=200)
            plt.close()

            print(f"Saved plot: {path}")


# -- RUN --

# Process sessions
master_df = convert_sessions(data_directory)

# Sort chronologically & reset index
master_df = master_df.sort_values("time").reset_index(drop=True)

# Write output CSV

master_df.to_csv(output_path, index=False)

plot_sensors(master_df)

print(f"Successful: {output_file}")