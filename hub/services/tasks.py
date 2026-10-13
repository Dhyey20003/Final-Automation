"""Recurring task definitions and schedule calculation.

IT task:  every N days (default 14) from an anchor date, at a set local time.
HR task:  monthly on a chosen day at a set local time. If the day does not
          exist in a month (for example the 31st in September), the last day
          of that month is used.
Times are in the configured timezone (default Asia/Kolkata).
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, time, timedelta
from typing import Any

from .. import audit
from ..db import get_setting, jdump, utcnow
from ..errors import Conflict, HubError, NotFound, ValidationFailed
from ..permissions import CurrentUser
from ..security import valid_email
from ..timeutil import clamp_day, parse_iso_dt, to_utc_iso, zone

INCLUSIONS = {"all_active": "All active employees", "changed": "Only new or changed headshots since the last completed submission"}
OUTPUT_FORMATS = {"original": "Keep original format", "jpg": "Convert to JPG", "png": "Convert to PNG"}
NAME_TOKENS = {"original_name", "original_stem", "rhc_id", "full_name", "first_name", "last_name", "location", "designation"}
ARCHIVE_TOKENS = {"location", "period_date", "period_month", "task", "kind"}
EMAIL_TOKENS = {"task_name", "period_label", "period_key", "location_list", "location_summary", "omission_summary",
                "sender_name", "attachment_names", "total_photos", "generated_date"}


def load_task(conn: sqlite3.Connection, task_id: int) -> dict:
    r = conn.execute("SELECT * FROM task_defs WHERE id = ?", (task_id,)).fetchone()
    if r is None:
        raise NotFound("Task not found.")
    t = dict(r)
    t["config"] = json.loads(t["config"])
    return t


def list_tasks(conn) -> list[dict]:
    return [load_task(conn, r["id"]) for r in conn.execute("SELECT id FROM task_defs ORDER BY id").fetchall()]


def _tokens(template: str) -> set[str]:
    return set(re.findall(r"\{([a-zA-Z_]+)\}", template or ""))


def validate_config(cfg: dict, locations: list[str]) -> dict[str, str]:
    errors = {}
    sch = cfg.get("schedule") or {}
    if sch.get("type") == "interval":
        try:
            n = int(sch.get("interval_days") or 0)
            if n < 1 or n > 365:
                raise ValueError
        except (TypeError, ValueError):
            errors["schedule.interval_days"] = "Interval must be between 1 and 365 days."
        if sch.get("anchor_date"):
            try:
                date.fromisoformat(sch["anchor_date"])
            except ValueError:
                errors["schedule.anchor_date"] = "Anchor date must be a valid date."
    elif sch.get("type") == "monthly":
        try:
            d = int(sch.get("day_of_month") or 0)
            if d < 1 or d > 31:
                raise ValueError
        except (TypeError, ValueError):
            errors["schedule.day_of_month"] = "Day of month must be between 1 and 31."
    else:
        errors["schedule.type"] = "Unknown schedule type."
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", sch.get("time") or ""):
        errors["schedule.time"] = "Time must be HH:MM (24-hour)."
    if cfg.get("inclusion") not in (None, *INCLUSIONS):
        errors["inclusion"] = "Choose which employees to include."
    if cfg.get("output_format") not in OUTPUT_FORMATS:
        errors["output_format"] = "Choose an output format."
    bad = _tokens(cfg.get("naming_template", "")) - NAME_TOKENS
    if bad or not cfg.get("naming_template"):
        errors["naming_template"] = "Unknown placeholders: " + ", ".join(sorted(bad)) if bad else "Enter a naming template."
    bad = _tokens(cfg.get("archive_name_template", "")) - ARCHIVE_TOKENS
    if bad or "{location}" not in (cfg.get("archive_name_template") or ""):
        errors["archive_name_template"] = "The archive name must include {location} and only known placeholders."
    locs = cfg.get("locations") or []
    if not locs or any(l not in locations for l in locs):
        errors["locations"] = "Choose one or more valid locations."
    em = cfg.get("email") or {}
    for key in ("to", "cc"):
        for addr in em.get(key) or []:
            if not valid_email(addr):
                errors[f"email.{key}"] = f"'{addr}' is not a valid email address."
    if em.get("sender") and not valid_email(em["sender"]):
        errors["email.sender"] = "Sender must be an email address of an account in Outlook."
    for key in ("subject_template", "body_template"):
        bad = _tokens(em.get(key, "")) - EMAIL_TOKENS
        if bad:
            errors[f"email.{key}"] = "Unknown placeholders: " + ", ".join(sorted(bad))
    try:
        lim = float(cfg.get("attachment_limit_mb", 18))
        if lim <= 0 or lim > 150:
            raise ValueError
    except (TypeError, ValueError):
        errors["attachment_limit_mb"] = "Attachment limit must be between 1 and 150 MB."
    return errors


def update_task(conn, user: CurrentUser, task_id: int, expected_version: int, patch: dict) -> dict:
    t = load_task(conn, task_id)
    if t["version"] != expected_version:
        raise Conflict("Another administrator changed this task. Reload before saving.")
    cfg = json.loads(json.dumps(t["config"]))
    for k, v in (patch.get("config") or {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    em = cfg.get("email") or {}
    for key in ("to", "cc"):
        if isinstance(em.get(key), str):
            em[key] = [a.strip() for a in re.split(r"[;,\s]+", em[key]) if a.strip()]
    locations = get_setting(conn, "locations", [])
    errors = validate_config(cfg, locations)
    if errors:
        raise ValidationFailed("Some task settings are not valid.", details={"fields": errors})
    name = (patch.get("name") or t["name"]).strip()
    old_em = t["config"].get("email") or {}
    recipients_changed = any((old_em.get(k) or "") != (em.get(k) or "") for k in ("sender", "to", "cc"))
    auto = t["auto_send_enabled"]
    reason = t["revalidation_reason"]
    if recipients_changed and auto:
        auto = 0
        reason = "Sender or recipients changed; automatic sending must be enabled again."
    conn.execute("UPDATE task_defs SET name=?, config=?, auto_send_enabled=?, revalidation_reason=?, version=version+1, "
                 "updated_at=?, updated_by=? WHERE id=?", (name, jdump(cfg), auto, reason, utcnow(), user.email, task_id))
    audit.log(conn, user.email, "task.configure", "task", task_id,
              {"changes": patch, "auto_send_disabled": bool(t["auto_send_enabled"] and not auto)}, user.ip)
    return load_task(conn, task_id)


def set_enabled(conn, user: CurrentUser, task_id: int, enabled: bool) -> dict:
    t = load_task(conn, task_id)
    cfg = t["config"]
    if enabled:
        problems = []
        if cfg.get("inclusion") not in INCLUSIONS:
            problems.append("Choose and save the inclusion setting (all active employees, or only new/changed headshots).")
        if cfg["schedule"]["type"] == "interval" and not cfg["schedule"].get("anchor_date"):
            problems.append("Choose the anchor date for the 14-day schedule.")
        if problems:
            raise ValidationFailed(" ".join(problems))
        cfg["schedule"]["enabled_from"] = today_str(conn)
    conn.execute("UPDATE task_defs SET enabled=?, config=?, version=version+1, updated_at=?, updated_by=? WHERE id=?",
                 (1 if enabled else 0, jdump(cfg), utcnow(), user.email, task_id))
    if not enabled:
        conn.execute("UPDATE task_defs SET auto_send_enabled = 0 WHERE id = ?", (task_id,))
    audit.log(conn, user.email, "task.enable" if enabled else "task.disable", "task", task_id, None, user.ip)
    return load_task(conn, task_id)


def set_auto_send(conn, user: CurrentUser, task_id: int, enabled: bool, confirm: bool, outlook_check: dict | None,
                  outlook_mode: str) -> dict:
    t = load_task(conn, task_id)
    em = t["config"].get("email") or {}
    if enabled:
        problems = []
        if not t["enabled"]:
            problems.append("Turn on the task schedule first.")
        if not em.get("to"):
            problems.append("Add at least one To recipient. Recipients are never filled in automatically.")
        if outlook_mode == "classic":
            if not em.get("sender"):
                problems.append("Choose the sender account.")
            if not outlook_check or not outlook_check.get("available"):
                problems.append("Classic Outlook is not available on this computer: " + (outlook_check or {}).get("message", ""))
            elif em.get("sender") and em["sender"].lower() not in [a["smtp"].lower() for a in outlook_check.get("accounts", []) if a.get("smtp")]:
                problems.append(f"The sender {em['sender']} is not an account in the current Outlook profile.")
        elif outlook_mode == "manual":
            problems.append("Automatic sending is not possible in manual mode (no classic Outlook).")
        if not confirm:
            problems.append("Confirm that emails may be sent automatically to the listed recipients.")
        if problems:
            raise ValidationFailed(" ".join(p for p in problems if p))
        conn.execute("UPDATE task_defs SET auto_send_enabled=1, auto_send_enabled_by=?, auto_send_enabled_at=?, needs_revalidation=0, "
                     "revalidation_reason=NULL, version=version+1, updated_at=?, updated_by=? WHERE id=?",
                     (user.email, utcnow(), utcnow(), user.email, task_id))
    else:
        conn.execute("UPDATE task_defs SET auto_send_enabled=0, version=version+1, updated_at=?, updated_by=? WHERE id=?",
                     (utcnow(), user.email, task_id))
    audit.log(conn, user.email, "task.auto_send_on" if enabled else "task.auto_send_off", "task", task_id,
              {"sender": em.get("sender"), "to": em.get("to"), "cc": em.get("cc"), "mode": outlook_mode}, user.ip)
    return load_task(conn, task_id)


def revalidate(conn, user: CurrentUser, task_id: int) -> dict:
    conn.execute("UPDATE task_defs SET needs_revalidation=0, revalidation_reason=NULL, version=version+1, updated_at=?, updated_by=? "
                 "WHERE id=?", (utcnow(), user.email, task_id))
    audit.log(conn, user.email, "task.revalidated", "task", task_id, None, user.ip)
    return load_task(conn, task_id)


# ---------------------------------------------------------------- schedule

def tzname(conn) -> str:
    return get_setting(conn, "timezone", "Asia/Kolkata")


def today_str(conn) -> str:
    return datetime.now(zone(tzname(conn))).date().isoformat()


def _at(d: date, hhmm: str, tz) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(d, time(h, m), tzinfo=tz)


def period_for_date(cfg: dict, d: date) -> tuple[str, str]:
    if cfg["schedule"]["type"] == "interval":
        return d.isoformat(), d.strftime("%d %b %Y")
    return d.strftime("%Y-%m"), d.strftime("%B %Y")


def iter_periods(cfg: dict, tz, start: date, end_dt: datetime, include_next: int = 0):
    """Yield (period_key, label, due_datetime_local) for periods due from ``start`` up to ``end_dt``,
    then ``include_next`` future periods."""
    sch = cfg["schedule"]
    extra = 0
    if sch["type"] == "interval":
        anchor = date.fromisoformat(sch["anchor_date"])
        step = int(sch.get("interval_days") or 14)
        if start > anchor:
            k = (start - anchor).days // step
            if anchor + timedelta(days=k * step) < start:
                k += 1
        else:
            k = 0
        for _ in range(5000):
            d = anchor + timedelta(days=k * step)
            due = _at(d, sch["time"], tz)
            if due > end_dt:
                if extra >= include_next:
                    return
                extra += 1
            key, label = period_for_date(cfg, d)
            yield key, label, due
            k += 1
    else:
        dom = int(sch.get("day_of_month") or 1)
        y, m = start.year, start.month
        for _ in range(1200):
            d = clamp_day(y, m, dom)
            due = _at(d, sch["time"], tz)
            if d >= start:
                if due > end_dt:
                    if extra >= include_next:
                        return
                    extra += 1
                key, label = period_for_date(cfg, d)
                yield key, label, due
            m += 1
            if m > 12:
                y, m = y + 1, 1


def next_due(conn, task: dict) -> dict | None:
    cfg = task["config"]
    if cfg["schedule"]["type"] == "interval" and not cfg["schedule"].get("anchor_date"):
        return None
    tz = zone(tzname(conn))
    now = datetime.now(tz)
    for key, label, due in iter_periods(cfg, tz, now.date() - timedelta(days=1), now, include_next=1):
        if due > now:
            return {"period_key": key, "label": label, "due_at": to_utc_iso(due), "due_local": due.isoformat()}
    return None


def ensure_period(conn, task: dict, key: str, label: str, due_utc: str, backlog: bool = False) -> dict:
    now = utcnow()
    conn.execute(
        "INSERT INTO task_periods(task_id, period_key, period_label, due_at, status, backlog, created_at, updated_at) "
        "VALUES (?,?,?,?, 'Due', ?, ?, ?) ON CONFLICT(task_id, period_key) DO NOTHING",
        (task["id"], key, label, due_utc, 1 if backlog else 0, now, now))
    return dict(conn.execute("SELECT * FROM task_periods WHERE task_id = ? AND period_key = ?", (task["id"], key)).fetchone())


def period_row(conn, period_id: int) -> dict:
    r = conn.execute("SELECT * FROM task_periods WHERE id = ?", (period_id,)).fetchone()
    if r is None:
        raise NotFound("Task period not found.")
    return dict(r)


def set_period_status(conn, period_id: int, status: str, detail: Any = None, actor: str | None = None) -> None:
    now = utcnow()
    completed = status in ("Confirmed in Sent Items", "Manually Completed")
    conn.execute("UPDATE task_periods SET status=?, status_detail=?, updated_at=?, completed_at=CASE WHEN ? THEN ? ELSE completed_at END, "
                 "completed_by=CASE WHEN ? THEN ? ELSE completed_by END WHERE id=?",
                 (status, jdump(detail) if detail is not None and not isinstance(detail, str) else detail, now,
                  completed, now, completed, actor, period_id))


def period_for_manual(conn, task: dict, period_key: str | None) -> dict:
    """Create (or fetch) a period for manual preparation: the given key, or the current/next period."""
    cfg = task["config"]
    if cfg["schedule"]["type"] == "interval" and not cfg["schedule"].get("anchor_date"):
        raise ValidationFailed("Set the anchor date for this task first.")
    tz = zone(tzname(conn))
    now = datetime.now(tz)
    if period_key:
        row = conn.execute("SELECT * FROM task_periods WHERE task_id = ? AND period_key = ?", (task["id"], period_key)).fetchone()
        if row:
            return dict(row)
        for key, label, due in iter_periods(cfg, tz, now.date() - timedelta(days=400), now, include_next=3):
            if key == period_key:
                return ensure_period(conn, task, key, label, to_utc_iso(due))
        raise ValidationFailed("That period is not part of this task's schedule.")
    candidates = list(iter_periods(cfg, tz, now.date() - timedelta(days=40), now, include_next=1))
    past = [c for c in candidates if c[2] <= now]
    chosen = past[-1] if past else candidates[-1]
    existing = conn.execute("SELECT * FROM task_periods WHERE task_id = ? AND period_key = ?", (task["id"], chosen[0])).fetchone()
    if existing and existing["status"] in ("Confirmed in Sent Items", "Manually Completed") and candidates[-1][2] > now:
        chosen = candidates[-1]
    return ensure_period(conn, task, chosen[0], chosen[1], to_utc_iso(chosen[2]))


def local_display(conn, iso_utc: str | None) -> str | None:
    if not iso_utc:
        return None
    return parse_iso_dt(iso_utc).astimezone(zone(tzname(conn))).strftime("%d %b %Y %H:%M")
