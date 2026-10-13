from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import ADMIN, Api, emp, image_bytes, make_user


def test_first_run_setup_only_once_and_no_default_credentials(app, client):
    st = client.get("/api/auth/state").json()
    assert st["setup_required"] is True
    assert client.post("/api/auth/login", json={"email": "admin", "password": "admin"}).status_code == 401
    assert client.post("/api/auth/setup", json={"email": "a@b.test", "display_name": "A", "password": "short"}).status_code == 422
    assert client.post("/api/auth/setup", json={"email": ADMIN[0], "display_name": "A", "password": ADMIN[1]}).status_code == 200
    assert client.post("/api/auth/setup", json={"email": "x@b.test", "display_name": "X", "password": ADMIN[1]}).status_code == 403


def test_setup_refused_from_network(app):
    c = TestClient(app, client=("192.168.1.20", 5000))
    # LAN access is off by default, so the request is refused before anything else.
    assert c.get("/api/auth/state").status_code == 403


def test_lan_requires_admin_enable_and_private_network(api, app):
    api.put("/api/settings/network", {"lan_enabled": True}, status=422)  # needs explicit confirmation
    api.put("/api/settings/network", {"allowed_networks": ["8.8.8.0/24"]}, status=422)  # public networks refused
    api.put("/api/settings/network", {"lan_enabled": True, "confirm": True, "allowed_networks": ["192.168.1.0/24"]})
    lan = TestClient(app, client=("192.168.1.20", 5000))
    assert lan.get("/api/auth/state").status_code == 200
    assert lan.post("/api/auth/setup", json={}).status_code in (403, 422)
    other = TestClient(app, client=("10.1.1.1", 5000))
    assert other.get("/api/auth/state").status_code == 403


def test_csrf_and_session_required(api, client):
    e = emp(api, "C1", "Csrf")
    r = client.patch(f"/api/employees/{e['id']}", json={"version": 1, "changes": {"designation": "x"}})
    assert r.status_code == 403
    client.post("/api/auth/logout", headers={"X-CSRF-Token": api.csrf})
    assert client.get("/api/employees").status_code == 401


def test_login_lockout(api, app):
    make_user(api, app, "locked@relay.test", "viewer")
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": "locked@relay.test", "password": "wrong-password"}).status_code == 401
    r = c.post("/api/auth/login", json={"email": "locked@relay.test", "password": "Another-Strong-Pass2"})
    assert r.status_code == 429


def test_roles_are_enforced_on_the_server(api, app):
    e = emp(api, "V1", "Viewer Target")
    viewer = make_user(api, app, "viewer@relay.test", "viewer")
    viewer.get("/api/employees")
    viewer.patch(f"/api/employees/{e['id']}", {"version": 1, "changes": {"designation": "x"}}, status=403)
    viewer.get("/api/employees/export", status=403)
    viewer.post("/api/imports/upload", files={"file": ("a.csv", b"RHC ID\nX")}, status=403)
    viewer.get("/api/users", status=403)
    viewer.post("/api/backups", status=403)
    # export can be granted separately
    uid = [u for u in api.get("/api/users")["users"] if u["email"] == "viewer@relay.test"][0]["id"]
    api.put(f"/api/users/{uid}", {"extra_permissions": ["employees.export"]})
    viewer.get("/api/employees/export")
    # admin-only permissions cannot be granted to non-admins
    api.put(f"/api/users/{uid}", {"extra_permissions": ["users.manage"]}, status=422)
    editor = make_user(api, app, "editor@relay.test", "editor")
    editor.patch(f"/api/employees/{e['id']}", {"version": 1, "changes": {"designation": "ok"}})
    editor.put("/api/tasks/1", {"version": 1, "config": {}}, status=403)
    op = make_user(api, app, "ops@relay.test", "export_operator")
    op.get("/api/employees/export")
    op.patch(f"/api/employees/{e['id']}", {"version": 2, "changes": {"designation": "no"}}, status=403)


def test_last_admin_cannot_be_removed(api):
    me = api.get("/api/users")["users"][0]
    api.put(f"/api/users/{me['id']}", {"role": "viewer"}, status=422)


def test_location_scope_applies_to_records_photos_and_exports(api, app, ctx):
    a = emp(api, "L1", "Ahmedabad Person", "Ahmedabad")
    v = emp(api, "L2", "Vadodara Person", "Vadodara")
    b = api.post("/api/photo-batches", {})["id"]
    api.post(f"/api/photo-batches/{b}/files", files=[("files", ("L1.jpg", image_bytes()))])
    cand = api.get(f"/api/photo-batches/{b}")["candidates"][0]
    api.post(f"/api/photo-batches/{b}/assign", {"assignments": [{"candidate_id": cand["id"], "employee_id": a["id"]}]})
    pid = api.get(f"/api/employees/{a['id']}")["employee"]["photo_id"]
    vad = make_user(api, app, "vad@relay.test", "editor", location_scope=["Vadodara"])
    rows = vad.get("/api/employees")["rows"]
    assert [r["rhc_id"] for r in rows] == ["L2"]
    vad.get(f"/api/employees/{a['id']}", status=403)
    vad.patch(f"/api/employees/{a['id']}", {"version": 2, "changes": {"designation": "x"}}, status=403)
    vad.get(f"/api/photos/{pid}/thumb", status=403)
    vad.patch(f"/api/employees/{v['id']}", {"version": 1, "changes": {"location": "Ahmedabad"}}, status=403)
    vad.post("/api/employees", {"values": {"rhc_id": "L3", "full_name": "X", "location": "Ahmedabad"}}, status=403)
    assert [r["rhc_id"] for r in vad.get("/api/employees/lookup?q=L")] == ["L2"]


def test_field_restrictions_and_dob_masking(api, app):
    e = emp(api, "D1", "Dob Person", date_of_birth="1990-08-15", notes="private note")
    viewer = make_user(api, app, "dob@relay.test", "editor", restricted_fields=["notes"])
    row = viewer.get(f"/api/employees/{e['id']}")["employee"]
    assert row["date_of_birth"] == "15-Aug" and "notes" not in row
    viewer.patch(f"/api/employees/{e['id']}", {"version": 1, "changes": {"date_of_birth": "1991-01-01"}}, status=422)
    viewer.patch(f"/api/employees/{e['id']}", {"version": 1, "changes": {"notes": "x"}}, status=422)
    assert all("notes" not in h["field"] for h in viewer.get(f"/api/employees/{e['id']}")["history"])
    hidden = make_user(api, app, "nodob@relay.test", "viewer", dob_access="none")
    assert "date_of_birth" not in hidden.get(f"/api/employees/{e['id']}")["employee"]
    # restricted fields cannot be used to probe values through filters
    assert hidden.get('/api/employees?filters={"date_of_birth": "1990"}')["total"] == 1
    full = make_user(api, app, "fulldob@relay.test", "viewer", dob_access="full")
    assert full.get(f"/api/employees/{e['id']}")["employee"]["date_of_birth"] == "1990-08-15"


def test_fetch_tokens_are_scoped_hashed_and_revocable(api, app, ctx):
    emp(api, "T1", "Token Amd", "Ahmedabad", date_of_birth="1990-01-01", designation="Analyst")
    emp(api, "T2", "Token Vad", "Vadodara")
    api.post("/api/tokens", {"name": "bad", "allowed_fields": ["date_of_birth"]}, status=422)
    res = api.post("/api/tokens", {"name": "Birthday tool", "location_scope": ["Ahmedabad"], "allowed_fields": ["rhc_id", "full_name", "designation"]})
    secret = res["secret"]
    with ctx.db() as conn:
        stored = conn.execute("SELECT token_hash FROM api_tokens").fetchone()[0]
    assert secret not in stored
    c = TestClient(app)
    h = {"Authorization": f"Bearer {secret}"}
    data = c.get("/api/fetch/v1/employees", headers=h).json()
    assert [e["rhc_id"] for e in data["employees"]] == ["T1"]
    assert "date_of_birth" not in data["employees"][0]
    assert c.get("/api/fetch/v1/employees?location=Vadodara", headers=h).status_code == 403
    assert c.get("/api/employees", headers=h).status_code == 401  # no interactive access
    assert c.get("/api/fetch/v1/employees/T1/photo", headers=h).status_code == 403
    api.post(f"/api/tokens/{res['token']['id']}/revoke")
    assert c.get("/api/fetch/v1/employees", headers=h).status_code == 401


def test_audit_records_account_and_data_changes(api, app):
    make_user(api, app, "aud@relay.test", "viewer")
    emp(api, "AU1", "Audited")
    actions = {r["action"] for r in api.get("/api/audit?limit=200")["rows"]}
    assert {"user.created", "employee.create", "auth.login", "user.first_admin_created"} <= actions
