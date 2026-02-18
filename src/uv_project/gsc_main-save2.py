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

			if ser.in_waiting > 0:
				rawLine = ser.readline().decode('utf-8', errors="ignore").strip()
				if "|" in rawLine:
					parts = rawLine.split("|")
					sensor_data["valves"] = parts[0].replace("VALVES:", "")
					val = float(parts[1].replace("SENSE:", ""))
					sensor_data["sensor_val"] = val
					sensor_data["history"].append(val)
					if len(sensor_data["history"]) > 100: sensor_data["history"]

			time.sleep(0.1)
	except serial.SerialException as e:
		print(f"Error: {e}")
	except KeyboardInterrupt:
		print("\nClosing connection.")

def on_submit():
	bits = ""
	for i in range(1, 7):
		bits += "1" if dpg.get_value(f"V{i}") else "0"

def update_gui():
	dpg.set_value("status_text", f"Active Values: {sensor_data['values']}")
	dpg.set_value("plot_series", [list(range(len(sensor_data['history'])))])

dpg.create_context()
with dpg.window(label="Control Center", width=500, height=400):
	dpg.add_text("Valve Control (0 = Off, 1 = On)")
	with dpg.group(horizontal=True):
		for i in range(1, 7):
			dpg.add_checkbox(label=f"V{i}", tag=f"V{i}")

	dpg.add_button(label="Update Solenoids", callback=on_submit)
	dpg.add_separator()
	dpg.add_text("Feedback: 000000", tag="status_text")

	with dpg.plot(label="Sensor Data", height=200, width=-1):
		dpg.add_plot_axis(dpg.mvXaxis, label="Time")
		dpg.app_plot_axis(dpg.mvYaxis, label="Value", tag="y_axis")
		dpg.add_line_series([], [], label="Sensor", parent="y_axis", tag="plot_series")

threading.Thread(target=serial_worker, daemon=True).start()

dpg.create_viewport(title="Solenoid Dashboard", width=600, height=500)
dpg.setup_dearpygui()
dpg.show_viewport()

while dpg.is_dearpygui_running():
	update_gui()
	dpg.render_dearpygui_frame()

dpg.destroy_context()