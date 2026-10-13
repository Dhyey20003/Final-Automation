from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Body, Depends, File, UploadFile
from fastapi.responses import FileResponse

from ..db import tx
from ..deps import get_conn, get_ctx, require
from ..errors import HubError, ValidationFailed
from ..permissions import CurrentUser
from ..services import photos as PH

router = APIRouter(prefix="/api", tags=["photos"])

NOCACHE = {"Cache-Control": "private, max-age=300"}


@router.get("/photos/missing")
def missing(user: CurrentUser = Depends(require("photos.view")), conn: sqlite3.Connection = Depends(get_conn)):
    return PH.missing_photos(conn, user)


@router.get("/photos/{photo_id}/thumb")
def thumb(photo_id: int, user: CurrentUser = Depends(require("photos.view")), conn: sqlite3.Connection = Depends(get_conn),
          ctx=Depends(get_ctx)):
    return FileResponse(PH.authorised_photo_path(conn, ctx, user, photo_id, True), headers=NOCACHE)


@router.get("/photos/{photo_id}/file")
def full(photo_id: int, user: CurrentUser = Depends(require("photos.view")), conn: sqlite3.Connection = Depends(get_conn),
         ctx=Depends(get_ctx)):
    return FileResponse(PH.authorised_photo_path(conn, ctx, user, photo_id, False), headers=NOCACHE)


@router.post("/employees/{emp_id}/photo")
async def replace_photo(emp_id: int, file: UploadFile = File(...), user: CurrentUser = Depends(require("photos.manage")),
                        conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    data = await file.read()
    with tx(conn):
        pid = PH.store_photo(conn, ctx, user, emp_id, data, file.filename, "upload", "profile upload")
    return {"photo_id": pid, "versions": PH.versions(conn, emp_id)}


@router.get("/photo-batches")
def batches(user: CurrentUser = Depends(require("photos.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    return PH.list_batches(conn)


@router.post("/photo-batches")
def new_batch(body: dict = Body(default={}), user: CurrentUser = Depends(require("photos.manage")),
              conn: sqlite3.Connection = Depends(get_conn)):
    return {"id": PH.create_batch(conn, user, body.get("label"))}


@router.get("/photo-batches/{batch_id}")
def batch(batch_id: int, user: CurrentUser = Depends(require("photos.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    return PH.batch_detail(conn, user, batch_id)


@router.post("/photo-batches/{batch_id}/files")
async def add_files(batch_id: int, files: list[UploadFile] = File(...), user: CurrentUser = Depends(require("photos.manage")),
                    conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    items = []
    total = 0
    for f in files:
        data = await f.read()
        total += len(data)
        if total > 1024 * 1024 * 1024:
            raise HubError("The upload is larger than 1 GB. Upload the files in smaller groups.")
        items.append((f.filename or "file", data))
    return PH.add_files(conn, ctx, user, batch_id, items)


@router.get("/photo-batches/{batch_id}/candidates/{cid}/thumb")
def cand_thumb(batch_id: int, cid: int, user: CurrentUser = Depends(require("photos.manage")),
               conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    return FileResponse(PH.candidate_file(conn, ctx, batch_id, cid, True), headers=NOCACHE)


@router.get("/photo-batches/{batch_id}/candidates/{cid}/image")
def cand_image(batch_id: int, cid: int, user: CurrentUser = Depends(require("photos.manage")),
               conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    return FileResponse(PH.candidate_file(conn, ctx, batch_id, cid, False), headers=NOCACHE)


@router.post("/photo-batches/{batch_id}/mapping")
def mapping(batch_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("photos.manage")),
            conn: sqlite3.Connection = Depends(get_conn)):
    return PH.apply_explicit_mapping(conn, user, batch_id, body.get("text", ""))


@router.post("/photo-batches/{batch_id}/assign")
def assign(batch_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("photos.manage")),
           conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    items = body.get("assignments") or []
    if not items:
        raise ValidationFailed("Select at least one photo to assign.")
    return PH.assign(conn, ctx, user, batch_id, items)


@router.post("/photo-batches/{batch_id}/skip")
def skip(batch_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("photos.manage")),
         conn: sqlite3.Connection = Depends(get_conn)):
    return {"changed": PH.set_candidate_status(conn, user, batch_id, [int(i) for i in body.get("ids") or []],
                                               "pending" if body.get("undo") else "skipped")}


@router.post("/photo-batches/{batch_id}/sequence-preview")
def seq_preview(batch_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("photos.manage")),
                conn: sqlite3.Connection = Depends(get_conn)):
    return PH.sequence_preview(conn, user, batch_id, [int(i) for i in body.get("candidate_ids") or []], body.get("rhc_list", ""))


@router.post("/photo-batches/{batch_id}/sequence-assign")
def seq_assign(batch_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("photos.manage")),
               conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    if not body.get("confirm"):
        raise ValidationFailed("Review the side-by-side preview and confirm the sequence before assigning.")
    prev = PH.sequence_preview(conn, user, batch_id, [int(i) for i in body.get("candidate_ids") or []], body.get("rhc_list", ""))
    if not prev["ok"]:
        raise ValidationFailed("The sequence has problems: " + " ".join(prev["problems"]))
    items = [{"candidate_id": p["candidate_id"], "employee_id": p["employee_id"]} for p in prev["pairs"]]
    return PH.assign(conn, ctx, user, batch_id, items, method_note="sequence mapping confirmed by operator")


@router.get("/photo-batches/{batch_id}/pdfs/{pdf_id}/pages/{page_no}")
def pdf_page(batch_id: int, pdf_id: int, page_no: int, user: CurrentUser = Depends(require("photos.manage")),
             conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    return FileResponse(PH.page_image(conn, ctx, batch_id, pdf_id, page_no), headers=NOCACHE)


@router.post("/photo-batches/{batch_id}/pdfs/{pdf_id}/crop")
def pdf_crop(batch_id: int, pdf_id: int, body: dict = Body(...), user: CurrentUser = Depends(require("photos.manage")),
             conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    rect = body.get("rect")
    if not isinstance(rect, list) or len(rect) != 4:
        raise ValidationFailed("Draw a rectangle on the page first.")
    return PH.pdf_crop(conn, ctx, user, batch_id, pdf_id, int(body.get("page_no", 1)), rect)


@router.post("/photo-batches/{batch_id}/close")
def close(batch_id: int, user: CurrentUser = Depends(require("photos.manage")), conn: sqlite3.Connection = Depends(get_conn),
          ctx=Depends(get_ctx)):
    PH.close_batch(conn, ctx, user, batch_id)
    return {"ok": True}
