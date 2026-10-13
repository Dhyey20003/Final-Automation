from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path

from conftest import ADMIN, Api, emp, image_bytes
from hub.services import backups as B

STATIC = Path(__file__).resolve().parent.parent / "hub" / "static"


def test_backup_restore_roundtrip_with_photos(api, ctx, client):
    e = emp(api, "RHC0001", "Backed Up", "Ahmedabad")
    api.post("/api/fields", {"label": "Shift", "field_type": "text"})
    pid = api.post(f"/api/employees/{e['id']}/photo", files={"file": ("me.jpg", image_bytes())})["photo_id"]
    t = api.get("/api/tasks")["tasks"][0]
    api.put("/api/tasks/1", {"version": t["version"], "config": {"email": {"to": "it@relay-sample.test"}}})
    b = api.post("/api/backups")
    assert b["status"] == "verified"
    path = Path(b["path"])
    names = zipfile.ZipFile(path).namelist()
    assert "employees.db" in names and "backup_manifest.json" in names
    assert any(n.startswith("images/") for n in names) and any(n.startswith("thumbnails/") for n in names)
    assert not any(n.startswith("backups/") for n in names)  # no recursive growth
    # change data after the backup
    api.post("/api/employees", {"values": {"rhc_id": "RHC0002", "full_name": "After Backup"}})
    shutil.rmtree(ctx.paths.images)
    with ctx.db() as conn:
        conn.execute("UPDATE task_defs SET auto_send_enabled = 1")
    api.post("/api/backups/restore", {"backup_id": b["id"], "confirm_text": "nope"}, status=422)
    res = api.post("/api/backups/restore", {"backup_id": b["id"], "confirm_text": "RESTORE"})
    assert res["pre_restore_backup"] and Path(res["pre_restore_backup"]).exists()
    # sessions were cleared: sign in again
    assert client.get("/api/employees").status_code == 401
    api.login(*ADMIN)
    rows = api.get("/api/employees")["rows"]
    assert [r["rhc_id"] for r in rows] == ["RHC0001"]
    api.get(f"/api/photos/{pid}/file")
    assert any(f["key"] == "cf_shift" for f in api.get("/api/meta")["fields"])
    tasks = api.get("/api/tasks")["tasks"]
    assert tasks[0]["config"]["email"]["to"] == ["it@relay-sample.test"]
    assert all(not t["auto_send_enabled"] and t["needs_revalidation"] for t in tasks)


def test_tampered_or_unsafe_backup_is_rejected(api, ctx, tmp_path):
    emp(api, "RHC0001", "X")
    b = api.post("/api/backups")
    src = Path(b["path"])
    # tampered content
    bad = tmp_path / "tampered.zip"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(bad, "w") as zout:
        for n in zin.namelist():
            data = zin.read(n)
            if n == "employees.db":
                data = data[:-10] + b"0123456789"
            zout.writestr(n, data)
    r = B.verify_archive(ctx, bad)
    assert not r["ok"] and any("Checksum" in p for p in r["problems"])
    # unsafe path
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(evil, "w") as zout:
        for n in zin.namelist():
            zout.writestr(n, zin.read(n))
        zout.writestr("../../outside.txt", b"x")
    r = B.verify_archive(ctx, evil)
    assert not r["ok"] and any("unsafe" in p for p in r["problems"])
    # upload path is verified before anything is restored
    res = api.post("/api/backups/inspect-upload", files={"file": ("evil.zip", evil.read_bytes())})
    assert res["upload_ref"] is None
    assert not (ctx.paths.data.parent / "outside.txt").exists()


def test_backup_destination_and_retention(api, ctx, tmp_path):
    api.put("/api/settings/backup", {"destination": str(ctx.paths.images / "x")}, status=422)
    ext = tmp_path / "external_drive"
    api.put("/api/settings/backup", {"destination": str(ext), "retention": 2})
    for _ in range(3):
        B.create_backup(ctx, "daily", "system")
        import time

        time.sleep(1.1)
    with ctx.db() as conn:
        statuses = [r[0] for r in conn.execute("SELECT status FROM backups WHERE kind='daily' ORDER BY id")]
    assert statuses == ["deleted_by_retention", "verified", "verified"]
    assert len(list(ext.glob("*.zip"))) == 2


def test_pre_migration_backup(tmp_path):
    import sqlite3

    from hub import db as DB

    p = tmp_path / "employees.db"
    DB.migrate(p, tmp_path / "backups")
    c = sqlite3.connect(p)
    c.execute("PRAGMA user_version = 0")  # pretend an older schema with data
    c.execute("CREATE TABLE IF NOT EXISTS legacy(x)")
    c.commit()
    c.close()
    old = list(DB.MIGRATIONS)
    try:
        DB.MIGRATIONS[:] = ["CREATE TABLE IF NOT EXISTS extra(x);"]
        DB.SCHEMA_VERSION = 1
        res = DB.migrate(p, tmp_path / "backups")
        assert res["backup"] is None  # version 0 counts as new
        c = sqlite3.connect(p)
        c.execute("PRAGMA user_version = 1")
        c.commit()
        c.close()
        DB.MIGRATIONS[:] = ["CREATE TABLE IF NOT EXISTS extra(x);", "CREATE TABLE extra2(y);"]
        DB.SCHEMA_VERSION = 2
        res = DB.migrate(p, tmp_path / "backups")
        assert res["backup"] and Path(res["backup"]).exists()
    finally:
        DB.MIGRATIONS[:] = old
        DB.SCHEMA_VERSION = len(old)


def test_interface_assets_are_all_local(client):
    html = (STATIC / "index.html").read_text()
    assert not re.search(r"(src|href)=\"https?://", html)
    for f in STATIC.rglob("*"):
        if f.suffix in (".js", ".css", ".html"):
            text = f.read_text(encoding="utf-8")
            assert not re.search(r"https?://(?!127\.0\.0\.1|<)", text), f"external reference in {f}"
            assert "import(" not in text or "http" not in text
    r = client.get("/")
    assert r.status_code == 200 and "default-src 'self'" in r.headers["content-security-policy"]
    for asset in ["/static/js/app.js", "/static/js/grid.js", "/static/css/app.css", "/static/js/pages/employees.js",
                  "/static/js/pages/imports.js", "/static/js/pages/photos.js", "/static/js/pages/tasks.js",
                  "/static/js/pages/settings.js", "/static/img/favicon.png", "/static/samples/employee_import_template.xlsx"]:
        assert client.get(asset).status_code == 200, asset
