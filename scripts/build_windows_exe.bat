@echo off
rem ---------------------------------------------------------------------------
rem Builds the packaged Windows application: dist\RelayEmployeeHub\RelayEmployeeHub.exe
rem Run on a Windows 10/11 64-bit computer with Python 3.12 or 3.13 installed.
rem Everything is installed offline from wheelhouse\. The built folder runs offline and
rem does not need Python on the target computer.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0.."
set "PYEXE="
for %%V in (3.13 3.12) do (
  if not defined PYEXE ( py -%%V -c "import sys" >nul 2>&1 && set "PYEXE=py -%%V" && set "PYVER=%%V" )
)
if not defined PYEXE ( echo Python 3.12 or 3.13 is required to build. & pause & exit /b 1 )
set "WHEELS=wheelhouse\py%PYVER:.=%"

if not exist ".buildenv\Scripts\python.exe" %PYEXE% -m venv .buildenv
".buildenv\Scripts\python.exe" -m pip install --no-index --find-links "%WHEELS%" -r requirements.txt -r requirements-build.txt || ( pause & exit /b 1 )

".buildenv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\relay_hub.spec || ( pause & exit /b 1 )

rem Copy documentation, licences, samples and helper scripts next to the executable.
set "OUT=dist\RelayEmployeeHub"
copy /y README.md "%OUT%\" >nul
copy /y THIRD_PARTY_LICENSES.md "%OUT%\" >nul
xcopy /e /i /y docs "%OUT%\docs" >nul
xcopy /e /i /y samples "%OUT%\samples" >nul
xcopy /e /i /y licenses "%OUT%\licenses" >nul
copy /y scripts\create_shortcut.ps1 "%OUT%\" >nul
copy /y scripts\logon_start.ps1 "%OUT%\" >nul
copy /y scripts\stop_hub.bat "%OUT%\Stop Relay Employee Hub.bat" >nul
copy /y scripts\set_data_folder.bat "%OUT%\Choose data folder.bat" >nul
".buildenv\Scripts\python.exe" scripts\collect_licenses.py "%OUT%\licenses"

rem Quick self-check of the packaged program (starts, answers, and stops).
"%OUT%\RelayEmployeeHub.exe" --help >nul 2>&1
echo.
echo Build complete: %OUT%\RelayEmployeeHub.exe
echo Copy the whole %OUT% folder to the operator's computer (for example C:\Program Files\RelayEmployeeHub
echo or C:\RelayEmployeeHub), then run create_shortcut.ps1 from that folder to add a desktop shortcut.
pause
