@echo off
cd /d "%~dp0"
if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" "%~dp0watchdog_service.py"
) else (
    .\venv\Scripts\python.exe "%~dp0run_server.py" --host 127.0.0.1
)
