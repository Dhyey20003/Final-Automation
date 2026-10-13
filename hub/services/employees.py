"""Employee records: validation, create/update with version checks, history and listing.

Current bench days
------------------
Shown only when assignment status is Bench and a bench start date is set.
Value = calendar days from the bench start date to today (Asia/Kolkata by
default), so the start date itself shows 0. If a bench end date earlier than
today is recorded, the count stops at the end date. If the start date is in
the future the value is blank and a warning is shown.

Service functions never commit: the caller owns the transaction.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from .. import audit
from ..db import get_setting, jdump, utcnow
from ..errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..fields import CORE_COLUMNS, FieldDef, FieldError, get_locations, normalize, registry
from ..permissions import CurrentUser
from ..timeutil import today_local

META_COLUMNS = {"id", "created_at", "updated_at", "created_by", "updated_by"}
ASSIGNMENT_KEYS = ("assignment_status", "client_name", "assignment_start", "assignment_end", "bench_start", "bench_end")

VIEWS = {
    "active": "e.record_scope = 'directory' AND e.archived = 0 AND e.employment_status = 'Active'",
    "pending": "e.record_scope = 'directory' AND e.archived = 0 AND e.employment_status = 'Pending Review'",
    "left": "e.record_scope = 'directory' AND e.archived = 0 AND e.employment_status = 'Left'",
    "directory": "e.record_scope = 'directory' AND e.archived = 0",
    "historical": "e.record_scope = 'historical'",
    "archived": "e.archived = 1",
    "all": "1 = 1",
}


def bench_sql(today_param: str = ":today") -> str:
    return (
        "(CASE WHEN e.assignment_status = 'Bench' AND e.bench_start IS NOT NULL AND e.bench_start <= {t} "
        "THEN CAST(julianday(MIN(COALESCE(e.bench_end, {t}), {t})) - julianday(e.bench_start) AS INTEGER) END)"
    ).format(t=today_param)


def bench_days(rec: dict, today: date) -> int | None:
    if rec.get("assignment_status") != "Bench" or not rec.get("bench_start"):
        return None
    try:
        start = date.fromisoformat(rec["bench_start"])
    except ValueError:
        return None
    if start > today:
        return None
    end = today
    if rec.get("bench_end"):
        try:
            be = date.fromisoformat(rec["bench_end"])
            if be < today:
                end = be
        except ValueError:
            pass
    return max(0, (end - start).days)


def tz_today(conn: sqlite3.Connection) -> date:
    return today_local(get_setting(conn, "timezone", "Asia/Kolkata"))


def hv(value: Any) -> str | None:
    """History value encoding (JSON text so types survive undo)."""
    return None if value is None else json.dumps(value, ensure_ascii=False)


def hv_load(value: str | None) -> Any:
    return None if value is None else json.loads(value)


# ---------------------------------------------------------------- record helpers

def row_record(row: sqlite3.Row) -> dict:
    rec = dict(row)
    rec["custom"] = json.loads(rec.get("custom") or "{}")
    return rec


def flat_value(rec: dict, key: str) -> Any:
    if key.startswith("cf_"):
        return rec["custom"].get(key)
    return rec.get(key)


def fetch_record(conn: sqlite3.Connection, emp_id: int) -> dict:
    row = conn.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
    if row is None:
        raise NotFound("That employee record does not exist.")
    return row_record(row)


def check_scope(user: CurrentUser, rec: dict) -> None:
    if not user.in_scope(rec.get("location")):
        raise Forbidden("This employee is outside your location access.")


def mask_dob(value: str | None, access: str) -> str | None:
    if not value:
        return value
    if access == "full":
        return value
    if access == "day_month":
        try:
            d = date.fromisoformat(value)
            return d.strftime("%d-%b")
        except ValueError:
            return None
    return None


def serialize(rec: dict, user: CurrentUser, fields: list[FieldDef], today: date) -> dict:
    out: dict[str, Any] = {
        "id": rec["id"],
        "version": rec["version"],
        "record_scope": rec["record_scope"],
        "archived": bool(rec["archived"]),
    }
    for fd in fields:
        if not user.can_see_field(fd.key):
            continue
        if fd.key == "bench_days":
            out["bench_days"] = bench_days(rec, today)
        elif fd.key == "photo":
            if user.can("photos.view"):
                out["photo_id"] = rec.get("current_photo_id")
        elif fd.key == "date_of_birth":
            out["date_of_birth"] = mask_dob(rec.get("date_of_birth"), "full" if user.is_admin else user.dob_access)
        else:
            out[fd.key] = flat_value(rec, fd.key)
    return out


# ---------------------------------------------------------------- validation

def normalize_changes(
    conn: sqlite3.Connection, user: CurrentUser | None, changes: dict[str, Any], fields: dict[str, FieldDef]
) -> dict[str, Any]:
    date_order = get_setting(conn, "date_order", "DMY")
    aliases = get_setting(conn, "location_aliases", {}) or {}
    out, errors = {}, {}
    for key, raw in changes.items():
        fd = fields.get(key)
        if fd is None:
            errors[key] = "Unknown field"
            continue
        if not fd.editable:
            errors[key] = f"{fd.label} cannot be edited"
            continue
        if user is not None and not user.can_edit_field(key):
            errors[key] = f"You do not have permission to change {fd.label}"
            continue
        try:
            out[key] = normalize(fd, raw, date_order=date_order, location_aliases=aliases)
        except FieldError as exc:
            errors[key] = str(exc)
    if errors:
        raise ValidationFailed("Some values are not valid.", details={"fields": errors})
    return out


def _d(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def validate_record(rec: dict, today: date, fields: dict[str, FieldDef] | None = None) -> tuple[dict, list[str]]:
    """Cross-field checks. Returns (errors by field, warnings)."""
    errors: dict[str, str] = {}
    warnings: list[str] = []
    if not rec.get("rhc_id"):
        errors["rhc_id"] = "RHC ID is required"
    if not rec.get("full_name"):
        errors["full_name"] = "Full name is required"
    pairs = [
        ("date_of_joining", "last_working_date", "Last working date cannot be before the date of joining"),
        ("assignment_start", "assignment_end", "Assignment end cannot be before assignment start"),
        ("bench_start", "bench_end", "Bench end cannot be before bench start"),
    ]
    for a, b, msg in pairs:
        da, db_ = _d(rec.get(a)), _d(rec.get(b))
        if da and db_ and db_ < da:
            errors[b] = msg
    dob, doj = _d(rec.get("date_of_birth")), _d(rec.get("date_of_joining"))
    if dob and dob > today:
        errors["date_of_birth"] = "Date of birth cannot be in the future"
    if dob and doj:
        if doj <= dob:
            errors["date_of_joining"] = "Date of joining must be after the date of birth"
        elif (doj - dob).days < 16 * 365:
            warnings.append("Employee would have been under 16 on the joining date; check the dates.")
    if rec.get("employment_status") == "Left" and not rec.get("last_working_date"):
        warnings.append("Status is Left but no last working date is recorded.")
    if rec.get("employment_status") == "Active" and _d(rec.get("last_working_date")) and _d(rec.get("last_working_date")) < today:
        warnings.append("Status is Active but the last working date is in the past.")
    if rec.get("assignment_status") == "Client" and not rec.get("client_name"):
        warnings.append("Assignment status is Client but no client name is recorded.")
    if rec.get("assignment_status") == "Bench":
        if not rec.get("bench_start"):
            warnings.append("Assignment status is Bench but no bench start date is recorded.")
        elif _d(rec.get("bench_start")) and _d(rec.get("bench_start")) > today:
            warnings.append("Bench start date is in the future, so bench days are not counted yet.")
    if rec.get("record_scope") == "directory" and rec.get("employment_status") == "Active" and not rec.get("location"):
        warnings.append("No location recorded; the employee cannot be included in headshot packages.")
    if fields:
        for fd in fields.values():
            if fd.custom and fd.required and flat_value(rec, fd.key) in (None, ""):
                errors[fd.key] = f"{fd.label} is required"
    return errors, warnings


def check_unique(conn: sqlite3.Connection, rec: dict, exclude_id: int | None = None) -> dict:
    errors = {}
    if rec.get("rhc_id"):
        row = conn.execute(
            "SELECT id, full_name FROM employees WHERE rhc_id = ? COLLATE NOCASE AND id IS NOT ?",
            (rec["rhc_id"], exclude_id),
        ).fetchone()
        if row:
            errors["rhc_id"] = f"RHC ID {rec['rhc_id']} is already used by {row['full_name']}"
    if rec.get("official_email"):
        row = conn.execute(
            "SELECT id, rhc_id, full_name FROM employees WHERE official_email = ? COLLATE NOCASE AND id IS NOT ?",
            (rec["official_email"], exclude_id),
        ).fetchone()
        if row:
            errors["official_email"] = f"This email is already used by {row['full_name']} ({row['rhc_id']})"
    return errors


# ---------------------------------------------------------------- write operations

def _history(conn, emp_id, field, old, new, actor, source, batch_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO employee_history(employee_id, changed_at, changed_by, source, import_batch_id, field, old_value, new_value) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (emp_id, utcnow(), actor, source, batch_id, field, hv(old), hv(new)),
    )
    return cur.lastrowid


def create_employee(
    conn: sqlite3.Connection,
    user: CurrentUser,
    values: dict[str, Any],
    *,
    source: str = "manual",
    batch_id: int | None = None,
    record_scope: str = "directory",
    normalized: bool = False,
    enforce_permissions: bool = True,
) -> tuple[int, list[str]]:
    fields = {f.key: f for f in registry(conn)}
    vals = dict(values) if normalized else normalize_changes(conn, user if enforce_permissions else None, values, fields)
    rec = {k: None for k in CORE_COLUMNS}
    rec.update({"employment_status": "Active", "assignment_status": "Unknown", "custom": {}})
    for k, v in vals.items():
        if k.startswith("cf_"):
            if v is not None:
                rec["custom"][k] = v
        else:
            rec[k] = v
    if rec.get("employment_status") is None:
        rec["employment_status"] = "Active"
    if rec.get("assignment_status") is None:
        rec["assignment_status"] = "Unknown"
    rec["record_scope"] = record_scope
    if enforce_permissions and not user.in_scope(rec.get("location")):
        raise Forbidden("You can only add employees in your permitted locations.")
    errors, warnings = validate_record(rec, tz_today(conn), fields)
    errors.update(check_unique(conn, rec))
    if errors:
        raise ValidationFailed("The employee could not be saved.", details={"fields": errors})
    now = utcnow()
    cols = sorted(CORE_COLUMNS)
    cur = conn.execute(
        f"INSERT INTO employees({', '.join(cols)}, record_scope, custom, version, created_by, created_at, updated_by, updated_at) "
        f"VALUES ({', '.join('?' for _ in cols)}, ?, ?, 1, ?, ?, ?, ?)",
        [rec.get(c) for c in cols] + [record_scope, jdump(rec["custom"]), user.email, now, user.email, now],
    )
    emp_id = cur.lastrowid
    _history(conn, emp_id, "__created__", None, {"rhc_id": rec["rhc_id"], "record_scope": record_scope}, user.email, source, batch_id)
    return emp_id, warnings


def apply_update(
    conn: sqlite3.Connection,
    user: CurrentUser,
    emp_id: int,
    expected_version: int | None,
    changes: dict[str, Any],
    *,
    source: str = "manual",
    batch_id: int | None = None,
    normalized: bool = False,
    enforce_permissions: bool = True,
    extra_columns: dict[str, Any] | None = None,
) -> dict:
    """Update fields with optimistic version checking.

    Returns {"record", "changed": [(field, old, new, history_id)], "warnings"}.
    ``extra_columns`` allows system columns (record_scope, archived) to be set.
    """
    fields = {f.key: f for f in registry(conn)}
    rec = fetch_record(conn, emp_id)
    if enforce_permissions:
        check_scope(user, rec)
    if expected_version is not None and rec["version"] != expected_version:
        raise Conflict(
            "Someone else changed this record after you opened it. Review their changes before saving again.",
            details={"current_version": rec["version"], "your_version": expected_version, "current": _conflict_view(rec, user, fields, conn)},
        )
    vals = dict(changes) if normalized else normalize_changes(conn, user if enforce_permissions else None, changes, fields)
    new = dict(rec)
    new["custom"] = dict(rec["custom"])
    changed = []
    for k, v in vals.items():
        old = flat_value(rec, k)
        if old == v:
            continue
        if k.startswith("cf_"):
            if v is None:
                new["custom"].pop(k, None)
            else:
                new["custom"][k] = v
        else:
            new[k] = v
        changed.append((k, old, v))
    for k, v in (extra_columns or {}).items():
        if rec.get(k) != v:
            new[k] = v
            changed.append((k, rec.get(k), v))
    if not changed:
        return {"record": rec, "changed": [], "warnings": []}
    if enforce_permissions and not user.in_scope(new.get("location")):
        raise Forbidden("You cannot move an employee to a location outside your access.")
    errors, warnings = validate_record(new, tz_today(conn), fields)
    old_errors, _ = validate_record(rec, tz_today(conn), fields)
    # Problems that already existed (for example a required field added later) do not block unrelated edits.
    errors = {k: v for k, v in errors.items() if old_errors.get(k) != v}
    if any(k in ("rhc_id", "official_email") for k, _, _ in changed):
        errors.update(check_unique(conn, new, exclude_id=emp_id))
    if errors:
        raise ValidationFailed("The change could not be saved.", details={"fields": errors})
    had_assignment = rec.get("assignment_status") not in (None, "Unknown") or any(
        rec.get(k) for k in ASSIGNMENT_KEYS if k != "assignment_status"
    )
    if had_assignment and any(k in ASSIGNMENT_KEYS for k, _, _ in changed):
        # Preserve the previous assignment / bench period before it is overwritten.
        conn.execute(
            "INSERT INTO assignment_history(employee_id, assignment_status, client_name, assignment_start, assignment_end, "
            "bench_start, bench_end, recorded_at, recorded_by, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (emp_id, rec["assignment_status"], rec["client_name"], rec["assignment_start"], rec["assignment_end"],
             rec["bench_start"], rec["bench_end"], utcnow(), user.email, source),
        )
    sets, params = [], []
    for col in sorted(CORE_COLUMNS | {"record_scope", "archived", "archived_at", "archived_by", "current_photo_id"}):
        if col in new and new.get(col) != rec.get(col):
            sets.append(f"{col} = ?")
            params.append(new[col])
    if new["custom"] != rec["custom"]:
        sets.append("custom = ?")
        params.append(jdump(new["custom"]))
    sets += ["version = version + 1", "updated_by = ?", "updated_at = ?"]
    params += [user.email, utcnow(), emp_id, rec["version"]]
    cur = conn.execute(f"UPDATE employees SET {', '.join(sets)} WHERE id = ? AND version = ?", params)
    if cur.rowcount != 1:
        raise Conflict("This record changed while saving. Reload and try again.")
    out_changes = []
    for k, old, v in changed:
        hid = _history(conn, emp_id, k, old, v, user.email, source, batch_id)
        out_changes.append((k, old, v, hid))
    return {"record": fetch_record(conn, emp_id), "changed": out_changes, "warnings": warnings}


def _conflict_view(rec, user, fields, conn):
    return serialize(rec, user, list(fields.values()), tz_today(conn))


def set_archived(conn: sqlite3.Connection, user: CurrentUser, emp_id: int, expected_version: int, archived: bool) -> dict:
    rec = fetch_record(conn, emp_id)
    check_scope(user, rec)
    if archived and rec["employment_status"] == "Active" and rec["record_scope"] == "directory":
        raise ValidationFailed("Only former or pending employees can be archived. Set the employment status to Left first.")
    extra = {"archived": 1 if archived else 0, "archived_at": utcnow() if archived else None,
             "archived_by": user.email if archived else None}
    res = apply_update(conn, user, emp_id, expected_version, {}, source="archive" if archived else "unarchive",
                       extra_columns=extra)
    audit.log(conn, user.email, "employee.archive" if archived else "employee.unarchive", "employee", emp_id,
              {"rhc_id": rec["rhc_id"]}, user.ip)
    return res


# ---------------------------------------------------------------- listing

def column_expr(key: str, fields: dict[str, FieldDef]) -> str | None:
    if key in CORE_COLUMNS or key in META_COLUMNS:
        return f"e.{key}"
    if key == "bench_days":
        return bench_sql()
    if key.startswith("cf_") and key in fields:
        return f"json_extract(e.custom, '$.\"{key}\"')"
    return None


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def build_filters(user: CurrentUser, params: dict, fields: dict[str, FieldDef], today: date) -> tuple[str, dict]:
    where = [VIEWS.get(params.get("view") or "active", VIEWS["active"])]
    args: dict[str, Any] = {"today": today.isoformat()}
    if user.locations:
        names = []
        for i, loc in enumerate(user.locations):
            args[f"scope{i}"] = loc
            names.append(f":scope{i}")
        where.append(f"e.location IN ({', '.join(names)})")
    q = (params.get("q") or "").strip()
    if q:
        args["q"] = _like(q)
        where.append("(e.full_name LIKE :q ESCAPE '\\' OR e.rhc_id LIKE :q ESCAPE '\\' OR e.official_email LIKE :q ESCAPE '\\')")
    for i, (key, raw) in enumerate((params.get("filters") or {}).items()):
        if raw is None or str(raw).strip() == "" or not user.can_see_field(key):
            continue
        if key == "date_of_birth" and not user.is_admin and user.dob_access != "full":
            continue
        expr = column_expr(key, fields)
        if expr is None:
            continue
        val = str(raw).strip()
        p = f"f{i}"
        low = val.lower()
        if low == "(empty)" or low == "empty":
            where.append(f"({expr} IS NULL OR {expr} = '')")
            continue
        if low in ("(not empty)", "!empty", "not empty"):
            where.append(f"({expr} IS NOT NULL AND {expr} <> '')")
            continue
        for op in (">=", "<=", "!=", ">", "<", "="):
            if val.startswith(op):
                operand = val[len(op):].strip()
                num = _maybe_number(operand) if key == "bench_days" or (fields.get(key) and fields[key].type == "number") else None
                args[p] = num if num is not None else operand
                sql_op = "<>" if op == "!=" else op
                if op in ("=", "!=") and num is None:
                    where.append(f"{expr} {sql_op} :{p} COLLATE NOCASE")
                else:
                    where.append(f"{expr} {sql_op} :{p}")
                break
        else:
            fd = fields.get(key)
            if fd and fd.type == "dropdown":
                args[p] = val
                where.append(f"{expr} = :{p} COLLATE NOCASE")
            else:
                args[p] = _like(val)
                where.append(f"CAST({expr} AS TEXT) LIKE :{p} ESCAPE '\\'")
    return " AND ".join(where), args


def _maybe_number(v: str):
    try:
        return float(v) if "." in v else int(v)
    except ValueError:
        return None


def list_employees(conn: sqlite3.Connection, user: CurrentUser, params: dict, paginate: bool = True) -> dict:
    fields_list = registry(conn)
    fields = {f.key: f for f in fields_list}
    today = tz_today(conn)
    where, args = build_filters(user, params, fields, today)
    sort = params.get("sort") or "full_name"
    direction = "DESC" if str(params.get("dir", "asc")).lower() == "desc" else "ASC"
    sort_expr = column_expr(sort, fields) if user.can_see_field(sort) else None
    if sort == "date_of_birth" and not user.is_admin and user.dob_access != "full":
        sort_expr = "strftime('%m-%d', e.date_of_birth)"
    if sort_expr is None:
        sort_expr = "e.full_name"
    total = conn.execute(f"SELECT COUNT(*) FROM employees e WHERE {where}", args).fetchone()[0]
    sql = f"SELECT e.* FROM employees e WHERE {where} ORDER BY {sort_expr} IS NULL, {sort_expr} {direction}, e.id"
    page = max(1, int(params.get("page") or 1))
    size = min(500, max(1, int(params.get("page_size") or 50)))
    if paginate:
        sql += " LIMIT :lim OFFSET :off"
        args.update(lim=size, off=(page - 1) * size)
    rows = [serialize(row_record(r), user, fields_list, today) for r in conn.execute(sql, args).fetchall()]
    return {"rows": rows, "total": total, "page": page, "page_size": size}


def history(conn: sqlite3.Connection, user: CurrentUser, emp_id: int, limit: int = 300) -> list[dict]:
    out = []
    for r in conn.execute(
        "SELECT * FROM employee_history WHERE employee_id = ? ORDER BY id DESC LIMIT ?", (emp_id, limit)
    ).fetchall():
        f = r["field"]
        if f != "__created__" and not user.can_see_field(f):
            continue
        old, new = hv_load(r["old_value"]), hv_load(r["new_value"])
        if f == "date_of_birth" and not user.is_admin and user.dob_access != "full":
            old, new = mask_dob(old, user.dob_access), mask_dob(new, user.dob_access)
        out.append({"id": r["id"], "at": r["changed_at"], "by": r["changed_by"], "source": r["source"],
                    "batch": r["import_batch_id"], "field": f, "old": old, "new": new})
    return out


def assignment_history(conn: sqlite3.Connection, emp_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM assignment_history WHERE employee_id = ? ORDER BY id DESC", (emp_id,)).fetchall()]


def locations(conn: sqlite3.Connection) -> list[str]:
    return get_locations(conn)
