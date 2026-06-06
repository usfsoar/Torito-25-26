import csv
import struct
from pathlib import Path

# -----------------------------
# CONFIG
# -----------------------------
INPUT_FILE = Path("data.bin")
OUTPUT_DIR = Path("decoded")

MARKER = 0xA5A5A5A5
MARKER_BYTES = struct.pack("<I", MARKER)

FRAME_FMT = "<I I B B H 4I 4H"
FRAME_SIZE = struct.calcsize(FRAME_FMT)  # Should be 36 bytes

SENSOR_COUNT = 4


# -----------------------------
# Helpers
# -----------------------------
def find_all_markers(data: bytes):
    offsets = []
    start = 0
    while True:
        idx = data.find(MARKER_BYTES, start)
        if idx == -1:
            break
        offsets.append(idx)
        start = idx + 4
    return offsets


def decode_frames(region: bytes):
    frames = []

    usable_len = len(region) - (len(region) % FRAME_SIZE)

    for off in range(0, usable_len, FRAME_SIZE):
        chunk = region[off:off + FRAME_SIZE]

        try:
            vals = struct.unpack(FRAME_FMT, chunk)
        except struct.error:
            break

        ts_us, seq, valid_mask, status_bits, sol_state, *rest = vals
        payload = rest[:SENSOR_COUNT]
        raw_adc = rest[SENSOR_COUNT:SENSOR_COUNT + SENSOR_COUNT]

        frames.append({
            "timestamp_us": ts_us,
            "seq": seq,
            "valid_mask": valid_mask,
            "status_bits": status_bits,
            "solenoid_state": sol_state,
            "payload0_centi_psi": payload[0],
            "payload1_centi_psi": payload[1],
            "payload2_centi_psi": payload[2],
            "payload3_centi_psi": payload[3],
            "raw_adc0": raw_adc[0],
            "raw_adc1": raw_adc[1],
            "raw_adc2": raw_adc[2],
            "raw_adc3": raw_adc[3],
        })

    return frames


def write_csv(frames, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)

    headers = [
        "timestamp_us", "seq", "valid_mask", "status_bits", "solenoid_state",
        "payload0_centi_psi", "payload1_centi_psi",
        "payload2_centi_psi", "payload3_centi_psi",
        "raw_adc0", "raw_adc1", "raw_adc2", "raw_adc3",
    ]

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(frames)


def estimate_rate(frames):
    if len(frames) < 2:
        return None

    deltas = []
    for i in range(1, len(frames)):
        dt = (frames[i]["timestamp_us"] - frames[i-1]["timestamp_us"]) & 0xFFFFFFFF
        if 0 < dt < 5_000_000:
            deltas.append(dt)

    if not deltas:
        return None

    avg_dt = sum(deltas) / len(deltas)
    return 1_000_000.0 / avg_dt


# -----------------------------
# Main
# -----------------------------
def main():
    if not INPUT_FILE.exists():
        print("ERROR: data.bin not found in project root.")
        return

    data = INPUT_FILE.read_bytes()
    markers = find_all_markers(data)

    if not markers:
        print("ERROR: No session marker found.")
        return

    print(f"Found {len(markers)} session marker(s).")

    for i, marker_pos in enumerate(markers):
        start = marker_pos + 4
        end = markers[i + 1] if i + 1 < len(markers) else len(data)

        region = data[start:end]
        frames = decode_frames(region)

        output_file = OUTPUT_DIR / f"session_{i:03d}.csv"
        write_csv(frames, output_file)

        rate = estimate_rate(frames)
        rate_str = f"{rate:.2f} Hz" if rate else "unknown"

        print(f"Session {i:03d}: {len(frames)} frames → {output_file} | Estimated rate: {rate_str}")

    print("\nDone.")


if __name__ == "__main__":
    main()
