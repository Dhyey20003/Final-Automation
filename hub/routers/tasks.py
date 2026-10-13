from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends
from fastapi.responses import FileResponse

from .. import audit
from ..db import get_setting, jload, tx
from ..deps import get_conn, get_ctx, require
from ..errors import Forbidden, HubError, NotFound
from ..fsutil import open_folder
from ..permissions import CurrentUser
from ..services import dispatch as D
from ..services import packages as P
from ..services import tasks as T
from ..services.outlook import build_adapter
from ..timeutil import parse_iso_dt, zone

router = APIRouter(prefix="/api", tags=["tasks"])

OPEN_STATUSES = ("Due", "Preparing", "Ready", "Needs Attention", "Submitted to Outlook", "Failed", "Delivery Uncertain")


def _period_out(conn, p: dict) -> dict:
    d = dict(p)
    d["status_detail"] = jload(p["status_detail"], None) if p.get("status_detail") and str(p["status_detail"]).startswith("{") else (
        {"message": p["status_detail"]} if p.get("status_detail") else None)
    d["due_local"] = T.local_display(conn, p["due_at"])
    d["overdue"] = p["status"] in OPEN_STATUSES and parse_iso_dt(p["due_at"]) < datetime.now(zone(T.tzname(conn)))
    return d


def _task_out(conn, t: dict) -> dict:
    periods = [dict(r) for r in conn.execute("SELECT * FROM task_periods WHERE task_id = ? ORDER BY due_at DESC LIMIT 24", (t["id"],))]
    out = {k: v for k, v in t.items()}
    out["next_due"] = T.next_due(conn, t)
    out["periods"] = [_period_out(conn, p) for p in periods]
    out["open_count"] = sum(1 for p in out["periods"] if p["status"] in OPEN_STATUSES)
    out["overdue_count"] = sum(1 for p in out["periods"] if p["overdue"])
    return out


@router.get("/tasks")
def tasks(user: CurrentUser = Depends(require("tasks.view")), conn: sqlite3.Connection = Depends(get_conn)):
    return {"tasks": [_task_out(conn, t) for t in T.list_tasks(conn)], "outlook_mode": get_setting(conn, "outlook_mode", "dryrun"),
            "inclusions": T.INCLUSIONS, "output_formats": T.OUTPUT_FORMATS, "name_tokens": sorted(T.NAME_TOKENS),
            "archive_tokens": sorted(T.ARCHIVE_TOKENS), "email_tokens": sorted(T.EMAIL_TOKENS),
            "locations": get_setting(conn, "locations", [])}


@router.put("/tasks/{task_id}")
def update_task(task_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("tasks.configure")),
                conn: sqlite3.Connection = Depends(get_conn)):
    with tx(conn):
        t = T.update_task(conn, user, task_id, int(body.get("version", -1)), body)
    return _task_out(conn, t)


@router.post("/tasks/{task_id}/enable")
def enable(task_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("tasks.configure")),
           conn: sqlite3.Connection = Depends(get_conn)):
    with tx(conn):
        t = T.set_enabled(conn, user, task_id, bool(body.get("enabled")))
    return _task_out(conn, t)


@router.post("/tasks/{task_id}/auto-send")
def auto_send(task_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("tasks.configure")),
              conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    mode = get_setting(conn, "outlook_mode", "dryrun")
    check = build_adapter(ctx).check() if body.get("enabled") else None
    with tx(conn):
        t = T.set_auto_send(conn, user, task_id, bool(body.get("enabled")), bool(body.get("confirm")), check, mode)
    return _task_out(conn, t)


@router.post("/tasks/{task_id}/revalidate")
def revalidate(task_id: int, user: CurrentUser = Depends(require("tasks.configure")), conn: sqlite3.Connection = Depends(get_conn)):
    with tx(conn):
        t = T.revalidate(conn, user, task_id)
    return _task_out(conn, t)


@router.post("/tasks/{task_id}/prepare")
def prepare(task_id: int, body: dict = Body(default={}), user: CurrentUser = Depends(require("tasks.operate")),
            conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    t = T.load_task(conn, task_id)
    _check_task_scope(user, t)
    with tx(conn):
        p = T.period_for_manual(conn, t, body.get("period_key"))
    res = P.prepare_period(conn, ctx, user, p["id"], regenerate=bool(body.get("regenerate")))
    return {"period": _period_out(conn, T.period_row(conn, p["id"])), **res}


def _check_task_scope(user: CurrentUser, t: dict) -> None:
    if user.locations and any(l not in user.locations for l in t["config"]["locations"]):
        raise Forbidden("This task covers locations outside your access.")


def _period_scope(conn, user: CurrentUser, period_id: int) -> None:
    """Operating on a period requires access to every location of its task."""
    _check_task_scope(user, T.load_task(conn, T.period_row(conn, period_id)["task_id"]))


def _dispatch_scope(conn, user: CurrentUser, dispatch_id: int) -> None:
    _period_scope(conn, user, D.dispatch_row(conn, dispatch_id)["period_id"])


@router.get("/periods/{period_id}")
def period(period_id: int, user: CurrentUser = Depends(require("tasks.view")), conn: sqlite3.Connection = Depends(get_conn),
           ctx=Depends(get_ctx)):
    p = T.period_row(conn, period_id)
    t = T.load_task(conn, p["task_id"])
    pkgs = [dict(r) for r in conn.execute("SELECT * FROM packages WHERE period_id = ? ORDER BY location, revision DESC", (period_id,))
            if user.in_scope(r["location"])]
    full_scope = not user.locations or all(l in user.locations for l in t["config"]["locations"])
    for pk in pkgs:
        pk["manifest"] = jload(pk["manifest"], {})
    disp = [D.dispatch_row(conn, r["id"]) for r in conn.execute("SELECT id FROM dispatches WHERE period_id = ? ORDER BY id DESC", (period_id,))]
    preview = D.preview(conn, ctx, period_id) if full_scope and any(pk["status"] != "superseded" for pk in pkgs) else None
    if not full_scope:
        disp = []
    return {"period": _period_out(conn, p), "task": {"id": t["id"], "name": t["name"], "kind": t["kind"],
                                                     "auto_send_enabled": t["auto_send_enabled"]},
            "packages": pkgs, "dispatches": disp, "email_preview": preview, "outlook_mode": get_setting(conn, "outlook_mode", "dryrun")}


@router.post("/periods/{period_id}/regenerate")
def regenerate(period_id: int, user: CurrentUser = Depends(require("tasks.operate")), conn: sqlite3.Connection = Depends(get_conn),
               ctx=Depends(get_ctx)):
    _check_task_scope(user, T.load_task(conn, T.period_row(conn, period_id)["task_id"]))
    return P.prepare_period(conn, ctx, user, period_id, regenerate=True)


@router.post("/periods/{period_id}/prepare")
def prepare_existing(period_id: int, user: CurrentUser = Depends(require("tasks.operate")), conn: sqlite3.Connection = Depends(get_conn),
                     ctx=Depends(get_ctx)):
    _check_task_scope(user, T.load_task(conn, T.period_row(conn, period_id)["task_id"]))
    return P.prepare_period(conn, ctx, user, period_id, regenerate=False)


@router.post("/periods/{period_id}/accept-omissions")
def accept(period_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("tasks.operate")),
           conn: sqlite3.Connection = Depends(get_conn)):
    _period_scope(conn, user, period_id)
    return P.accept_omissions(conn, user, period_id, body.get("note", ""))


@router.post("/periods/{period_id}/dispatch")
def dispatch(period_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("tasks.operate")),
             conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    mode = body.get("mode")
    if mode not in ("draft", "send"):
        raise HubError("Choose Create Draft or Send Now.")
    _check_task_scope(user, T.load_task(conn, T.period_row(conn, period_id)["task_id"]))
    return D.start(conn, ctx, user, period_id, mode, confirm_draft_exists=bool(body.get("confirm_draft_exists")))


@router.post("/periods/{period_id}/ready-folder")
def ready(period_id: int, user: CurrentUser = Depends(require("tasks.operate")), conn: sqlite3.Connection = Depends(get_conn),
          ctx=Depends(get_ctx)):
    _period_scope(conn, user, period_id)
    return D.ready_folder(conn, ctx, user, period_id)


@router.post("/periods/{period_id}/open-ready-folder")
def open_ready(period_id: int, user: CurrentUser = Depends(require("tasks.operate")), conn: sqlite3.Connection = Depends(get_conn),
               ctx=Depends(get_ctx)):
    _period_scope(conn, user, period_id)
    if user.ip not in ("127.0.0.1", "::1", "testclient"):
        raise HubError("The folder can only be opened on the computer running the application.")
    info = D.ready_folder(conn, ctx, user, period_id)
    opened = open_folder(Path(info["folder"]))
    return {**info, "opened": opened}


@router.post("/periods/{period_id}/manual-complete")
def manual(period_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("tasks.operate")),
           conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    _period_scope(conn, user, period_id)
    return D.manual_complete(conn, ctx, user, period_id, body.get("note", ""), bool(body.get("nothing_sent")))


@router.post("/dispatches/{dispatch_id}/reconcile")
def reconcile(dispatch_id: int, user: CurrentUser = Depends(require("tasks.operate")), conn: sqlite3.Connection = Depends(get_conn),
              ctx=Depends(get_ctx)):
    _dispatch_scope(conn, user, dispatch_id)
    res = D.reconcile(conn, ctx, dispatch_id=dispatch_id, actor=user.email)
    return {"result": res, "dispatch": D.dispatch_row(conn, dispatch_id)}


@router.post("/dispatches/{dispatch_id}/resolve")
def resolve(dispatch_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("tasks.operate")),
            conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    _dispatch_scope(conn, user, dispatch_id)
    return D.resolve_uncertain(conn, ctx, user, dispatch_id, body.get("outcome", ""), body.get("note", ""))


def _package(conn, user: CurrentUser, package_id: int) -> dict:
    r = conn.execute("SELECT * FROM packages WHERE id = ?", (package_id,)).fetchone()
    if r is None:
        raise NotFound("Package not found.")
    if not user.in_scope(r["location"]):
        raise Forbidden("This package is outside your location access.")
    return dict(r)


@router.get("/packages/{package_id}/download")
def download(package_id: int, user: CurrentUser = Depends(require("tasks.operate")), conn: sqlite3.Connection = Depends(get_conn),
             ctx=Depends(get_ctx)):
    p = _package(conn, user, package_id)
    if not p["zip_path"]:
        raise NotFound("This package has no ZIP file (no photos were included).")
    with tx(conn):
        audit.log(conn, user.email, "package.download", "package", package_id, {"zip": p["zip_name"]}, user.ip)
    return FileResponse(ctx.paths.data / p["zip_path"], filename=p["zip_name"], media_type="application/zip")


@router.get("/packages/{package_id}/manifest")
def manifest(package_id: int, user: CurrentUser = Depends(require("tasks.view")), conn: sqlite3.Connection = Depends(get_conn)):
    p = _package(conn, user, package_id)
    m = jload(p["manifest"], {})
    m["acceptance"] = {"by": p["omissions_accepted_by"], "at": p["omissions_accepted_at"], "note": p["omissions_accept_note"]}
    m["package_status"] = p["status"]
    return m


@router.get("/outlook/status")
def outlook_status(user: CurrentUser = Depends(require("tasks.view")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    mode = get_setting(conn, "outlook_mode", "dryrun")
    return {"mode": mode, **build_adapter(ctx).check()}


@router.post("/scheduler/run-now")
def run_now(user: CurrentUser = Depends(require("tasks.configure")), ctx=Depends(get_ctx)):
    if ctx.scheduler is None:
        raise HubError("The scheduler is not running.")
    return ctx.scheduler.tick()
