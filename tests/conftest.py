"""Shared fixtures: an isolated app per test with a temporary data folder, dry-run email and synthetic data."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hub.main import create_app  # noqa: E402

ADMIN = ("admin@relay.test", "Very-Strong-Pass1")


class Api:
    """TestClient wrapper that sends the CSRF header and raises on unexpected status codes."""

    def __init__(self, client: TestClient):
        self.c = client
        self.csrf = None

    def login(self, email, password):
        r = self.c.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        self.csrf = self.c.get("/api/auth/me").json()["csrf_token"]
        return r

    def _h(self):
        return {"X-CSRF-Token": self.csrf} if self.csrf else {}

    def get(self, url, status=200, **kw):
        r = self.c.get(url, **kw)
        assert r.status_code == status, f"{url}: {r.status_code} {r.text[:500]}"
        return r.json() if "json" in r.headers.get("content-type", "") else r

    def post(self, url, json=None, status=200, files=None, data=None):
        r = self.c.post(url, json=json if files is None else None, files=files, data=data, headers=self._h())
        assert r.status_code == status, f"{url}: {r.status_code} {r.text[:800]}"
        return r.json() if "json" in r.headers.get("content-type", "") else r

    def put(self, url, json=None, status=200):
        r = self.c.put(url, json=json, headers=self._h())
        assert r.status_code == status, f"{url}: {r.status_code} {r.text[:800]}"
        return r.json()

    def patch(self, url, json=None, status=200):
        r = self.c.patch(url, json=json, headers=self._h())
        assert r.status_code == status, f"{url}: {r.status_code} {r.text[:800]}"
        return r.json()

    def delete(self, url, status=200):
        r = self.c.delete(url, headers=self._h())
        assert r.status_code == status, r.text
        return r.json()


@pytest.fixture
def app(tmp_path):
    application = create_app(tmp_path / "data", outlook_mode="dryrun", start_background=False, testing=True)
    yield application
    application.state.ctx.release_instance_lock()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def api(client):
    a = Api(client)
    r = client.post("/api/auth/setup", json={"email": ADMIN[0], "display_name": "Admin", "password": ADMIN[1]})
    assert r.status_code == 200, r.text
    a.csrf = client.get("/api/auth/me").json()["csrf_token"]
    return a


@pytest.fixture
def ctx(app):
    return app.state.ctx


def make_user(api: Api, app, email, role, **extra) -> Api:
    res = api.post("/api/users", {"email": email, "display_name": email, "role": role, **extra})
    other = Api(TestClient(app))
    other.c.__enter__()
    r = other.c.post("/api/auth/login", json={"email": email, "password": res["temporary_password"]})
    assert r.status_code == 200
    other.csrf = other.c.get("/api/auth/me").json()["csrf_token"]
    other.post("/api/auth/change-password", {"current_password": res["temporary_password"], "new_password": "Another-Strong-Pass2"})
    return other


def emp(api: Api, rhc, name, location="Ahmedabad", **values):
    return api.post("/api/employees", {"values": {"rhc_id": rhc, "full_name": name, "location": location, **values}})["employee"]


def image_bytes(fmt="JPEG", color=(120, 80, 200), size=(300, 380)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGBA" if fmt == "PNG" else "RGB", size, color + ((255,) if fmt == "PNG" else ()))
    img.save(buf, fmt)
    return buf.getvalue()


def csv_bytes(rows: list[list]) -> bytes:
    import csv

    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode("utf-8")


HEAD = ["RHC ID", "Employee Name", "Designation", "Official Email", "Location", "Date of Joining", "Status"]


def run_import(api: Api, content: bytes, purpose: str, filename="data.csv", options=None, resolutions=None, mapping=None,
               commit=True, commit_body=None, expect_commit=200):
    up = api.post("/api/imports/upload", files={"file": (filename, content)})
    bid = up["batch_id"]
    info = api.get(f"/api/imports/{bid}/sheet?header_row=1")
    body = {"purpose": purpose, "sheet": info["sheet"], "header_row": 1, "mapping": mapping or info["suggested_mapping"],
            "options": options or {}, "resolutions": resolutions or {}}
    plan = api.post(f"/api/imports/{bid}/preview", body)
    if not commit:
        return bid, plan, None
    cb = {"plan_hash": plan["plan_hash"], **(commit_body or {})}
    res = api.post(f"/api/imports/{bid}/commit", cb, status=expect_commit)
    return bid, plan, res
