#!/usr/bin/env python3
"""
Jetson LoRa Full Terminal
-------------------------

1) Initializes each LoRa module and prints RAW OK responses.
2) Enters interactive terminal mode:
      - type: 1:HELLO   → send "HELLO" on LoRa #1
      - type: 2:AT      → send AT to LoRa #2
3) Continuously prints ALL incoming serial lines.

No parsing. Pure raw visibility.
"""

import serial
import threading
import time
import sys

# ============================================================
# CONFIGURE YOUR LORA UARTS HERE
# ============================================================

LORAS = [
    {"name": "LORA_1", "port": "/dev/ttyTHS1", "baud": 115200, "addr": "2"},
    {"name": "LORA_2", "port": "/dev/ttyS0",   "baud": 115200, "addr": "7"},
]

NETWORK_ID = "18"
BAND = "915000000"
PARAMS = "11,9,4,24"

# ============================================================

class LoRaDevice:
    def __init__(self, cfg):
        self.name = cfg["name"]
        self.port = cfg["port"]
        self.baud = cfg["baud"]
        self.addr = cfg["addr"]
        self.ser = None
        self.running = False

    # --------------------------------------------------------

    def open(self):
        print(f"[{self.name}] Opening {self.port}")
        self.ser = serial.Serial(self.port, self.baud, timeout=0.1)

    # --------------------------------------------------------

    def send(self, cmd: str):
        """Send raw command with CRLF."""
        self.ser.write((cmd + "\r\n").encode())
        print(f"[{self.name} TX] {cmd}")

    # --------------------------------------------------------

    def send_and_wait(self, cmd: str, timeout=1.5):
        """
        Send AT command and print RAW responses.
        """
        self.send(cmd)

        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.ser.in_waiting:
                line = self.ser.readline().decode(errors="ignore").rstrip()
                if line:
                    print(f"[{self.name} RX] {line}")

        time.sleep(0.1)

    # --------------------------------------------------------

    def initialize(self):
        """Run AT initialization sequence."""
        print(f"\n=== INITIALIZING {self.name} ===")

        commands = [
            "AT",
            f"AT+ADDRESS={self.addr}",
            f"AT+NETWORKID={NETWORK_ID}",
            f"AT+BAND={BAND}",
            f"AT+PARAMETER={PARAMS}",
        ]

        for cmd in commands:
            self.send_and_wait(cmd)

        print(f"=== {self.name} READY ===\n")

    # --------------------------------------------------------

    def start_reader(self):
        """Background thread printing RAW incoming serial."""
        self.running = True
        threading.Thread(target=self._reader_loop, daemon=True).start()

    def _reader_loop(self):
        while self.running:
            try:
                if self.ser.in_waiting:
                    line = self.ser.readline().decode(errors="ignore").rstrip()
                    if line:
                        print(f"[{self.name} RX] {line}")
                else:
                    time.sleep(0.01)
            except Exception as e:
                print(f"[{self.name} ERROR] {e}")
                time.sleep(0.1)

    # --------------------------------------------------------

    def stop(self):
        self.running = False
        if self.ser and self.ser.is_open:
            self.ser.close()


# ============================================================
# MAIN
# ============================================================

def main():

    # ---- open devices ----
    devices = []
    for cfg in LORAS:
        d = LoRaDevice(cfg)
        d.open()
        devices.append(d)

    # ---- initialize LoRas ----
    for d in devices:
        d.initialize()

    # ---- start continuous readers ----
    for d in devices:
        d.start_reader()

    print("====================================")
    print(" LoRa interactive terminal started")
    print(" Format: <device#>:<message>")
    print(" Example: 1:HELLO")
    print(" Ctrl+C to exit")
    print("====================================\n")

    # ---- interactive loop ----
    try:
        while True:
            user = input("> ").strip()

            if not user:
                continue

            if ":" not in user:
                print("Use format: <device#>:<message>")
                continue

            dev_id, msg = user.split(":", 1)

            try:
                idx = int(dev_id) - 1
                if idx < 0 or idx >= len(devices):
                    raise ValueError
            except ValueError:
                print("Invalid device number.")
                continue

            devices[idx].send(msg)

    except KeyboardInterrupt:
        print("\nStopping...")

    finally:
        for d in devices:
            d.stop()
        sys.exit(0)


# ============================================================

if __name__ == "__main__":
    main()
