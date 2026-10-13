@echo off
rem Chooses where Relay Employee Hub keeps its data (database, photos, exports, backups).
rem Stop the application first. Do not choose a OneDrive, SharePoint or other synchronised folder.
setlocal
cd /d "%~dp0.."
set /p "DATA=Full path of the data folder (for example D:\RelayHubData): "
if "%DATA%"=="" exit /b 0
if exist "RelayEmployeeHub.exe" (
  "RelayEmployeeHub.exe" --save-data-dir "%DATA%"
) else (
  ".venv\Scripts\python.exe" "launcher\run_hub.py" --save-data-dir "%DATA%"
)
pause
