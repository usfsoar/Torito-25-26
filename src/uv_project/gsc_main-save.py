import serial
import time

PORT = '/dev/ttyACM0'
BAUD = 115200

try:
	ser = serial.Serial(PORT, BAUD, timeout=0.1)
	time.sleep(2)
	print(f"Connected to {PORT}. Press Ctrl+C to stop.")
    
	while True:
		while ser.in_waiting > 0:
			line = ser.readline().decode('utf-8', errors="ignore").strip()
			if line:
				print(f"ESP says: {line}")
		msg = "heya,1\n"
		ser.write(msg.encode('utf-8'))
		time.sleep(0.3)
        
except serial.SerialException as e:
    print(f"Error: {e}")
except KeyboardInterrupt:
    print("\nClosing connection.")

def bin_to_hex(bin_string):
	return hex(int(bin_string,2))

def hex_to_bin(hex_string):
	return bin(int(hex_string,16))

solenoid_status = [0,0,0,0,0,0]