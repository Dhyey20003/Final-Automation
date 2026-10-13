from __future__ import annotations

import shutil
import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, UploadFile

from .. import audit
from ..db import tx, utcnow
from ..deps import get_conn, get_ctx, require
from ..errors import HubError, NotFound, ValidationFailed
from ..permissions import CurrentUser
from ..services import backups as B

router = APIRouter(prefix="/api/backups", tags=["backups"])


@router.get("")
def list_backups(user: CurrentUser = Depends(require("backups.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    return B.list_backups(conn)


@router.post("")
def backup_now(user: CurrentUser = Depends(require("backups.manage")), ctx=Depends(get_ctx)):
    return B.create_backup(ctx, "manual", user.email)


@router.post("/{backup_id}/verify")
def verify(backup_id: int, user: CurrentUser = Depends(require("backups.manage")), conn: sqlite3.Connection = Depends(get_conn),
           ctx=Depends(get_ctx)):
    r = conn.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone()
    if r is None or not r["path"] or not Path(r["path"]).is_file():
        raise NotFound("The backup file is not available (it may be on a disconnected drive).")
    res = B.verify_archive(ctx, Path(r["path"]))
    with tx(conn):
        conn.execute("UPDATE backups SET verified_at = ?, status = ? WHERE id = ?",
                     (utcnow(), "verified" if res["ok"] else "verify_failed", backup_id))
        audit.log(conn, user.email, "backup.verify", "backup", backup_id, {"ok": res["ok"], "problems": res["problems"]}, user.ip)
    return res


def _restore_dir(ctx) -> Path:
    d = ctx.paths.tmp / "restore_uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/inspect-upload")
async def inspect_upload(file: UploadFile = File(...), user: CurrentUser = Depends(require("backups.manage")), ctx=Depends(get_ctx)):
    ref = uuid.uuid4().hex
    dest = _restore_dir(ctx) / f"{ref}.zip"
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)
    res = B.verify_archive(ctx, dest)
    if not res["ok"]:
        dest.unlink(missing_ok=True)
    return {**res, "upload_ref": ref if res["ok"] else None}


@router.post("/{backup_id}/inspect")
def inspect_existing(backup_id: int, user: CurrentUser = Depends(require("backups.manage")), conn: sqlite3.Connection = Depends(get_conn),
                     ctx=Depends(get_ctx)):
    r = conn.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone()
    if r is None or not r["path"] or not Path(r["path"]).is_file():
        raise NotFound("The backup file is not available.")
    return B.verify_archive(ctx, Path(r["path"]))


@router.post("/restore")
def restore(body: dict = Body(...), user: CurrentUser = Depends(require("backups.manage")), ctx=Depends(get_ctx)):
    if body.get("confirm_text") != "RESTORE":
        raise ValidationFailed("Type RESTORE to confirm. Restoring replaces all current data (a safety backup is taken first).")
    if body.get("upload_ref"):
        ref = str(body["upload_ref"])
        if not ref.isalnum():
            raise ValidationFailed("Invalid upload reference.")
        path = _restore_dir(ctx) / f"{ref}.zip"
    elif body.get("backup_id"):
        with ctx.db() as conn:
            r = conn.execute("SELECT path FROM backups WHERE id = ?", (int(body["backup_id"]),)).fetchone()
        if r is None:
            raise NotFound("Backup not found.")
        path = Path(r["path"])
    else:
        raise ValidationFailed("Choose a backup to restore.")
    if not path.is_file():
        raise NotFound("The backup file is not available.")
    ctx.maintenance = True
    ctx.maintenance_reason = "A restore is in progress. Please wait."
    try:
        if not ctx.wait_for_quiet(own_requests=1, timeout=30):
            raise HubError("Other requests are still running. Try again in a minute.")
        if not ctx.work_lock.acquire(timeout=120):
            raise HubError("Scheduled work is still running. Try again in a few minutes.")
        try:
            result = B.restore(ctx, path, user.email)
        finally:
            ctx.work_lock.release()
    finally:
        ctx.maintenance = False
        ctx.maintenance_reason = ""
    if body.get("upload_ref"):
        path.unlink(missing_ok=True)
    ctx.startup_warnings.append("Data was restored from a backup. Automatic sending is off until an administrator re-checks each task.")
    return {**result, "sign_in_again": True}
