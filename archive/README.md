About uv_project:
UV is holistic Python development manager. It organizes Python projects, offering Python/dependencies version control. This folder holds the src for the Jetson Nano (currently entirely Python).

[src_a] For testing
'lora_class.py'
    A class file for 'lora_timing.py'. It is also able to run independently, sending a message to an address for a set of coded parameters.
'lora_timing.py'
    A file that times latency of a from-and-back transmission (Python).

[src_b] For production
'gsc_main.py'
    -
    # -save successfully TX/RX a hard-coded message. -save2 creates a rough template for GUI. -save3 fixes and defines next steps for serial_worker() as
    # well as fixes minor issues with the GUI boilerplate. GUI development must happen in parallel to control flow modifications to ensure RX data quality.
'gsc_dashboard.py'
    -

[env] Organizes the project
dir '.venv'
    Procures and manages dependencies from uv.lock. Is git-ignored; equivalent src is found in uv.lock and can be built from there.
'.python-version'
    Identifies project python version.
'pyproject.toml'
    Sets the general dependencies for the UV Project as well as provides basic information.
'pyrightconfig.json'
    Adds dir '.venv' to the path for Pylance (a common extension for Python development).
'uv.lock'
    A snapshot of the exact dependencies used in the project as well as src for them.