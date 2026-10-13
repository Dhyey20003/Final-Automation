# Relay Employee Hub 2.0

A local Windows application for Relay Human Cloud that keeps employee records and photos, imports HR spreadsheets and screenshots, generates and publishes deployment posts, requests logos through classic Outlook, runs the interview Video Studio, and prepares and sends the recurring IT and HR headshot packages.

Version 2.0 folds the former Automation Hub, Post Studio and Video Studio launchers into this one application. See `START_HERE.txt` for setup and `docs/CHANGES.md` for what changed.

* Python + FastAPI + SQLite, with a browser interface whose files are all bundled locally (no CDNs, no cloud, no telemetry, no paid services).
* Runs on one designated Windows computer. Localhost only by default; office network access can be switched on by an administrator.
* All writable data lives in one data folder, separate from the program files.

## Download contents

`RelayEmployeeHub_v2.0.0.zip` (built with `python scripts/build_release_zip.py`) holds the complete program folder: source, launcher scripts, bundled automations (video builder, question cards, fonts, post template, deep-filter.exe), docs, samples, licences and the offline wheelhouse for Python 3.12 and 3.13. Extract it anywhere local and keep the folder structure.

`RelayEmployeeHub_v1.0.0_app.zip` in this repository is the untouched original 1.0 delivery, kept as a backup.

Not bundled (checked and reported in Settings > About): FFmpeg/FFprobe (`Tools\ffmpeg`), Tesseract OCR, classic Outlook, faster-whisper.

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
  images\             original photos by employee ID and version; images\clients\ holds client logos
  thumbnails\         generated previews
  exports\            package snapshots, ready-to-send folders, dry-run outbox, exports\posts\<project id>\ post pictures
  import_staging\     uploaded spreadsheets, photo batches, screenshots and email attachments awaiting review
  video\              Video Studio data: config.json, jobs.json (queue), batch\ (prepared clips), output\ (finished videos)
  backups\            default backup destination (never included inside backups)
  logs\               hub.log and video_studio.log
  tmp\                temporary work files
```

## Development (any OS)

```
pip install -r requirements-dev.txt
python launcher/run_hub.py --data-dir ./devdata --outlook-mode dryrun
python -m pytest tests            # 97 tests; the two browser tests need `pip install playwright` + Chromium
python scripts/video_smoke_test.py  # starts the Video Studio, confirms a tiny video, waits for Completed (needs ffmpeg)
python scripts/make_samples.py    # regenerates the synthetic sample files
```

## Project structure

```
hub/                 application package
  main.py            FastAPI app factory, network guard, security headers
  context.py         data folder, startup checks, instance lock, transfer detection, defaults
  db.py              schema, migrations (with pre-migration backup), transactions
  permissions.py     roles, actions, location scope, field restrictions
  services/          employees, imports, photos, pdfproc, packages, tasks, dispatch, outlook, scheduler, backups, users, sheets,
                     clients, posts, postrender, postmail, screenshots (OCR), legacy_import, videosvc, depcheck
  routers/           HTTP endpoints (every one checks permissions on the server)
  static/            interface (HTML, CSS, JavaScript modules, icons, sample templates), no external assets
automations/
  posts/             post template (internal_post_base.png) and the retired desktop Post Studio for reference
  video/             interview_studio_web.py (Video Studio), build_interview.py (renderer), config.json, assets (cards, fonts), tools (deep-filter.exe)
legacy/              the old dashboard and config files, kept for reference only
launcher/run_hub.py  double-click entry point (starts server and Video Studio service, opens browser, single instance)
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
