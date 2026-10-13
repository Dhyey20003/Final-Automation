@echo off
rem Asks a running Relay Employee Hub to shut down cleanly (use before copying the data folder).
cd /d "%~dp0.."
if exist "RelayEmployeeHub.exe" (
  "RelayEmployeeHub.exe" --stop
) else (
  ".venv\Scripts\python.exe" "launcher\run_hub.py" --stop
)
