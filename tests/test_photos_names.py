"""Photo filename matching by first and last name, legacy data import, video status endpoint."""
from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

from conftest import emp, image_bytes


def test_first_and_last_name_match_is_clear_only_when_unique(api):
    a = emp(api, "RHC0001", "John Michael Smith")
    emp(api, "RHC0002", "Jane Smith")
    emp(api, "RHC0003", "Amit Kumar Shah")
    emp(api, "RHC0004", "Amit Shah")
    bid = api.post("/api/photo-batches", {})["id"]
    d = api.post(f"/api/photo-batches/{bid}/files", files=[("files", f) for f in [
        ("John Smith.jpg", image_bytes()), ("john_smith.png", image_bytes("PNG")), ("Amit Shah.jpg", image_bytes(color=(1, 1, 1))),
        ("Smith.jpg", image_bytes(color=(2, 2, 2)))]])
    c = {x["original_name"]: x for x in d["candidates"]}
    assert c["John Smith.jpg"]["suggestion"]["employee_id"] == a["id"] and c["John Smith.jpg"]["suggestion"]["certain"]
    assert c["john_smith.png"]["suggestion"]["employee_id"] == a["id"] and c["john_smith.png"]["suggestion"]["certain"]
    assert c["Amit Shah.jpg"]["suggestion"]["employee_id"] == api.get("/api/employees/lookup?q=RHC0004")[0]["id"]  # exact full name wins
    assert c["Smith.jpg"]["suggestion"] is None
    # two employees share first and last name: no automatic choice
    emp(api, "RHC0005", "John Smith")
    d = api.post(f"/api/photo-batches/{bid}/files", files=[("files", ("John  Smith (2).jpg", image_bytes(color=(5, 5, 5))))])
    c = {x["original_name"]: x for x in d["candidates"]}
    sg = c["John  Smith (2).jpg"]["suggestion"]
    assert (sg is None or not sg["certain"]) and any("could" in f or "more than one" in f for f in c["John  Smith (2).jpg"]["flags"])
    # assignment stores the relationship by employee ID; renaming keeps the photo
    cid = c["John Smith.jpg"]["id"]
    api.post(f"/api/photo-batches/{bid}/assign", {"assignments": [{"candidate_id": cid, "employee_id": a["id"]}]})
    cur = api.get(f"/api/employees/{a['id']}")["employee"]
    api.patch(f"/api/employees/{a['id']}", {"version": cur["version"], "changes": {"full_name": "Jonathan M. Smith"}})
    assert api.get(f"/api/employees/{a['id']}")["employee"]["photo_id"] == cur["photo_id"]


def test_legacy_post_studio_import(api, ctx, tmp_path):
    a = emp(api, "RHC0010", "Hetal Desai", designation="Developer")
    emp(api, "RHC0011", "Dup Name")
    emp(api, "RHC0012", "Dup Name", location="Vadodara")
    old = tmp_path / "files"
    (old / "Data" / "Posts").mkdir(parents=True)
    (old / "Raw_Files" / "Posts" / "saved_logos").mkdir(parents=True)
    (old / "Raw_Files" / "saved_profile").mkdir(parents=True)
    (old / "Output" / "Posts").mkdir(parents=True)
    (old / "Raw_Files" / "Posts" / "saved_logos" / "Acme Corp.png").write_bytes(image_bytes("PNG", (3, 3, 200), (300, 150)))
    (old / "Raw_Files" / "saved_profile" / "Hetal Desai.jpg").write_bytes(image_bytes())
    (old / "Raw_Files" / "saved_profile" / "Dup Name.jpg").write_bytes(image_bytes(color=(7, 7, 7)))
    (old / "Output" / "Posts" / "Hetal Desai.png").write_bytes(image_bytes("PNG", (0, 0, 0), (600, 600)))
    wb = Workbook()
    ws = wb.active
    ws.append(["Timestamp", "Screenshot", "Employee", "Client", "Email", "Status", "Designation"])
    ws.append(["2026-01-01 10:00", "s.png", "Hetal Desai", "Acme Corp", "hetal@relay.test", "Sent", "Senior Developer"])
    ws.append(["2026-01-02 10:00", "s2.png", "Dup Name", "Globex", "", "Sent", ""])
    wb.save(old / "Data" / "Posts" / "log.xlsx")
    (old / "Data" / "Posts" / "studio_state.json").write_text(json.dumps({"posts": {"hetaldesai": {"name": "Hetal Desai", "client": "Acme Corp", "uploaded": True, "pronoun": "her"}}}))
    plan = api.post("/api/posts-legacy/preview", {"folder": str(old)})
    assert len(plan["logos"]) == 1 and len(plan["profiles"]) == 2 and len(plan["posts"]) == 2
    assert [r["file"] for r in plan["review"]] == ["Dup Name.jpg", "Dup Name"]
    res = api.post("/api/posts-legacy/commit", {"folder": str(old), "decisions": {}})
    assert res["logos"] == 1 and res["profiles"] == 1 and res["posts"] == 1 and len(res["skipped"]) == 2
    ps = api.get("/api/posts")["projects"]
    assert len(ps) == 1 and ps[0]["status"] == "published" and ps[0]["data"]["designation"] == "Senior Developer" and ps[0]["data"]["pronoun"] == "her"
    assert api.get(f"/api/employees/{a['id']}")["employee"]["photo_id"]
    assert api.get("/api/clients")[0]["current_logo_id"]
    versions = api.get(f"/api/posts/{ps[0]['id']}")["versions_list"]
    assert any(v["kind"] == "legacy" for v in versions)
    # the old folder is untouched
    assert (old / "Output" / "Posts" / "Hetal Desai.png").exists() and (old / "Data" / "Posts" / "log.xlsx").exists()


def test_video_status_and_dependencies(api):
    st = api.get("/api/videos/status")
    assert st["url"].startswith("http://127.0.0.1:") and "jobs" in st
    deps = api.get("/api/system/dependencies")
    keys = {i["key"] for i in deps["items"]}
    assert {"ffmpeg", "ffprobe", "tesseract", "outlook", "post_template", "video_cards", "video_fonts", "deepfilter"} <= keys
    assert next(i for i in deps["items"] if i["key"] == "post_template")["ok"]
    assert next(i for i in deps["items"] if i["key"] == "video_cards")["ok"]
