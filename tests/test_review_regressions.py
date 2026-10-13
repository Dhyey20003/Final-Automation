"""Regression tests for issues found in the internal code review."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from conftest import HEAD, csv_bytes, emp, image_bytes, make_user, run_import
from hub.services.outlook import _com_message, _is_denied


def test_promotion_never_touches_fields_the_user_cannot_edit(api, app):
    hist_head = HEAD + ["Remarks", "Client Name"]
    run_import(api, csv_bytes([hist_head, ["RHC0900", "Hist Person", "", "", "Ahmedabad", "", "Left", "secret note", "SecretCo"]]), "historical")
    ed = make_user(api, app, "ed@relay.test", "editor", restricted_fields=["notes", "client_name"])
    _, plan, _ = run_import(ed, csv_bytes([HEAD, ["RHC0900", "Hist Person", "", "", "Ahmedabad", "", ""]]), "roster",
                            options={"scope": ["Ahmedabad"], "enrich_fields": []})
    changed = {c["field"] for c in plan["rows"][0]["changes"]}
    assert "notes" not in changed and "client_name" not in changed
    assert "secret" not in str(plan) and "SecretCo" not in str(plan)
    e = api.get("/api/employees?q=RHC0900")["rows"][0]
    assert e["notes"] == "secret note" and e["client_name"] == "SecretCo"


def test_period_and_import_scope(api, app):
    a = emp(api, "RHC0001", "Amd", "Ahmedabad")
    api.post(f"/api/employees/{a['id']}/photo", files={"file": ("a.jpg", image_bytes())})
    t = api.get("/api/tasks")["tasks"][0]
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    api.put("/api/tasks/1", {"version": t["version"], "config": {"inclusion": "all_active", "email": {"to": "it@relay-sample.test"},
                                                                  "schedule": {"anchor_date": today, "time": "00:00"}}})
    pid = api.post("/api/tasks/1/prepare", {})["period"]["id"]
    op = make_user(api, app, "vadop@relay.test", "export_operator", location_scope=["Vadodara"])
    d = op.get(f"/api/periods/{pid}")
    assert all(p["location"] == "Vadodara" for p in d["packages"]) and d["email_preview"] is None
    op.post(f"/api/periods/{pid}/manual-complete", {"note": "x"}, status=403)
    op.post(f"/api/periods/{pid}/accept-omissions", {"note": "x"}, status=403)
    op.post(f"/api/periods/{pid}/ready-folder", status=403)
    # import batches belong to their operator
    bid, _, _ = run_import(api, csv_bytes([HEAD, ["RHC0001", "Amd", "X", "", "", "", ""]]), "partial")
    ed = make_user(api, app, "ed2@relay.test", "editor", location_scope=["Vadodara"])
    ed.get(f"/api/imports/{bid}/report", status=403)
    ed.get(f"/api/imports/{bid}/undo-preview", status=403)
    assert ed.get("/api/imports") == []


def test_assignment_history_respects_field_restrictions(api, app):
    e = emp(api, "RHC0001", "Hist", assignment_status="Client", client_name="SecretCo")
    api.patch(f"/api/employees/{e['id']}", {"version": e["version"], "changes": {"assignment_status": "Bench", "client_name": None,
                                                                                 "bench_start": "2026-09-01"}})
    v = make_user(api, app, "v@relay.test", "viewer", restricted_fields=["client_name"])
    hist = v.get(f"/api/employees/{e['id']}")["assignment_history"]
    assert hist and "client_name" not in hist[0] and "SecretCo" not in str(hist)


def test_token_drops_fields_marked_sensitive_later(api, app):
    api.post("/api/fields", {"label": "Salary band", "field_type": "text"})
    emp(api, "RHC0001", "Tok", cf_salary_band="B3")
    secret = api.post("/api/tokens", {"name": "t", "allowed_fields": ["rhc_id", "cf_salary_band"]})["secret"]
    c = TestClient(app)
    h = {"Authorization": f"Bearer {secret}"}
    assert c.get("/api/fetch/v1/employees", headers=h).json()["employees"][0]["cf_salary_band"] == "B3"
    fid = api.get("/api/fields")["custom"][0]["id"]
    api.put(f"/api/fields/{fid}", {"sensitive": True})
    assert "cf_salary_band" not in c.get("/api/fetch/v1/employees", headers=h).json()["employees"][0]


def test_required_field_added_later_does_not_block_other_edits_or_photos(api, ctx):
    e = emp(api, "RHC0001", "Old Record")
    api.post("/api/fields", {"label": "Blood group", "field_type": "text", "required": True})
    api.patch(f"/api/employees/{e['id']}", {"version": e["version"], "changes": {"designation": "Analyst"}})
    bid = api.post("/api/photo-batches", {})["id"]
    d = api.post(f"/api/photo-batches/{bid}/files", files=[("files", ("RHC0001.jpg", image_bytes()))])
    res = api.post(f"/api/photo-batches/{bid}/assign", {"assignments": [{"candidate_id": d["candidates"][0]["id"], "employee_id": e["id"]}]})
    assert res["assigned"] and not res["problems"]
    with ctx.db() as conn:
        cur = conn.execute("SELECT current_photo_id FROM employees WHERE id = ?", (e["id"],)).fetchone()[0]
        flagged = conn.execute("SELECT id FROM photos WHERE employee_id = ? AND is_current = 1", (e["id"],)).fetchall()
    assert [r[0] for r in flagged] == [cur]


def test_failed_assignment_rolls_back_cleanly(api, ctx):
    e = emp(api, "RHC0001", "Scope")
    bid = api.post("/api/photo-batches", {})["id"]
    d = api.post(f"/api/photo-batches/{bid}/files", files=[("files", ("a.jpg", image_bytes())), ("files", ("b.jpg", image_bytes(color=(5, 5, 5))))])
    c1, c2 = [c["id"] for c in d["candidates"]]
    api.post(f"/api/photo-batches/{bid}/assign", {"assignments": [{"candidate_id": c1, "employee_id": e["id"]}]})
    # second assignment of an identical image to the same employee fails; nothing half-written remains
    d = api.post(f"/api/photo-batches/{bid}/files", files=[("files", ("a_again.jpg", image_bytes()))])
    c3 = [c for c in d["candidates"] if c["original_name"] == "a_again.jpg"][0]["id"]
    res = api.post(f"/api/photo-batches/{bid}/assign", {"assignments": [{"candidate_id": c3, "employee_id": e["id"]}]})
    assert res["problems"]
    with ctx.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM photos WHERE employee_id = ?", (e["id"],)).fetchone()[0] == 1


def test_outlook_disp_exception_codes_are_unwrapped():
    class Err(Exception):
        hresult = -2147352567
        excepinfo = (0, "Microsoft Outlook", "Operation aborted", None, 0, -2147467260)

    assert _is_denied(Err()) and "security prompt" in _com_message(Err())
