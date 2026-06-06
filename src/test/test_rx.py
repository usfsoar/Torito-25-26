import serial
import sys

PORT = "COM6"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0)

while True:
    data = ser.read(ser.in_waiting or 1)
    if data:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()