import serial, struct, threading , queue, time, csv
import dearpygui.dearpygui as dpg

PORT = 'COM6'
BAUD = 115200
SENSOR_MAX_COUNT = 16
SENSOR_REGISTRY = {
	0: {"name": "S1", "unit": "cubits", "scale": 0.25},
    1: {"name": "S2", "unit": "cubits", "scale": 0.25},
    2: {"name": "S3", "unit": "cubits", "scale": 0.1},
    3: {"name": "S4", "unit": "cubits", "scale": 1.0}
}
SENSOR_COUNT = len(SENSOR_REGISTRY)
PACKET_SIZE = 12 + (SENSOR_COUNT * 2)
PACKET_FORMAT = f"<IIBBH{SENSOR_COUNT}H"

class TelemetryData:
    def __init__(self):
        self.solenoid_bits = 0
        self.history = {i: [] for i in SENSOR_REGISTRY}
        self.lock = threading.Lock()

data_store = TelemetryData()
command_queue = queue.Queue()

def serial_worker():
	log_file = open(f"telemetry_{int(time.time())}.csv", "w", newline='')
	writer = csv.writer(log_file)
	writer.writerow(["timestamp", "seq", "solenoids"] + [s["name"] for s in SENSOR_REGISTRY.values()])	

	try:
		ser = serial.Serial(PORT, BAUD, timeout=0.1)
		print(f"Connected to {PORT}. Press Ctrl+C to stop.")
		
		while True:
			while not command_queue.empty():
				cmd_bits = command_queue.get()
				ser.write(struct.pack('<H', cmd_bits))

			if ser.in_waiting >= PACKET_SIZE:
				raw = ser.read(PACKET_SIZE)
				unpacked = struct.unpack(PACKET_FORMAT, raw)
                
				ts, seq, mask, stat, solenoids = unpacked[:5]
				adc_values = unpacked[5:]
				
				with data_store.lock:
					data_store.solenoid_bits = solenoids
					for idx, val in enumerate(adc_values):
						hist = data_store.history[idx]
						hist.append(val)
						if len(hist) > 100: hist.pop(0)
				
				writer.writerow([ts, seq, f"{solenoids:016b}"] + list(adc_values))
				log_file.flush()

	except Exception as e:
		print(f"Serial Error: {e}")
	finally:
		log_file.close()

def on_update_solenoids(): # Reads valve values from the GUI and formats into "xxxxxx".
	bits = 0
	for i in range(SENSOR_MAX_COUNT):
		if dpg.get_value(f"V{i}"):
			bits |= (1 << i)
	command_queue.put(bits)

def update_gui():
    with data_store.lock:
        # Update Status Text
        dpg.set_value("status_text", f"Solenoid States: {data_store.solenoid_bits:016b}")
        
        # Update Plots for each sensor in registry
        for i in SENSOR_REGISTRY:
            if len(data_store.history[i]) > 0:
                y_data = data_store.history[i]
                x_data = list(range(len(y_data)))
                dpg.set_value(f"plot_series_{i}", [x_data, y_data])

dpg.create_context()
with dpg.window(label="GSC", width=600, height=600):
    dpg.add_text("Solenoid Control")
    with dpg.group(horizontal=True, horizontal_spacing=10):
        # Create 16 checkboxes in two rows of 8
        with dpg.group():
            for i in range(8): dpg.add_checkbox(label=f"V{i}", tag=f"V{i}")
        with dpg.group():
            for i in range(8, 16): dpg.add_checkbox(label=f"V{i}", tag=f"V{i}")

    dpg.add_button(label="Update", callback=on_update_solenoids, width=-1, height=30)
    dpg.add_separator()
    dpg.add_text("States: 0000000000000000", tag="status_text", color=(0, 255, 0))

    # Dynamic Plots based on Registry
    for i, info in SENSOR_REGISTRY.items():
        with dpg.plot(label=f"{info['name']} ({info['unit']})", height=150, width=-1):
            dpg.add_plot_axis(dpg.mvXAxis, label="Ticks", no_tick_labels=True)
            with dpg.plot_axis(dpg.mvYAxis, label=info['unit']):
                dpg.add_line_series([], [], label=info['name'], tag=f"plot_series_{i}")

dpg.create_viewport(title="Telemetry Dashboard", width=650, height=700)
dpg.setup_dearpygui()
dpg.show_viewport()

threading.Thread(target=serial_worker, daemon=True).start()


while dpg.is_dearpygui_running():
	update_gui()
	dpg.render_dearpygui_frame()

dpg.destroy_context()