import dearpygui.dearpygui as dpg

def key_press_handler(sender, app_data):
    key_code = app_data
    shift = dpg.is_key_down(dpg.mvKey_LShift) or dpg.is_key_down(dpg.mvKey_RShift)
    msg = f"Key code: {key_code} | Shift held: {shift}"
    print(msg)
    dpg.set_value("status_text", msg)

dpg.create_context()

with dpg.handler_registry():
    dpg.add_key_press_handler(callback=key_press_handler)

with dpg.window(label="Key Code Debug", width=500, height=200, no_close=True):
    dpg.add_text("Press ANY key and check the output below.", color=(0, 255, 100))
    dpg.add_text("Then try SHIFT + Number keys.", color=(255, 255, 0))
    dpg.add_separator()
    dpg.add_text("Waiting...", tag="status_text", color=(0, 255, 255))

dpg.create_viewport(title="Key Code Debug", width=500, height=200)
dpg.setup_dearpygui()
dpg.show_viewport()

while dpg.is_dearpygui_running():
    dpg.render_dearpygui_frame()

dpg.destroy_context()