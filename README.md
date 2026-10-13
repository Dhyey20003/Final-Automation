# Relay Employee Hub

A local Windows application for Relay Human Cloud that keeps employee records and photos, imports HR spreadsheets, and prepares and sends the recurring IT and HR headshot packages through classic Outlook.

* Python + FastAPI + SQLite, with a browser interface whose files are all bundled locally (no CDNs, no cloud, no telemetry, no paid services).
* Runs on one designated Windows computer. Localhost only by default; office network access can be switched on by an administrator.
* All writable data lives in one data folder, separate from the program files.

## Download contents

The delivery is split into three ZIP files because of an upload size limit:

* `RelayEmployeeHub_v1.0.0_app.zip`: the application (source, scripts, docs, samples, tests).
* `RelayEmployeeHub_wheelhouse_py313.zip` and `RelayEmployeeHub_wheelhouse_py312.zip`: the pinned Windows components for offline installation.

Extract the app ZIP, then extract the wheelhouse ZIP for your Python version **into the same folder**, so you get `RelayEmployeeHub\wheelhouse\py313\...` (or `py312`). Only one of the two wheelhouse ZIPs is needed.

## Quick start on Windows

There are two ways to install. Both run fully offline once installed.

### A. Packaged application (recommended for the operator)

1. On any Windows 10/11 64-bit computer with Python 3.12 or 3.13, run `scripts\build_windows_exe.bat`. It installs the build tools from the bundled `wheelhouse` folder (no internet) and produces `dist\RelayEmployeeHub\`.
2. Copy the `dist\RelayEmployeeHub` folder to the operator's computer (for example `C:\RelayEmployeeHub`). That computer does not need Python.
3. Right-click `create_shortcut.ps1` in that folder > Run with PowerShell to add a desktop shortcut.
4. Double-click the shortcut. The interface opens in the default browser at `http://127.0.0.1:8765/`. Create the first administrator account.

### B. Run from source

1. Install Python 3.12 or 3.13 (64-bit) from python.org once. This is the only download.
2. Double-click `Setup Relay Employee Hub.bat`. It creates a private environment and installs every component from `wheelhouse\` without internet access, then offers to create a desktop shortcut.
3. Double-click `Start Relay Employee Hub.bat` (or the shortcut).

### Stopping, data folder, logon start

* Stop: Settings > About > Stop application, or `scripts\stop_hub.bat` (packaged: `Stop Relay Employee Hub.bat`).
* Data folder: by default `%LOCALAPPDATA%\RelayEmployeeHub\Data`. To use another local folder, stop the app and run `scripts\set_data_folder.bat` (packaged: `Choose data folder.bat`). The app refuses to start on a OneDrive/SharePoint/Dropbox path.
* Start automatically at Windows sign-in (background mode, no browser window): `powershell -ExecutionPolicy Bypass -File scripts\logon_start.ps1 -Enable`.

## Data folder layout

```
<data folder>\
  employees.db        SQLite database (WAL mode)
  images\             original photos, stored by permanent employee ID and photo version
  thumbnails\         generated previews
  exports\            immutable package snapshots, ready-to-send folders, dry-run outbox
  import_staging\     uploaded spreadsheets and photo batches awaiting review
  backups\            default backup destination (never included inside backups)
  logs\               application log
  tmp\                temporary work files
```

## Development (any OS)

```
pip install -r requirements-dev.txt
python launcher/run_hub.py --data-dir ./devdata --outlook-mode dryrun
python -m pytest tests            # 79 tests; the browser test needs `pip install playwright` + Chromium
python scripts/make_samples.py    # regenerates the synthetic sample files
```

## Project structure

```
hub/                 application package
  main.py            FastAPI app factory, network guard, security headers
  context.py         data folder, startup checks, instance lock, transfer detection, defaults
  db.py              schema, migrations (with pre-migration backup), transactions
  permissions.py     roles, actions, location scope, field restrictions
  services/          employees, imports, photos, pdfproc, packages, tasks, dispatch, outlook, scheduler, backups, users, sheets
  routers/           HTTP endpoints (every one checks permissions on the server)
  static/            interface (HTML, CSS, JavaScript modules, icons, sample templates), no external assets
launcher/run_hub.py  double-click entry point (starts server, opens browser, single instance)
scripts/             Windows setup/build/shortcut/logon/stop scripts, sample generator, licence collector
packaging/           PyInstaller spec and icon
wheelhouse/          pinned Windows wheels for Python 3.12 and 3.13 (offline installation)
samples/             synthetic import files, placeholder headshots, ZIP and PDF
tests/               automated tests
docs/                operator guide, Windows manual checklist, test results, assumptions and limitations
```

## Documentation

* [Operator guide](docs/OPERATOR_GUIDE.md)
* [Windows manual checklist (Outlook and packaged app)](docs/WINDOWS_MANUAL_CHECKLIST.md)
* [Test results](docs/TEST_RESULTS.md)
* [Assumptions, design notes and limitations](docs/ASSUMPTIONS_AND_LIMITATIONS.md)
* [Third-party licences](THIRD_PARTY_LICENSES.md)
