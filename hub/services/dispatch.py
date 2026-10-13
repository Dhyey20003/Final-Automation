"""Email dispatch for a task period: one email per task period with both city ZIPs attached.

Duplicate protection
--------------------
* A unique database index allows only one active send per period (active_send_lock = 1).
  A send stays "active" while in progress, submitted, confirmed, uncertain or manually completed.
* An in-process lock serialises dispatch starts.
* Uncertain results are never retried automatically. They are reconciled against Outlook
  (Sent Items / Outbox / Drafts, found by a dispatch reference) or resolved by the operator.

The export baseline (used by "only new or changed headshots") advances only when a
send is confirmed in Sent Items or the period is manually completed.
"""
from __future__ import annotations

import os
import secrets
import stat
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .. import audit
from ..db import get_setting, jdump, jload, tx, utcnow, utcnow_dt
from ..errors import Conflict, HubError, NotFound, ValidationFailed
from ..permissions import CurrentUser
from ..timeutil import parse_iso_dt, zone
from . import packages as P
from . import tasks as T
from .outlook import MailRequest, build_adapter

SEND_MODES = ("send", "auto")
UNCERTAIN_AFTER = timedelta(hours=2)


def new_uid() -> str:
    return "RH-" + datetime.now().strftime("%Y%m%d") + "-" + secrets.token_hex(4).upper()


def render_email(conn, task: dict, period: dict, pkgs: list[dict]) -> tuple[str, str, list[dict]]:
    cfg = task["config"]
    em = cfg.get("email") or {}
    attach = [p for p in pkgs if p["zip_path"] and p["included_count"]]
    lines = []
    for p in pkgs:
        if p["included_count"]:
            lines.append(f"{p['location']}: {p['included_count']} photos ({p['zip_name']})")
        else:
            lines.append(f"{p['location']}: no photos in this period")
    omitted = sum(p["omitted_count"] for p in pkgs)
    om = "All active employees with photos are included." if not omitted else (
        f"{omitted} employees were left out (for example missing photos); details are in the package manifest.")
    if cfg.get("inclusion") == "changed":
        om = "Only new or changed headshots since the last completed submission are included. " + om
    tz = zone(T.tzname(conn))
    values = {
        "task_name": task["name"], "period_label": period["period_label"], "period_key": period["period_key"],
        "location_list": " and ".join(p["location"] for p in attach) or "no locations",
        "location_summary": "\n".join(lines), "omission_summary": om,
        "sender_name": em.get("signature_name") or "Relay Human Cloud",
        "attachment_names": ", ".join(p["zip_name"] for p in attach), "total_photos": sum(p["included_count"] for p in pkgs),
        "generated_date": datetime.now(tz).strftime("%d %b %Y"),
    }
    subject = P.render_name(em.get("subject_template") or "{task_name} {period_label}", values).strip()
    body = P.render_name(em.get("body_template") or "", values)
    return subject, body, attach


def preview(conn, ctx, period_id: int) -> dict:
    period = T.period_row(conn, period_id)
    task = T.load_task(conn, period["task_id"])
    pkgs = P.current_packages(conn, period_id)
    subject, body, attach = render_email(conn, task, period, pkgs)
    em = task["config"].get("email") or {}
    ref = "\n\nReference: <assigned when sent>" if em.get("include_reference", True) else ""
    return {"subject": subject, "body": body + ref, "to": em.get("to") or [], "cc": em.get("cc") or [],
            "sender": em.get("sender") or "", "attachments": [{"name": p["zip_name"], "bytes": p["bytes"]} for p in attach],
            "total_bytes": sum(p["bytes"] or 0 for p in attach)}


def _active_dispatch(conn, period_id: int):
    return conn.execute("SELECT * FROM dispatches WHERE period_id = ? AND active_send_lock = 1", (period_id,)).fetchone()


def start(conn, ctx, user: CurrentUser, period_id: int, mode: str, confirm_draft_exists: bool = False) -> dict:
    if mode not in ("draft", "send", "auto"):
        raise ValidationFailed("Unknown send mode.")
    with ctx.dispatch_lock:
        period = T.period_row(conn, period_id)
        task = T.load_task(conn, period["task_id"])
        cfg = task["config"]
        em = cfg.get("email") or {}
        adapter = build_adapter(ctx)
        if not adapter.can_send:
            raise HubError("Classic Outlook automation is not available in the current mode. Use the ready folder and "
                           "mark the period as sent manually.")
        if mode == "auto":
            if not task["auto_send_enabled"] or task["needs_revalidation"]:
                raise HubError("Automatic sending is not enabled for this task.")
            if period["backlog"]:
                raise HubError("Missed periods are never sent automatically. Review and send it manually.")
        if period["status"] != "Ready":
            raise HubError(f"The period is '{period['status']}'. Only a Ready period can be emailed.")
        if not em.get("to"):
            raise ValidationFailed("No To recipients are configured for this task. Add them in the task settings.")
        if get_setting(conn, "outlook_mode", "dryrun") == "classic" and mode in SEND_MODES and not em.get("sender"):
            raise ValidationFailed("Choose the sender account in the task settings before sending.")
        pkgs = P.current_packages(conn, period_id)
        problems = P.verify_package_files(ctx, pkgs)
        if problems:
            with tx(conn):
                T.set_period_status(conn, period_id, "Needs Attention", {"message": " ".join(problems), "problems": problems})
            raise HubError("Package files failed verification: " + " ".join(problems))
        subject, body, attach = render_email(conn, task, period, pkgs)
        if not attach:
            raise HubError("There is nothing to attach for this period.")
        total = sum(p["bytes"] or 0 for p in attach)
        if total > float(cfg.get("attachment_limit_mb", 18)) * 1048576:
            raise HubError("The attachments are larger than the configured attachment size limit.")
        uid = new_uid()
        if em.get("include_reference", True):
            body = body.rstrip() + f"\n\nReference: {uid}"
        with tx(conn):
            if mode in SEND_MODES:
                if _active_dispatch(conn, period_id):
                    raise Conflict("This period already has a send in progress, sent, or awaiting review. It will not be sent twice.")
                draft = conn.execute("SELECT 1 FROM dispatches WHERE period_id = ? AND mode = 'draft' AND status = 'draft_created'",
                                     (period_id,)).fetchone()
                if draft and not confirm_draft_exists:
                    if mode == "auto":
                        raise HubError("A draft for this period already exists in Outlook, so it was not sent automatically.")
                    raise ValidationFailed("A draft for this period was already created in Outlook. If you also send the draft, "
                                           "recipients will get two emails. Confirm to send anyway.", code="draft_exists")
            try:
                did = conn.execute(
                    "INSERT INTO dispatches(dispatch_uid, task_id, period_id, package_ids, mode, adapter, status, sender, recipients_to, "
                    "recipients_cc, subject, body, attachments, active_send_lock, created_at, created_by, updated_at) "
                    "VALUES (?,?,?,?,?,?, 'in_progress', ?,?,?,?,?,?,?,?,?,?)",
                    (uid, task["id"], period_id, jdump([p["id"] for p in attach]), mode, adapter.name, em.get("sender") or "",
                     jdump(em.get("to") or []), jdump(em.get("cc") or []), subject, body,
                     jdump([p["zip_name"] for p in attach]), 1 if mode in SEND_MODES else None, utcnow(), user.email, utcnow()),
                ).lastrowid
            except sqlite3.IntegrityError:
                raise Conflict("This period already has an active send. It will not be sent twice.") from None
            audit.log(conn, user.email, f"dispatch.{mode}_started", "dispatch", did,
                      {"uid": uid, "period": period["period_key"], "task": task["task_key"], "to": em.get("to"), "cc": em.get("cc"),
                       "attachments": [p["zip_name"] for p in attach]}, user.ip)
        req = MailRequest(uid, em.get("sender") or "", em.get("to") or [], em.get("cc") or [], subject, body,
                          [ctx.paths.data / p["zip_path"] for p in attach])
        result = adapter.deliver(req, "draft" if mode == "draft" else "send")
        with tx(conn):
            now = utcnow()
            if result.status == "draft_saved":
                conn.execute("UPDATE dispatches SET status='draft_created', outlook_entry_id=?, updated_at=?, notes=? WHERE id=?",
                             (result.entry_id, now, result.message, did))
                T.set_period_status(conn, period_id, "Ready",
                                    {"message": "A draft with attachments was created in Outlook Drafts. Review and send it from Outlook; "
                                                "it will be confirmed automatically when it appears in Sent Items.", "dispatch_uid": uid})
            elif result.status == "submitted":
                conn.execute("UPDATE dispatches SET status='submitted', submitted_at=?, outlook_entry_id=?, updated_at=?, notes=? WHERE id=?",
                             (now, result.entry_id, now, result.message, did))
                T.set_period_status(conn, period_id, "Submitted to Outlook",
                                    {"message": "Outlook accepted the email for sending. Waiting for it to appear in Sent Items. "
                                                "Recipient delivery cannot be confirmed by automation.", "dispatch_uid": uid})
            elif result.status == "uncertain":
                conn.execute("UPDATE dispatches SET status='uncertain', error=?, updated_at=? WHERE id=?", (result.message, now, did))
                T.set_period_status(conn, period_id, "Delivery Uncertain",
                                    {"message": result.message + " It will not be resent automatically. Check Outlook, then record "
                                                                 "whether it was sent.", "dispatch_uid": uid})
            else:
                conn.execute("UPDATE dispatches SET status='failed', active_send_lock=NULL, error=?, updated_at=? WHERE id=?",
                             (result.message, now, did))
                T.set_period_status(conn, period_id, "Failed" if mode in SEND_MODES else "Ready",
                                    {"message": result.message, "dispatch_uid": uid})
            audit.log(conn, user.email, f"dispatch.{result.status}", "dispatch", did, {"uid": uid, "message": result.message}, user.ip)
    if result.status == "submitted":
        reconcile(conn, ctx, dispatch_id=did)
    return dispatch_row(conn, did)


def dispatch_row(conn, did: int) -> dict:
    r = conn.execute("SELECT * FROM dispatches WHERE id = ?", (did,)).fetchone()
    if r is None:
        raise NotFound("Dispatch not found.")
    d = dict(r)
    for k in ("package_ids", "recipients_to", "recipients_cc", "attachments"):
        d[k] = jload(d[k], [])
    return d


def advance_baseline(conn, task_id: int, period: dict, package_ids: list[int], dispatch_id: int | None) -> int:
    n = 0
    for pid in package_ids:
        for it in conn.execute("SELECT * FROM package_items WHERE package_id = ?", (pid,)).fetchall():
            conn.execute(
                "INSERT INTO export_baseline(task_id, employee_id, photo_id, location, period_key, dispatch_id, recorded_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(task_id, employee_id) DO UPDATE SET photo_id=excluded.photo_id, "
                "location=excluded.location, period_key=excluded.period_key, dispatch_id=excluded.dispatch_id, recorded_at=excluded.recorded_at",
                (task_id, it["employee_id"], it["photo_id"], it["location"], period["period_key"], dispatch_id, utcnow()))
            n += 1
    return n


def reconcile(conn, ctx, dispatch_id: int | None = None, actor: str = "system") -> list[dict]:
    """Look for dispatched messages in Outlook and update statuses. Never resends anything."""
    adapter = build_adapter(ctx)
    q = "SELECT * FROM dispatches WHERE status IN ('submitted', 'uncertain', 'draft_created')"
    args: tuple = ()
    if dispatch_id:
        q += " AND id = ?"
        args = (dispatch_id,)
    out = []
    for d in [dict(r) for r in conn.execute(q, args).fetchall()]:
        if d["adapter"] != adapter.name:
            continue
        loc = adapter.locate(d["dispatch_uid"], d["subject"])
        where = loc.get("where")
        with tx(conn):
            cur = conn.execute("SELECT status FROM dispatches WHERE id = ?", (d["id"],)).fetchone()
            if cur["status"] != d["status"]:
                continue
            period = T.period_row(conn, d["period_id"])
            if where == "sent":
                other = conn.execute("SELECT id FROM dispatches WHERE period_id = ? AND active_send_lock = 1 AND id != ?",
                                     (d["period_id"], d["id"])).fetchone()
                lock = 1 if not other else None
                conn.execute("UPDATE dispatches SET status='confirmed_sent', confirmed_at=?, active_send_lock=?, updated_at=?, notes=? WHERE id=?",
                             (utcnow(), lock, utcnow(), f"Found in Sent Items (sent on {loc.get('sent_on')}).", d["id"]))
                T.set_period_status(conn, d["period_id"], "Confirmed in Sent Items",
                                    {"message": "The email was found in Outlook's Sent Items. This confirms Outlook sent it; "
                                                "it does not prove the recipients received it.", "dispatch_uid": d["dispatch_uid"],
                                     "sent_on": loc.get("sent_on")}, actor)
                advance_baseline(conn, d["task_id"], period, jload(d["package_ids"], []), d["id"])
                audit.log(conn, actor, "dispatch.confirmed_in_sent_items", "dispatch", d["id"], {"uid": d["dispatch_uid"]})
                out.append({"id": d["id"], "result": "confirmed"})
            elif d["status"] == "submitted" and where != "outbox":
                age = utcnow_dt() - parse_iso_dt(d["submitted_at"] or d["created_at"])
                if age > UNCERTAIN_AFTER:
                    conn.execute("UPDATE dispatches SET status='uncertain', updated_at=?, notes=? WHERE id=?",
                                 (utcnow(), "Not found in Sent Items or Outbox after 2 hours.", d["id"]))
                    T.set_period_status(conn, d["period_id"], "Delivery Uncertain",
                                        {"message": "The email was submitted but has not appeared in Sent Items. Check Outlook, then "
                                                    "record the outcome. It will not be resent automatically.",
                                         "dispatch_uid": d["dispatch_uid"]})
                    audit.log(conn, actor, "dispatch.became_uncertain", "dispatch", d["id"], {"uid": d["dispatch_uid"]})
                    out.append({"id": d["id"], "result": "uncertain"})
            elif where == "outbox":
                conn.execute("UPDATE dispatches SET notes=?, updated_at=? WHERE id=?",
                             ("Still in the Outbox. Outlook may be offline or waiting to send.", utcnow(), d["id"]))
                out.append({"id": d["id"], "result": "outbox"})
    return out


def resolve_uncertain(conn, ctx, user: CurrentUser, dispatch_id: int, outcome: str, note: str) -> dict:
    if outcome not in ("sent", "not_sent"):
        raise ValidationFailed("Choose whether the email was sent.")
    if not (note or "").strip():
        raise ValidationFailed("Add a note describing what you checked in Outlook.")
    with tx(conn):
        d = dispatch_row(conn, dispatch_id)
        if d["status"] not in ("uncertain", "submitted"):
            raise HubError("Only submitted or uncertain sends can be resolved this way.")
        period = T.period_row(conn, d["period_id"])
        if outcome == "sent":
            conn.execute("UPDATE dispatches SET status='manual_confirmed', confirmed_at=?, updated_at=?, notes=? WHERE id=?",
                         (utcnow(), utcnow(), f"Operator confirmed sent: {note}", dispatch_id))
            T.set_period_status(conn, d["period_id"], "Manually Completed",
                                {"message": f"Confirmed as sent by {user.email} after checking Outlook: {note}"}, user.email)
            advance_baseline(conn, d["task_id"], period, d["package_ids"], dispatch_id)
        else:
            conn.execute("UPDATE dispatches SET status='failed', active_send_lock=NULL, updated_at=?, notes=? WHERE id=?",
                         (utcnow(), f"Operator confirmed NOT sent: {note}", dispatch_id))
            T.set_period_status(conn, d["period_id"], "Ready",
                                {"message": f"Previous attempt recorded as not sent by {user.email}: {note}. You may send again."})
        audit.log(conn, user.email, f"dispatch.resolved_{outcome}", "dispatch", dispatch_id, {"note": note}, user.ip)
    return dispatch_row(conn, dispatch_id)


def ready_folder(conn, ctx, user: CurrentUser, period_id: int) -> dict:
    """Copy the period's ZIPs and email text into a folder for manual sending (new Outlook / web)."""
    period = T.period_row(conn, period_id)
    task = T.load_task(conn, period["task_id"])
    pkgs = P.current_packages(conn, period_id)
    if not pkgs:
        raise HubError("Prepare the packages first.")
    problems = P.verify_package_files(ctx, pkgs)
    if problems:
        raise HubError("Package files failed verification: " + " ".join(problems))
    subject, body, attach = render_email(conn, task, period, pkgs)
    em = task["config"].get("email") or {}
    folder = ctx.paths.ready / f"{task['task_key']}_{period['period_key']}"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.zip"):
        os.chmod(old, stat.S_IREAD | stat.S_IWRITE)
        old.unlink()
    for p in attach:
        shutil.copyfile(ctx.paths.data / p["zip_path"], folder / p["zip_name"])  # copy without the read-only attribute
    text = (f"To: {'; '.join(em.get('to') or [])}\nCC: {'; '.join(em.get('cc') or [])}\nSubject: {subject}\n\n{body}\n\n"
            f"Attach these files: {', '.join(p['zip_name'] for p in attach)}\n")
    (folder / "email.txt").write_text(text, encoding="utf-8")
    with tx(conn):
        audit.log(conn, user.email, "dispatch.ready_folder", "task_period", period_id, {"folder": str(folder)}, user.ip)
    return {"folder": str(folder), "subject": subject, "body": body, "to": em.get("to") or [], "cc": em.get("cc") or [],
            "attachments": [p["zip_name"] for p in attach]}


def manual_complete(conn, ctx, user: CurrentUser, period_id: int, note: str, nothing_sent: bool = False) -> dict:
    if not (note or "").strip():
        raise ValidationFailed("Add a note (for example: 'Sent from new Outlook to it-team@... at 10:15').")
    with ctx.dispatch_lock, tx(conn):
        period = T.period_row(conn, period_id)
        task = T.load_task(conn, period["task_id"])
        if period["status"] in ("Confirmed in Sent Items", "Manually Completed"):
            raise HubError("This period is already complete.")
        if _active_dispatch(conn, period_id):
            raise Conflict("A send for this period is in progress or awaiting review. Resolve it first.")
        pkgs = P.current_packages(conn, period_id)
        attach = [p for p in pkgs if p["zip_path"] and p["included_count"]]
        if not nothing_sent and not attach:
            raise HubError("There are no prepared packages to mark as sent.")
        did = conn.execute(
            "INSERT INTO dispatches(dispatch_uid, task_id, period_id, package_ids, mode, adapter, status, subject, body, attachments, "
            "active_send_lock, created_at, created_by, updated_at, confirmed_at, notes) VALUES (?,?,?,?, 'manual', 'manual', "
            "'manual_completed', '', '', ?, 1, ?, ?, ?, ?, ?)",
            (new_uid(), task["id"], period_id, jdump([] if nothing_sent else [p["id"] for p in attach]),
             jdump([] if nothing_sent else [p["zip_name"] for p in attach]), utcnow(), user.email, utcnow(), utcnow(), note)).lastrowid
        if not nothing_sent:
            advance_baseline(conn, task["id"], period, [p["id"] for p in attach], did)
        T.set_period_status(conn, period_id, "Manually Completed",
                            {"message": ("Marked complete with nothing sent" if nothing_sent else "Marked as sent manually")
                                        + f" by {user.email}: {note}"}, user.email)
        audit.log(conn, user.email, "dispatch.manual_completed", "task_period", period_id,
                  {"note": note, "nothing_sent": nothing_sent, "attachments": [p["zip_name"] for p in attach]}, user.ip)
    return T.period_row(conn, period_id)


def recover_after_restart(conn) -> dict:
    """Run at startup: interrupted work is made visible, never silently retried."""
    with tx(conn):
        prep = conn.execute("UPDATE task_periods SET status='Due', status_detail=?, updated_at=? WHERE status='Preparing'",
                            (jdump({"message": "Preparation was interrupted (application restart); it will be retried."}), utcnow())).rowcount
        rows = conn.execute("SELECT * FROM dispatches WHERE status = 'in_progress'").fetchall()
        for d in rows:
            if d["mode"] == "draft":
                conn.execute("UPDATE dispatches SET status='failed', updated_at=?, error=? WHERE id=?",
                             (utcnow(), "Interrupted by restart while creating a draft; check Outlook Drafts.", d["id"]))
            else:
                conn.execute("UPDATE dispatches SET status='uncertain', updated_at=?, error=? WHERE id=?",
                             (utcnow(), "The application stopped while sending; the result is unknown.", d["id"]))
                T.set_period_status(conn, d["period_id"], "Delivery Uncertain",
                                    {"message": "The application stopped while this email was being sent. It will not be resent "
                                                "automatically. Check Outlook's Sent Items and Outbox, then record the outcome.",
                                     "dispatch_uid": d["dispatch_uid"]})
            audit.log(conn, "system", "dispatch.recovered_after_restart", "dispatch", d["id"], {"mode": d["mode"]})
    return {"preparing_reset": prep, "dispatches_flagged": len(rows)}
