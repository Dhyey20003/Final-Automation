@echo off
rem ---------------------------------------------------------------------------
rem Relay Employee Hub: first-time setup from source (Windows 10/11, 64-bit).
rem Requires Python 3.12 or 3.13 (64-bit) from python.org, installed once.
rem All other components are installed OFFLINE from the bundled wheelhouse folder.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "PYEXE="
for %%V in (3.13 3.12) do (
  if not defined PYEXE (
    py -%%V -c "import sys" >nul 2>&1 && set "PYEXE=py -%%V" && set "PYVER=%%V"
  )
)
if not defined PYEXE (
  python -c "import sys; assert sys.version_info[:2] in ((3,12),(3,13))" >nul 2>&1 && (
    set "PYEXE=python"
    for /f %%i in ('python -c "import sys;print(f'{sys.version_info[0]}.{sys.version_info[1]}')"') do set "PYVER=%%i"
  )
)
if not defined PYEXE (
  echo.
  echo Python 3.12 or 3.13 ^(64-bit^) was not found.
  echo Install it once from https://www.python.org/downloads/windows/ ^(tick "Add python.exe to PATH"^),
  echo then run this setup again. No other downloads are needed.
  pause
  exit /b 1
)
set "WHEELS=wheelhouse\py%PYVER:.=%"
if not exist "%WHEELS%" (
  echo The folder %WHEELS% is missing. Copy the complete application folder and try again.
  pause
  exit /b 1
)

echo Using Python %PYVER%
if not exist ".venv\Scripts\python.exe" (
  echo Creating the private Python environment...
  %PYEXE% -m venv .venv || (echo Could not create the environment. & pause & exit /b 1)
)
echo Installing components from the local wheelhouse (no internet needed)...
".venv\Scripts\python.exe" -m pip install --no-index --find-links "%WHEELS%" -r requirements.txt || (
  echo Installation failed. See the messages above.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, openpyxl, PIL, pypdfium2, tzdata; print('Components OK')" || (pause & exit /b 1)

if not exist "Tools\ffmpeg" mkdir "Tools\ffmpeg"
echo.
echo For videos, copy ffmpeg.exe and ffprobe.exe into:  %CD%\Tools\ffmpeg
echo For screenshot imports, install Tesseract OCR (Settings ^> About shows what is found).
echo.
choice /M "Create a desktop shortcut for Relay Employee Hub"
if %errorlevel%==1 powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\create_shortcut.ps1" -Mode source

echo.
echo Setup finished. Double-click "Start Relay Employee Hub.bat" (or the desktop shortcut) to start.
pause
