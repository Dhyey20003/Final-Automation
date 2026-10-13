"""Deployment posts: automatic creation from client assignments, dedupe, missing assets, editing, versions,
publishing, clients and logos, email requests and replies (dry run)."""
from __future__ import annotations

import base64
import json

from conftest import emp, image_bytes


def assign(api, e, client, **extra):
    cur = api.get(f"/api/employees/{e['id']}")["employee"]
    return api.patch(f"/api/employees/{e['id']}", {"version": cur["version"], "changes": {"assignment_status": "Client", "client_name": client, **extra}})


def projects(api, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return api.get("/api/posts" + ("?" + q if q else ""))["projects"]


def noisy_png(size=(400, 200)) -> bytes:
    """A real-looking logo file (solid colours compress below the 3000 byte signature threshold)."""
    import io
    import random

    from PIL import Image

    rnd = random.Random(7)
    im = Image.new("RGB", size)
    im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(size[0] * size[1])])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def add_logo(api, client_id, color=(10, 20, 200)):
    return api.post(f"/api/clients/{client_id}/logo", files={"file": ("logo.png", image_bytes("PNG", color, (240, 120)))})


def test_assignment_creates_one_project_and_resaving_does_not_duplicate(api):
    e = emp(api, "RHC0001", "Asha Patel", designation="Developer")
    r = assign(api, e, "Acmee Corp")  # typo on purpose
    assert r["post"]["outcome"] == "created"
    ps = projects(api)
    assert len(ps) == 1 and ps[0]["status"] == "needs_assets"
    assert any("photo" in m.lower() for m in ps[0]["missing"]) and any("logo" in m.lower() for m in ps[0]["missing"])
    # saving the same assignment again (unchanged) and re-saving other fields creates nothing new
    cur = api.get(f"/api/employees/{e['id']}")["employee"]
    api.patch(f"/api/employees/{e['id']}", {"version": cur["version"], "changes": {"assignment_status": "Client", "client_name": "Acmee Corp"}})
    cur = api.get(f"/api/employees/{e['id']}")["employee"]
    api.patch(f"/api/employees/{e['id']}", {"version": cur["version"], "changes": {"department": "Engineering"}})
    assert len(projects(api)) == 1
    # "Acme Corp" and "Acme Corporation" are the same client: nothing new
    r = assign(api, e, "Acmee Corporation")
    assert r["post"]["outcome"] == "exists" and len(projects(api)) == 1
    # a spelling correction moves the project instead of creating a second one
    r = assign(api, e, "Acme Corporation")
    assert r["post"]["outcome"] == "renamed"
    ps = projects(api)
    assert len(ps) == 1 and ps[0]["client"]["name"] == "Acme Corporation" and ps[0]["data"]["client_display"] == "Acme Corporation"
    # a genuinely new deployment is a new project; the old one is preserved
    r = assign(api, e, "Globex", assignment_start="2026-09-01")
    assert r["post"]["outcome"] == "created"
    ps = projects(api)
    assert len(ps) == 2 and {p["client"]["name"] for p in ps} == {"Acme Corporation", "Globex"}


def test_missing_assets_complete_automatically(api, ctx):
    e = emp(api, "RHC0002", "Ravi Shah")  # no designation yet
    assign(api, e, "Initech")
    p = projects(api)[0]
    assert p["status"] == "needs_assets" and len(p["missing"]) == 3
    # photo arrives
    api.post(f"/api/employees/{e['id']}/photo", files={"file": ("ravi.jpg", image_bytes())})
    p = api.get(f"/api/posts/{p['id']}")
    assert not any("photo" in m.lower() for m in p["missing"]) and p["data"]["photo_id"] == api.get(f"/api/employees/{e['id']}")["employee"]["photo_id"]
    # designation arrives on the master record
    cur = api.get(f"/api/employees/{e['id']}")["employee"]
    api.patch(f"/api/employees/{e['id']}", {"version": cur["version"], "changes": {"designation": "QA Engineer"}})
    p = api.get(f"/api/posts/{p['id']}")
    assert p["data"]["designation"] == "QA Engineer" and p["missing"] == ["Client logo is missing. Upload it under Clients and logos, or request it by email."]
    # logo arrives for the client: the draft renders by itself
    add_logo(api, p["client"]["id"])
    p = api.get(f"/api/posts/{p['id']}")
    assert p["status"] == "ready" and p["has_render"] and p["draft_version"] == 1
    img = api.get(f"/api/posts/{p['id']}/image")
    assert img.headers["content-type"].startswith("image/png")
    assert (ctx.paths.data / p["render_path"]).exists() and "POST-" in p["render_path"]


def test_edits_are_versioned_and_never_touch_the_employee(api):
    e = emp(api, "RHC0003", "Meera Joshi", designation="Analyst")
    assign(api, e, "Umbrella")
    p = projects(api)[0]
    api.post(f"/api/employees/{e['id']}/photo", files={"file": ("m.jpg", image_bytes())})
    add_logo(api, p["client"]["id"])
    p = api.get(f"/api/posts/{p['id']}")
    assert p["status"] == "ready"
    r = api.patch(f"/api/posts/{p['id']}", {"version": p["version"], "changes": {"designation": "Senior Analyst", "pronoun": "her",
                                                                                 "layout": {"name": {"max_size": 28}}, "photo_crop": {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}}})
    assert r["data"]["designation"] == "Senior Analyst" and "designation" in r["data"]["overrides"] and r["draft_version"] == 2
    assert api.get(f"/api/employees/{e['id']}")["employee"]["designation"] == "Analyst"  # master record unchanged
    assert "her" in r["announcement_plain"] and "<b>Meera Joshi</b>" in r["announcement_html"]
    # stale version is refused
    api.patch(f"/api/posts/{p['id']}", {"version": p["version"], "changes": {"designation": "X"}}, status=409)
    # a change to the master record flags the post but keeps the edit
    cur = api.get(f"/api/employees/{e['id']}")["employee"]
    api.patch(f"/api/employees/{e['id']}", {"version": cur["version"], "changes": {"designation": "Lead Analyst"}})
    p2 = api.get(f"/api/posts/{p['id']}")
    assert p2["data"]["designation"] == "Senior Analyst" and any("Designation changed" in f for f in p2["review_flags"])
    # live preview renders without saving
    prev = api.c.post(f"/api/posts/{p['id']}/preview", json={"data": {"designation": "PREVIEW ONLY"}}, headers={"X-CSRF-Token": api.csrf})
    assert prev.status_code == 200 and prev.headers["content-type"] == "image/png"
    assert api.get(f"/api/posts/{p['id']}")["data"]["designation"] == "Senior Analyst"
    # publish, then edit: published picture is preserved as its own version
    pub = api.post(f"/api/posts/{p['id']}/publish")
    assert pub["status"] == "published" and pub["published_version"] == pub["draft_version"]
    api.patch(f"/api/posts/{p['id']}", {"version": pub["version"], "changes": {"display_name": "Meera J."}})
    p3 = api.get(f"/api/posts/{p['id']}")
    assert p3["status"] == "published" and p3["has_unpublished_changes"]
    kinds = [v["kind"] for v in p3["versions_list"]]
    assert "published" in kinds and kinds.count("draft") >= 3
    # export has a versioned filename and follows the record again after reset
    ex = api.get(f"/api/posts/{p['id']}/export")
    assert "_v00" in ex.headers["content-disposition"]
    api.post(f"/api/posts/{p['id']}/reset-field", {"field": "designation"})
    assert api.get(f"/api/posts/{p['id']}")["data"]["designation"] == "Lead Analyst"
    api.delete(f"/api/posts/{p['id']}", status=422)  # published posts are kept


def test_two_employees_same_client_keep_their_own_designation(api):
    a = emp(api, "RHC0010", "Dev One", designation="Developer")
    b = emp(api, "RHC0011", "Dev Two", designation="Tester")
    assign(api, a, "Shared Client")
    assign(api, b, "Shared Client")
    ps = {p["employee"]["rhc_id"]: p for p in projects(api)}
    assert ps["RHC0010"]["client"]["id"] == ps["RHC0011"]["client"]["id"]
    add_logo(api, ps["RHC0010"]["client"]["id"])
    pa, pb = api.get(f"/api/posts/{ps['RHC0010']['id']}"), api.get(f"/api/posts/{ps['RHC0011']['id']}")
    assert pa["data"]["logo_id"] == pb["data"]["logo_id"]  # logo is shared
    assert pa["data"]["designation"] == "Developer" and pb["data"]["designation"] == "Tester"
    clients = api.get("/api/clients")
    assert len(clients) == 1 and clients[0]["employees"] == 2 and clients[0]["projects"] == 2


def test_imports_do_not_create_posts_but_backfill_does(api):
    from conftest import HEAD, csv_bytes, run_import

    rows = [HEAD + ["Assignment status", "Client"], ["RHC0100", "Imported Person", "Dev", "", "Ahmedabad", "", "Active", "Client", "Hooli"]]
    run_import(api, csv_bytes(rows), "partial", options={"create_unmatched": True})
    assert api.get("/api/employees/lookup?q=Imported")[0]["full_name"] == "Imported Person"
    assert projects(api) == []
    cands = api.get("/api/posts/backfill")["candidates"]
    assert len(cands) == 1 and cands[0]["client_name"] == "Hooli"
    r = api.post("/api/posts/backfill", {"employee_ids": [cands[0]["id"]]})
    assert r["created"] == 1 and len(projects(api)) == 1
    assert api.get("/api/posts/backfill")["candidates"] == []


def test_logo_versions_and_rejections(api, ctx):
    c = api.post("/api/clients", {"name": "Logo Co"})
    r = api.post(f"/api/clients/{c['id']}/logo", files={"file": ("tiny.png", image_bytes("PNG", (0, 0, 0), (40, 40)))}, status=400)
    assert "too small" in r["error"]["message"]
    api.post(f"/api/clients/{c['id']}/logo", files={"file": ("bad.png", b"not an image")}, status=400)
    v1 = add_logo(api, c["id"], (1, 2, 3))["logo_id"]
    v2 = add_logo(api, c["id"], (4, 5, 6))["logo_id"]
    d = api.get(f"/api/clients/{c['id']}")
    assert d["client"]["current_logo_id"] == v2 and [l["version_no"] for l in d["logos"]] == [2, 1]
    api.post(f"/api/clients/{c['id']}/logos/{v1}/make-current")
    assert api.get(f"/api/clients/{c['id']}")["client"]["current_logo_id"] == v1
    assert api.get(f"/api/logos/{v1}/thumb").headers["content-type"].startswith("image/")
    # same logo again is refused as duplicate; the previous file is untouched
    api.post(f"/api/clients/{c['id']}/logo", files={"file": ("same.png", image_bytes("PNG", (1, 2, 3), (240, 120)))}, status=400)


def test_email_request_and_reply_dry_run(api, ctx):
    e = emp(api, "RHC0020", "Pooja Mehta", official_email="pooja@relay.test")
    assign(api, e, "Wayne Enterprises")
    p = projects(api)[0]
    prev = api.get(f"/api/posts/{p['id']}/request-preview")
    assert prev["to"] == "pooja@relay.test" and set(prev["asks"]) == {"designation", "logo"} and "Wayne Enterprises" in prev["subject"]
    r = api.post(f"/api/posts/{p['id']}/request", {"to": prev["to"], "subject": prev["subject"], "body": prev["body"], "asks": prev["asks"]})
    assert r["status"] == "submitted" and r["request_uid"].startswith("POSTREQ-") and r["request_uid"] in r["subject"]
    # a second send right away needs confirmation (reminder protection)
    rr = api.post(f"/api/posts/{p['id']}/request", {"to": prev["to"], "asks": prev["asks"]}, status=422)
    assert rr["error"]["code"] == "request_exists"
    assert len(api.get("/api/post-requests")) == 1
    # the dry-run outbox recorded exactly one message
    sent = list((ctx.paths.dryrun_outbox / "sent").iterdir())
    assert len(sent) == 1
    # simulate a reply in the dry-run inbox: reference in subject, a signature icon plus a real logo, designation in body
    inbox = ctx.paths.dryrun_outbox / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "r1.json").write_text(json.dumps({
        "entry_id": "ENTRY-1", "conversation_id": "CONV-1", "sender_email": "pooja@relay.test", "sender_name": "Pooja Mehta",
        "subject": "RE: " + r["subject"], "received_at": "2026-10-01T10:00:00+00:00",
        "body": "Hi,\n\nDesignation: Senior Consultant\n\nLogo attached.\n\nFrom: Relay",
        "attachments": [{"name": "image001.png", "data_b64": base64.b64encode(image_bytes("PNG", (9, 9, 9), (40, 40))).decode()},
                        {"name": "wayne_logo.png", "data_b64": base64.b64encode(noisy_png()).decode()}]}))
    res = api.post("/api/post-replies/scan", {})
    assert res["new"] == 1 and res["applied"] == 1
    replies = api.get("/api/post-replies")
    assert replies[0]["status"] == "applied" and replies[0]["match_method"] == "request reference"
    atts = replies[0]["attachments"]
    assert [a["accepted"] for a in atts] == [False, True]
    assert "icon" in atts[0]["reason"].lower() or "pixel" in atts[0]["reason"].lower()
    p = api.get(f"/api/posts/{p['id']}")
    assert p["data"]["designation"] == "Senior Consultant" and p["data"]["logo_id"] and p["status"] == "needs_assets"  # still no photo
    assert api.get(f"/api/employees/{e['id']}")["employee"]["designation"] is None  # master record untouched
    assert api.get("/api/post-requests")[0]["status"] == "replied"
    # an older reply (received earlier) must not overwrite the newer accepted designation
    (inbox / "r0.json").write_text(json.dumps({
        "entry_id": "ENTRY-0", "conversation_id": "CONV-1", "sender_email": "pooja@relay.test", "subject": "RE: " + r["subject"],
        "received_at": "2026-09-20T09:00:00+00:00", "body": "Designation: Old Title", "attachments": []}))
    api.post("/api/post-replies/scan", {})
    assert api.get(f"/api/posts/{p['id']}")["data"]["designation"] == "Senior Consultant"
    # scanning again skips handled replies; nothing changes
    res = api.post("/api/post-replies/scan", {})
    assert res["new"] == 0 and res["skipped"] == 2
    # an unmatched reply from an unknown sender waits for review and can be ignored
    (inbox / "r2.json").write_text(json.dumps({"entry_id": "ENTRY-2", "sender_email": "someone@else.test", "subject": "Logo request - Unknown Co",
                                               "received_at": "2026-10-02T10:00:00+00:00", "body": "hi", "attachments": []}))
    res = api.post("/api/post-replies/scan", {})
    assert res["review"] == 1
    pending = [x for x in api.get("/api/post-replies?status=pending")]
    assert len(pending) == 1
    api.post(f"/api/post-replies/{pending[0]['id']}/ignore", {"note": "spam"})
    assert api.get("/api/post-replies?status=pending") == []


def test_send_failure_is_recorded_without_followup_confusion(api, ctx):
    e = emp(api, "RHC0021", "Fail Case", official_email="fail@relay.test")
    assign(api, e, "Failing Client")
    p = projects(api)[0]
    api.put("/api/settings/outlook", {"mode": "dryrun", "dryrun_scenario": "fail_before_send"})
    r = api.post(f"/api/posts/{p['id']}/request", {"to": "fail@relay.test", "asks": ["logo"]})
    assert r["status"] == "failed" and r["send_error"] and r["followup_error"] is None
    api.put("/api/settings/outlook", {"mode": "dryrun", "dryrun_scenario": "success"})
    r2 = api.post(f"/api/posts/{p['id']}/request", {"to": "fail@relay.test", "asks": ["logo"]})
    assert r2["status"] == "submitted"
    reqs = api.get("/api/post-requests")
    assert [x["status"] for x in reqs] == ["submitted", "failed"]


def test_permissions_viewer_cannot_edit_posts(api, app):
    from conftest import make_user

    e = emp(api, "RHC0030", "View Only", designation="Dev")
    assign(api, e, "Perm Client")
    pid = projects(api)[0]["id"]
    viewer = make_user(api, app, "viewer@relay.test", "viewer")
    assert viewer.get("/api/posts")["projects"]
    viewer.patch(f"/api/posts/{pid}", {"version": 1, "changes": {"designation": "X"}}, status=403)
    viewer.post(f"/api/posts/{pid}/request", {"to": "a@b.test", "asks": ["logo"]}, status=403)
