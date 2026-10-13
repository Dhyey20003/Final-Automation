"""Background scheduler (runs inside the application process, in the operator's Windows session).

Every 30 seconds it:
* refreshes the data-folder lock heartbeat;
* creates task periods that have become due (restart-safe: unique task + period keys);
* prepares packages for due periods;
* sends automatically only when enabled, only for the latest period within the catch-up
  window, and never for missed (backlog) periods, which are flagged for review instead;
* reconciles submitted or uncertain sends with Outlook (never resends);
* runs the daily backup.

It needs the computer to be on and the application running in the operator's Windows
session. It is not a Windows service, because Outlook automation must not run unattended
as a service.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone

from ..db import jdump, tx, utcnow
from ..errors import HubError
from ..permissions import system_user
from ..timeutil import parse_iso_dt, to_utc_iso, zone
from . import backups
from . import dispatch as D
from . import packages as P
from . import tasks as T

log = logging.getLogger("hub.scheduler")


class Scheduler:
    def __init__(self, ctx, interval: float = 30.0):
        self.ctx = ctx
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_reconcile = 0.0
        self.last_tick: str | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="hub-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        time.sleep(3)
        while not self._stop.is_set():
            try:
                self.tick()
                self.last_error = None
            except Exception as exc:  # keep the loop alive; the error is shown in Settings
                log.exception("Scheduler tick failed")
                self.last_error = str(exc)
            self._stop.wait(self.interval)

    def _lease(self, conn) -> bool:
        now = datetime.now(timezone.utc)
        with tx(conn):
            row = conn.execute("SELECT owner, expires_at FROM worker_lease WHERE name = 'scheduler'").fetchone()
            if row and row["owner"] != self.ctx.instance_id and parse_iso_dt(row["expires_at"]) > now:
                return False
            exp = (now + timedelta(seconds=max(120, self.interval * 4))).replace(microsecond=0).isoformat()
            conn.execute("INSERT INTO worker_lease(name, owner, expires_at) VALUES ('scheduler', ?, ?) "
                         "ON CONFLICT(name) DO UPDATE SET owner = excluded.owner, expires_at = excluded.expires_at",
                         (self.ctx.instance_id, exp))
        return True

    def tick(self, now: datetime | None = None) -> dict:
        ctx = self.ctx
        ctx.heartbeat()
        if ctx.maintenance:
            return {"skipped": "maintenance"}
        report = {"created": 0, "prepared": [], "sent": [], "flagged": [], "reconciled": []}
        with ctx.work_lock:
            if ctx.maintenance:
                return {"skipped": "maintenance"}
            with ctx.db() as conn:
                if not self._lease(conn):
                    return {"skipped": "another worker holds the lease"}
                process_tasks(ctx, conn, now, report)
                if time.time() - self._last_reconcile > 120 or now is not None:
                    self._last_reconcile = time.time()
                    report["reconciled"] = D.reconcile(conn, ctx)
            if now is None:
                backups.maybe_daily_backup(ctx)
        self.last_tick = utcnow()
        return report


def process_tasks(ctx, conn, now: datetime | None, report: dict) -> None:
    tz = zone(T.tzname(conn))
    now_local = (now or datetime.now(tz)).astimezone(tz)
    sysuser = system_user()
    for task in T.list_tasks(conn):
        if not task["enabled"]:
            continue
        cfg = task["config"]
        if cfg["schedule"]["type"] == "interval" and not cfg["schedule"].get("anchor_date"):
            continue
        start = date.fromisoformat(cfg["schedule"].get("enabled_from") or now_local.date().isoformat())
        catch = timedelta(hours=float(cfg.get("catch_up_hours", 48)))
        with tx(conn):
            for key, label, due in T.iter_periods(cfg, tz, start, now_local):
                exists = conn.execute("SELECT 1 FROM task_periods WHERE task_id = ? AND period_key = ?", (task["id"], key)).fetchone()
                if not exists:
                    T.ensure_period(conn, task, key, label, to_utc_iso(due), backlog=(now_local - due) > catch)
                    report["created"] += 1
        periods = [dict(r) for r in conn.execute(
            "SELECT * FROM task_periods WHERE task_id = ? AND status IN ('Due', 'Ready') ORDER BY due_at", (task["id"],)).fetchall()]
        for p in periods:
            if parse_iso_dt(p["due_at"]) > now_local:
                continue
            if p["status"] == "Due":
                if p["backlog"]:
                    with tx(conn):
                        T.set_period_status(conn, p["id"], "Needs Attention", {
                            "message": "This period was missed while the application was not running. It is not sent automatically. "
                                       "Review it: prepare and send it manually, or mark it complete."})
                    report["flagged"].append(p["id"])
                    continue
                try:
                    res = P.prepare_period(conn, ctx, sysuser, p["id"])
                    report["prepared"].append({"period": p["id"], "status": res["status"]})
                    p["status"] = res["status"]
                except HubError as exc:
                    log.warning("Automatic preparation failed for period %s: %s", p["id"], exc.message)
                    continue
            if p["status"] == "Ready" and not p["backlog"] and task["auto_send_enabled"] and not task["needs_revalidation"]:
                attempted = conn.execute("SELECT 1 FROM dispatches WHERE period_id = ? AND mode IN ('auto','send','draft')",
                                         (p["id"],)).fetchone()
                if attempted:
                    # Never retry automatically after any earlier attempt or draft; the operator decides.
                    continue
                try:
                    d = D.start(conn, ctx, sysuser, p["id"], "auto")
                    report["sent"].append({"period": p["id"], "status": d["status"]})
                except HubError as exc:
                    with tx(conn):
                        T.set_period_status(conn, p["id"], "Needs Attention", {"message": f"Automatic sending did not start: {exc.message}"})
                    report["flagged"].append(p["id"])
