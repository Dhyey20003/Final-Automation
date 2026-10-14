"""Relay Employee Hub launcher (the program behind the desktop shortcut).

Double-click: starts the local server (if it is not already running) and opens the
interface in the default browser.

Options:
  --data-dir PATH       use this data folder (and remember it for next time with --remember)
  --remember            save --data-dir as the default data folder
  --background          start without opening the browser (used by the Windows logon start option)
  --no-browser          same as --background
  --stop                ask a running copy to shut down cleanly
  --outlook-mode MODE   override the email mode for this run (classic, manual, dryrun)
  --save-data-dir PATH  remember PATH as the data folder, then exit
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub import APP_NAME, APP_VERSION  # noqa: E402
from hub.config import Paths, resolve_data_dir, save_bootstrap, sync_folder_warning  # noqa: E402


def message(text: str, title: str = APP_NAME, error: bool = False) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 if error else 0x40)
            return
        except Exception:
            pass
    print(("ERROR: " if error else "") + text, file=sys.stderr)


def read_network(paths: Paths) -> dict:
    if not paths.db.exists():
        return {"lan_enabled": False, "port": 8765}
    import sqlite3

    try:
        c = sqlite3.connect(str(paths.db), timeout=5)
        try:
            row = c.execute("SELECT value FROM settings WHERE key = 'network'").fetchone()
        finally:
            c.close()
        return json.loads(row[0]) if row else {"lan_enabled": False, "port": 8765}
    except Exception:
        return {"lan_enabled": False, "port": 8765}


def running_state(port: int) -> dict | None:
    """The /api/auth/state of a copy already answering on this port, or None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/auth/state", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data if data.get("app") == APP_NAME else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def running_here(port: int) -> bool:
    return running_state(port) is not None


def stop_other_version(paths: Paths, port: int, old_version: str) -> bool:
    """An older copy is running on this data folder: ask it to stop and wait for the port."""
    ctl = paths.data / "hub.control"
    token = ctl.read_text(encoding="utf-8").strip() if ctl.exists() else ""
    if token:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/control/stop", method="POST", headers={"X-Control-Token": token})
        try:
            urllib.request.urlopen(req, timeout=5).read()
        except (urllib.error.URLError, OSError):
            pass
    for _ in range(60):
        if not running_here(port):
            time.sleep(1)
            return True
        time.sleep(0.5)
    return False


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name != "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def setup_logging(paths: Paths) -> None:
    logs = paths.data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(logs / "hub.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr and not getattr(sys, "frozen", False):
        root.addHandler(logging.StreamHandler())


def stop_running(paths: Paths, port: int) -> int:
    ctl = paths.data / "hub.control"
    if not ctl.exists():
        message("Relay Employee Hub does not appear to be running.")
        return 0
    token = ctl.read_text(encoding="utf-8").strip()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/control/stop", method="POST", headers={"X-Control-Token": token})
    try:
        urllib.request.urlopen(req, timeout=5).read()
        message("Relay Employee Hub is shutting down.")
    except (urllib.error.URLError, OSError):
        message("Relay Employee Hub did not respond. It may already be stopped.")
    return 0


def run_video_builder(argv: list[str]) -> int:
    """Packaged build only: the Video Studio starts the builder through this executable."""
    import runpy

    from hub.config import automations_dir

    script = automations_dir() / "video" / "build_interview.py"
    sys.argv = [str(script)] + argv
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) or exc.code is None else 1
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if raw and raw[0] == "--video-builder":
        return run_video_builder(raw[1:])
    ap = argparse.ArgumentParser(description=f"{APP_NAME} {APP_VERSION}")
    ap.add_argument("--data-dir")
    ap.add_argument("--remember", action="store_true")
    ap.add_argument("--background", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--allow-sync-folder", action="store_true")
    ap.add_argument("--outlook-mode", choices=["classic", "manual", "dryrun"])
    ap.add_argument("--save-data-dir", metavar="PATH", help="remember PATH as the data folder and exit")
    args = ap.parse_args(argv)

    if args.save_data_dir:
        target = Path(args.save_data_dir).expanduser().resolve()
        warn = sync_folder_warning(target)
        if warn and not args.allow_sync_folder:
            message(warn, error=True)
            return 2
        save_bootstrap(target)
        message(f"The data folder is now:\n{target}\n\nStart Relay Employee Hub to use it.")
        return 0
    data_dir = resolve_data_dir(args.data_dir)
    if args.data_dir and args.remember:
        save_bootstrap(data_dir)
    paths = Paths(data_dir)
    net = read_network(paths)
    port = int(net.get("port") or 8765)
    host = "0.0.0.0" if net.get("lan_enabled") else "127.0.0.1"
    url = f"http://127.0.0.1:{port}/"

    if args.stop:
        return stop_running(paths, port)

    warn = sync_folder_warning(data_dir)
    if warn and not args.allow_sync_folder:
        message(warn + "\n\nThe application will not start. Move the data folder or start with --data-dir.", error=True)
        return 2

    other = running_state(port)
    if other is not None:
        if other.get("version") == APP_VERSION:
            if not (args.background or args.no_browser):
                webbrowser.open(url)
            return 0
        # A different (older) version is still running. Stop it so this version takes over.
        if not stop_other_version(paths, port, other.get("version", "?")):
            message(f"Relay Employee Hub version {other.get('version', '?')} is still running on port {port} and could not be stopped "
                    f"automatically.\n\nClose it first: in that browser tab use Settings > About > Stop application, or run "
                    f"Stop Relay Employee Hub.bat in its old folder (or end pythonw.exe in Task Manager). Then start this version again.",
                    error=True)
            return 6
    if not port_free(host, port):
        message(f"Port {port} is already used by another program. Close it, or change the port in Settings "
                f"(or edit the network setting) and try again.", error=True)
        return 3

    paths.ensure()
    setup_logging(paths)
    log = logging.getLogger("hub.launcher")
    log.info("Starting %s %s with data folder %s on %s:%s", APP_NAME, APP_VERSION, data_dir, host, port)
    log.info("Video Studio is started automatically by the hub on port 8756 when FFmpeg is available.")

    import uvicorn

    from hub.context import InstanceLockError
    from hub.main import create_app

    try:
        app = create_app(data_dir, outlook_mode=args.outlook_mode)
    except InstanceLockError as exc:
        message(str(exc), error=True)
        return 4
    except Exception as exc:
        logging.getLogger("hub.launcher").exception("Startup failed")
        message(f"Relay Employee Hub could not start:\n\n{exc}\n\nSee the log in {data_dir / 'logs'}.", error=True)
        return 5

    ctx = app.state.ctx
    ctx.bound_host, ctx.bound_port = host, port
    control_token = secrets.token_urlsafe(24)
    (paths.data / "hub.control").write_text(control_token, encoding="utf-8")

    config = uvicorn.Config(app, host=host, port=port, log_config=None, access_log=False, lifespan="on",
                            timeout_graceful_shutdown=10)
    server = uvicorn.Server(config)
    ctx.server = server
    ctx.control_token = control_token

    if not (args.background or args.no_browser):
        def opener():
            for _ in range(100):
                if server.started:
                    webbrowser.open(url)
                    return
                time.sleep(0.2)

        threading.Thread(target=opener, daemon=True).start()
    try:
        server.run()
    finally:
        try:
            (paths.data / "hub.control").unlink()
        except OSError:
            pass
        log.info("Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
