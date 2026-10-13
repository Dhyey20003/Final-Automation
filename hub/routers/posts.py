"""Posts page: deployment post projects, clients and logos, email requests and replies, backfill, legacy import."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Body, Depends, File, Response, UploadFile
from fastapi.responses import FileResponse

from .. import audit
from ..db import set_setting, tx
from ..deps import get_conn, get_ctx, require
from ..errors import HubError, ValidationFailed
from ..permissions import CurrentUser
from ..services import clients as C
from ..services import legacy_import as LEGACY
from ..services import postmail as PM
from ..services import posts as P
from ..services import postrender as R
from ..services.screenshots import find_tesseract, ocr_settings

router = APIRouter(prefix="/api", tags=["posts"])
NOCACHE = {"Cache-Control": "no-store"}


# ---------------------------------------------------------------- projects

@router.get("/posts")
def list_posts(status: str = "", q: str = "", employee_id: int | None = None, user: CurrentUser = Depends(require("posts.view")),
               conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    return {"projects": P.list_projects(conn, ctx, {"status": status or None, "q": q, "employee_id": employee_id}), "counts": P.counts(conn)}


@router.get("/posts/settings")
def post_settings(user: CurrentUser = Depends(require("posts.view")), conn: sqlite3.Connection = Depends(get_conn)):
    s = P.settings(conn)
    o = ocr_settings(conn)
    return {"posts": s, "ocr": {**o, "detected": find_tesseract(o.get("tesseract_path"))}, "default_template": str(R.default_template()),
            "layout_defaults": R.DEFAULT_LAYOUT, "pronouns": list(P.PRONOUNS)}


@router.put("/posts/settings")
def put_post_settings(body: dict = Body(...), user: CurrentUser = Depends(require("settings.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    s = P.settings(conn)
    allowed = set(P.DEFAULT_POST_SETTINGS)
    for k, v in (body.get("posts") or {}).items():
        if k not in allowed:
            continue
        if k in ("days_back", "min_logo_bytes", "reminder_hours"):
            try:
                v = max(0, int(v))
            except (TypeError, ValueError):
                raise ValidationFailed(f"{k} must be a whole number.") from None
        if k in ("ignore_logo_names", "request_cc"):
            v = [str(x).strip() for x in (v if isinstance(v, list) else str(v).splitlines()) if str(x).strip()]
        if k == "auto_create_on_assignment":
            v = bool(v)
        s[k] = v
    o = ocr_settings(conn)
    for k in ("tesseract_path", "lang", "psm"):
        if k in (body.get("ocr") or {}):
            o[k] = body["ocr"][k]
    with tx(conn):
        set_setting(conn, "posts", s, user.email)
        set_setting(conn, "ocr", o, user.email)
        audit.log(conn, user.email, "settings.posts", "settings", None, {"posts": list((body.get("posts") or {}).keys()), "ocr": list((body.get("ocr") or {}).keys())}, user.ip)
    return post_settings(user, conn)


@router.post("/posts")
def create_post(body: dict = Body(...), user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    """Create a deployment post by hand for an employee and client (for example a past deployment)."""
    emp_id = int(body.get("employee_id") or 0)
    client = (body.get("client_name") or "").strip()
    if not emp_id or not client:
        raise ValidationFailed("Choose the employee and enter the client name.")
    with tx(conn):
        proj, outcome = P.ensure_project(conn, ctx, user.email, emp_id, client, body.get("assignment_start") or None, "manual-post")
    return {"project": P.serialize(conn, ctx, proj), "outcome": outcome}


@router.get("/posts/backfill")
def backfill_list(user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    return {"candidates": P.backfill_candidates(conn)}


@router.post("/posts/backfill")
def backfill(body: dict = Body(...), user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    ids = [int(x) for x in body.get("employee_ids") or []]
    if not ids:
        raise ValidationFailed("Tick at least one employee.")
    with tx(conn):
        return P.backfill(conn, ctx, user, ids)


@router.post("/posts/complete-pending")
def complete_pending(user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    with tx(conn):
        return {"completed": P.complete_pending(conn, ctx, user.email)}


@router.get("/posts/{pid}")
def get_post(pid: int, user: CurrentUser = Depends(require("posts.view")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    proj = P.project_row(conn, pid)
    out = P.serialize(conn, ctx, proj)
    out["versions_list"] = P.versions(conn, ctx, pid)
    emp = conn.execute("SELECT id FROM employees WHERE id = ?", (proj["employee_id"],)).fetchone()
    from ..services import photos as PH

    out["photo_options"] = PH.versions(conn, proj["employee_id"]) if emp else []
    out["logo_options"] = C.logo_versions(conn, proj["client_id"]) if proj.get("client_id") else []
    out["replies"] = [r for r in PM.list_replies(conn) if r.get("project_id") == pid]
    return out


@router.patch("/posts/{pid}")
def update_post(pid: int, body: dict = Body(...), user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn),
                ctx=Depends(get_ctx)):
    with tx(conn):
        proj = P.update_project(conn, ctx, user, pid, body.get("version"), body.get("changes") or {}, note=body.get("note"),
                                render=bool(body.get("render", True)))
    return P.serialize(conn, ctx, proj)


@router.post("/posts/{pid}/reset-field")
def reset_field(pid: int, body: dict = Body(...), user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn),
                ctx=Depends(get_ctx)):
    field = body.get("field")
    if field not in P.SOURCE_FIELDS:
        raise ValidationFailed("Unknown field.")
    with tx(conn):
        return P.serialize(conn, ctx, P.reset_field(conn, ctx, user, pid, field))


@router.post("/posts/{pid}/render")
def render_post(pid: int, user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    with tx(conn):
        return P.serialize(conn, ctx, P.render_now(conn, ctx, user, pid))


@router.post("/posts/{pid}/preview")
def preview_post(pid: int, body: dict = Body(default={}), user: CurrentUser = Depends(require("posts.view")), conn: sqlite3.Connection = Depends(get_conn),
                 ctx=Depends(get_ctx)):
    """Live preview: render the given editable data without saving anything."""
    proj = P.project_row(conn, pid)
    data = dict(proj["data"])
    for k, v in (body.get("data") or {}).items():
        if k in P.EDITABLE:
            data[k] = v
    if data.get("layout") is not None and not isinstance(data.get("layout"), dict):
        data["layout"] = {}
    try:
        png = P.render_png(conn, ctx, data)
    except HubError as exc:
        raise ValidationFailed(exc.message) from None
    return Response(png, media_type="image/png", headers=NOCACHE)


@router.get("/posts/{pid}/image")
def post_image(pid: int, user: CurrentUser = Depends(require("posts.view")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    return FileResponse(P.render_file(conn, ctx, pid), headers=NOCACHE)


@router.get("/posts/{pid}/export")
def export_post(pid: int, user: CurrentUser = Depends(require("posts.view")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    p = P.render_file(conn, ctx, pid)
    proj = P.project_row(conn, pid)
    name = f"{R.safe_filename(proj['data'].get('display_name') or 'post')}_{R.safe_filename(proj['data'].get('client_display') or 'client')}_v{proj['draft_version']:03d}.png"
    return FileResponse(p, media_type="image/png", filename=name, headers=NOCACHE)


@router.get("/posts/{pid}/versions/{vid}/image")
def version_image(pid: int, vid: int, user: CurrentUser = Depends(require("posts.view")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    return FileResponse(P.version_file(conn, ctx, pid, vid), headers=NOCACHE)


@router.post("/posts/{pid}/versions/{vid}/restore")
def restore_version(pid: int, vid: int, user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    with tx(conn):
        return P.serialize(conn, ctx, P.restore_version(conn, ctx, user, pid, vid))


@router.post("/posts/{pid}/publish")
def publish(pid: int, user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    with tx(conn):
        return P.serialize(conn, ctx, P.publish(conn, ctx, user, pid))


@router.post("/posts/{pid}/unpublish")
def unpublish(pid: int, user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    with tx(conn):
        return P.serialize(conn, ctx, P.unpublish(conn, ctx, user, pid))


@router.post("/posts/{pid}/dismiss-flags")
def dismiss(pid: int, user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    with tx(conn):
        return P.serialize(conn, ctx, P.dismiss_flags(conn, user, pid))


@router.delete("/posts/{pid}")
def delete_post(pid: int, user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    with tx(conn):
        P.delete_project(conn, ctx, user, pid)
    return {"ok": True}


@router.post("/posts/{pid}/asset")
async def custom_asset(pid: int, kind: str, file: UploadFile = File(...), user: CurrentUser = Depends(require("posts.manage")),
                       conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    if kind not in ("photo", "logo"):
        raise ValidationFailed("kind must be photo or logo.")
    data = await file.read()
    with tx(conn):
        return P.serialize(conn, ctx, P.store_custom_asset(conn, ctx, user, pid, kind, data, file.filename))


# ---------------------------------------------------------------- email requests and replies

@router.get("/posts/{pid}/request-preview")
def request_preview(pid: int, user: CurrentUser = Depends(require("posts.email")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    return PM.compose(conn, ctx, pid)


@router.post("/posts/{pid}/request")
def send_request(pid: int, body: dict = Body(...), user: CurrentUser = Depends(require("posts.email")), conn: sqlite3.Connection = Depends(get_conn),
                 ctx=Depends(get_ctx)):
    return PM.send(conn, ctx, user, pid, body)


@router.get("/post-requests")
def requests(status: str = "", user: CurrentUser = Depends(require("posts.view")), conn: sqlite3.Connection = Depends(get_conn)):
    return PM.list_requests(conn, status or None)


@router.post("/post-requests/{rid}/mark")
def mark_request(rid: int, body: dict = Body(...), user: CurrentUser = Depends(require("posts.email")), conn: sqlite3.Connection = Depends(get_conn)):
    note = (body.get("note") or "").strip()
    if not note:
        raise ValidationFailed("Add a note describing what you checked in Outlook.")
    return PM.mark_request(conn, user, rid, body.get("status"), note)


@router.post("/post-replies/scan")
def scan_replies(body: dict = Body(default={}), user: CurrentUser = Depends(require("posts.email")), conn: sqlite3.Connection = Depends(get_conn),
                 ctx=Depends(get_ctx)):
    return PM.scan(conn, ctx, user, recheck=bool(body.get("recheck")))


@router.get("/post-replies")
def replies(status: str = "", user: CurrentUser = Depends(require("posts.view")), conn: sqlite3.Connection = Depends(get_conn)):
    return PM.list_replies(conn, status or None)


@router.get("/post-replies/{rid}/attachments/{index}")
def reply_attachment(rid: int, index: int, user: CurrentUser = Depends(require("posts.view")), conn: sqlite3.Connection = Depends(get_conn)):
    return FileResponse(PM.attachment_file(conn, rid, index), headers=NOCACHE)


@router.post("/post-replies/{rid}/apply")
def apply_reply(rid: int, body: dict = Body(...), user: CurrentUser = Depends(require("posts.email")), conn: sqlite3.Connection = Depends(get_conn),
                ctx=Depends(get_ctx)):
    with tx(conn):
        return PM.apply(conn, ctx, user, rid, body)


@router.post("/post-replies/{rid}/ignore")
def ignore_reply(rid: int, body: dict = Body(default={}), user: CurrentUser = Depends(require("posts.email")), conn: sqlite3.Connection = Depends(get_conn)):
    return PM.ignore(conn, user, rid, body.get("note") or "")


# ---------------------------------------------------------------- clients and logos

@router.get("/clients")
def clients(q: str = "", user: CurrentUser = Depends(require("posts.view")), conn: sqlite3.Connection = Depends(get_conn)):
    return C.list_clients(conn, q)


@router.post("/clients")
def add_client(body: dict = Body(...), user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    with tx(conn):
        return C.get_or_create(conn, body.get("name") or "", user.email)


@router.post("/clients/sync")
def sync_clients(user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn)):
    with tx(conn):
        return {"created": C.sync_from_employees(conn, user.email)}


@router.get("/clients/{cid}")
def get_client(cid: int, user: CurrentUser = Depends(require("posts.view")), conn: sqlite3.Connection = Depends(get_conn)):
    return {"client": C.row(conn, cid), "logos": C.logo_versions(conn, cid),
            "employees": [dict(r) for r in conn.execute(
                "SELECT e.id, e.rhc_id, e.full_name, e.designation, e.assignment_start FROM employees e JOIN clients c ON c.id = ? "
                "WHERE lower(e.client_name) = lower(c.name) AND e.archived = 0 ORDER BY e.full_name", (cid,)).fetchall()],
            "projects": [dict(r) for r in conn.execute("SELECT id, project_uid, status, employee_id FROM post_projects WHERE client_id = ?", (cid,)).fetchall()]}


@router.patch("/clients/{cid}")
def rename_client(cid: int, body: dict = Body(...), user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn),
                  ctx=Depends(get_ctx)):
    with tx(conn):
        client = C.rename(conn, user.email, cid, body.get("name") or "")
        P.refresh_client_projects(conn, ctx, user.email, client["id"])
    return client


@router.post("/clients/{cid}/logo")
async def upload_logo(cid: int, file: UploadFile = File(...), user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn),
                      ctx=Depends(get_ctx)):
    data = await file.read()
    with tx(conn):
        lid = C.store_logo(conn, ctx, user.email, cid, data, file.filename, "upload", "Clients page upload")
        P.refresh_client_projects(conn, ctx, user.email, cid)
    return {"logo_id": lid, "logos": C.logo_versions(conn, cid)}


@router.post("/clients/{cid}/logos/{lid}/make-current")
def make_current(cid: int, lid: int, user: CurrentUser = Depends(require("posts.manage")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    with tx(conn):
        C.set_current_logo(conn, user.email, cid, lid)
        P.refresh_client_projects(conn, ctx, user.email, cid)
    return {"logos": C.logo_versions(conn, cid)}


@router.get("/logos/{lid}/file")
def logo_file(lid: int, user: CurrentUser = Depends(require("posts.view")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    return FileResponse(C.logo_path(conn, ctx, lid), headers={"Cache-Control": "private, max-age=300"})


@router.get("/logos/{lid}/thumb")
def logo_thumb(lid: int, user: CurrentUser = Depends(require("posts.view")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    return FileResponse(C.logo_path(conn, ctx, lid, thumb=True), headers={"Cache-Control": "private, max-age=300"})


# ---------------------------------------------------------------- legacy Post Studio data

@router.post("/posts-legacy/preview")
def legacy_preview(body: dict = Body(...), user: CurrentUser = Depends(require("settings.manage")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    return LEGACY.preview(conn, ctx, user, body.get("folder") or "")


@router.post("/posts-legacy/commit")
def legacy_commit(body: dict = Body(...), user: CurrentUser = Depends(require("settings.manage")), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    return LEGACY.commit(conn, ctx, user, body.get("folder") or "", body.get("decisions") or {}, bool(body.get("mark_published", True)))
