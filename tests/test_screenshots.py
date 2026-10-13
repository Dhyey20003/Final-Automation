"""Screenshot imports: OCR text is injected (the OCR engine itself is a Windows dependency) and the
matching, review, apply, post creation and undo paths are exercised."""
from __future__ import annotations

from conftest import emp, image_bytes


def shot(api, text, name="shot.png"):
    return api.post("/api/screenshots/upload", files=[("files", (name, image_bytes("PNG", (250, 250, 250), (800, 400))))], data={"ocr_text": text})


def test_labeled_screenshot_updates_only_visible_fields(api):
    e = emp(api, "RHC0001", "Asha Patel", designation="Developer", official_email="asha@relay.test", department="Engineering")
    d = shot(api, "Employee Details\nName: Asha Patel\nEmail: asha@relay.test\nDesignation: Senior Developer\nClient: Acme Corp\n")
    assert d["batch"]["status"] == "previewed"
    row = d["rows"][0]
    assert row["employee_id"] == e["id"] and row["match_certain"] and row["match_method"] == "official email"
    fields = {c["field"]: c["new"] for c in row["changes"]}
    assert fields == {"designation": "Senior Developer", "client_name": "Acme Corp", "assignment_status": "Client"}
    assert row["status"] == "auto"
    r = api.post(f"/api/screenshots/{d['batch']['id']}/commit", {})
    assert r["summary"]["applied"] == 1 and r["summary"]["posts_created"] == 1
    rec = api.get(f"/api/employees/{e['id']}")["employee"]
    assert rec["designation"] == "Senior Developer" and rec["client_name"] == "Acme Corp" and rec["assignment_status"] == "Client"
    assert rec["department"] == "Engineering" and rec["official_email"] == "asha@relay.test"  # untouched
    assert len(api.get("/api/posts")["projects"]) == 1
    # importing the same screenshot again changes nothing and creates no second post
    d2 = shot(api, "Name: Asha Patel\nEmail: asha@relay.test\nDesignation: Senior Developer\nClient: Acme Corp\n")
    assert d2["rows"][0]["status"] == "unchanged"
    api.post(f"/api/screenshots/{d2['batch']['id']}/commit", {})
    assert len(api.get("/api/posts")["projects"]) == 1
    # it appears in import history and can be undone like any import
    hist = api.get("/api/imports")
    assert any(b["purpose"] == "screenshot" and b["status"] == "committed" for b in hist)
    plan = api.get(f"/api/imports/{d['batch']['id']}/undo-preview")
    assert len(plan["revertible"]) == 3 and not plan["conflicts"]
    api.post(f"/api/imports/{d['batch']['id']}/undo", {"mode": "all"})
    rec = api.get(f"/api/employees/{e['id']}")["employee"]
    assert rec["designation"] == "Developer" and rec["client_name"] is None


def test_ambiguous_and_identity_changes_need_review(api):
    a = emp(api, "RHC0002", "Ravi Shah", designation="Dev")
    b = emp(api, "RHC0003", "Ravi Shah", designation="QA", location="Vadodara")
    c = emp(api, "RHC0004", "Neel Dave", official_email="neel@relay.test")
    text = "Ravi Shah\nDesignation: Lead\n\nName: Neel Dave\nEmail: neel.new@relay.test\nDesignation: Architect\n"
    d = shot(api, text)
    rows = {r["seq"]: r for r in d["rows"]}
    r1, r2 = rows[1], rows[2]
    assert r1["employee_id"] is None and r1["status"] == "review" and {x["id"] for x in r1["candidates"]} == {a["id"], b["id"]}
    assert r2["employee_id"] == c["id"] and r2["status"] == "review"
    assert any(ch["field"] == "official_email" and ch.get("review") for ch in r2["changes"])
    # nothing is applied without a decision
    bid = d["batch"]["id"]
    res = api.post(f"/api/screenshots/{bid}/commit", {})
    assert res["summary"]["applied"] == 0 and res["summary"]["skipped"] == 2
    # a new batch: the operator picks the right Ravi and approves the email change for Neel
    d = shot(api, text)
    rows_payload = [{"extracted": r["extracted"], "employee_id": (b["id"] if r["seq"] == 1 else r["employee_id"])} for r in d["rows"]]
    d = api.post(f"/api/screenshots/{d['batch']['id']}/rows", {"rows": rows_payload})
    assert d["rows"][0]["employee_id"] == b["id"] and d["rows"][0]["match_method"] == "chosen by operator"
    res = api.post(f"/api/screenshots/{d['batch']['id']}/commit", {"approve_rows": [r["id"] for r in d["rows"]]})
    assert res["summary"]["applied"] == 2
    assert api.get(f"/api/employees/{b['id']}")["employee"]["designation"] == "Lead"
    assert api.get(f"/api/employees/{a['id']}")["employee"]["designation"] == "Dev"
    assert api.get(f"/api/employees/{c['id']}")["employee"]["official_email"] == "neel.new@relay.test"


def test_first_last_name_and_table_rows(api):
    e = emp(api, "RHC0005", "John Michael Smith", designation="Engineer")
    other = emp(api, "RHC0006", "Priya Nair")
    d = shot(api, "John Smith   john.smith@relay.test   Senior Engineer\nPriya Nair priya@relay.test Consultant\n")
    rows = {r["seq"]: r for r in d["rows"]}
    assert rows[1]["employee_id"] == e["id"] and rows[1]["match_method"] in ("first and last name", "full name")
    assert {c["field"] for c in rows[1]["changes"]} >= {"designation", "official_email"}
    assert rows[2]["employee_id"] == other["id"]
    # the old "Client - Name" screenshot layout is understood too
    d = shot(api, "Globex - Priya Nair")
    r = d["rows"][0]
    assert r["employee_id"] == other["id"] and {c["field"]: c["new"] for c in r["changes"]}["client_name"] == "Globex"


def test_edited_text_and_unmatched_rows(api):
    emp(api, "RHC0007", "Kavya Rao")
    d = shot(api, "garbled ocr text ~~~ nobody\n")
    assert d["rows"] == [] or all(r["status"] in ("unmatched", "review") for r in d["rows"])
    d = api.post(f"/api/screenshots/{d['batch']['id']}/text", {"text": "Name: Kavya Rao\nDesignation: Designer"})
    assert d["rows"][0]["status"] == "auto"
    api.post(f"/api/screenshots/{d['batch']['id']}/discard")
    assert api.get("/api/screenshots")[0]["status"] == "discarded"


def test_ocr_missing_is_reported_not_hidden(api, monkeypatch):
    emp(api, "RHC0008", "Some One")
    from hub.services import screenshots as S

    monkeypatch.setattr(S, "find_tesseract", lambda configured=None: None)
    d = api.post("/api/screenshots/upload", files=[("files", ("x.png", image_bytes("PNG")))])
    assert d["problems"] and "Tesseract OCR was not found" in d["problems"][0]
    assert d["rows"] == []
    deps = api.get("/api/system/dependencies")
    assert any(i["key"] == "tesseract" and not i["ok"] for i in deps["items"])
