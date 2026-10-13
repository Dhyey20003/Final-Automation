from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import zipfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from conftest import emp, image_bytes
from hub.db import tx, utcnow
from hub.services import dispatch as D
from hub.services import tasks as T
from hub.services.scheduler import Scheduler

TZ = ZoneInfo("Asia/Kolkata")


def today():
    return datetime.now(TZ).date()


def photo(api, emp_id, name, fmt="JPEG", color=(100, 100, 100)):
    return api.post(f"/api/employees/{emp_id}/photo", files={"file": (name, image_bytes(fmt, color))})["photo_id"]


def configure(api, task_id, inclusion="all_active", **extra):
    t = [x for x in api.get("/api/tasks")["tasks"] if x["id"] == task_id][0]
    cfg = {"inclusion": inclusion, "email": {"to": "it-team@relay-sample.test", "cc": ""}}
    if t["kind"] == "IT":
        cfg["schedule"] = {"anchor_date": today().isoformat(), "time": "00:00"}
    cfg.update(extra)
    return api.put(f"/api/tasks/{task_id}", {"version": t["version"], "config": cfg})


def zip_entries(ctx, pkg):
    with zipfile.ZipFile(ctx.paths.data / pkg["zip_path"]) as z:
        return {n: z.read(n) for n in z.namelist()}


def setup_people(api):
    a1 = emp(api, "RHC0001", "Asha Patel", "Ahmedabad")
    a2 = emp(api, "RHC0002", "Ravi Shah", "Ahmedabad")
    v1 = emp(api, "RHC0010", "Vina Rao", "Vadodara")
    a3 = emp(api, "RHC0003", "No Photo", "Ahmedabad")
    pend = emp(api, "RHC0004", "Pending Person", "Ahmedabad", employment_status="Pending Review")
    left = emp(api, "RHC0005", "Left Person", "Ahmedabad", employment_status="Left", last_working_date="2026-01-01")
    photo(api, a1["id"], "headshot.jpg", color=(10, 20, 30))
    photo(api, a2["id"], "headshot.jpg", color=(40, 50, 60))
    photo(api, v1["id"], "vina.png", "PNG", (70, 80, 90))
    photo(api, left["id"], "left.jpg", color=(1, 1, 1))
    return a1, a2, v1, a3, pend


def test_it_packages_city_zips_names_and_omissions(api, ctx):
    a1, a2, v1, a3, pend = setup_people(api)
    configure(api, 1)
    res = api.post("/api/tasks/1/prepare", {})
    period = res["period"]
    assert period["status"] == "Needs Attention"
    pk = {p["location"]: p for p in res["packages"]}
    key = period["period_key"]
    assert pk["Ahmedabad"]["zip_name"] == f"Ahmedabad_IT_Headshots_{key}.zip"
    assert pk["Vadodara"]["zip_name"] == f"Vadodara_IT_Headshots_{key}.zip"
    amd = zip_entries(ctx, pk["Ahmedabad"])
    assert sorted(amd) == ["headshot_RHC0001.jpg", "headshot_RHC0002.jpg"]  # deterministic duplicate resolution
    vad = zip_entries(ctx, pk["Vadodara"])
    assert list(vad) == ["vina.png"] and vad["vina.png"][:8] == b"\x89PNG\r\n\x1a\n"
    man = api.get(f"/api/packages/{pk['Ahmedabad']['id']}/manifest")
    reasons = {e["rhc_id"]: e["reason"] for e in man["exclusions"]}
    assert reasons["RHC0003"] == "No photo on record" and "Pending Review" in reasons["RHC0004"]
    assert "RHC0005" not in reasons and man["complete"] is False
    assert all(e["renamed_for_duplicate"] for e in man["employees"])
    assert man["reporting_period"] and man["generated_at_utc"] and man["employees"][0]["photo_version"] == 1
    # incomplete packages are not labelled complete and cannot be sent until accepted
    api.post(f"/api/periods/{period['id']}/dispatch", {"mode": "send"}, status=400)
    api.post(f"/api/periods/{period['id']}/accept-omissions", {"note": ""}, status=422)
    r = api.post(f"/api/periods/{period['id']}/accept-omissions", {"note": "Photos pending"})
    assert r["status"] == "Ready"
    assert api.get(f"/api/packages/{pk['Ahmedabad']['id']}/manifest")["complete"] is False


def test_regenerate_creates_new_immutable_revision(api, ctx):
    a1, *_ = setup_people(api)
    configure(api, 1)
    res = api.post("/api/tasks/1/prepare", {})
    pid = res["period"]["id"]
    first = {p["location"]: p for p in res["packages"]}["Ahmedabad"]
    path = ctx.paths.data / first["zip_path"]
    assert not os.stat(path).st_mode & stat.S_IWRITE
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    photo(api, a1["id"], "newer.jpg", color=(200, 10, 10))
    api.post(f"/api/periods/{pid}/regenerate")
    d = api.get(f"/api/periods/{pid}")
    revs = sorted((p["revision"], p["status"]) for p in d["packages"] if p["location"] == "Ahmedabad")
    assert revs[0] == (1, "superseded") and revs[1][0] == 2
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha  # old snapshot untouched
    new = [p for p in d["packages"] if p["location"] == "Ahmedabad" and p["revision"] == 2][0]
    assert "newer.jpg" in zip_entries(ctx, new)


def test_hr_names_by_rhc_id_and_real_jpg_conversion(api, ctx):
    a1 = emp(api, "RHC1234", "Jpeg Person", "Ahmedabad")
    v1 = emp(api, "RHC0099", "Png Person", "Vadodara")
    photo(api, a1["id"], "original.jpg")
    photo(api, v1["id"], "transparent.png", "PNG")
    configure(api, 2)
    res = api.post("/api/tasks/2/prepare", {})
    pk = {p["location"]: p for p in res["packages"]}
    month = res["period"]["period_key"]
    assert pk["Ahmedabad"]["zip_name"] == f"Ahmedabad_HR_Headshots_{month}.zip"
    amd, vad = zip_entries(ctx, pk["Ahmedabad"]), zip_entries(ctx, pk["Vadodara"])
    assert list(amd) == ["RHC1234.jpg"] and list(vad) == ["RHC0099.jpg"]
    assert vad["RHC0099.jpg"][:3] == b"\xff\xd8\xff"  # genuinely converted to JPEG
    with ctx.db() as conn:
        stored = conn.execute("SELECT stored_path FROM photos WHERE employee_id = ?", (a1["id"],)).fetchone()[0]
    assert amd["RHC1234.jpg"] == (ctx.paths.data / stored).read_bytes()  # JPEG kept byte-for-byte
    man = api.get(f"/api/packages/{pk['Vadodara']['id']}/manifest")
    assert man["employees"][0]["converted_from"] == "PNG"
    assert (ctx.paths.data / [p for p in (ctx.paths.images / str(v1["id"])).iterdir()][0]).read_bytes()[:4] == b"\x89PNG"  # original untouched


def test_changed_only_baseline_including_location_changes(api, ctx):
    a1, a2, v1, a3, pend = setup_people(api)
    configure(api, 1, inclusion="changed")
    p1 = api.post("/api/tasks/1/prepare", {})["period"]
    api.post(f"/api/periods/{p1['id']}/accept-omissions", {"note": "ok"})
    d = api.post(f"/api/periods/{p1['id']}/dispatch", {"mode": "send"})
    assert d["status"] == "confirmed_sent"
    assert api.get(f"/api/periods/{p1['id']}")["period"]["status"] == "Confirmed in Sent Items"
    with ctx.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM export_baseline WHERE task_id = 1").fetchone()[0] == 3
    k2 = (today() + timedelta(days=14)).isoformat()
    p2 = api.post("/api/tasks/1/prepare", {"period_key": k2})
    assert p2["period"]["status"] == "Needs Attention"
    assert all(p["included_count"] == 0 for p in p2["packages"])
    # a new photo and a location change are both picked up
    photo(api, a1["id"], "updated.jpg", color=(250, 1, 1))
    v = api.get(f"/api/employees/{v1['id']}")["employee"]
    api.patch(f"/api/employees/{v1['id']}", {"version": v["version"], "changes": {"location": "Ahmedabad"}})
    k3 = (today() + timedelta(days=28)).isoformat()
    p3 = api.post("/api/tasks/1/prepare", {"period_key": k3})
    amd = [p for p in p3["packages"] if p["location"] == "Ahmedabad"][0]
    reasons = {e["rhc_id"]: e["change_reason"] for e in amd["manifest"]["employees"]}
    assert reasons == {"RHC0001": "photo changed", "RHC0010": "location changed"}


def test_failed_and_uncertain_sends_never_advance_baseline_or_resend(api, ctx):
    setup_people(api)
    configure(api, 1)
    pid = api.post("/api/tasks/1/prepare", {})["period"]["id"]
    api.post(f"/api/periods/{pid}/accept-omissions", {"note": "ok"})
    api.put("/api/settings/outlook", {"mode": "dryrun", "dryrun_scenario": "fail_before_send"})
    d = api.post(f"/api/periods/{pid}/dispatch", {"mode": "send"})
    assert d["status"] == "failed" and api.get(f"/api/periods/{pid}")["period"]["status"] == "Failed"
    with ctx.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM export_baseline").fetchone()[0] == 0
    # A failed period can be prepared again, then sent with an uncertain result
    api.post(f"/api/periods/{pid}/regenerate")
    api.post(f"/api/periods/{pid}/accept-omissions", {"note": "ok"})
    api.put("/api/settings/outlook", {"mode": "dryrun", "dryrun_scenario": "uncertain"})
    d = api.post(f"/api/periods/{pid}/dispatch", {"mode": "send"})
    assert d["status"] == "uncertain"
    assert api.get(f"/api/periods/{pid}")["period"]["status"] == "Delivery Uncertain"
    api.post(f"/api/periods/{pid}/dispatch", {"mode": "send"}, status=400)  # never resent blindly
    api.post(f"/api/periods/{pid}/regenerate", status=400)
    with ctx.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM export_baseline").fetchone()[0] == 0
    # reconciliation does not find it in Sent Items; operator records "not sent", then resend works
    api.post(f"/api/dispatches/{d['id']}/resolve", {"outcome": "not_sent", "note": ""}, status=422)
    api.post(f"/api/dispatches/{d['id']}/resolve", {"outcome": "not_sent", "note": "Checked Sent Items and Outbox"})
    api.put("/api/settings/outlook", {"mode": "dryrun", "dryrun_scenario": "success"})
    d2 = api.post(f"/api/periods/{pid}/dispatch", {"mode": "send"})
    assert d2["status"] == "confirmed_sent"
    # already complete: a second send is refused
    api.post(f"/api/periods/{pid}/dispatch", {"mode": "send"}, status=400)


def test_database_prevents_two_active_sends(api, ctx):
    setup_people(api)
    configure(api, 1)
    pid = api.post("/api/tasks/1/prepare", {})["period"]["id"]
    import sqlite3

    with ctx.db() as conn:
        now = utcnow()
        sql = ("INSERT INTO dispatches(dispatch_uid, task_id, period_id, mode, status, active_send_lock, created_at, updated_at) "
               "VALUES (?, 1, ?, 'send', 'in_progress', 1, ?, ?)")
        conn.execute(sql, ("A", pid, now, now))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql, ("B", pid, now, now))


def test_draft_then_send_requires_confirmation_and_reconciles(api, ctx):
    setup_people(api)
    configure(api, 1)
    pid = api.post("/api/tasks/1/prepare", {})["period"]["id"]
    api.post(f"/api/periods/{pid}/accept-omissions", {"note": "ok"})
    d = api.post(f"/api/periods/{pid}/dispatch", {"mode": "draft"})
    assert d["status"] == "draft_created"
    rec = json.loads((ctx.paths.dryrun_outbox / "drafts" / d["dispatch_uid"] / "message.json").read_text())
    assert rec["attachments"] == sorted(rec["attachments"]) and len(rec["attachments"]) == 2  # both city ZIPs, one email
    assert rec["to"] == ["it-team@relay-sample.test"] and d["dispatch_uid"] in rec["body"]
    r = api.post(f"/api/periods/{pid}/dispatch", {"mode": "send"}, status=422)
    assert r["error"]["code"] == "draft_exists"
    d2 = api.post(f"/api/periods/{pid}/dispatch", {"mode": "send", "confirm_draft_exists": True})
    assert d2["status"] == "confirmed_sent"


def test_outbox_and_manual_completion(api, ctx):
    setup_people(api)
    configure(api, 1)
    pid = api.post("/api/tasks/1/prepare", {})["period"]["id"]
    api.post(f"/api/periods/{pid}/accept-omissions", {"note": "ok"})
    api.put("/api/settings/outlook", {"mode": "dryrun", "dryrun_scenario": "stuck_in_outbox"})
    d = api.post(f"/api/periods/{pid}/dispatch", {"mode": "send"})
    assert d["status"] == "submitted" and "Outbox" in (d["notes"] or "")
    assert api.get(f"/api/periods/{pid}")["period"]["status"] == "Submitted to Outlook"
    api.post(f"/api/periods/{pid}/manual-complete", {"note": "x"}, status=409)  # unresolved send blocks manual completion
    api.post(f"/api/dispatches/{d['id']}/resolve", {"outcome": "sent", "note": "It left the Outbox at 10:05"})
    assert api.get(f"/api/periods/{pid}")["period"]["status"] == "Manually Completed"
    with ctx.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM export_baseline").fetchone()[0] == 3


def test_manual_mode_ready_folder(api, ctx):
    setup_people(api)
    configure(api, 1)
    ctx.forced_outlook_mode = None
    api.put("/api/settings/outlook", {"mode": "manual"})
    pid = api.post("/api/tasks/1/prepare", {})["period"]["id"]
    api.post(f"/api/periods/{pid}/accept-omissions", {"note": "ok"})
    api.post(f"/api/periods/{pid}/dispatch", {"mode": "send"}, status=400)
    info = api.post(f"/api/periods/{pid}/ready-folder")
    folder = ctx.paths.ready / f"it_headshots_{info['subject'] and api.get(f'/api/periods/{pid}')['period']['period_key']}"
    assert sorted(p.name for p in folder.glob("*.zip")) == sorted(info["attachments"])
    assert "Subject:" in (folder / "email.txt").read_text()
    api.post(f"/api/periods/{pid}/manual-complete", {"note": ""}, status=422)
    api.post(f"/api/periods/{pid}/manual-complete", {"note": "Sent from new Outlook at 10:15"})
    assert api.get(f"/api/periods/{pid}")["period"]["status"] == "Manually Completed"
    assert any(a["action"] == "dispatch.manual_completed" for a in api.get("/api/audit")["rows"])


def test_attachment_size_ceiling(api):
    setup_people(api)
    configure(api, 1, attachment_limit_mb=0.001)
    res = api.post("/api/tasks/1/prepare", {})
    assert any("limit" in p for p in res["detail"]["problems"])


def test_recipients_are_required_and_never_invented(api):
    setup_people(api)
    t = api.get("/api/tasks")["tasks"][0]
    api.put("/api/tasks/1", {"version": t["version"], "config": {"inclusion": "all_active", "schedule": {"anchor_date": today().isoformat(), "time": "00:00"}}})
    pid = api.post("/api/tasks/1/prepare", {})["period"]["id"]
    api.post(f"/api/periods/{pid}/accept-omissions", {"note": "ok"})
    r = api.post(f"/api/periods/{pid}/dispatch", {"mode": "send"}, status=422)
    assert "recipients" in r["error"]["message"].lower()
    api.post("/api/tasks/1/auto-send", {"enabled": True, "confirm": True}, status=422)


def test_restart_recovery(api, ctx):
    setup_people(api)
    configure(api, 1)
    pid = api.post("/api/tasks/1/prepare", {})["period"]["id"]
    with ctx.db() as conn:
        now = utcnow()
        conn.execute("INSERT INTO dispatches(dispatch_uid, task_id, period_id, mode, status, active_send_lock, created_at, updated_at) "
                     "VALUES ('RH-X', 1, ?, 'auto', 'in_progress', 1, ?, ?)", (pid, now, now))
        conn.execute("UPDATE task_periods SET status = 'Preparing' WHERE id != ?", (pid,))
        info = D.recover_after_restart(conn)
        assert info["dispatches_flagged"] == 1
        assert conn.execute("SELECT status FROM dispatches WHERE dispatch_uid = 'RH-X'").fetchone()[0] == "uncertain"
        assert conn.execute("SELECT status FROM task_periods WHERE id = ?", (pid,)).fetchone()[0] == "Delivery Uncertain"


def test_monthly_schedule_uses_last_day_of_short_months():
    cfg = {"schedule": {"type": "monthly", "day_of_month": 31, "time": "10:00"}}
    now = datetime(2027, 3, 1, 12, tzinfo=TZ)
    periods = [(k, due.date()) for k, _, due in T.iter_periods(cfg, TZ, date(2026, 9, 1), now)]
    assert periods[:3] == [("2026-09", date(2026, 9, 30)), ("2026-10", date(2026, 10, 31)), ("2026-11", date(2026, 11, 30))]
    assert ("2027-02", date(2027, 2, 28)) in periods
    cfg = {"schedule": {"type": "interval", "interval_days": 14, "anchor_date": "2026-10-01", "time": "09:00"}}
    keys = [k for k, _, _ in T.iter_periods(cfg, TZ, date(2026, 10, 1), datetime(2026, 11, 1, tzinfo=TZ))]
    assert keys == ["2026-10-01", "2026-10-15", "2026-10-29"]


def test_scheduler_is_restart_safe_flags_backlog_and_auto_sends_latest(api, ctx):
    setup_people(api)
    anchor = today() - timedelta(days=42)
    t = api.get("/api/tasks")["tasks"][0]
    api.put("/api/tasks/1", {"version": t["version"], "config": {"inclusion": "all_active", "schedule": {"anchor_date": anchor.isoformat(), "time": "00:00"},
                                                                  "email": {"to": "it@relay-sample.test"}}})
    api.post("/api/tasks/1/enable", {"enabled": True})
    with ctx.db() as conn, tx(conn):
        cfg = json.loads(conn.execute("SELECT config FROM task_defs WHERE id = 1").fetchone()[0])
        cfg["schedule"]["enabled_from"] = anchor.isoformat()
        conn.execute("UPDATE task_defs SET config = ? WHERE id = 1", (json.dumps(cfg),))
    # Everyone has a photo so the latest period can be complete.
    miss = api.get("/api/photos/missing")
    for m in miss:
        photo(api, m["id"], f"{m['rhc_id']}.jpg", color=(m["id"], 3, 3))
    pend = api.get("/api/employees?view=pending")["rows"]
    for p in pend:
        api.patch(f"/api/employees/{p['id']}", {"version": p["version"], "changes": {"employment_status": "Left", "last_working_date": "2026-01-01"}})
    api.post("/api/tasks/1/auto-send", {"enabled": True, "confirm": False}, status=422)
    api.post("/api/tasks/1/auto-send", {"enabled": True, "confirm": True})
    sched = Scheduler(ctx)
    now = datetime.now(TZ)
    r1 = sched.tick(now=now)
    assert r1["created"] == 4
    with ctx.db() as conn:
        rows = conn.execute("SELECT period_key, status, backlog FROM task_periods ORDER BY period_key").fetchall()
        statuses = [(r["backlog"], r["status"]) for r in rows]
    assert statuses[:3] == [(1, "Needs Attention")] * 3  # missed periods are shown for review, never auto-sent
    assert statuses[3] == (0, "Confirmed in Sent Items")
    r2 = sched.tick(now=now)
    assert r2["created"] == 0 and not r2["sent"]
    with ctx.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_periods").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM dispatches").fetchone()[0] == 1
    # backlog period cannot be auto-sent even if prepared and ready
    with ctx.db() as conn:
        pid = conn.execute("SELECT id FROM task_periods WHERE backlog = 1 ORDER BY period_key LIMIT 1").fetchone()[0]
    api.post(f"/api/periods/{pid}/prepare")
    with ctx.db() as conn:
        with pytest.raises(Exception):
            D.start(conn, ctx, __import__("hub.permissions", fromlist=["system_user"]).system_user(), pid, "auto")


def test_transfer_detection_disables_auto_send(tmp_path):
    from hub.context import AppContext

    c1 = AppContext(tmp_path / "d", outlook_mode="dryrun")
    c1.initialise()
    with c1.db() as conn, tx(conn):
        conn.execute("UPDATE task_defs SET auto_send_enabled = 1")
    c1.release_instance_lock()
    c2 = AppContext(tmp_path / "d", outlook_mode="dryrun")
    c2.hostname = "another-pc"
    c2.initialise()
    with c2.db() as conn:
        rows = conn.execute("SELECT auto_send_enabled, needs_revalidation FROM task_defs").fetchall()
    assert all(r[0] == 0 and r[1] == 1 for r in rows)
    assert c2.transfer_detected


def test_second_instance_is_refused(tmp_path):
    from hub.context import AppContext, InstanceLockError

    c1 = AppContext(tmp_path / "d", outlook_mode="dryrun")
    c1.initialise()
    c2 = AppContext(tmp_path / "d", outlook_mode="dryrun")
    with pytest.raises(InstanceLockError):
        c2.initialise()
