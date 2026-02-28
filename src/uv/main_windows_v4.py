import serial
import serial.tools.list_ports
import struct, threading, queue, time, csv
import dearpygui.dearpygui as dpg

BAUD = 115200
HISTORY_LENGTH = 150 # Number of ticks to display on the scrolling plots

V_MAX = 4.5
V_MIN = 0.5
V_DIFF = V_MAX - V_MIN
HIGH_PRESSURE_MAX = 5000
LOW_PRESSURE_MAX = 2000

ORIGINAL_ADS_VOLTAGE_RANGE = 4.096
GAIN = 2/3
ADS_VOLTAGE_RANGE = ORIGINAL_ADS_VOLTAGE_RANGE / GAIN

ADS_COUNT_RANGE = 2**15 - 1
ADS_UNIT_VOLTAGE = ADS_VOLTAGE_RANGE / ADS_COUNT_RANGE

# Global configuration set by the setup window
config = {
    "port": "COM6",
    "num_p": 4,
    "num_t": 0,
    "num_lc": 0,
    "num_sol": 6,
    "total_sensors": 4,
    "packet_format": "<IIBBH4H",
    "packet_size": 20,
    "sensor_type": ["high", "low", "low","low"]
}

class TelemetryData:
    def __init__(self):
        self.solenoid_bits = 0
        self.cmd_solenoid_bits = 0 # What we want to send
        self.history_y = {} # Will be populated dynamically
        self.history_x = {}
        self.current_tick = 0
        self.lock = threading.Lock()
        self.is_connected = False
        self.serial_port = None
        self.start_time = time.time()
        self.last_timestamp = 0
        self.pressure_zero_offsets = {}

data_store = TelemetryData()
command_queue = queue.Queue()

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
        ser.timeout = 0  # non-blocking

        print(f"[SERIAL] Connected to {config['port']}")
        print(f"[SERIAL] Expecting {config['packet_size']} payload bytes")

        SYNC = b'\xAA\x55'
        buffer = bytearray()

        data_store.start_time = time.time()

        while data_store.is_connected:

            # --------------------------
            # SEND COMMANDS
            # --------------------------
            while not command_queue.empty():
                cmd_bits = command_queue.get()

                # Format exactly like Serial Monitor command
                message = f"0x{cmd_bits:04X},2\n"

                ser.write(message.encode())

                print(f"[GUI] Sent: {message.strip()}")

            # --------------------------
            # READ BYTES
            # --------------------------
            incoming = ser.read(ser.in_waiting or 1)
            if incoming:
                buffer.extend(incoming)

            # --------------------------
            # PACKET PROCESSING LOOP
            # --------------------------
            while True:

                # Need at least 2 bytes to check header
                if len(buffer) < 2:
                    break

                # Find sync header
                sync_index = buffer.find(SYNC)

                if sync_index == -1:
                    # No header found, discard junk
                    buffer.clear()
                    break

                # Remove junk before header
                if sync_index > 0:
                    del buffer[:sync_index]

                # Need full header + payload
                total_packet_size = 2 + config["packet_size"]

                if len(buffer) < total_packet_size:
                    break

                # Extract payload (skip header)
                raw = buffer[2:total_packet_size]
                del buffer[:total_packet_size]

                try:
                    unpacked = struct.unpack(config["packet_format"], raw)
                except Exception as e:
                    print("[SERIAL] Unpack failed:", e)
                    continue

                ts, seq, mask, stat, solenoids = unpacked[:5]
                adc_values = unpacked[5:]

                elapsed = time.time() - data_store.start_time

                print(f"[SERIAL] seq={seq} sol={solenoids} adc={adc_values}")

                with data_store.lock:
                    data_store.solenoid_bits = solenoids
                    data_store.current_tick = elapsed

                    for idx, val in enumerate(adc_values):
                        hist_y = data_store.history_y[idx]
                        hist_x = data_store.history_x[idx]

                        hist_y.append(val)
                        hist_x.append(elapsed)

                        if len(hist_y) > HISTORY_LENGTH:
                            hist_y.pop(0)
                            hist_x.pop(0)

                writer.writerow([ts, seq, f"{solenoids:016b}"] + list(adc_values))
                log_file.flush()

            time.sleep(0.001)

    except Exception as e:
        print("[SERIAL ERROR]:", e)
        data_store.is_connected = False

    finally:
        log_file.close()

# --- INPUT HANDLING ---

def toggle_solenoid(solenoid_idx):
    if solenoid_idx >= config["num_sol"]:
        return

    with data_store.lock:
        # Map solenoid 0–5 to bits 14–9
        bit_position = 14 - solenoid_idx

        data_store.cmd_solenoid_bits ^= (1 << bit_position)

        # Always force MSB validation bit
        data_store.cmd_solenoid_bits |= 0x8000

        bits_to_send = data_store.cmd_solenoid_bits

    command_queue.put(bits_to_send)

    print(f"Command bits: {bits_to_send:016b}")

def key_press_handler(sender, app_data):
    """Listens for Shift + Number Row to actuate valves."""
    key_code = app_data
    # print("KEY: ", key_code)
    # Check specifically for Left Shift or Right Shift
    if dpg.is_key_down(dpg.mvKey_LShift) or dpg.is_key_down(dpg.mvKey_RShift):
        # Map keys '1' through '9
        # ' (key codes 537-545) to solenoid indices 0 through 8
        if 537 <= key_code <= 545: 
            sol_index = key_code - 537
            toggle_solenoid(sol_index)
        if key_code == 546:
            emergency_stop()
            return
  

def emergency_stop():
    """SHIFT + A: Turn OFF all valves except Valve 4 (index 3)."""
    print("!!! EMERGENCY STOP ACTIVATED !!!")
    with data_store.lock:

        # Start clean
        data_store.cmd_solenoid_bits = 0

        # Always set validation bit
        data_store.cmd_solenoid_bits |= 0x8000
        # data_store.cmd_solenoid_bits &= 0x8800  # Clear all other bits
        # Valve 4 = index 3
        bit_position = 14 - 3
        # data_store.cmd_solenoid_bits |= (1 << bit_position)

        bits_to_send = data_store.cmd_solenoid_bits

    # Clear queued commands so E-Stop is immediate
    while not command_queue.empty():
        command_queue.get()

    command_queue.put(bits_to_send)

    print("!!! EMERGENCY STOP ACTIVATED !!!")
    print(f"Command bits: {bits_to_send:016b}")

def zero_pressures():
    with data_store.lock:
        for i in range(config["num_p"]):
            if len(data_store.history_y[i]) > 0:
                # Convert centiPSI to PSI
                psi = data_store.history_y[i][-1] / 10.0
                data_store.pressure_zero_offsets[i] = psi

    print("Pressures zeroed.")

def convert_pressure(raw_adc, sensor_index):

    voltage = raw_adc * ADS_UNIT_VOLTAGE

    if config["sensor_type"][sensor_index] == "low":
        psi = (voltage - V_MIN) / V_DIFF * LOW_PRESSURE_MAX
    elif config["sensor_type"][sensor_index] == "high":
        psi = (voltage - V_MIN) / V_DIFF * HIGH_PRESSURE_MAX
    else:
        return 0

    zero = data_store.pressure_zero_offsets.get(sensor_index, 0.0)

    return psi - zero

# --- GUI UPDATE LOOP ---

def update_gui():
    if not data_store.is_connected: return
        
    with data_store.lock:
        # 1. Update Solenoid Feedback Indicators
        for i in range(config["num_sol"]):

            # Relays are mapped to bits 14..9
            bit_position = 14 - i

            is_on = (data_store.cmd_solenoid_bits & (1 << bit_position)) != 0

            status_text = "[ ON ]" if is_on else "[ OFF ]"
            color = (0, 255, 0) if is_on else (255, 50, 50)

            dpg.set_value(f"sol_ind_{i}", f"Valve {i+1}: {status_text}")
            dpg.configure_item(f"sol_ind_{i}", color=color)
        
        # 2. Helper to update plots and text for a specific category
        def update_category(cat_prefix, start_idx, count):

            all_vals = []

            for i in range(count):
                global_idx = start_idx + i

                if len(data_store.history_y[global_idx]) > 0:

                    y_data = data_store.history_y[global_idx]
                    x_data = data_store.history_x[global_idx]
                    if cat_prefix == "P":
                        current_val = convert_pressure(y_data[-1], i)
                    else:
                        current_val = y_data[-1]

                    # Update numeric text
                    dpg.set_value(f"{cat_prefix}_text_{i}",
                                f"{cat_prefix}-{i+1}: {current_val:.1f}")

                    # Update plot line
                    if cat_prefix == "P":
                        y_plot = [convert_pressure(v, i) for v in y_data]
                    else:
                        y_plot = y_data

                    dpg.set_value(f"{cat_prefix}_plot_{i}", [x_data, y_plot])

                    if cat_prefix == "P":
                        calibrated_vals = [convert_pressure(v, i) for v in y_data]
                        all_vals.extend(calibrated_vals)
                    else:
                        all_vals.extend(y_data)

            # --- X AXIS (20 second rolling window) ---
            window = 20

            dpg.set_axis_limits(
                f"{cat_prefix}_x_axis",
                max(0, data_store.current_tick - window),
                data_store.current_tick
            )

            # --- Y AXIS (auto scale with padding) ---
            if all_vals:
                ymin = min(all_vals)
                ymax = max(all_vals)

                if ymin == ymax:
                    padding = 1
                else:
                    padding = (ymax - ymin) * 0.10  # 10% padding

                # Find the Y axis (it is the parent of the line series)
                y_axis = dpg.get_item_parent(f"{cat_prefix}_plot_0")

                dpg.set_axis_limits(
                    y_axis,
                    ymin - padding,
                    ymax + padding
                )
                    
            # Auto-scroll the X axis for this plot window
            window = 20
            dpg.set_axis_limits(f"{cat_prefix}_x_axis", max(0, data_store.current_tick - window), data_store.current_tick)
        
        # Update Pressure
        if config["num_p"] > 0:
            update_category("P", 0, config["num_p"])

        # Update Temp
        if config["num_t"] > 0:
            update_category("T", config["num_p"], config["num_t"])

        # Update Load Cell
        if config["num_lc"] > 0:
            update_category("LC", config["num_p"] + config["num_t"], config["num_lc"])

# --- UI BUILDERS ---

def launch_main_ui():
    """Reads setup config, sets up dynamic structures, and builds main UI."""
    config["port"] = dpg.get_value("setup_port")
    config["num_p"] = dpg.get_value("setup_p")
    config["num_t"] = dpg.get_value("setup_t")
    config["num_lc"] = dpg.get_value("setup_lc")
    config["num_sol"] = dpg.get_value("setup_sol")
    
    if config["port"] == "No Ports Found" or not config["port"]:
        print("Invalid COM port selected.")
        return

    # Calculate required packet format based on total sensors
    config["total_sensors"] = config["num_p"] + config["num_t"] + config["num_lc"]
    config["packet_format"] = f"<IIBBH{config['total_sensors']}H"
    config["packet_size"] = 12 + (config["total_sensors"] * 2)
    data_store.pressure_zero_offsets = {i: 0.0 for i in range(config["num_p"])}
    
    # Initialize data arrays
    data_store.history_y = {i: [] for i in range(config["total_sensors"])}
    data_store.history_x = {i: [] for i in range(config["total_sensors"])}

    dpg.hide_item("setup_window")
    build_main_windows()
    connect_serial()

def build_sensor_window(title, prefix, count, pos):
    """Helper to build a unified window for a category of sensors."""
    if count == 0: return # Don't build the window if there are 0 sensors

    with dpg.window(label=title, width=450, height=300, pos=pos, no_close=True):
        # Numerical readouts at the top
        with dpg.group(horizontal=True, horizontal_spacing=20):
            for i in range(count):
                dpg.add_text(f"{prefix}-{i+1}: --", tag=f"{prefix}_text_{i}", color=(0, 255, 255))
        
        if prefix == "P":
            dpg.add_button(label="Zero Pressures", callback=zero_pressures)
        
        # Unified Plot
        with dpg.plot(label="", height=-1, width=-1):
            dpg.add_plot_legend()
            dpg.add_plot_axis(dpg.mvXAxis, label="Ticks", no_tick_labels=True, tag=f"{prefix}_x_axis")
            with dpg.plot_axis(dpg.mvYAxis, label="PSI", auto_fit=True):
                for i in range(count):
                    # DPG will automatically assign different colors to multiple series on the same axis
                    dpg.add_line_series([], [], label=f"{prefix}-{i+1}", tag=f"{prefix}_plot_{i}")

def build_main_windows():
    # 1. Solenoid Window (Top Left)
    with dpg.window(label="Valve Telemetry", width=300, height=300, pos=(10, 10), no_close=True):
        dpg.add_text("Hold SHIFT + Number Key (1-9) to toggle.")
        dpg.add_separator()
        for i in range(config["num_sol"]):
            dpg.add_text(f"Valve {i+1}: [ OFF ]", tag=f"sol_ind_{i}", color=(255, 50, 50))

    # 2. Sensor Windows
    build_sensor_window("Pressure Data", "P", config["num_p"], pos=(320, 10))
    build_sensor_window("Temperature Data", "T", config["num_t"], pos=(10, 320))
    build_sensor_window("Load Cell Data", "LC", config["num_lc"], pos=(470, 320))

# --- DPG INITIALIZATION ---

dpg.create_context()

# ---------- CUSTOM FONT ----------
with dpg.font_registry():
    default_font = dpg.add_font("C:/Windows/Fonts/arial.ttf", 24)  # adjust size here

dpg.bind_font(default_font)

# ---------------- LIGHT THEME ----------------
with dpg.theme() as light_theme:
    with dpg.theme_component(dpg.mvAll):
        # Backgrounds
        dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (245, 245, 245, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (250, 250, 250, 255))
        dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (255, 255, 255, 255))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (255, 255, 255, 255))

        # Title bars
        dpg.add_theme_color(dpg.mvThemeCol_TitleBg, (220, 220, 220, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (200, 200, 200, 255))

        # Buttons
        dpg.add_theme_color(dpg.mvThemeCol_Button, (220, 220, 220, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (200, 200, 200, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (180, 180, 180, 255))
        dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
        dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 6)

        # Text
        dpg.add_theme_color(dpg.mvThemeCol_Text, (0, 0, 0, 255))

        # Plots
        dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 3)

        # Border
        dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 2)
        dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 2)


dpg.bind_theme(light_theme)
# ------------------------------------------------

# Keyboard Handler Registry
with dpg.handler_registry():
    dpg.add_key_press_handler(callback=key_press_handler)

# Setup Window (First thing the user sees)
with dpg.window(label="Test Stand Configuration", tag="setup_window", width=400, height=300, pos=(200, 150), no_close=True):
    dpg.add_text("Configure Ground Station UI & Telemetry", color=(0, 255, 100))
    dpg.add_separator()
    
    ports = get_available_ports()
    dpg.add_combo(ports, label="COM Port", tag="setup_port", default_value=config["port"])
    
    dpg.add_input_int(label="# of Pressure Sensors", tag="setup_p", default_value=4, min_value=0, max_value=10)
    dpg.add_input_int(label="# of Temperature Sensors", tag="setup_t", default_value=0, min_value=0, max_value=10)
    dpg.add_input_int(label="# of Load Cells", tag="setup_lc", default_value=0, min_value=0, max_value=4)
    dpg.add_input_int(label="# of Solenoids", tag="setup_sol", default_value=5, min_value=1, max_value=9)
    
    dpg.add_spacer(height=20)
    dpg.add_button(label="Launch Ground Station", width=-1, height=40, callback=launch_main_ui)

dpg.create_viewport(title="Liquid Propulsion Ground Station", width=1000, height=800)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.set_primary_window("setup_window", True)

while dpg.is_dearpygui_running():
    update_gui()
    dpg.render_dearpygui_frame()

dpg.destroy_context()