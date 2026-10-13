from __future__ import annotations

import ipaddress
import os
import re
import sqlite3

from fastapi import APIRouter, Body, Depends, Request

from .. import APP_VERSION, audit
from ..config import bootstrap_file, sync_folder_warning
from ..db import SCHEMA_VERSION, get_setting, jdump, jload, set_setting, tx, utcnow
from ..deps import get_conn, get_ctx, require, require_any
from ..errors import Forbidden, NotFound, ValidationFailed
from ..fields import DEFAULT_LOCATIONS, slugify_key
from ..permissions import ACTIONS, ADMIN_ONLY, ROLES, CurrentUser
from ..services import users as U
from ..timeutil import zone

router = APIRouter(prefix="/api", tags=["settings"])

FIELD_TYPES = {"text": "Text", "number": "Number", "date": "Date", "dropdown": "Dropdown", "yesno": "Yes / No"}


# ---------------------------------------------------------------- system

@router.get("/system")
def system(user: CurrentUser = Depends(require("employees.view")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    net = get_setting(conn, "network", {})
    info = {
        "version": APP_VERSION, "schema_version": SCHEMA_VERSION, "outlook_mode": get_setting(conn, "outlook_mode", "dryrun"),
        "warnings": list(ctx.startup_warnings), "lan_enabled": bool(net.get("lan_enabled")),
        "revalidation_needed": [dict(r) for r in conn.execute(
            "SELECT id, name, revalidation_reason FROM task_defs WHERE needs_revalidation = 1").fetchall()],
    }
    if user.is_admin:
        sched = ctx.scheduler
        info.update({
            "data_dir": str(ctx.paths.data), "bootstrap_file": str(bootstrap_file()), "host": ctx.hostname,
            "bound": f"{ctx.bound_host}:{ctx.bound_port}", "network": net,
            "scheduler": {"last_tick": getattr(sched, "last_tick", None), "last_error": getattr(sched, "last_error", None)},
            "migration": ctx.migration_info, "installation": get_setting(conn, "installation", {}),
            "counts": {k: conn.execute(q).fetchone()[0] for k, q in {
                "employees": "SELECT COUNT(*) FROM employees", "active": "SELECT COUNT(*) FROM employees WHERE employment_status='Active' "
                "AND record_scope='directory' AND archived=0", "photos": "SELECT COUNT(*) FROM photos",
                "users": "SELECT COUNT(*) FROM users"}.items()},
        })
    return info


@router.get("/system/dependencies")
def dependencies(user: CurrentUser = Depends(require("employees.view")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    from ..services.depcheck import check_all

    return check_all(ctx, conn)


# ---------------------------------------------------------------- general settings

@router.get("/settings/general")
def get_general(user: CurrentUser = Depends(require("settings.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    return {k: get_setting(conn, k) for k in ("locations", "timezone", "date_order", "session_idle_minutes",
                                               "session_absolute_hours", "location_aliases", "limits", "import_defaults")}


@router.put("/settings/general")
def put_general(body: dict = Body(...), user: CurrentUser = Depends(require("settings.manage")),
                conn: sqlite3.Connection = Depends(get_conn)):
    errors = {}
    if "locations" in body:
        locs = [str(l).strip() for l in body["locations"] if str(l).strip()]
        if not locs:
            errors["locations"] = "Keep at least one location."
        current = get_setting(conn, "locations", DEFAULT_LOCATIONS)
        removed = [l for l in current if l not in locs]
        for l in removed:
            n = conn.execute("SELECT COUNT(*) FROM employees WHERE location = ?", (l,)).fetchone()[0]
            if n:
                errors["locations"] = f"{l} is still used by {n} employee records and cannot be removed."
        body["locations"] = locs
    if "timezone" in body:
        try:
            zone(body["timezone"])
        except Exception:
            errors["timezone"] = "Unknown timezone."
    if "date_order" in body and body["date_order"] not in ("DMY", "MDY"):
        errors["date_order"] = "Choose DMY or MDY."
    for k, lo, hi in (("session_idle_minutes", 5, 720), ("session_absolute_hours", 1, 72)):
        if k in body:
            try:
                v = int(body[k])
                if v < lo or v > hi:
                    raise ValueError
                body[k] = v
            except (TypeError, ValueError):
                errors[k] = f"Must be between {lo} and {hi}."
    if "limits" in body:
        lim = get_setting(conn, "limits", {})
        for k, v in (body["limits"] or {}).items():
            if k in lim:
                try:
                    lim[k] = max(1, int(v))
                except (TypeError, ValueError):
                    errors[f"limits.{k}"] = "Must be a whole number."
        body["limits"] = lim
    if errors:
        raise ValidationFailed("Some settings are not valid.", details={"fields": errors})
    with tx(conn):
        for k in ("locations", "timezone", "date_order", "session_idle_minutes", "session_absolute_hours", "location_aliases",
                  "limits", "import_defaults"):
            if k in body:
                set_setting(conn, k, body[k], user.email)
        audit.log(conn, user.email, "settings.general", "settings", None, body, user.ip)
    return get_general(user, conn)


@router.get("/settings/network")
def get_network(user: CurrentUser = Depends(require("settings.manage")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    return {**(get_setting(conn, "network", {}) or {}), "currently_bound": f"{ctx.bound_host}:{ctx.bound_port}"}


@router.put("/settings/network")
def put_network(body: dict = Body(...), user: CurrentUser = Depends(require("settings.manage")),
                conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    net = get_setting(conn, "network", {}) or {}
    if "lan_enabled" in body:
        if body["lan_enabled"] and not body.get("confirm"):
            raise ValidationFailed("Confirm that you understand office network access uses plain HTTP on your local network.")
        net["lan_enabled"] = bool(body["lan_enabled"])
    if "port" in body:
        try:
            p = int(body["port"])
            if p < 1024 or p > 65535:
                raise ValueError
            net["port"] = p
        except (TypeError, ValueError):
            raise ValidationFailed("Port must be between 1024 and 65535.") from None
    if "allowed_networks" in body:
        nets = []
        for c in body["allowed_networks"] or []:
            try:
                n = ipaddress.ip_network(str(c).strip(), strict=False)
            except ValueError:
                raise ValidationFailed(f"'{c}' is not a valid network (for example 192.168.1.0/24).") from None
            if not n.is_private:
                raise ValidationFailed(f"{c} is not a private office network. Public access is not allowed.")
            nets.append(str(n))
        net["allowed_networks"] = nets
    with tx(conn):
        set_setting(conn, "network", net, user.email)
        audit.log(conn, user.email, "settings.network", "settings", None, net, user.ip)
    ctx._net_cache = None
    return {**net, "restart_required": True, "currently_bound": f"{ctx.bound_host}:{ctx.bound_port}"}


@router.get("/settings/outlook")
def get_outlook(user: CurrentUser = Depends(require("settings.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    return {"mode": get_setting(conn, "outlook_mode", "dryrun"), "dryrun_scenario": get_setting(conn, "dryrun_scenario", "success"),
            "is_windows": os.name == "nt"}


@router.put("/settings/outlook")
def put_outlook(body: dict = Body(...), user: CurrentUser = Depends(require("settings.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    mode = body.get("mode")
    if mode not in ("classic", "manual", "dryrun"):
        raise ValidationFailed("Choose classic Outlook, manual, or dry-run mode.")
    scen = body.get("dryrun_scenario", "success")
    if scen not in ("success", "fail_before_send", "uncertain", "stuck_in_outbox"):
        raise ValidationFailed("Unknown dry-run scenario.")
    with tx(conn):
        old = get_setting(conn, "outlook_mode", "dryrun")
        set_setting(conn, "outlook_mode", mode, user.email)
        set_setting(conn, "dryrun_scenario", scen, user.email)
        if old != mode:
            conn.execute("UPDATE task_defs SET auto_send_enabled = 0, revalidation_reason = ? WHERE auto_send_enabled = 1",
                         (f"Email mode changed from {old} to {mode}; enable automatic sending again.",))
        audit.log(conn, user.email, "settings.outlook", "settings", None, {"mode": mode, "scenario": scen}, user.ip)
    return get_outlook(user, conn)


@router.get("/settings/backup")
def get_backup(user: CurrentUser = Depends(require("backups.manage")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    b = get_setting(conn, "backup", {}) or {}
    return {**b, "default_destination": str(ctx.paths.backups)}


@router.put("/settings/backup")
def put_backup(body: dict = Body(...), user: CurrentUser = Depends(require("backups.manage")), conn: sqlite3.Connection = Depends(get_conn),
               ctx=Depends(get_ctx)):
    from pathlib import Path

    from ..services.backups import validate_destination

    b = get_setting(conn, "backup", {}) or {}
    if "destination" in body:
        dest = (body.get("destination") or "").strip()
        if dest:
            p = Path(dest)
            if not p.is_absolute():
                raise ValidationFailed("Enter a full folder path, for example E:\\RelayHubBackups.")
            validate_destination(ctx, p)
        b["destination"] = dest
    if "daily_enabled" in body:
        b["daily_enabled"] = bool(body["daily_enabled"])
    if "daily_time" in body:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", body["daily_time"] or ""):
            raise ValidationFailed("Daily backup time must be HH:MM.")
        b["daily_time"] = body["daily_time"]
    if "retention" in body:
        try:
            r = int(body["retention"])
            if r < 1 or r > 365:
                raise ValueError
            b["retention"] = r
        except (TypeError, ValueError):
            raise ValidationFailed("Keep between 1 and 365 daily backups.") from None
    if "include_exports" in body:
        b["include_exports"] = bool(body["include_exports"])
    with tx(conn):
        set_setting(conn, "backup", b, user.email)
        audit.log(conn, user.email, "settings.backup", "settings", None, b, user.ip)
    return get_backup(user, conn, ctx)


# ---------------------------------------------------------------- custom fields

@router.get("/fields")
def fields(user: CurrentUser = Depends(require_any("fields.manage", "users.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    rows = [dict(r) for r in conn.execute("SELECT * FROM field_defs WHERE is_custom = 1 ORDER BY sort_order, id")]
    for r in rows:
        r["options"] = jload(r["options"], [])
    return {"custom": rows, "types": FIELD_TYPES}


def _field_payload(body: dict) -> dict:
    label = (body.get("label") or "").strip()
    if not label or len(label) > 60:
        raise ValidationFailed("Enter a field name (up to 60 characters).")
    ftype = body.get("field_type")
    if ftype not in FIELD_TYPES:
        raise ValidationFailed("Choose a field type.")
    options = [str(o).strip() for o in (body.get("options") or []) if str(o).strip()]
    if ftype == "dropdown" and not options:
        raise ValidationFailed("Add at least one option for a dropdown field.")
    return {"label": label, "field_type": ftype, "options": jdump(options if ftype == "dropdown" else []),
            "sensitive": 1 if body.get("sensitive") else 0, "required": 1 if body.get("required") else 0,
            "show_in_table": 1 if body.get("show_in_table", True) else 0, "help_text": (body.get("help_text") or "").strip()[:300],
            "sort_order": int(body.get("sort_order") or 0)}


@router.post("/fields")
def add_field(body: dict = Body(...), user: CurrentUser = Depends(require("fields.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    data = _field_payload(body)
    key = slugify_key(data["label"])
    with tx(conn):
        base, n = key, 2
        while conn.execute("SELECT 1 FROM field_defs WHERE key = ?", (key,)).fetchone():
            key = f"{base}_{n}"
            n += 1
        cols = ["key", "is_custom", "active", "created_at", "created_by", *data]
        fid = conn.execute(f"INSERT INTO field_defs({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                           [key, 1, 1, utcnow(), user.email, *data.values()]).lastrowid
        audit.log(conn, user.email, "field.created", "field", fid, {"key": key, **body}, user.ip)
    return fields(user, conn)


@router.put("/fields/{field_id}")
def edit_field(field_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("fields.manage")),
               conn: sqlite3.Connection = Depends(get_conn)):
    with tx(conn):
        row = conn.execute("SELECT * FROM field_defs WHERE id = ? AND is_custom = 1", (field_id,)).fetchone()
        if row is None:
            raise NotFound("Field not found.")
        data = _field_payload({**dict(row), "options": jload(row["options"], []), **body})
        if data["field_type"] != row["field_type"]:
            used = conn.execute("SELECT COUNT(*) FROM employees WHERE json_extract(custom, ?) IS NOT NULL",
                                (f'$."{row["key"]}"',)).fetchone()[0]
            if used:
                raise ValidationFailed(f"The type cannot be changed because {used} records already have a value.")
        active = 1 if body.get("active", row["active"]) else 0
        sets = ", ".join(f"{k} = ?" for k in data)
        conn.execute(f"UPDATE field_defs SET {sets}, active = ? WHERE id = ?", [*data.values(), active, field_id])
        audit.log(conn, user.email, "field.updated", "field", field_id, body, user.ip)
    return fields(user, conn)


# ---------------------------------------------------------------- users and tokens

@router.get("/users")
def users(user: CurrentUser = Depends(require("users.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    return {"users": U.list_users(conn), "roles": {k: {"label": v["label"], "actions": sorted(v["actions"])} for k, v in ROLES.items()},
            "actions": ACTIONS, "admin_only": sorted(ADMIN_ONLY), "locations": get_setting(conn, "locations", [])}


@router.post("/users")
def add_user(body: dict = Body(...), user: CurrentUser = Depends(require("users.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    u, pw = U.create_user(conn, user, body)
    return {"user": u, "temporary_password": pw}


@router.put("/users/{user_id}")
def edit_user(user_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("users.manage")),
              conn: sqlite3.Connection = Depends(get_conn)):
    return U.update_user(conn, user, user_id, body)


@router.post("/users/{user_id}/reset-password")
def reset(user_id: int, user: CurrentUser = Depends(require("users.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    return {"temporary_password": U.reset_password(conn, user, user_id)}


@router.get("/tokens")
def tokens(user: CurrentUser = Depends(require("users.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    return U.list_tokens(conn)


@router.post("/tokens")
def add_token(body: dict = Body(...), user: CurrentUser = Depends(require("users.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    t, raw = U.create_token(conn, user, body)
    return {"token": t, "secret": raw}


@router.post("/tokens/{token_id}/revoke")
def revoke(token_id: int, user: CurrentUser = Depends(require("users.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    U.revoke_token(conn, user, token_id)
    return {"ok": True}


# ---------------------------------------------------------------- audit

@router.get("/audit")
def audit_log(q: str = "", limit: int = 100, offset: int = 0, user: CurrentUser = Depends(require("audit.view")),
              conn: sqlite3.Connection = Depends(get_conn)):
    like = f"%{q}%"
    rows = conn.execute("SELECT * FROM audit_log WHERE (? = '' OR action LIKE ? OR actor LIKE ? OR details LIKE ? OR entity_id = ?) "
                        "ORDER BY id DESC LIMIT ? OFFSET ?", (q, like, like, like, q, min(max(limit, 1), 500), max(offset, 0))).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM audit_log WHERE (? = '' OR action LIKE ? OR actor LIKE ? OR details LIKE ? OR entity_id = ?)",
                         (q, like, like, like, q)).fetchone()[0]
    return {"rows": [{**dict(r), "details": jload(r["details"], r["details"])} for r in rows], "total": total}


@router.get("/settings/data-folder-check")
def check_folder(path: str, user: CurrentUser = Depends(require("settings.manage"))):
    from pathlib import Path

    return {"warning": sync_folder_warning(Path(path))}


# ---------------------------------------------------------------- stop the application

def _request_stop(ctx) -> bool:
    server = getattr(ctx, "server", None)
    if server is None:
        return False
    server.should_exit = True
    return True


@router.post("/system/shutdown")
def shutdown(user: CurrentUser = Depends(require("settings.manage")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    if user.ip not in ("127.0.0.1", "::1", "testclient"):
        raise ValidationFailed("The application can only be stopped from the computer it runs on.")
    with tx(conn):
        audit.log(conn, user.email, "system.shutdown", "system", None, None, user.ip)
    return {"stopping": _request_stop(ctx)}


@router.post("/control/stop")
def control_stop(request: Request, ctx=Depends(get_ctx)):
    host = request.client.host if request.client else ""
    token = getattr(ctx, "control_token", None)
    if host not in ("127.0.0.1", "::1") or not token or request.headers.get("x-control-token") != token:
        raise Forbidden("Not allowed.")
    return {"stopping": _request_stop(ctx)}
