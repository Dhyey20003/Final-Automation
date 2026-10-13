@echo off
rem Starts Relay Employee Hub (source installation) and opens it in the default browser.
rem If it is already running, this just opens the browser.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run "Setup Relay Employee Hub.bat" first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0launcher\run_hub.py" %*
