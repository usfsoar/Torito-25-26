#----IMPORT LIBRARIES----
import serial
import serial.tools.list_ports
import struct, threading, queue, time, csv
import dearpygui.dearpygui as dpg

#----IMPORT CLASSES----
from message_decoder import FrameDecoder
dest_address = 2
BAUD = 115200
HISTORY_LENGTH = 150 # Number of ticks to display on the scrolling plots

# Global configuration set by the setup window
config = {
    "port": "",
    "num_p": 4,
    "num_t": 0,
    "num_lc": 0,
    "num_sol": 6,
    "total_sensors": 4,
    "packet_format": "",
    "packet_size": 20
}

class TelemetryData:
    def __init__(self):
        self.solenoid_bits = 0
        self.cmd_solenoid_bits = 0
        self.history_y = {}
        self.history_x = {}
        self.lock = threading.Lock()
        self.is_connected = False
        self.serial_port = None

data_store = TelemetryData()
command_queue = queue.Queue()
decoder = FrameDecoder()

# --- SERIAL LOGIC ---

def get_available_ports():
    ports = [port.device for port in serial.tools.list_ports.comports()]
    return ports if ports else ["No Ports Found"]

def connect_serial():
    try:
        data_store.serial_port = serial.Serial(config["port"], BAUD, timeout=0.1)
        data_store.is_connected = True
        threading.Thread(target=serial_worker, daemon=True).start()
    except Exception as e:
        print(f"Failed to connect: {e}")
        dpg.set_value("setup_status", f"Error: {e}")

def serial_worker():
    log_file = open(f"telemetry_{int(time.time())}.csv", "w", newline='')
    writer = csv.writer(log_file)

    headers = ["timestamp", "seq", "solenoids"]
    headers += [f"P_{i}" for i in range(config["num_p"])]
    headers += [f"T_{i}" for i in range(config["num_t"])]
    headers += [f"LC_{i}" for i in range(config["num_lc"])]
    writer.writerow(headers)

    try:
        ser = data_store.serial_port
        ser.reset_input_buffer() # FIX: Flush stale buffered data on connect
        print(f"Connected to {config['port']}. Expecting {config['packet_size']} byte packets.")

        while data_store.is_connected:
            # Send commands if any
            while not command_queue.empty():
                cmd_bits = command_queue.get()
                ser.write(struct.pack('<H', cmd_bits))

            # Read incoming telemetry
            if ser.in_waiting >= config["packet_size"]:
                raw = ser.read(config["packet_size"])

                decoded_data = decoder.decoder(raw.hex())
                if decoded_data is None:
                    continue
                ts_raw, seq, mask, stat, solenoids, adc_values = decoded_data
                print(f"[t={ts_raw:.3f}s] Solenoids: {solenoids:016b} | ADC: {adc_values}")

                with data_store.lock:
                    data_store.solenoid_bits = solenoids

                    for idx, val in enumerate(adc_values):
                        hist_y = data_store.history_y[idx]
                        hist_x = data_store.history_x[idx]

                        hist_y.append(val)
                        hist_x.append(ts_raw)

                        if len(hist_y) > HISTORY_LENGTH:
                            hist_y.pop(0)
                            hist_x.pop(0)

                writer.writerow([ts_raw, seq, f"{solenoids:016b}"] + list(adc_values))
                log_file.flush()

    except Exception as e:
        print(f"Serial Error: {e}")
        data_store.is_connected = False
    finally:
        log_file.close()

# --- INPUT HANDLING ---

def toggle_solenoid(solenoid_idx):
    """Toggles the state of a specific solenoid and queues the command."""
    if solenoid_idx >= config["num_sol"]: return

    with data_store.lock:
        # Toggle the bit — MSB-first ordering (solenoid 0 = bit 15)
        data_store.cmd_solenoid_bits ^= (0x8000 >> solenoid_idx)
        bits_to_send = data_store.cmd_solenoid_bits

    # FIX: Send cmd_solenoid_bits (not feedback bits), encoded as bytes
    data_store.serial_port.write(f"{bits_to_send:#06x},{dest_address}".encode())
    print(f"Commanded Solenoid {solenoid_idx} Toggle. Current CMD state: {bits_to_send:016b}")

def key_press_handler(sender, app_data):
    """Listens for Shift + Number Row (1-9) to actuate valves."""
    key_code = app_data
    if dpg.is_key_down(dpg.mvKey_LShift) or dpg.is_key_down(dpg.mvKey_RShift):
        # FIX: Correct Windows DPG key codes for number row (537=1, 545=9)
        if 537 <= key_code <= 545:
            sol_index = key_code - 537
            toggle_solenoid(sol_index)

# --- GUI UPDATE LOOP ---

def update_gui():
    if not data_store.is_connected: return

    with data_store.lock:
        # 1. Update Solenoid Feedback Indicators
        for i in range(config["num_sol"]):
            # FIX: Match MSB-first bit ordering used in toggle_solenoid
            is_on = (data_store.solenoid_bits & (0x8000 >> i)) != 0
            status_text = "[ ON ]" if is_on else "[ OFF ]"
            color = (0, 255, 0) if is_on else (255, 50, 50)
            dpg.set_value(f"sol_ind_{i}", f"Valve {i+1}: {status_text}")
            dpg.configure_item(f"sol_ind_{i}", color=color)

        # 2. Helper to update plots and text for a specific category
        def update_category(cat_prefix, start_idx, count):
            if count == 0: return
            for i in range(count):
                global_idx = start_idx + i
                if len(data_store.history_y[global_idx]) > 0:
                    y_data = data_store.history_y[global_idx]
                    x_data = data_store.history_x[global_idx]
                    current_val = y_data[-1]

                    dpg.set_value(f"{cat_prefix}_text_{i}", f"{cat_prefix}-{i+1}: {current_val:.1f}")
                    dpg.set_value(f"{cat_prefix}_plot_{i}", [x_data, y_data])

            # FIX: Auto-scroll using real timestamps, not current_tick
            if len(data_store.history_x[start_idx]) > 0:
                latest_t = data_store.history_x[start_idx][-1]
                earliest_t = data_store.history_x[start_idx][0]
                dpg.set_axis_limits(f"{cat_prefix}_x_axis", earliest_t, latest_t)

        update_category("P", 0, config["num_p"])
        update_category("T", config["num_p"], config["num_t"])
        update_category("LC", config["num_p"] + config["num_t"], config["num_lc"])

# --- UI BUILDERS ---

def launch_main_ui():
    config["port"] = dpg.get_value("setup_port")
    config["num_p"] = dpg.get_value("setup_p")
    config["num_t"] = dpg.get_value("setup_t")
    config["num_lc"] = dpg.get_value("setup_lc")
    config["num_sol"] = dpg.get_value("setup_sol")

    if config["port"] == "No Ports Found" or not config["port"]:
        print("Invalid COM port selected.")
        return

    config["total_sensors"] = config["num_p"] + config["num_t"] + config["num_lc"]
    config["packet_format"] = f"<IIBBH{config['total_sensors']}H"
    config["packet_size"] = 12 + (config["total_sensors"] * 2)

    decoder.__init__(num_sensors=config["total_sensors"])

    data_store.history_y = {i: [] for i in range(config["total_sensors"])}
    data_store.history_x = {i: [] for i in range(config["total_sensors"])}

    dpg.hide_item("setup_window")
    build_main_windows()
    connect_serial()

def build_sensor_window(title, prefix, count, pos):
    if count == 0: return

    with dpg.window(label=title, width=450, height=300, pos=pos, no_close=True):
        with dpg.group(horizontal=True, horizontal_spacing=20):
            for i in range(count):
                dpg.add_text(f"{prefix}-{i+1}: --", tag=f"{prefix}_text_{i}", color=(0, 255, 255))

        with dpg.plot(label="", height=-1, width=-1):
            dpg.add_plot_legend()
            dpg.add_plot_axis(dpg.mvXAxis, label="Time (s)", tag=f"{prefix}_x_axis")
            with dpg.plot_axis(dpg.mvYAxis, label="PSI"):
                for i in range(count):
                    dpg.add_line_series([], [], label=f"{prefix}-{i+1}", tag=f"{prefix}_plot_{i}")

def build_main_windows():
    with dpg.window(label="Valve Telemetry", width=300, height=300, pos=(10, 10), no_close=True):
        dpg.add_text("Hold SHIFT + Number Key (1-9) to toggle.")
        dpg.add_separator()
        for i in range(config["num_sol"]):
            dpg.add_text(f"Valve {i+1}: [ OFF ]", tag=f"sol_ind_{i}", color=(255, 50, 50))

    build_sensor_window("Pressure Data", "P", config["num_p"], pos=(320, 10))
    build_sensor_window("Temperature Data", "T", config["num_t"], pos=(10, 320))
    build_sensor_window("Load Cell Data", "LC", config["num_lc"], pos=(470, 320))

# --- DPG INITIALIZATION ---

dpg.create_context()

with dpg.handler_registry():
    dpg.add_key_press_handler(callback=key_press_handler)

with dpg.window(label="Test Stand Configuration", tag="setup_window", width=400, height=320, pos=(200, 150), no_close=True):
    dpg.add_text("Configure Ground Station UI & Telemetry", color=(0, 255, 100))
    dpg.add_separator()

    ports = get_available_ports()
    dpg.add_combo(ports, label="COM Port", tag="setup_port", default_value=ports[0])

    dpg.add_input_int(label="# of Pressure Sensors", tag="setup_p", default_value=4, min_value=0, max_value=10)
    dpg.add_input_int(label="# of Temperature Sensors", tag="setup_t", default_value=2, min_value=0, max_value=10)
    dpg.add_input_int(label="# of Load Cells", tag="setup_lc", default_value=1, min_value=0, max_value=4)
    dpg.add_input_int(label="# of Solenoids", tag="setup_sol", default_value=6, min_value=1, max_value=9)

    dpg.add_spacer(height=10)
    dpg.add_text("", tag="setup_status", color=(255, 80, 80))  # Error display
    dpg.add_button(label="Launch Ground Station", width=-1, height=40, callback=launch_main_ui)

dpg.create_viewport(title="Liquid Propulsion Ground Station", width=1000, height=800)
dpg.setup_dearpygui()
dpg.show_viewport()

while dpg.is_dearpygui_running():
    update_gui()
    dpg.render_dearpygui_frame()

dpg.destroy_context()