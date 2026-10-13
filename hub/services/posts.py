"""Deployment post projects.

A project is created when an employee is assigned to a client. It keeps the
editable design (text, assets, layout) separately from the employee record,
so changing the wording of a post never changes the master data, and it keeps
every saved draft and the published picture as versions.

Rules
-----
* One project per (employee, client, assignment start). Saving the same
  assignment again does nothing. A new client (or a new start date with the
  same client) is a new deployment and gets its own project.
* A spelling correction of the client name moves the open project to the
  corrected client instead of creating a second project.
* Fields the operator edited are never overwritten by a refresh from the
  employee record; a review flag is added instead when the source changed.
* Missing photo, logo or designation keep the project in "needs_assets". When
  the missing item arrives, the draft is rendered automatically.
"""
from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from .. import audit
from ..db import get_setting, jdump, jload, utcnow
from ..errors import Conflict, HubError, NotFound, ValidationFailed
from ..permissions import CurrentUser
from . import clients as C
from . import employees as emps
from . import photos as PH
from . import postrender as R

STATUSES = ("needs_assets", "ready", "published")
EDITABLE = ("display_name", "designation", "client_display", "pronoun", "announcement", "photo_id", "photo_crop",
            "logo_id", "layout", "custom_photo_path", "custom_logo_path")
SOURCE_FIELDS = ("display_name", "designation", "client_display", "photo_id", "logo_id")
PRONOUNS = ("him", "her", "them")

DEFAULT_POST_SETTINGS: dict[str, Any] = {
    "announcement_template": R.DEFAULT_ANNOUNCEMENT,
    "template_path": "",
    "auto_create_on_assignment": True,
    "email_subject": "Logo request - {client} [{ref}]",
    "email_body": (
        "Hi {first_name},\n\nHope you're doing well!\n\n"
        "According to our records, you are currently placed with {client}. We are preparing an internal post and need the "
        "following details from you:\n\n{asks}\n\n"
        "Please share these details by replying to this email rather than sending them through Teams. Kindly send them as soon "
        "as possible so we can finalize and publish the post.\n\nThank you so much!"
    ),
    "ask_designation": "Designation: [Please enter your designation here]",
    "ask_logo": "Company Logo: Please attach the logo of {client} in a high-resolution PNG or JPG format. A PNG with a transparent "
                "background would be ideal.",
    "reply_subject_match": "Logo request",
    "mail_folder": "",
    "days_back": 30,
    "min_logo_bytes": 3000,
    "ignore_logo_names": ["relay", "signature", "sig-", "logo_relay", "footer", "facebook", "linkedin", "twitter", "instagram",
                          "youtube", "insta", "icon", "social", "spacer", "pixel", "image001", "emailsig", "banner"],
    "request_sender": "",
    "request_cc": [],
    "reminder_hours": 24,
}


def settings(conn) -> dict:
    s = get_setting(conn, "posts", {}) or {}
    return {**DEFAULT_POST_SETTINGS, **s}


def new_uid() -> str:
    return "POST-" + datetime.now().strftime("%Y%m%d") + "-" + secrets.token_hex(3).upper()


def dedupe_key(employee_id: int, client_name: str | None, assignment_start: str | None) -> str:
    return f"{employee_id}:{C.norm_client(client_name)}:{assignment_start or ''}"


# ---------------------------------------------------------------- rows

def project_row(conn: sqlite3.Connection, pid: int) -> dict:
    r = conn.execute("SELECT * FROM post_projects WHERE id = ?", (pid,)).fetchone()
    if r is None:
        raise NotFound("That post project does not exist.")
    return _parse(dict(r))


def _parse(d: dict) -> dict:
    d["data"] = jload(d["data"], {}) or {}
    d["source_snapshot"] = jload(d["source_snapshot"], {}) or {}
    d["missing"] = jload(d["missing"], []) or []
    d["review_flags"] = jload(d["review_flags"], []) or []
    return d


def _employee(conn, emp_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
    return emps.row_record(r) if r else None


def _client(conn, client_id: int | None) -> dict | None:
    if not client_id:
        return None
    r = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    return dict(r) if r else None


def serialize(conn, ctx, proj: dict, user: CurrentUser | None = None) -> dict:
    emp = _employee(conn, proj["employee_id"]) or {}
    cl = _client(conn, proj.get("client_id")) or {}
    data = proj["data"]
    versions = conn.execute("SELECT COUNT(*) FROM post_versions WHERE project_id = ?", (proj["id"],)).fetchone()[0]
    reqs = [dict(r) for r in conn.execute(
        "SELECT id, request_uid, status, to_email, sent_at, send_error, followup_error, created_at FROM post_requests "
        "WHERE project_id = ? ORDER BY id DESC", (proj["id"],)).fetchall()]
    replies = conn.execute("SELECT COUNT(*) FROM post_replies WHERE project_id = ? AND status = 'pending'", (proj["id"],)).fetchone()[0]
    plain, html = R.announcement_texts(data.get("announcement") or settings(conn)["announcement_template"],
                                       data.get("display_name") or emp.get("full_name") or "",
                                       data.get("client_display") or cl.get("name") or "", data.get("pronoun") or "him")
    out = {
        "id": proj["id"], "project_uid": proj["project_uid"], "status": proj["status"], "version": proj["version"],
        "employee": {"id": emp.get("id"), "rhc_id": emp.get("rhc_id"), "full_name": emp.get("full_name"), "designation": emp.get("designation"),
                     "official_email": emp.get("official_email"), "client_name": emp.get("client_name"), "photo_id": emp.get("current_photo_id"),
                     "assignment_status": emp.get("assignment_status"), "assignment_start": emp.get("assignment_start")},
        "client": {"id": cl.get("id"), "name": cl.get("name"), "logo_id": cl.get("current_logo_id")} if cl else None,
        "data": data, "source_snapshot": proj["source_snapshot"], "missing": proj["missing"], "review_flags": proj["review_flags"],
        "draft_version": proj["draft_version"], "published_version": proj["published_version"],
        "has_unpublished_changes": bool(proj["published_version"] and proj["draft_version"] > proj["published_version"]),
        "render_path": proj["render_path"], "has_render": bool(proj["render_path"] and (ctx.paths.data / proj["render_path"]).exists()),
        "last_rendered_at": proj["last_rendered_at"], "published_at": proj["published_at"], "published_by": proj["published_by"],
        "created_at": proj["created_at"], "created_by": proj["created_by"], "created_source": proj["created_source"],
        "updated_at": proj["updated_at"], "updated_by": proj["updated_by"], "versions": versions, "requests": reqs,
        "pending_replies": replies, "announcement_plain": plain, "announcement_html": html,
    }
    return out


def list_projects(conn, ctx, params: dict) -> list[dict]:
    where, args = ["1 = 1"], []
    st = params.get("status")
    if st in STATUSES:
        where.append("p.status = ?")
        args.append(st)
    if params.get("employee_id"):
        where.append("p.employee_id = ?")
        args.append(int(params["employee_id"]))
    q = (params.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        where.append("(e.full_name LIKE ? OR e.rhc_id LIKE ? OR c.name LIKE ? OR p.project_uid LIKE ?)")
        args += [like, like, like, like]
    rows = conn.execute(
        f"SELECT p.* FROM post_projects p JOIN employees e ON e.id = p.employee_id LEFT JOIN clients c ON c.id = p.client_id "
        f"WHERE {' AND '.join(where)} ORDER BY p.updated_at DESC, p.id DESC LIMIT 500", args).fetchall()
    return [serialize(conn, ctx, _parse(dict(r))) for r in rows]


def counts(conn) -> dict:
    out = {s: 0 for s in STATUSES}
    for r in conn.execute("SELECT status, COUNT(*) AS n FROM post_projects GROUP BY status").fetchall():
        out[r["status"]] = r["n"]
    out["flagged"] = conn.execute("SELECT COUNT(*) FROM post_projects WHERE review_flags <> '[]'").fetchone()[0]
    out["pending_replies"] = conn.execute("SELECT COUNT(*) FROM post_replies WHERE status = 'pending'").fetchone()[0]
    return out


# ---------------------------------------------------------------- creation and assignment trigger

def _source_values(conn, emp: dict, client: dict | None) -> dict:
    return {
        "display_name": emp.get("full_name") or "",
        "designation": emp.get("designation") or "",
        "client_display": (client or {}).get("name") or emp.get("client_name") or "",
        "photo_id": emp.get("current_photo_id"),
        "logo_id": (client or {}).get("current_logo_id"),
        "assignment_start": emp.get("assignment_start"),
    }


def create_project(conn, ctx, actor: str, employee_id: int, client_name: str, source: str = "manual",
                   assignment_start: str | None = None, published: bool = False) -> dict:
    """Create a project (no dedupe check here; see ensure_project). Caller owns the transaction."""
    emp = _employee(conn, employee_id)
    if emp is None:
        raise NotFound("Employee not found.")
    client = C.get_or_create(conn, client_name, actor)
    src = _source_values(conn, emp, client)
    src["assignment_start"] = assignment_start
    data = {
        "display_name": src["display_name"], "designation": src["designation"], "client_display": src["client_display"],
        "pronoun": R.guess_pronoun(emp.get("full_name")), "announcement": settings(conn)["announcement_template"],
        "photo_id": src["photo_id"], "photo_crop": None, "logo_id": src["logo_id"], "layout": {}, "overrides": [],
    }
    now = utcnow()
    uid = new_uid()
    pid = conn.execute(
        "INSERT INTO post_projects(project_uid, employee_id, client_id, dedupe_key, status, data, source_snapshot, missing, created_at, "
        "created_by, created_source, updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (uid, employee_id, client["id"], dedupe_key(employee_id, client["name"], assignment_start), "needs_assets", jdump(data),
         jdump(src), "[]", now, actor, source, now, actor)).lastrowid
    audit.log(conn, actor, "post.project_created", "post_project", pid,
              {"uid": uid, "employee_id": employee_id, "client": client["name"], "source": source})
    proj = project_row(conn, pid)
    _recompute(conn, ctx, proj, actor, render=not published)
    if published:
        conn.execute("UPDATE post_projects SET status = 'published', published_at = ?, published_by = ? WHERE id = ?", (now, actor, pid))
    return project_row(conn, pid)


def find_project(conn, employee_id: int, client_name: str | None, assignment_start: str | None) -> dict | None:
    key = dedupe_key(employee_id, client_name, assignment_start)
    r = conn.execute("SELECT * FROM post_projects WHERE dedupe_key = ? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
    return _parse(dict(r)) if r else None


def ensure_project(conn, ctx, actor: str, employee_id: int, client_name: str, assignment_start: str | None,
                   source: str, old_client_name: str | None = None) -> tuple[dict | None, str]:
    """Create the deployment project for an assignment if it does not exist.

    Returns (project, outcome) where outcome is one of created, exists, renamed, moved.
    """
    if not (client_name or "").strip():
        return None, "no_client"
    existing = find_project(conn, employee_id, client_name, assignment_start)
    if existing:
        return existing, "exists"
    norm_new = C.norm_client(client_name)
    # Same client, start date added or changed on an open project: keep the project, update the key.
    for r in conn.execute("SELECT * FROM post_projects WHERE employee_id = ? ORDER BY id DESC", (employee_id,)).fetchall():
        p = _parse(dict(r))
        cl = _client(conn, p.get("client_id"))
        if cl and C.norm_client(cl["name"]) == norm_new and p["status"] != "published" and not p["source_snapshot"].get("assignment_start"):
            conn.execute("UPDATE post_projects SET dedupe_key = ?, updated_at = ? WHERE id = ?",
                         (dedupe_key(employee_id, client_name, assignment_start), utcnow(), p["id"]))
            p["source_snapshot"]["assignment_start"] = assignment_start
            conn.execute("UPDATE post_projects SET source_snapshot = ? WHERE id = ?", (jdump(p["source_snapshot"]), p["id"]))
            return project_row(conn, p["id"]), "moved"
    # Spelling correction: the old client name is similar and the open project still points at it.
    if old_client_name and C.norm_client(old_client_name) != norm_new and C.similar(old_client_name, client_name) >= 0.8:
        old = find_project(conn, employee_id, old_client_name, assignment_start)
        if old is None:
            r = conn.execute("SELECT p.* FROM post_projects p JOIN clients c ON c.id = p.client_id WHERE p.employee_id = ? "
                             "AND c.norm_name = ? ORDER BY p.id DESC LIMIT 1", (employee_id, C.norm_client(old_client_name))).fetchone()
            old = _parse(dict(r)) if r else None
        if old is not None and old["status"] != "published":
            cl = _client(conn, old["client_id"])
            others = conn.execute("SELECT COUNT(*) FROM post_projects WHERE client_id = ? AND id <> ?", (old["client_id"], old["id"])).fetchone()[0]
            emp_refs = conn.execute("SELECT COUNT(*) FROM employees WHERE lower(client_name) = lower(?) AND id <> ?",
                                    (cl["name"], employee_id)).fetchone()[0] if cl else 0
            if cl and others == 0 and emp_refs == 0:
                client = C.rename(conn, actor, cl["id"], client_name)
            else:
                client = C.get_or_create(conn, client_name, actor)
            conn.execute("UPDATE post_projects SET client_id = ?, dedupe_key = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                         (client["id"], dedupe_key(employee_id, client_name, assignment_start), utcnow(), actor, old["id"]))
            p = project_row(conn, old["id"])
            if "client_display" not in p["data"].get("overrides", []):
                p["data"]["client_display"] = client["name"]
            p["source_snapshot"]["client_display"] = client["name"]
            p["source_snapshot"]["logo_id"] = client.get("current_logo_id")
            conn.execute("UPDATE post_projects SET data = ?, source_snapshot = ? WHERE id = ?", (jdump(p["data"]), jdump(p["source_snapshot"]), p["id"]))
            audit.log(conn, actor, "post.client_corrected", "post_project", p["id"], {"from": old_client_name, "to": client_name})
            _recompute(conn, ctx, project_row(conn, p["id"]), actor, render=True)
            return project_row(conn, p["id"]), "renamed"
    proj = create_project(conn, ctx, actor, employee_id, client_name, source=source, assignment_start=assignment_start)
    return proj, "created"


def sync_assignment(conn, ctx, actor: str, before: dict | None, after: dict, source: str = "manual") -> dict:
    """Called after an employee record is saved by a person (manual edit, paste, screenshot import).

    Creates the deployment post when a client assignment is saved, and refreshes open
    projects when the employee's name, designation or photo changed.
    """
    result: dict[str, Any] = {"outcome": None, "project_id": None}
    if not settings(conn).get("auto_create_on_assignment", True):
        return result
    is_client = (after.get("assignment_status") == "Client") and bool((after.get("client_name") or "").strip())
    was_client = bool(before) and before.get("assignment_status") == "Client" and bool((before.get("client_name") or "").strip())
    assignment_changed = (before is None) or any(before.get(k) != after.get(k) for k in ("assignment_status", "client_name", "assignment_start"))
    if is_client and (assignment_changed or not was_client):
        proj, outcome = ensure_project(conn, ctx, actor, after["id"], after["client_name"], after.get("assignment_start"), source,
                                       old_client_name=(before or {}).get("client_name"))
        result.update(outcome=outcome, project_id=proj["id"] if proj else None)
    # Keep open projects in step with the master record.
    if before is None or any(before.get(k) != after.get(k) for k in ("full_name", "designation", "current_photo_id")):
        refresh_employee_projects(conn, ctx, actor, after["id"])
    return result


def refresh_employee_projects(conn, ctx, actor: str, employee_id: int) -> None:
    for r in conn.execute("SELECT id FROM post_projects WHERE employee_id = ?", (employee_id,)).fetchall():
        _recompute(conn, ctx, project_row(conn, r["id"]), actor, render=True)


def refresh_client_projects(conn, ctx, actor: str, client_id: int) -> None:
    for r in conn.execute("SELECT id FROM post_projects WHERE client_id = ?", (client_id,)).fetchall():
        _recompute(conn, ctx, project_row(conn, r["id"]), actor, render=True)


def complete_pending(conn, ctx, actor: str = "system") -> int:
    n = 0
    for r in conn.execute("SELECT id FROM post_projects WHERE status = 'needs_assets'").fetchall():
        p = _recompute(conn, ctx, project_row(conn, r["id"]), actor, render=True)
        if p["status"] != "needs_assets":
            n += 1
    return n


# ---------------------------------------------------------------- refresh, missing, render

def _recompute(conn, ctx, proj: dict, actor: str, render: bool) -> dict:
    """Pull non-overridden fields from the source records, work out what is missing and render when possible."""
    emp = _employee(conn, proj["employee_id"]) or {}
    client = _client(conn, proj.get("client_id"))
    src = _source_values(conn, emp, client)
    data = proj["data"]
    overrides = set(data.get("overrides") or [])
    flags = list(proj["review_flags"])
    snap = dict(proj["source_snapshot"])
    labels = {"display_name": "Name", "designation": "Designation", "client_display": "Client", "photo_id": "Photo", "logo_id": "Logo"}
    for k in SOURCE_FIELDS:
        new_val = src.get(k)
        if k in overrides:
            if snap.get(k) != new_val and new_val not in (None, ""):
                msg = f"{labels[k]} changed on the {'client' if k in ('client_display', 'logo_id') else 'employee'} record"
                if k in ("display_name", "designation", "client_display"):
                    msg += f" from '{snap.get(k) or ''}' to '{new_val}'"
                msg += f"; this post keeps its own edited {labels[k].lower()}."
                if msg not in flags:
                    flags.append(msg)
        else:
            if k in ("photo_id", "logo_id") and data.get(k) and data.get(k) == snap.get(k) and new_val is None:
                pass  # the source lost its asset; keep what the post has
            else:
                data[k] = new_val if new_val not in (None, "") or k in ("photo_id", "logo_id") else data.get(k) or ""
        snap[k] = new_val
    snap["assignment_start"] = proj["source_snapshot"].get("assignment_start", src.get("assignment_start"))
    missing = compute_missing(conn, ctx, data)
    status = proj["status"]
    if status != "published":
        status = "needs_assets" if missing else "ready"
    now = utcnow()
    conn.execute("UPDATE post_projects SET data = ?, source_snapshot = ?, missing = ?, review_flags = ?, status = ?, updated_at = ? WHERE id = ?",
                 (jdump(data), jdump(snap), jdump(missing), jdump(flags), status, now, proj["id"]))
    proj = project_row(conn, proj["id"])
    if render and not missing:
        try:
            _render_to_file(conn, ctx, proj, actor)
        except HubError as exc:
            flags = proj["review_flags"] + [f"Rendering failed: {exc.message}"]
            conn.execute("UPDATE post_projects SET review_flags = ? WHERE id = ?", (jdump(flags), proj["id"]))
    return project_row(conn, proj["id"])


def compute_missing(conn, ctx, data: dict) -> list[str]:
    missing = []
    if not (data.get("display_name") or "").strip():
        missing.append("Employee name is empty.")
    if not (data.get("designation") or "").strip():
        missing.append("Designation is missing. Add it on the employee record, type it in the post, or request it by email.")
    if not data.get("photo_id") and not data.get("custom_photo_path"):
        missing.append("Profile photo is missing. Upload it in Photos or on the employee profile.")
    elif data.get("photo_id") and conn.execute("SELECT 1 FROM photos WHERE id = ?", (data["photo_id"],)).fetchone() is None:
        missing.append("The selected photo no longer exists.")
    if not data.get("logo_id") and not data.get("custom_logo_path"):
        missing.append("Client logo is missing. Upload it under Clients and logos, or request it by email.")
    elif data.get("logo_id") and conn.execute("SELECT 1 FROM client_logos WHERE id = ?", (data["logo_id"],)).fetchone() is None:
        missing.append("The selected logo no longer exists.")
    return missing


def _open_asset(conn, ctx, data: dict, kind: str) -> Image.Image | None:
    if kind == "photo":
        if data.get("custom_photo_path"):
            p = ctx.paths.data / data["custom_photo_path"]
            return Image.open(p) if p.exists() else None
        if data.get("photo_id"):
            r = conn.execute("SELECT stored_path FROM photos WHERE id = ?", (data["photo_id"],)).fetchone()
            if r and (ctx.paths.data / r["stored_path"]).exists():
                return Image.open(ctx.paths.data / r["stored_path"])
        return None
    if data.get("custom_logo_path"):
        p = ctx.paths.data / data["custom_logo_path"]
        return Image.open(p) if p.exists() else None
    if data.get("logo_id"):
        r = conn.execute("SELECT stored_path FROM client_logos WHERE id = ?", (data["logo_id"],)).fetchone()
        if r and (ctx.paths.data / r["stored_path"]).exists():
            return Image.open(ctx.paths.data / r["stored_path"])
    return None


def render_png(conn, ctx, data: dict) -> bytes:
    """Render a post from editable data (used for saved renders and the live preview)."""
    tpl = R.template_path(settings(conn).get("template_path"))
    if not tpl.exists():
        raise HubError(f"The post template is missing: {tpl}")
    profile = _open_asset(conn, ctx, data, "photo")
    logo = _open_asset(conn, ctx, data, "logo")
    try:
        img = R.render(tpl, data.get("display_name") or "", data.get("designation") or "", profile, logo,
                       layout=data.get("layout") or {}, photo_crop=data.get("photo_crop"))
    except ValueError as exc:
        raise HubError(str(exc)) from None
    finally:
        for im in (profile, logo):
            if im is not None:
                im.close()
    return R.to_png_bytes(img)


def _export_dir(ctx, proj: dict) -> Path:
    d = ctx.paths.post_exports / proj["project_uid"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _render_to_file(conn, ctx, proj: dict, actor: str) -> dict:
    png = render_png(conn, ctx, proj["data"])
    digest = R.png_hash(png)
    if proj.get("render_hash") == digest and proj.get("render_path") and (ctx.paths.data / proj["render_path"]).exists():
        return proj
    vno = _next_version(conn, proj["id"])
    name = f"v{vno:03d}_{R.safe_filename(proj['data'].get('display_name') or 'post')}_{R.safe_filename(proj['data'].get('client_display') or 'client')}.png"
    dest = _export_dir(ctx, proj) / name
    PH._atomic_write(dest, png)
    rel = dest.relative_to(ctx.paths.data).as_posix()
    now = utcnow()
    conn.execute("INSERT INTO post_versions(project_id, version_no, kind, data, render_path, render_hash, created_at, created_by) "
                 "VALUES (?,?,?,?,?,?,?,?)", (proj["id"], vno, "draft", jdump(proj["data"]), rel, digest, now, actor))
    conn.execute("UPDATE post_projects SET draft_version = ?, render_path = ?, render_hash = ?, last_rendered_at = ?, updated_at = ? WHERE id = ?",
                 (vno, rel, digest, now, now, proj["id"]))
    audit.log(conn, actor, "post.rendered", "post_project", proj["id"], {"version": vno, "file": rel})
    return project_row(conn, proj["id"])


def render_now(conn, ctx, user: CurrentUser, pid: int) -> dict:
    proj = project_row(conn, pid)
    proj = _recompute(conn, ctx, proj, user.email, render=False)
    if proj["missing"]:
        raise ValidationFailed("The post cannot be generated yet: " + " ".join(proj["missing"]), details={"missing": proj["missing"]})
    _render_to_file(conn, ctx, proj, user.email)
    return project_row(conn, pid)


# ---------------------------------------------------------------- editing

def update_project(conn, ctx, user: CurrentUser, pid: int, expected_version: int | None, changes: dict, note: str | None = None,
                   render: bool = True) -> dict:
    proj = project_row(conn, pid)
    if expected_version is not None and int(expected_version) != proj["version"]:
        raise Conflict("This post was changed by someone else after you opened it. Reload it before saving.")
    data = dict(proj["data"])
    overrides = set(data.get("overrides") or [])
    changed = []
    for k, v in (changes or {}).items():
        if k not in EDITABLE:
            raise ValidationFailed(f"'{k}' cannot be edited on a post.")
        if k == "pronoun":
            v = (v or "him").strip().lower()
            if v not in PRONOUNS:
                raise ValidationFailed("Pronoun must be him, her or them.")
        if k in ("display_name", "designation", "client_display", "announcement"):
            v = (v or "").strip() if isinstance(v, str) else ""
            if k != "announcement" and len(v) > 200:
                raise ValidationFailed(f"{k.replace('_', ' ').title()} is too long.")
        if k in ("photo_id", "logo_id"):
            v = int(v) if v not in (None, "", 0, "0") else None
            if k == "photo_id" and v and conn.execute("SELECT 1 FROM photos WHERE id = ?", (v,)).fetchone() is None:
                raise ValidationFailed("That photo does not exist.")
            if k == "logo_id" and v and conn.execute("SELECT 1 FROM client_logos WHERE id = ?", (v,)).fetchone() is None:
                raise ValidationFailed("That logo does not exist.")
        if k == "layout":
            v = _clean_layout(v)
        if k == "photo_crop":
            v = _clean_crop(v)
        if data.get(k) != v:
            data[k] = v
            changed.append(k)
            if k in SOURCE_FIELDS:
                overrides.add(k)
            if k == "custom_photo_path":
                data["photo_id"] = None
            if k == "custom_logo_path":
                data["logo_id"] = None
    if not changed:
        return proj
    data["overrides"] = sorted(overrides)
    now = utcnow()
    conn.execute("UPDATE post_projects SET data = ?, version = version + 1, updated_at = ?, updated_by = ? WHERE id = ? AND version = ?",
                 (jdump(data), now, user.email, pid, proj["version"]))
    audit.log(conn, user.email, "post.edited", "post_project", pid, {"fields": changed, "note": note}, user.ip)
    proj = _recompute(conn, ctx, project_row(conn, pid), user.email, render=render)
    if not render and not proj["missing"]:
        # Save Draft without rendering still records the editable state as a version.
        vno = _next_version(conn, pid)
        conn.execute("INSERT INTO post_versions(project_id, version_no, kind, data, note, created_at, created_by) VALUES (?,?,?,?,?,?,?)",
                     (pid, vno, "draft", jdump(proj["data"]), note, now, user.email))
        conn.execute("UPDATE post_projects SET draft_version = ? WHERE id = ?", (vno, pid))
    return project_row(conn, pid)


def reset_field(conn, ctx, user: CurrentUser, pid: int, field: str) -> dict:
    """Drop the manual override so the field follows the employee or client record again."""
    proj = project_row(conn, pid)
    data = dict(proj["data"])
    overrides = [o for o in data.get("overrides") or [] if o != field]
    data["overrides"] = overrides
    conn.execute("UPDATE post_projects SET data = ?, version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
                 (jdump(data), utcnow(), user.email, pid))
    return _recompute(conn, ctx, project_row(conn, pid), user.email, render=True)


def _clean_layout(v) -> dict:
    if not isinstance(v, dict):
        return {}
    out = {}
    for section in ("name", "designation", "photo", "logo"):
        vals = v.get(section)
        if not isinstance(vals, dict):
            continue
        clean = {}
        for k, val in vals.items():
            if k in ("mode",) and isinstance(val, str) and val in ("contain", "cover"):
                clean[k] = val
            elif k in ("bold", "upper") and isinstance(val, bool):
                clean[k] = val
            elif k == "color" and isinstance(val, list) and len(val) == 3:
                clean[k] = [max(0, min(255, int(c))) for c in val]
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                clean[k] = round(float(val), 3) if k == "scale" else int(val)
        if clean:
            out[section] = clean
    return out


def _clean_crop(v):
    if not isinstance(v, dict):
        return None
    try:
        crop = {k: round(float(v.get(k, d)), 4) for k, d in (("x", 0), ("y", 0), ("w", 1), ("h", 1))}
    except (TypeError, ValueError):
        return None
    if crop["w"] >= 0.999 and crop["h"] >= 0.999 and crop["x"] <= 0.001 and crop["y"] <= 0.001:
        return None
    return crop


def store_custom_asset(conn, ctx, user: CurrentUser, pid: int, kind: str, data: bytes, filename: str | None) -> dict:
    """A photo or logo used only by this post (not added to the employee or client record)."""
    proj = project_row(conn, pid)
    limits = get_setting(conn, "limits", {})
    fmt, _, _ = PH.image_info(data, limits)
    ext = PH.IMAGE_FORMATS[fmt]
    dest = _export_dir(ctx, proj) / "assets" / f"{kind}_{secrets.token_hex(4)}{ext}"
    PH._atomic_write(dest, data)
    rel = dest.relative_to(ctx.paths.data).as_posix()
    key = "custom_photo_path" if kind == "photo" else "custom_logo_path"
    return update_project(conn, ctx, user, pid, None, {key: rel}, note=f"custom {kind} {filename or ''}")


def publish(conn, ctx, user: CurrentUser, pid: int) -> dict:
    proj = project_row(conn, pid)
    if proj["missing"]:
        raise ValidationFailed("Generate the post before marking it as published: " + " ".join(proj["missing"]))
    if not proj["render_path"] or not (ctx.paths.data / proj["render_path"]).exists():
        proj = _render_to_file(conn, ctx, proj, user.email)
    now = utcnow()
    vno = int(proj["draft_version"] or 0)
    # The published picture is copied so later drafts never overwrite it.
    src = ctx.paths.data / proj["render_path"]
    pub = _export_dir(ctx, proj) / f"published_v{vno:03d}.png"
    PH._atomic_write(pub, src.read_bytes())
    rel = pub.relative_to(ctx.paths.data).as_posix()
    conn.execute("INSERT INTO post_versions(project_id, version_no, kind, data, render_path, render_hash, created_at, created_by) "
                 "VALUES (?,?,?,?,?,?,?,?)",
                 (pid, _next_version(conn, pid), "published", jdump(proj["data"]), rel, proj["render_hash"], now, user.email))
    conn.execute("UPDATE post_projects SET status = 'published', published_version = ?, published_at = ?, published_by = ?, "
                 "updated_at = ?, updated_by = ?, version = version + 1 WHERE id = ?", (vno, now, user.email, now, user.email, pid))
    audit.log(conn, user.email, "post.published", "post_project", pid, {"version": vno, "file": rel}, user.ip)
    return project_row(conn, pid)


def _next_version(conn, pid: int) -> int:
    return (conn.execute("SELECT COALESCE(MAX(version_no), 0) FROM post_versions WHERE project_id = ?", (pid,)).fetchone()[0] or 0) + 1


def unpublish(conn, ctx, user: CurrentUser, pid: int) -> dict:
    proj = project_row(conn, pid)
    conn.execute("UPDATE post_projects SET status = ?, updated_at = ?, updated_by = ?, version = version + 1 WHERE id = ?",
                 ("needs_assets" if proj["missing"] else "ready", utcnow(), user.email, pid))
    audit.log(conn, user.email, "post.unpublished", "post_project", pid, None, user.ip)
    return project_row(conn, pid)


def dismiss_flags(conn, user: CurrentUser, pid: int) -> dict:
    conn.execute("UPDATE post_projects SET review_flags = '[]', updated_at = ?, updated_by = ? WHERE id = ?", (utcnow(), user.email, pid))
    return project_row(conn, pid)


def versions(conn, ctx, pid: int) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM post_versions WHERE project_id = ? ORDER BY id DESC", (pid,)).fetchall():
        d = dict(r)
        d["data"] = jload(d["data"], {})
        d["has_file"] = bool(d["render_path"] and (ctx.paths.data / d["render_path"]).exists())
        out.append(d)
    return out


def version_file(conn, ctx, pid: int, version_id: int) -> Path:
    r = conn.execute("SELECT render_path FROM post_versions WHERE id = ? AND project_id = ?", (version_id, pid)).fetchone()
    if r is None or not r["render_path"]:
        raise NotFound("That version has no picture.")
    p = ctx.paths.data / r["render_path"]
    if not p.exists():
        raise NotFound("The picture file is missing from the data folder.")
    return p


def restore_version(conn, ctx, user: CurrentUser, pid: int, version_id: int) -> dict:
    r = conn.execute("SELECT data FROM post_versions WHERE id = ? AND project_id = ?", (version_id, pid)).fetchone()
    if r is None:
        raise NotFound("Version not found.")
    data = jload(r["data"], {})
    changes = {k: data.get(k) for k in EDITABLE if k in data}
    return update_project(conn, ctx, user, pid, None, changes, note=f"restored version {version_id}")


def render_file(conn, ctx, pid: int) -> Path:
    proj = project_row(conn, pid)
    if not proj["render_path"]:
        raise NotFound("This post has not been generated yet.")
    p = ctx.paths.data / proj["render_path"]
    if not p.exists():
        raise NotFound("The generated picture is missing. Generate it again.")
    return p


def delete_project(conn, ctx, user: CurrentUser, pid: int) -> None:
    proj = project_row(conn, pid)
    if proj["status"] == "published":
        raise ValidationFailed("Published posts are kept. Unpublish it first if it really has to go.")
    conn.execute("DELETE FROM post_projects WHERE id = ?", (pid,))
    audit.log(conn, user.email, "post.deleted", "post_project", pid, {"uid": proj["project_uid"]}, user.ip)


# ---------------------------------------------------------------- backfill

def backfill_candidates(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT e.id, e.rhc_id, e.full_name, e.client_name, e.assignment_start, e.designation, e.current_photo_id FROM employees e "
        "WHERE e.assignment_status = 'Client' AND e.client_name IS NOT NULL AND TRIM(e.client_name) <> '' AND e.archived = 0 "
        "AND e.record_scope = 'directory' ORDER BY e.full_name").fetchall()
    out = []
    for r in rows:
        if find_project(conn, r["id"], r["client_name"], r["assignment_start"]) is None:
            d = dict(r)
            cl = C.find_by_name(conn, r["client_name"])
            d["has_logo"] = bool(cl and cl.get("current_logo_id"))
            out.append(d)
    return out


def backfill(conn, ctx, user: CurrentUser, employee_ids: list[int]) -> dict:
    created, skipped = 0, []
    wanted = {c["id"]: c for c in backfill_candidates(conn)}
    for eid in employee_ids:
        c = wanted.get(int(eid))
        if c is None:
            skipped.append(int(eid))
            continue
        _, outcome = ensure_project(conn, ctx, user.email, c["id"], c["client_name"], c["assignment_start"], "backfill")
        if outcome == "created":
            created += 1
        else:
            skipped.append(int(eid))
    audit.log(conn, user.email, "post.backfill", "post_projects", None, {"created": created, "skipped": skipped}, user.ip)
    return {"created": created, "skipped": skipped}
