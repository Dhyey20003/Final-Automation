"""Screenshot import: upload or paste pictures, review what was read, apply to employee records."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Body, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse

from ..deps import get_conn, get_ctx, require
from ..errors import Forbidden, HubError
from ..permissions import CurrentUser
from ..services import screenshots as S

router = APIRouter(prefix="/api/screenshots", tags=["screenshots"])


def _own(conn, user: CurrentUser, bid: int) -> None:
    b = S._batch(conn, bid)
    if not user.is_admin and b["operator"] != user.email:
        raise Forbidden("This import belongs to another user.")


@router.get("")
def history(user: CurrentUser = Depends(require("imports.run")), conn: sqlite3.Connection = Depends(get_conn)):
    return S.list_batches(conn, user)


@router.post("/upload")
async def upload(files: list[UploadFile] = File(...), ocr_text: str | None = Form(default=None), user: CurrentUser = Depends(require("imports.run")),
                 conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    items = []
    total = 0
    for f in files:
        data = await f.read()
        total += len(data)
        if total > 200 * 1024 * 1024:
            raise HubError("Upload at most 200 MB of screenshots at once.")
        items.append((f.filename or "screenshot.png", data))
    return S.upload(conn, ctx, user, items, ocr_text_override=ocr_text)


@router.get("/{bid}")
def detail(bid: int, user: CurrentUser = Depends(require("imports.run")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    _own(conn, user, bid)
    return S.detail(conn, ctx, user, bid)


@router.get("/{bid}/files/{index}")
def file(bid: int, index: int, user: CurrentUser = Depends(require("imports.run")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    _own(conn, user, bid)
    return FileResponse(S.file_path(conn, ctx, bid, index), headers={"Cache-Control": "no-store"})


@router.post("/{bid}/rows")
def rows(bid: int, body: dict = Body(...), user: CurrentUser = Depends(require("imports.run")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    _own(conn, user, bid)
    return S.update_rows(conn, ctx, user, bid, body.get("rows") or [])


@router.post("/{bid}/text")
def text(bid: int, body: dict = Body(...), user: CurrentUser = Depends(require("imports.run")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    _own(conn, user, bid)
    return S.set_text(conn, ctx, user, bid, body.get("text") or "")


@router.post("/{bid}/commit")
def commit(bid: int, body: dict = Body(default={}), user: CurrentUser = Depends(require("imports.run")), conn: sqlite3.Connection = Depends(get_conn),
           ctx=Depends(get_ctx)):
    _own(conn, user, bid)
    return S.commit(conn, ctx, user, bid, body.get("approve_rows") or [])


@router.post("/{bid}/discard")
def discard(bid: int, user: CurrentUser = Depends(require("imports.run")), conn: sqlite3.Connection = Depends(get_conn)):
    _own(conn, user, bid)
    S.discard(conn, user, bid)
    return {"ok": True}
