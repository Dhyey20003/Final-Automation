from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Body, Depends, Query, Response

from .. import audit
from ..db import get_setting, tx
from ..deps import get_conn, get_ctx, require
from ..errors import Conflict, ValidationFailed
from ..fields import ASSIGNMENT_STATUSES, EMPLOYMENT_STATUSES, registry
from ..permissions import ACTIONS, ROLES, CurrentUser
from ..services import employees as E
from ..services import photos as PH
from ..services import posts as POSTS
from ..services.sheets import write_xlsx
from ..timeutil import fmt_display_date

router = APIRouter(prefix="/api", tags=["employees"])


def _params(q, view, sort, dir, page, page_size, filters) -> dict:
    try:
        f = json.loads(filters) if filters else {}
        if not isinstance(f, dict):
            raise ValueError
    except ValueError:
        raise ValidationFailed("Filters are not valid.") from None
    return {"q": q, "view": view, "sort": sort, "dir": dir, "page": page, "page_size": page_size, "filters": f}


@router.get("/meta")
def meta(user: CurrentUser = Depends(require("employees.view")), conn: sqlite3.Connection = Depends(get_conn)):
    fields = []
    for f in registry(conn):
        if not user.can_see_field(f.key):
            continue
        d = f.public()
        d["can_edit"] = f.editable and user.can_edit_field(f.key)
        if f.key == "date_of_birth" and not user.is_admin and user.dob_access != "full":
            d["masked"] = True
        fields.append(d)
    return {"fields": fields, "locations": E.locations(conn), "employment_statuses": EMPLOYMENT_STATUSES,
            "assignment_statuses": ASSIGNMENT_STATUSES, "views": list(E.VIEWS), "roles": {k: v["label"] for k, v in ROLES.items()},
            "actions": ACTIONS, "timezone": get_setting(conn, "timezone", "Asia/Kolkata"), "date_order": get_setting(conn, "date_order", "DMY")}


@router.get("/employees")
def list_employees(q: str = "", view: str = "active", sort: str = "full_name", dir: str = "asc", page: int = 1,
                   page_size: int = 50, filters: str = "", user: CurrentUser = Depends(require("employees.view")),
                   conn: sqlite3.Connection = Depends(get_conn)):
    return E.list_employees(conn, user, _params(q, view, sort, dir, page, page_size, filters))


@router.get("/employees/lookup")
def lookup(q: str = "", limit: int = 20, include_historical: bool = False,
           user: CurrentUser = Depends(require("employees.view")), conn: sqlite3.Connection = Depends(get_conn)):
    like = "%" + q.strip().replace("%", "") + "%"
    where = ["archived = 0"]
    args: list = []
    if not include_historical:
        where.append("record_scope = 'directory'")
    match = ["full_name LIKE ?", "rhc_id LIKE ?"]
    args += [like, like]
    if user.can_see_field("official_email"):
        match.append("official_email LIKE ?")
        args.append(like)
    where.append("(" + " OR ".join(match) + ")")
    if user.locations:
        where.append(f"location IN ({', '.join('?' for _ in user.locations)})")
        args += user.locations
    args.append(max(1, min(limit, 100)))
    rows = conn.execute(
        "SELECT id, rhc_id, full_name, location, employment_status, current_photo_id, record_scope FROM employees "
        f"WHERE {' AND '.join(where)} ORDER BY employment_status = 'Active' DESC, full_name LIMIT ?", args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if not user.can_see_field("photo"):
            d["current_photo_id"] = None
        if not user.can_see_field("location"):
            d["location"] = None
        out.append(d)
    return out


@router.get("/employees/export")
def export(q: str = "", view: str = "active", sort: str = "full_name", dir: str = "asc", filters: str = "",
           columns: str = "", user: CurrentUser = Depends(require("employees.export")), conn: sqlite3.Connection = Depends(get_conn)):
    params = _params(q, view, sort, dir, 1, 500, filters)
    data = E.list_employees(conn, user, params, paginate=False)
    fields = {f.key: f for f in registry(conn)}
    wanted = [c for c in (columns.split(",") if columns else []) if c in fields and user.can_see_field(c) and c != "photo"]
    if not wanted:
        wanted = [f.key for f in fields.values() if f.show_in_table and user.can_see_field(f.key) and f.key != "photo"]
    headers = [fields[c].label for c in wanted]
    rows = []
    for r in data["rows"]:
        rows.append([r.get(c) for c in wanted])
    body = write_xlsx(headers, rows, "Employees")
    with tx(conn):
        audit.log(conn, user.email, "employees.export", "employees", None,
                  {"rows": len(rows), "columns": wanted, "view": view, "q": q, "filters": params["filters"]}, user.ip)
    name = f"Employees_{datetime.now().strftime('%Y-%m-%d_%H%M')}.xlsx"
    return Response(body, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/employees")
def create(body: dict = Body(...), user: CurrentUser = Depends(require("employees.create")), conn: sqlite3.Connection = Depends(get_conn),
           ctx=Depends(get_ctx)):
    values = body.get("values") or {}
    with tx(conn):
        emp_id, warnings = E.create_employee(conn, user, values)
        audit.log(conn, user.email, "employee.create", "employee", emp_id, {"rhc_id": values.get("rhc_id")}, user.ip)
        post = POSTS.sync_assignment(conn, ctx, user.email, None, E.fetch_record(conn, emp_id), source="manual")
    return {"id": emp_id, "warnings": warnings, "employee": _one(conn, user, emp_id), "post": post}


def _one(conn, user, emp_id: int) -> dict:
    rec = E.fetch_record(conn, emp_id)
    E.check_scope(user, rec)
    return E.serialize(rec, user, registry(conn), E.tz_today(conn))


@router.get("/employees/{emp_id}")
def get_one(emp_id: int, user: CurrentUser = Depends(require("employees.view")), conn: sqlite3.Connection = Depends(get_conn)):
    emp = _one(conn, user, emp_id)
    rec = E.fetch_record(conn, emp_id)
    _, warnings = E.validate_record(rec, E.tz_today(conn))
    return {"employee": emp, "warnings": warnings, "history": E.history(conn, user, emp_id),
            "assignment_history": [{k: v for k, v in a.items() if k in ("id", "employee_id", "recorded_at", "recorded_by", "source")
                                    or user.can_see_field(k)} for a in E.assignment_history(conn, emp_id)]
            if user.can_see_field("assignment_status") else [],
            "photos": PH.versions(conn, emp_id) if user.can("photos.view") and user.can_see_field("photo") else []}


@router.patch("/employees/{emp_id}")
def update(emp_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("employees.edit")),
           conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    if "version" not in body:
        raise ValidationFailed("The record version is required so that other people's changes are not overwritten.")
    post = None
    with tx(conn):
        before = E.fetch_record(conn, emp_id)
        res = E.apply_update(conn, user, emp_id, int(body["version"]), body.get("changes") or {})
        if res["changed"]:
            audit.log(conn, user.email, "employee.update", "employee", emp_id,
                      {"fields": [c[0] for c in res["changed"]]}, user.ip)
            post = POSTS.sync_assignment(conn, ctx, user.email, before, res["record"], source="manual")
    return {"employee": _one(conn, user, emp_id), "warnings": res["warnings"], "changed": [c[0] for c in res["changed"]], "post": post}


@router.post("/employees/bulk-preview")
def bulk_preview(body: dict = Body(...), user: CurrentUser = Depends(require("employees.edit")),
                 conn: sqlite3.Connection = Depends(get_conn)):
    """Validate pasted cells without saving. Returns per-row, per-field results."""
    updates = body.get("updates") or []
    if len(updates) > 1000:
        raise ValidationFailed("Paste at most 1000 rows at a time.")
    results = []
    conn.execute("BEGIN")
    try:
        for u in updates:
            item = {"id": u.get("id"), "ok": True, "errors": {}, "warnings": [], "changes": []}
            try:
                conn.execute("SAVEPOINT p")
                rec = E.fetch_record(conn, int(u["id"]))
                res = E.apply_update(conn, user, int(u["id"]), int(u["version"]), u.get("changes") or {}, source="paste-preview")
                item["changes"] = [{"field": c[0], "old": c[1], "new": c[2]} for c in res["changed"]]
                item["warnings"] = res["warnings"]
                item["rhc_id"], item["name"] = rec["rhc_id"], rec["full_name"]
            except (ValidationFailed, Conflict) as exc:
                item["ok"] = False
                item["errors"] = (exc.details or {}).get("fields") or {"_": exc.message}
            except Exception as exc:  # scope errors, not found
                item["ok"] = False
                item["errors"] = {"_": getattr(exc, "message", str(exc))}
            finally:
                conn.execute("ROLLBACK TO p")
                conn.execute("RELEASE p")
            results.append(item)
    finally:
        conn.execute("ROLLBACK")
    return {"results": results, "ok": all(r["ok"] for r in results)}


@router.post("/employees/bulk-update")
def bulk_update(body: dict = Body(...), user: CurrentUser = Depends(require("employees.edit")),
                conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    """Apply pasted changes atomically: either every row is saved or none is."""
    updates = body.get("updates") or []
    if len(updates) > 1000:
        raise ValidationFailed("Paste at most 1000 rows at a time.")
    changed = 0
    with tx(conn):
        for u in updates:
            before = E.fetch_record(conn, int(u["id"]))
            res = E.apply_update(conn, user, int(u["id"]), int(u["version"]), u.get("changes") or {}, source="paste")
            changed += len(res["changed"])
            if res["changed"]:
                POSTS.sync_assignment(conn, ctx, user.email, before, res["record"], source="paste")
        audit.log(conn, user.email, "employee.bulk_paste", "employees", None,
                  {"rows": len(updates), "fields_changed": changed}, user.ip)
    return {"ok": True, "rows": len(updates), "fields_changed": changed}


@router.post("/employees/{emp_id}/archive")
def archive(emp_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("employees.archive")),
            conn: sqlite3.Connection = Depends(get_conn)):
    with tx(conn):
        E.set_archived(conn, user, emp_id, int(body.get("version", -1)), True)
    return {"employee": _one(conn, user, emp_id)}


@router.post("/employees/{emp_id}/unarchive")
def unarchive(emp_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("employees.archive")),
              conn: sqlite3.Connection = Depends(get_conn)):
    with tx(conn):
        E.set_archived(conn, user, emp_id, int(body.get("version", -1)), False)
    return {"employee": _one(conn, user, emp_id)}
