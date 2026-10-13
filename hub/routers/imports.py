from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Body, Depends, File, Response, UploadFile

from .. import audit
from ..db import tx
from ..deps import get_conn, get_ctx, require
from ..errors import Forbidden, NotFound
from ..permissions import CurrentUser
from ..services import imports as I

router = APIRouter(prefix="/api", tags=["imports"])


def _own(conn, user: CurrentUser, batch_id: int) -> None:
    """Import batches are visible to the operator who uploaded them and to administrators."""
    b = I.get_batch(conn, batch_id)
    if not user.is_admin and b["operator"] != user.email:
        raise Forbidden("This import belongs to another user.")


@router.get("/imports/purposes")
def purposes(user: CurrentUser = Depends(require("imports.run"))):
    return I.PURPOSES


@router.post("/imports/upload")
async def upload(file: UploadFile = File(...), user: CurrentUser = Depends(require("imports.run")),
                 conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    data = await file.read()
    return I.upload(conn, ctx, user, file.filename or "upload.xlsx", data)


@router.get("/imports")
def history(user: CurrentUser = Depends(require("imports.run")), conn: sqlite3.Connection = Depends(get_conn)):
    rows = I.list_batches(conn)
    return rows if user.is_admin else [b for b in rows if b["operator"] == user.email]


@router.get("/imports/{batch_id}/sheet")
def sheet(batch_id: int, sheet: str | None = None, header_row: int = 1, template_id: int | None = None,
          user: CurrentUser = Depends(require("imports.run")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    _own(conn, user, batch_id)
    return I.sheet_info(conn, ctx, user, batch_id, sheet, header_row, template_id)


@router.post("/imports/{batch_id}/preview")
def preview(batch_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("imports.run")),
            conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    _own(conn, user, batch_id)
    return I.preview(conn, ctx, user, batch_id, body)


@router.post("/imports/{batch_id}/commit")
def commit(batch_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("imports.run")),
           conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    _own(conn, user, batch_id)
    return I.commit(conn, ctx, user, batch_id, body)


@router.post("/imports/{batch_id}/discard")
def discard(batch_id: int, user: CurrentUser = Depends(require("imports.run")), conn: sqlite3.Connection = Depends(get_conn)):
    _own(conn, user, batch_id)
    I.discard(conn, user, batch_id)
    return {"ok": True}


@router.get("/imports/{batch_id}/report")
def report(batch_id: int, user: CurrentUser = Depends(require("imports.run")), conn: sqlite3.Connection = Depends(get_conn)):
    _own(conn, user, batch_id)
    name, data = I.error_report(conn, batch_id)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/imports/{batch_id}/undo-preview")
def undo_preview(batch_id: int, user: CurrentUser = Depends(require("imports.undo")), conn: sqlite3.Connection = Depends(get_conn)):
    _own(conn, user, batch_id)
    return I.undo_plan(conn, batch_id, user)


@router.post("/imports/{batch_id}/undo")
def undo(batch_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("imports.undo")),
         conn: sqlite3.Connection = Depends(get_conn)):
    _own(conn, user, batch_id)
    return I.undo(conn, user, batch_id, body.get("mode", "all"))


@router.get("/import-templates")
def templates(user: CurrentUser = Depends(require("imports.run")), conn: sqlite3.Connection = Depends(get_conn)):
    return I.list_templates(conn)


@router.delete("/import-templates/{template_id}")
def delete_template(template_id: int, user: CurrentUser = Depends(require("imports.run")), conn: sqlite3.Connection = Depends(get_conn)):
    with tx(conn):
        n = conn.execute("DELETE FROM import_templates WHERE id = ?", (template_id,)).rowcount
        if not n:
            raise NotFound("Template not found.")
        audit.log(conn, user.email, "import.template_deleted", "import_template", template_id, None, user.ip)
    return {"ok": True}
