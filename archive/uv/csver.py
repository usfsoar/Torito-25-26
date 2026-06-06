import csv
from pathlib import Path

# =========================
# USER CONFIG
# =========================

# One entry per pressure sensor AFTER skipped columns are removed.
# "high" = 0–5000 psig
# "low"  = 0–2000 psig
PRESSURE_SENSORS = [
    "high",
    "high",
    "high",
    "high",
]

# 1-based columns to skip from the input CSV.
# For your current logger:
# column 1 = timestamp
# column 2 = seq
# column 3 = solenoids
# column 4+ = ADC values
SKIP_COLUMNS = [2, 3]

TIMESTAMP_COLUMN = 1  # 1-based

ADC_MAX_COUNT = 32767
ADC_FULL_SCALE_VOLTAGE = 6.144

SENSOR_VOLTAGE_MIN = 0.5
SENSOR_VOLTAGE_MAX = 4.5

HIGH_PRESSURE_MAX_PSIG = 5000.0
LOW_PRESSURE_MAX_PSIG = 2000.0

INPUT_FOLDER = Path(".")
OUTPUT_FOLDER = INPUT_FOLDER / "converted"


# =========================
# CONVERSION
# =========================

def raw_adc_to_psi(raw_adc, sensor_kind):
    voltage = raw_adc / ADC_MAX_COUNT * ADC_FULL_SCALE_VOLTAGE

    if sensor_kind.lower() == "high":
        pressure_max = HIGH_PRESSURE_MAX_PSIG
    elif sensor_kind.lower() == "low":
        pressure_max = LOW_PRESSURE_MAX_PSIG
    else:
        raise ValueError(f"Bad sensor type: {sensor_kind}")

    return (
        (voltage - SENSOR_VOLTAGE_MIN)
        / (SENSOR_VOLTAGE_MAX - SENSOR_VOLTAGE_MIN)
        * pressure_max
    )


def unwrap_uint32_timestamp(current, previous, rollover_offset):
    if previous is not None and current < previous:
        rollover_offset += 2**32
    return current + rollover_offset, rollover_offset


def filter_row(row, skip_columns_1_based):
    skip_indices = {col - 1 for col in skip_columns_1_based}
    return [value for idx, value in enumerate(row) if idx not in skip_indices]


def convert_csv(input_path, output_path):
    timestamp_idx = TIMESTAMP_COLUMN - 1
    num_sensors = len(PRESSURE_SENSORS)

    first_timestamp_unwrapped = None
    previous_timestamp_raw = None
    rollover_offset = 0

    with input_path.open("r", newline="") as infile, output_path.open("w", newline="") as outfile:
        reader = csv.reader(infile)
        writer = csv.writer(outfile)

        # Skip input header if present.
        input_header = next(reader, None)

        output_header = ["time_s"]
        for i, sensor_kind in enumerate(PRESSURE_SENSORS, start=1):
            output_header.append(f"P{i}_{sensor_kind}_psi")
        writer.writerow(output_header)

        for row_num, row in enumerate(reader, start=2):
            if not row:
                continue

            if len(row) <= timestamp_idx:
                raise ValueError(
                    f"{input_path.name}, row {row_num}: missing timestamp column {TIMESTAMP_COLUMN}."
                )

            timestamp_raw = int(row[timestamp_idx])

            timestamp_unwrapped, rollover_offset = unwrap_uint32_timestamp(
                timestamp_raw,
                previous_timestamp_raw,
                rollover_offset,
            )
            previous_timestamp_raw = timestamp_raw

            if first_timestamp_unwrapped is None:
                first_timestamp_unwrapped = timestamp_unwrapped

            time_s = (timestamp_unwrapped - first_timestamp_unwrapped) / 1_000_000.0

            filtered_row = filter_row(row, SKIP_COLUMNS)

            # After skipping seq and solenoids, filtered_row is:
            # timestamp, ADC1, ADC2, ADC3, ...
            adc_values = filtered_row[1:1 + num_sensors]

            if len(adc_values) < num_sensors:
                raise ValueError(
                    f"{input_path.name}, row {row_num}: has {len(adc_values)} ADC values after skipping, "
                    f"but expected {num_sensors}."
                )

            psi_values = []
            for raw_adc, sensor_kind in zip(adc_values, PRESSURE_SENSORS):
                psi = raw_adc_to_psi(float(raw_adc), sensor_kind)
                psi_values.append(f"{psi:.3f}")

            writer.writerow([f"{time_s:.6f}"] + psi_values)


def main():
    OUTPUT_FOLDER.mkdir(exist_ok=True)

    csv_files = [
        path for path in INPUT_FOLDER.glob("*.csv")
        if path.parent != OUTPUT_FOLDER
        and not path.name.endswith("_converted.csv")
    ]

    if not csv_files:
        print("No CSV files found.")
        return

    for input_path in csv_files:
        output_path = OUTPUT_FOLDER / f"{input_path.stem}_converted.csv"

        try:
            convert_csv(input_path, output_path)
            print(f"Converted: {input_path.name} -> {output_path}")
        except Exception as exc:
            print(f"FAILED: {input_path.name}: {exc}")


if __name__ == "__main__":
    main()