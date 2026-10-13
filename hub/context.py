"""Application context: paths, startup checks, default settings and shared services."""
from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import APP_VERSION, audit
from .config import Paths, sync_folder_warning
from .db import connect, get_setting, migrate, set_setting, tx, utcnow
from .fields import DEFAULT_LOCATIONS
from .security import LoginThrottle

log = logging.getLogger("hub")

DEFAULT_SETTINGS: dict[str, Any] = {
    "locations": DEFAULT_LOCATIONS,
    "timezone": "Asia/Kolkata",
    "date_order": "DMY",
    "session_idle_minutes": 60,
    "session_absolute_hours": 12,
    "network": {
        "lan_enabled": False,
        "port": 8765,
        "allowed_networks": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
    },
    "limits": {
        "max_upload_mb": 300,
        "max_image_mb": 25,
        "max_image_pixels": 60_000_000,
        "max_zip_entries": 3000,
        "max_zip_total_mb": 1500,
        "max_zip_ratio": 200,
        "max_pdf_pages": 300,
        "max_import_rows": 50_000,
    },
    "backup": {
        "destination": "",
        "daily_enabled": True,
        "daily_time": "13:00",
        "retention": 30,
        "include_exports": True,
    },
    "import_defaults": {"email_fallback": False},
    "ocr": {"tesseract_path": "", "lang": "eng", "psm": 6},
}

IT_BODY = (
    "Hello,\n\nPlease find attached the {task_name} package for {period_label}.\n\n"
    "{location_summary}\n\n{omission_summary}\n\nRegards,\n{sender_name}"
)
HR_BODY = (
    "Hello,\n\nPlease find attached the monthly headshots for {period_label}, named by RHC ID.\n\n"
    "{location_summary}\n\n{omission_summary}\n\nRegards,\n{sender_name}"
)


def default_tasks(locations: list[str]) -> list[dict]:
    return [
        {
            "task_key": "it_headshots",
            "kind": "IT",
            "name": "IT headshots",
            "config": {
                "schedule": {"type": "interval", "interval_days": 14, "anchor_date": None, "time": "10:00"},
                "inclusion": None,
                "naming_template": "{original_name}",
                "output_format": "original",
                "archive_name_template": "{location}_IT_Headshots_{period_date}.zip",
                "locations": list(locations),
                "email": {
                    "sender": "",
                    "to": [],
                    "cc": [],
                    "subject_template": "IT headshots {period_label}: {location_list}",
                    "body_template": IT_BODY,
                    "include_reference": True,
                },
                "attachment_limit_mb": 18,
                "include_manifest_in_zip": False,
                "catch_up_hours": 48,
            },
        },
        {
            "task_key": "hr_headshots",
            "kind": "HR",
            "name": "HR headshots",
            "config": {
                "schedule": {"type": "monthly", "day_of_month": 1, "time": "10:00"},
                "inclusion": None,
                "naming_template": "{rhc_id}",
                "output_format": "jpg",
                "archive_name_template": "{location}_HR_Headshots_{period_month}.zip",
                "locations": list(locations),
                "email": {
                    "sender": "",
                    "to": [],
                    "cc": [],
                    "subject_template": "HR headshots {period_label}: {location_list}",
                    "body_template": HR_BODY,
                    "include_reference": True,
                },
                "attachment_limit_mb": 18,
                "include_manifest_in_zip": False,
                "catch_up_hours": 48,
            },
        },
    ]


class InstanceLockError(RuntimeError):
    pass


class AppContext:
    def __init__(self, data_dir: Path, outlook_mode: str | None = None, testing: bool = False):
        self.paths = Paths(Path(data_dir))
        self.testing = testing
        self.forced_outlook_mode = outlook_mode
        self.instance_id = uuid.uuid4().hex
        self.hostname = socket.gethostname()
        self.throttle = LoginThrottle()
        self.maintenance = False
        self.maintenance_reason = ""
        self._active_requests = 0
        self._req_lock = threading.Condition()
        self.dispatch_lock = threading.Lock()
        self.work_lock = threading.RLock()  # held by scheduler ticks; a restore waits for it
        self.scheduler = None
        self.video = None
        self.outlook = None
        self.startup_warnings: list[str] = []
        self.transfer_detected = False
        self.migration_info: dict | None = None
        self.bound_host = "127.0.0.1"
        self.bound_port = 8765

    # ---------------------------------------------------------- startup
    def initialise(self) -> None:
        self.paths.ensure()
        warn = sync_folder_warning(self.paths.data)
        if warn:
            self.startup_warnings.append(warn)
        self.acquire_instance_lock()
        self.migration_info = migrate(self.paths.db, self.paths.backups)
        with self.db() as conn:
            with tx(conn):
                self._seed(conn)
                self._detect_transfer(conn)
        from .services.outlook import build_adapter

        self.outlook = build_adapter(self)

    def _seed(self, conn: sqlite3.Connection) -> None:
        for key, value in DEFAULT_SETTINGS.items():
            cur = get_setting(conn, key)
            if cur is None:
                set_setting(conn, key, value, "system")
            elif isinstance(value, dict) and isinstance(cur, dict):
                merged = {**value, **cur}
                if merged != cur:
                    set_setting(conn, key, merged, "system")
        if get_setting(conn, "outlook_mode") is None:
            set_setting(conn, "outlook_mode", "classic" if os.name == "nt" else "dryrun", "system")
        locations = get_setting(conn, "locations", DEFAULT_LOCATIONS)
        now = utcnow()
        for t in default_tasks(locations):
            exists = conn.execute("SELECT 1 FROM task_defs WHERE task_key = ?", (t["task_key"],)).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO task_defs(task_key, kind, name, enabled, config, created_at, updated_at, updated_by) "
                    "VALUES (?,?,?,0,?,?,?, 'system')",
                    (t["task_key"], t["kind"], t["name"], json.dumps(t["config"]), now, now),
                )
        if get_setting(conn, "installation") is None:
            set_setting(
                conn,
                "installation",
                {"id": uuid.uuid4().hex, "host": self.hostname, "data_dir": str(self.paths.data), "created_at": now},
                "system",
            )

    def _detect_transfer(self, conn: sqlite3.Connection) -> None:
        inst = get_setting(conn, "installation") or {}
        moved = inst.get("host") != self.hostname or inst.get("data_dir") != str(self.paths.data)
        if moved:
            self.transfer_detected = True
            reason = (
                f"The data folder was moved or opened on a different computer "
                f"(was {inst.get('host')} at {inst.get('data_dir')}). Automatic sending is disabled until an "
                "administrator re-checks the sender, recipients and task ownership."
            )
            disable_auto_send(conn, reason)
            inst.update({"host": self.hostname, "data_dir": str(self.paths.data), "moved_at": utcnow()})
            set_setting(conn, "installation", inst, "system")
            audit.log(conn, "system", "installation.transfer_detected", "installation", inst.get("id"), {"reason": reason})
            self.startup_warnings.append(reason)

    # ---------------------------------------------------------- instance lock
    def acquire_instance_lock(self) -> None:
        lf = self.paths.lock_file
        if lf.exists():
            try:
                info = json.loads(lf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = {}
            age = time.time() - float(info.get("heartbeat", 0))
            same = info.get("instance") == self.instance_id
            if not same and age < 90:
                pid = info.get("pid")
                alive = True
                if info.get("host") == self.hostname and pid:
                    alive = _pid_alive(int(pid))
                if alive:
                    raise InstanceLockError(
                        f"Another copy of Relay Employee Hub is already using this data folder "
                        f"(computer {info.get('host')}, process {pid}). Only one copy may run against a data folder. "
                        "Close the other copy, or wait two minutes if it was closed unexpectedly."
                    )
        self.heartbeat()

    def heartbeat(self) -> None:
        info = {
            "instance": self.instance_id,
            "pid": os.getpid(),
            "host": self.hostname,
            "heartbeat": time.time(),
            "version": APP_VERSION,
        }
        tmp = self.paths.lock_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(info), encoding="utf-8")
        os.replace(tmp, self.paths.lock_file)

    def release_instance_lock(self) -> None:
        try:
            info = json.loads(self.paths.lock_file.read_text(encoding="utf-8"))
            if info.get("instance") == self.instance_id:
                self.paths.lock_file.unlink()
        except (OSError, ValueError):
            pass

    # ---------------------------------------------------------- db + maintenance
    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.paths.db)
        try:
            yield conn
        finally:
            conn.close()

    def request_started(self) -> None:
        with self._req_lock:
            self._active_requests += 1

    def request_finished(self) -> None:
        with self._req_lock:
            self._active_requests -= 1
            self._req_lock.notify_all()

    def wait_for_quiet(self, own_requests: int = 1, timeout: float = 30) -> bool:
        deadline = time.time() + timeout
        with self._req_lock:
            while self._active_requests > own_requests:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self._req_lock.wait(remaining)
        return True


def disable_auto_send(conn: sqlite3.Connection, reason: str) -> None:
    conn.execute(
        "UPDATE task_defs SET auto_send_enabled = 0, needs_revalidation = 1, revalidation_reason = ?, updated_at = ?",
        (reason, utcnow()),
    )


def _pid_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
