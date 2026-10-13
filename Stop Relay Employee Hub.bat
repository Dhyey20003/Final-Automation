@echo off
rem Asks a running Relay Employee Hub (and its Video Studio service) to shut down cleanly.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "launcher\run_hub.py" --stop
) else (
  echo Run "Setup Relay Employee Hub.bat" first.
  pause
)
