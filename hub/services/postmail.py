"""Logo and designation requests by email, and the replies to them.

* A request is tied to one post project (so to one employee and one client).
  Its reference ``[POSTREQ-...]`` goes into the subject so replies can be matched
  to the exact request even when several people are at the same client.
* Sending is recorded on its own. Follow-up work (updating the project, logging)
  is recorded separately in ``followup_error`` and never changes the send status,
  so a sent email is never reported as unsent or sent again by mistake.
* Replies are matched by reference, then conversation ID, then sender email.
  Anything less certain waits for review. Attachments are previewed and every
  rejected one carries the reason; the operator can always pick another file.
* An older reply never overwrites information accepted from a newer reply.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

from .. import audit
from ..db import jdump, jload, tx, utcnow
from ..errors import HubError, NotFound, ValidationFailed
from ..permissions import CurrentUser
from ..timeutil import parse_iso_dt
from . import clients as C
from . import employees as emps
from . import posts as POSTS
from .outlook import AdapterError, MailRequest, build_adapter

PLACEHOLDER_DESIG = "please enter your designation here"
LOGO_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
REF_RE = re.compile(r"\[(POSTREQ-[A-Z0-9-]+)\]", re.IGNORECASE)


def new_uid() -> str:
    return "POSTREQ-" + datetime.now().strftime("%Y%m%d") + "-" + secrets.token_hex(3).upper()


def compose(conn, ctx, pid: int) -> dict:
    """Preview of the request email for a project: recipients, subject, body, what is being asked for."""
    proj = POSTS.project_row(conn, pid)
    s = POSTS.settings(conn)
    emp = emps.fetch_record(conn, proj["employee_id"])
    client = conn.execute("SELECT * FROM clients WHERE id = ?", (proj["client_id"],)).fetchone()
    client_name = proj["data"].get("client_display") or (client["name"] if client else emp.get("client_name") or "")
    asks = []
    if not (proj["data"].get("designation") or "").strip():
        asks.append("designation")
    if not proj["data"].get("logo_id") and not proj["data"].get("custom_logo_path"):
        asks.append("logo")
    first = (emp.get("full_name") or "").split()[0].title() if (emp.get("full_name") or "").split() else ""
    ask_lines = []
    if "designation" in asks:
        ask_lines.append(s["ask_designation"])
    if "logo" in asks:
        ask_lines.append(s["ask_logo"].replace("{client}", client_name))
    ref = "<assigned when sent>"
    values = {"first_name": first, "client": client_name, "name": emp.get("full_name") or "", "asks": "\n\n".join(ask_lines), "ref": ref}
    subject = _fill(s["email_subject"], values)
    body = _fill(s["email_body"], values)
    previous = [dict(r) for r in conn.execute(
        "SELECT id, request_uid, status, sent_at, to_email, asks FROM post_requests WHERE project_id = ? ORDER BY id DESC", (pid,)).fetchall()]
    for p in previous:
        p["asks"] = jload(p["asks"], [])
    warnings = []
    if not emp.get("official_email"):
        warnings.append("This employee has no official email on record. Enter the address before sending.")
    if not asks:
        warnings.append("Nothing is missing for this post; no request is needed.")
    recent = [p for p in previous if p["status"] in ("submitted", "confirmed", "uncertain", "sending")]
    if recent:
        warnings.append(f"A request was already sent on {recent[0]['sent_at'] or recent[0]['id']}. Sending again is a reminder.")
    return {"to": emp.get("official_email") or "", "cc": list(s.get("request_cc") or []), "sender": s.get("request_sender") or "",
            "subject": subject, "body": body, "asks": asks, "previous": previous, "warnings": warnings,
            "employee": {"id": emp["id"], "full_name": emp["full_name"], "rhc_id": emp["rhc_id"]}, "client": client_name}


def _fill(tpl: str, values: dict) -> str:
    out = tpl or ""
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def send(conn, ctx, user: CurrentUser, pid: int, body: dict) -> dict:
    """Send (or save as draft) the request. The operator has reviewed recipients and text first."""
    proj = POSTS.project_row(conn, pid)
    prev = compose(conn, ctx, pid)
    to = (body.get("to") or prev["to"] or "").strip().lower()
    cc = [str(x).strip() for x in (body.get("cc") if body.get("cc") is not None else prev["cc"]) if str(x).strip()]
    subject = (body.get("subject") or prev["subject"]).strip()
    text = body.get("body") or prev["body"]
    asks = body.get("asks") or prev["asks"]
    mode = "draft" if body.get("mode") == "draft" else "send"
    if not to or "@" not in to:
        raise ValidationFailed("Enter the recipient's email address.")
    if not asks:
        raise ValidationFailed("Nothing is missing for this post, so there is nothing to request.")
    adapter = build_adapter(ctx)
    if not adapter.can_send:
        raise HubError("Classic Outlook automation is not available in the current email mode. Copy the text and send it yourself.")
    s = POSTS.settings(conn)
    from datetime import timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=float(s.get("reminder_hours", 24)))).replace(microsecond=0).isoformat()
    recent = conn.execute("SELECT id, sent_at FROM post_requests WHERE project_id = ? AND status IN ('sending', 'submitted', 'confirmed', 'uncertain') "
                          "AND created_at >= ? ORDER BY id DESC LIMIT 1", (pid, cutoff)).fetchone()
    if recent and not body.get("confirm_duplicate") and mode == "send":
        raise ValidationFailed(f"A request for this post was already sent recently (request #{recent['id']}). "
                               "Confirm that you want to send a reminder.", code="request_exists")
    uid = new_uid()
    subject = subject.replace("<assigned when sent>", uid)
    text = text.replace("<assigned when sent>", uid)
    if uid not in subject:
        subject = f"{subject} [{uid}]"
    now = utcnow()
    with tx(conn):
        rid = conn.execute(
            "INSERT INTO post_requests(request_uid, project_id, employee_id, client_id, asks, to_email, cc, sender, subject, body, status, adapter, "
            "created_at, created_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?, 'sending', ?,?,?,?)",
            (uid, pid, proj["employee_id"], proj["client_id"], jdump(asks), to, jdump(cc), prev["sender"], subject, text, adapter.name,
             now, user.email, now)).lastrowid
        audit.log(conn, user.email, "post.request_started", "post_request", rid, {"uid": uid, "to": to, "asks": asks, "mode": mode}, user.ip)
    result = adapter.deliver(MailRequest(uid, prev["sender"], [to], cc, subject, text, []), mode)
    status = {"submitted": "submitted", "draft_saved": "draft", "uncertain": "uncertain"}.get(result.status, "failed")
    with tx(conn):
        conn.execute("UPDATE post_requests SET status = ?, outlook_entry_id = ?, conversation_id = ?, sent_at = ?, send_error = ?, updated_at = ? WHERE id = ?",
                     (status, result.entry_id, result.conversation_id, now if status in ("submitted", "uncertain") else None,
                      None if status in ("submitted", "draft") else result.message, utcnow(), rid))
        audit.log(conn, user.email, f"post.request_{status}", "post_request", rid, {"uid": uid, "message": result.message}, user.ip)
    # Follow-up work is separate from the send result and never changes it.
    try:
        with tx(conn):
            flags = proj["review_flags"]
            note = {"submitted": f"Request {uid} sent to {to} on {now[:16].replace('T', ' ')} asking for {', '.join(asks)}.",
                    "draft": f"Request {uid} saved as an Outlook draft for {to}.",
                    "uncertain": f"Request {uid}: Outlook gave no clear result. Check Sent Items before sending again.",
                    "failed": f"Request {uid} could not be sent: {result.message}"}[status]
            if note not in flags:
                flags = flags + [note]
            conn.execute("UPDATE post_projects SET review_flags = ?, updated_at = ? WHERE id = ?", (jdump(flags), utcnow(), pid))
    except Exception as exc:  # pragma: no cover - defensive: the send already happened
        with tx(conn):
            conn.execute("UPDATE post_requests SET followup_error = ?, updated_at = ? WHERE id = ?", (str(exc)[:500], utcnow(), rid))
    return request_row(conn, rid)


def request_row(conn, rid: int) -> dict:
    r = conn.execute("SELECT * FROM post_requests WHERE id = ?", (rid,)).fetchone()
    if r is None:
        raise NotFound("Request not found.")
    d = dict(r)
    d["asks"] = jload(d["asks"], [])
    d["cc"] = jload(d["cc"], [])
    return d


def list_requests(conn, status: str | None = None, limit: int = 200) -> list[dict]:
    q = ("SELECT r.*, e.full_name, e.rhc_id, c.name AS client_name FROM post_requests r JOIN employees e ON e.id = r.employee_id "
         "LEFT JOIN clients c ON c.id = r.client_id")
    args: list = []
    if status:
        q += " WHERE r.status = ?"
        args.append(status)
    q += " ORDER BY r.id DESC LIMIT ?"
    args.append(limit)
    out = []
    for r in conn.execute(q, args).fetchall():
        d = dict(r)
        d["asks"] = jload(d["asks"], [])
        d["cc"] = jload(d["cc"], [])
        out.append(d)
    return out


def mark_request(conn, user: CurrentUser, rid: int, status: str, note: str) -> dict:
    """Operator resolves an uncertain or failed request after checking Outlook."""
    if status not in ("submitted", "failed", "cancelled"):
        raise ValidationFailed("Choose sent, not sent, or cancelled.")
    with tx(conn):
        conn.execute("UPDATE post_requests SET status = ?, send_error = ?, updated_at = ? WHERE id = ?",
                     (status, note if status != "submitted" else None, utcnow(), rid))
        audit.log(conn, user.email, "post.request_marked", "post_request", rid, {"status": status, "note": note}, user.ip)
    return request_row(conn, rid)


# ---------------------------------------------------------------- replies

def extract_designation(body: str) -> str:
    if not body:
        return ""
    low = body.lower()
    cut_at = len(body)
    for mk in ("\n-----original message-----", "\nfrom:", "\n________________________________", "\nsent from my", "\non "):
        idx = low.find(mk)
        if idx != -1:
            cut_at = min(cut_at, idx)
    lines = [l.strip() for l in body[:cut_at].splitlines()]
    for i, line in enumerate(lines):
        m = re.match(r"^\s*designation\s*[:\-]\s*(.*)$", line, re.IGNORECASE)
        if not m:
            continue
        value = m.group(1).strip().strip(" .-")
        if value:
            return "" if PLACEHOLDER_DESIG in value.lower() else value[:200]
        for nxt in lines[i + 1:]:
            if nxt.strip():
                cand = nxt.strip(" .-")
                if PLACEHOLDER_DESIG in cand.lower() or re.match(r"^\s*(company logo|logo)\s*[:\-]", cand, re.IGNORECASE):
                    return ""
                return cand[:200]
        return ""
    return ""


def evaluate_attachment(att: dict, s: dict) -> dict:
    """Decide whether a saved attachment can be a logo; always say why not."""
    out = {"name": att.get("name"), "size": att.get("size", 0), "path": att.get("path"), "accepted": False, "reason": "", "width": None, "height": None}
    if att.get("error"):
        out["reason"] = att["error"]
        return out
    ext = Path(att.get("name") or "").suffix.lower()
    if ext not in LOGO_EXTS:
        out["reason"] = f"'{ext or 'no extension'}' is not an image type (PNG, JPG, GIF, BMP, WEBP, TIFF)."
        return out
    if not att.get("path") or not Path(att["path"]).exists():
        out["reason"] = "The attachment could not be saved from Outlook."
        return out
    low = (att.get("name") or "").lower()
    frags = [f.lower() for f in (s.get("ignore_logo_names") or [])]
    hit = next((f for f in frags if f in low), None)
    try:
        with Image.open(att["path"]) as im:
            out["width"], out["height"] = im.size
            im.verify()
    except Exception:
        out["reason"] = "The file is not a readable image."
        return out
    if out["width"] < 60 and out["height"] < 60:
        out["reason"] = f"Only {out['width']} x {out['height']} pixels: an icon or tracking pixel, not a logo."
        return out
    if int(att.get("size") or 0) < int(s.get("min_logo_bytes", 3000)):
        out["reason"] = f"Smaller than {s.get('min_logo_bytes', 3000)} bytes (likely a signature image). You can still choose it manually."
        return out
    if hit:
        out["reason"] = f"The filename contains '{hit}', which usually means a signature or social icon. You can still choose it manually."
        return out
    out["accepted"] = True
    return out


def scan(conn, ctx, user: CurrentUser, recheck: bool = False) -> dict:
    """Read Outlook, record every matching reply once, apply the clear ones, leave the rest for review."""
    s = POSTS.settings(conn)
    adapter = build_adapter(ctx)
    known = {r["to_email"].lower() for r in conn.execute("SELECT to_email FROM post_requests WHERE to_email IS NOT NULL").fetchall()}
    known |= {r["official_email"].lower() for r in conn.execute(
        "SELECT e.official_email FROM employees e JOIN post_projects p ON p.employee_id = e.id WHERE e.official_email IS NOT NULL").fetchall()}
    try:
        mails = adapter.list_replies(int(s.get("days_back", 30)), s.get("reply_subject_match") or "Logo request", s.get("mail_folder") or "",
                                     ctx.paths.reply_staging, known)
    except AdapterError as exc:
        raise HubError(f"Replies could not be read: {exc}")
    summary = {"scanned": len(mails), "new": 0, "applied": 0, "review": 0, "skipped": 0, "errors": []}
    for m in mails:
        existing = conn.execute("SELECT id, status FROM post_replies WHERE entry_id = ?", (m.entry_id,)).fetchone()
        if existing and not recheck:
            summary["skipped"] += 1
            continue
        try:
            with tx(conn):
                rid = _record_reply(conn, ctx, user, m, s, existing["id"] if existing else None)
                reply = reply_row(conn, rid)
                summary["new"] += 1
                if reply["match_certain"] and reply["project_id"]:
                    res = _auto_apply(conn, ctx, user, reply, s)
                    if res == "applied":
                        summary["applied"] += 1
                    else:
                        summary["review"] += 1
                else:
                    summary["review"] += 1
        except Exception as exc:
            summary["errors"].append({"entry_id": m.entry_id, "subject": m.subject, "error": str(exc)[:300]})
            with tx(conn):
                conn.execute("INSERT OR IGNORE INTO post_replies(entry_id, subject, sender_email, received_at, status, error, scanned_at) "
                             "VALUES (?,?,?,?, 'error', ?, ?)", (m.entry_id, m.subject, m.sender_email, m.received_at, str(exc)[:300], utcnow()))
    with tx(conn):
        audit.log(conn, user.email, "post.replies_scanned", "post_replies", None, {k: v for k, v in summary.items() if k != "errors"}, user.ip)
    return summary


def _record_reply(conn, ctx, user, m, s: dict, existing_id: int | None) -> int:
    request = project = None
    method, certain = None, 0
    ref = REF_RE.search(m.subject or "") or REF_RE.search(m.body or "")
    if ref:
        request = conn.execute("SELECT * FROM post_requests WHERE request_uid = ? COLLATE NOCASE", (ref.group(1),)).fetchone()
        if request:
            method, certain = "request reference", 1
    if request is None and m.conversation_id:
        request = conn.execute("SELECT * FROM post_requests WHERE conversation_id = ? ORDER BY id DESC LIMIT 1", (m.conversation_id,)).fetchone()
        if request:
            method, certain = "Outlook conversation", 1
    candidates: list[dict] = []
    if request is None and m.sender_email:
        reqs = conn.execute("SELECT * FROM post_requests WHERE lower(to_email) = ? ORDER BY id DESC", (m.sender_email,)).fetchall()
        projects = {r["project_id"] for r in reqs}
        if not projects:
            emp_ids = [r["id"] for r in conn.execute("SELECT id FROM employees WHERE lower(official_email) = ?", (m.sender_email,)).fetchall()]
            for eid in emp_ids:
                for r in conn.execute("SELECT id FROM post_projects WHERE employee_id = ? AND status <> 'published'", (eid,)).fetchall():
                    projects.add(r["id"])
        if len(projects) == 1:
            pid = projects.pop()
            project = POSTS.project_row(conn, pid)
            request = reqs[0] if reqs else None
            method, certain = "sender email (one open post)", 1
        elif projects:
            method = "sender email (several posts)"
            for pid in projects:
                p = POSTS.project_row(conn, pid)
                candidates.append({"project_id": pid, "label": f"{p['data'].get('display_name')} at {p['data'].get('client_display')}"})
    if request is not None and project is None:
        project = POSTS.project_row(conn, request["project_id"])
    if project is None and not candidates:
        # Subject "Logo request - Client": offer the open posts for that client as candidates.
        mm = re.search(re.escape(s.get("reply_subject_match") or "Logo request") + r"\s*[-:]\s*(.+)", m.subject or "", re.IGNORECASE)
        if mm:
            cname = REF_RE.sub("", mm.group(1)).strip()
            cl = C.find_by_name(conn, cname)
            if cl:
                for r in conn.execute("SELECT id FROM post_projects WHERE client_id = ? AND status <> 'published'", (cl["id"],)).fetchall():
                    p = POSTS.project_row(conn, r["id"])
                    candidates.append({"project_id": r["id"], "label": f"{p['data'].get('display_name')} at {p['data'].get('client_display')}"})
                method = f"client name in subject ({cl['name']})"
    atts = [evaluate_attachment(a, s) for a in m.attachments]
    designation = extract_designation(m.body)
    excerpt = (m.body or "")[:1500]
    row = (m.entry_id, m.conversation_id, request["id"] if request else None, project["id"] if project else None,
           project["employee_id"] if project else None, m.sender_email, m.sender_name, m.subject, m.received_at, excerpt, designation,
           jdump(atts), method, certain, "pending", jdump({"candidates": candidates}), utcnow())
    if existing_id:
        conn.execute("UPDATE post_replies SET conversation_id=?, request_id=?, project_id=?, employee_id=?, sender_email=?, sender_name=?, subject=?, "
                     "received_at=?, body_excerpt=?, designation=?, attachments=?, match_method=?, match_certain=?, status=?, applied=?, scanned_at=?, "
                     "error=NULL WHERE id=?", row[1:] + (existing_id,))
        return existing_id
    return conn.execute("INSERT INTO post_replies(entry_id, conversation_id, request_id, project_id, employee_id, sender_email, sender_name, subject, "
                        "received_at, body_excerpt, designation, attachments, match_method, match_certain, status, applied, scanned_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row).lastrowid


def reply_row(conn, rid: int) -> dict:
    r = conn.execute("SELECT * FROM post_replies WHERE id = ?", (rid,)).fetchone()
    if r is None:
        raise NotFound("Reply not found.")
    d = dict(r)
    d["attachments"] = jload(d["attachments"], [])
    d["applied"] = jload(d["applied"], {})
    return d


def list_replies(conn, status: str | None = None, limit: int = 200) -> list[dict]:
    q = ("SELECT r.*, e.full_name, e.rhc_id, p.project_uid FROM post_replies r LEFT JOIN employees e ON e.id = r.employee_id "
         "LEFT JOIN post_projects p ON p.id = r.project_id")
    args: list = []
    if status:
        q += " WHERE r.status = ?"
        args.append(status)
    q += " ORDER BY r.received_at DESC, r.id DESC LIMIT ?"
    args.append(limit)
    out = []
    for r in conn.execute(q, args).fetchall():
        d = dict(r)
        d["attachments"] = jload(d["attachments"], [])
        d["applied"] = jload(d["applied"], {})
        for a in d["attachments"]:
            a.pop("path", None)
        out.append(d)
    return out


def _received(reply: dict) -> datetime | None:
    try:
        return parse_iso_dt(reply["received_at"]) if reply.get("received_at") else None
    except ValueError:
        return None


def _newer_already_applied(conn, project: dict, reply: dict, what: str) -> bool:
    """True if a reply received later than this one already supplied this item."""
    stamp = (project["data"].get("reply_times") or {}).get(what)
    mine = _received(reply)
    if not stamp or mine is None:
        return False
    try:
        return parse_iso_dt(stamp) > mine
    except ValueError:
        return False


def _auto_apply(conn, ctx, user, reply: dict, s: dict) -> str:
    accepted = [i for i, a in enumerate(reply["attachments"]) if a.get("accepted")]
    choices = {"designation": bool(reply.get("designation")), "attachment_index": None}
    if len(accepted) == 1:
        choices["attachment_index"] = accepted[0]
    elif len(accepted) > 1:
        # pick the largest; the operator can swap it later from the reply
        choices["attachment_index"] = max(accepted, key=lambda i: int(reply["attachments"][i].get("size") or 0))
    if choices["attachment_index"] is None and not choices["designation"]:
        return "review"
    apply(conn, ctx, user, reply["id"], {**choices, "auto": True})
    return "applied"


def apply(conn, ctx, user: CurrentUser, rid: int, choices: dict) -> dict:
    """Apply a reply to its post: logo to the client (shared), designation to the post only (never to other people)."""
    reply = reply_row(conn, rid)
    pid = choices.get("project_id") or reply.get("project_id")
    if not pid:
        raise ValidationFailed("Choose which post this reply belongs to.")
    project = POSTS.project_row(conn, int(pid))
    actor = user.email
    applied = dict(reply.get("applied") or {})
    applied.pop("candidates", None)
    notes = []
    idx = choices.get("attachment_index")
    if idx is not None and idx != "":
        idx = int(idx)
        if idx < 0 or idx >= len(reply["attachments"]):
            raise ValidationFailed("That attachment does not exist.")
        att = reply["attachments"][idx]
        if _newer_already_applied(conn, project, reply, "logo") and not choices.get("force"):
            notes.append("A newer reply already supplied the logo; this older one was not applied.")
        else:
            if not att.get("path") or not Path(att["path"]).exists():
                raise HubError("The attachment file is no longer in the staging folder. Run Check replies again.")
            data = Path(att["path"]).read_bytes()
            if not project.get("client_id"):
                raise HubError("This post has no client; set the client first.")
            try:
                lid = C.store_logo(conn, ctx, actor, project["client_id"], data, att.get("name"), "email reply",
                                   f"{reply['sender_email']} {reply['received_at'] or ''} {reply['entry_id'][-12:]}", allow_same=True)
            except HubError as exc:
                raise HubError(f"The logo could not be saved: {exc.message}. The previous logo is unchanged.") from None
            applied["logo_id"] = lid
            project = POSTS.project_row(conn, project["id"])
            times = dict(project["data"].get("reply_times") or {})
            times["logo"] = reply.get("received_at") or utcnow()
            project["data"]["reply_times"] = times
            if "logo_id" in (project["data"].get("overrides") or []):
                project["data"]["logo_id"] = lid
            conn.execute("UPDATE post_projects SET data = ? WHERE id = ?", (jdump(project["data"]), project["id"]))
            notes.append(f"Logo saved for {C.row(conn, project['client_id'])['name']} (version {C.logo_row(conn, lid)['version_no']}).")
    desig = (choices.get("designation_text") or reply.get("designation") or "").strip()
    if choices.get("designation") and desig:
        if _newer_already_applied(conn, project, reply, "designation") and not choices.get("force"):
            notes.append("A newer reply already supplied the designation; this older one was not applied.")
        else:
            project = POSTS.project_row(conn, project["id"])
            times = dict(project["data"].get("reply_times") or {})
            times["designation"] = reply.get("received_at") or utcnow()
            data = dict(project["data"])
            data["reply_times"] = times
            conn.execute("UPDATE post_projects SET data = ? WHERE id = ?", (jdump(data), project["id"]))
            POSTS.update_project(conn, ctx, user, project["id"], None, {"designation": desig}, note=f"designation from reply {reply['entry_id'][-8:]}",
                                 render=True)
            applied["designation"] = desig
            notes.append(f"Designation '{desig}' applied to this post only.")
            if choices.get("update_employee"):
                emp = emps.fetch_record(conn, project["employee_id"])
                if (emp.get("designation") or "") != desig:
                    emps.apply_update(conn, user, emp["id"], None, {"designation": desig}, source="email-reply", enforce_permissions=False)
                    notes.append("The employee record's designation was updated too.")
                    applied["employee_designation"] = desig
    project = POSTS._recompute(conn, ctx, POSTS.project_row(conn, project["id"]), actor, render=True)
    now = utcnow()
    conn.execute("UPDATE post_replies SET status = 'applied', project_id = ?, employee_id = ?, request_id = COALESCE(request_id, ?), applied = ?, "
                 "resolved_at = ?, resolved_by = ?, error = NULL WHERE id = ?",
                 (project["id"], project["employee_id"], choices.get("request_id"), jdump({**applied, "notes": notes}), now, actor, rid))
    if reply.get("request_id"):
        conn.execute("UPDATE post_requests SET status = 'replied', updated_at = ? WHERE id = ? AND status <> 'replied'", (now, reply["request_id"]))
    audit.log(conn, actor, "post.reply_applied", "post_reply", rid, {"project_id": project["id"], "notes": notes, "auto": bool(choices.get("auto"))},
              getattr(user, "ip", None))
    return reply_row(conn, rid)


def ignore(conn, user: CurrentUser, rid: int, note: str = "") -> dict:
    with tx(conn):
        conn.execute("UPDATE post_replies SET status = 'ignored', resolved_at = ?, resolved_by = ?, applied = ? WHERE id = ?",
                     (utcnow(), user.email, jdump({"note": note}), rid))
        audit.log(conn, user.email, "post.reply_ignored", "post_reply", rid, {"note": note}, user.ip)
    return reply_row(conn, rid)


def attachment_file(conn, rid: int, index: int) -> Path:
    reply = reply_row(conn, rid)
    try:
        att = reply["attachments"][int(index)]
    except (IndexError, ValueError):
        raise NotFound("Attachment not found.") from None
    if not att.get("path") or not Path(att["path"]).exists():
        raise NotFound("The attachment file is no longer available. Run Check replies again.")
    return Path(att["path"])
