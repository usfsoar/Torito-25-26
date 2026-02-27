import serial
import threading
import time
import queue
import dearpygui.dearpygui as dpg

PORT = '/dev/ttyACM0'
BAUD = 115200
command_queue = queue.Queue()
sensor_data = {"valves": "000000", "sensor_val": 0, "history": []}

def serial_worker():
	try:
		ser = serial.Serial(PORT, BAUD, timeout=0.1)
		print(f"Connected to {PORT}. Press Ctrl+C to stop.")
		
		while True:
			try:
				msg = command_queue.get_nowait()
				ser.write(f"{msg}\n".encode('utf-8'))
			except queue.Empty:
				pass

			# On GSC LoRa RX, read from serial and save data into dict 'sensor_data'. Assumes format 'VALVES:xxxxxx|SENSE:x.xxxx'. key 'history' in dict
			# 'sensor_data' holds last 100 sensor readings (sliding window). Needs to be updated for any number of sensors and low-level data format.

			# We must modify the code block below to conform to actual data format as well as store valve/sensor states in a file for post-CF1 review.
			# Confirm '.endswith()' won't end with \r because that has already been removed.

			rawLine = ser.read_until(b'\r')
			if rawLine.endswith(b'\r'):
				rawLine = rawLine.decode('utf-8', errors="ignore").strip()
				if "|" in rawLine:
					parts = rawLine.split("|")
					sensor_data["valves"] = parts[0].replace("VALVES:", "")
					val = float(parts[1].replace("SENSE:", ""))
					sensor_data["sensor_val"] = val
					sensor_data["history"].append(val)
					if len(sensor_data["history"]) > 100: sensor_data["history"].pop(0)
	
	# On serial error or Ctrl+C

	except serial.SerialException as e:
		print(f"Error: {e}")
	except KeyboardInterrupt:
		print("\nClosing connection.")

def on_submit(): # Reads valve values from the GUI and formats into "xxxxxx".
	bits = ""
	for i in range(1, 7):
		bits += "1" if dpg.get_value(f"V{i}") else "0"

def update_gui():
	dpg.set_value("status_text", f"Active Valves: {sensor_data['valves']}")
	dpg.set_value("plot_series", [list(range(len(sensor_data['history']))), sensor_data('history')])

dpg.create_context()
with dpg.window(label="Control Center", width=500, height=400):
	dpg.add_text("Valve Control (0 = Off, 1 = On)")
	with dpg.group(horizontal=True):
		for i in range(1, 7):
			dpg.add_checkbox(label=f"V{i}", tag=f"V{i}")

	dpg.add_button(label="Update Solenoids", callback=on_submit)
	dpg.add_separator()
	dpg.add_text("Current: 000000", tag="status_text")

	with dpg.plot(label="Sensor Data", height=200, width=-1):
		dpg.add_plot_axis(dpg.mvXaxis, label="Time")
		dpg.add_plot_axis(dpg.mvYaxis, label="Value", tag="y_axis")
		dpg.add_line_series([], [], label="Sensor", parent="y_axis", tag="plot_series")

threading.Thread(target=serial_worker, daemon=True).start()

dpg.create_viewport(title="Solenoid Dashboard", width=600, height=500)
dpg.setup_dearpygui()
dpg.show_viewport()

while dpg.is_dearpygui_running():
	update_gui()
	dpg.render_dearpygui_frame()

dpg.destroy_context()