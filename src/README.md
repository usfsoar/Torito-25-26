About src:
Contains all source code for GSC. Currently, GSC ESP uses '.ino' files (dir 'arduino') and GSC Jetson uses '.py' files (dir 'uv_project'). We hope to transition to '.cpp' for both.

dir 'arduino'

dir 'uv_project'
    Hosts all Python

Notice, we are using UV (refer to './uv_project/README.md') over a typical Python installation. This is because of instability of Python and Pip from available src installations and built-in virtual environment (dir './uv_project/.venv') management with UV.