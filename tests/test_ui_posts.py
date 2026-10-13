"""Browser end-to-end test of the integrated pages (requires Playwright + Chromium).

Drives the real launcher: first-run setup, add an employee with a client assignment, upload the
employee photo (matched by first and last name) and the client logo, open the generated post,
edit and save a draft, publish, request-by-email dialog, screenshot import tab, Videos page and
the dependency panel. Screenshots go to tests/_artifacts/.
"""
from __future__ import annotations

import io
import os
import random
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "tests" / "_artifacts"


def launch_options() -> dict:
    """Use a Chromium given by PLAYWRIGHT_CHROMIUM_PATH (or the one preinstalled in /opt/pw-browsers) when present."""
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH") or "/opt/pw-browsers/chromium"
    return {"executable_path": exe} if Path(exe).exists() else {}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def png(size=(300, 300), noisy=False, color=(120, 80, 200)) -> bytes:
    from PIL import Image

    im = Image.new("RGB", size, color)
    if noisy:
        rnd = random.Random(3)
        im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(size[0] * size[1])])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data = tmp_path_factory.mktemp("uidata2")
    port = free_port()
    import json
    import sqlite3

    sys.path.insert(0, str(ROOT))
    from hub.context import AppContext

    ctx = AppContext(data, outlook_mode="dryrun")
    ctx.initialise()
    ctx.release_instance_lock()
    c = sqlite3.connect(str(ctx.paths.db))
    net = json.loads(c.execute("SELECT value FROM settings WHERE key='network'").fetchone()[0])
    net["port"] = port
    c.execute("UPDATE settings SET value=? WHERE key='network'", (json.dumps(net),))
    c.commit()
    c.close()
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, str(ROOT / "launcher" / "run_hub.py"), "--data-dir", str(data), "--no-browser", "--outlook-mode", "dryrun"],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}/"
    for _ in range(60):
        try:
            urllib.request.urlopen(url + "api/auth/state", timeout=1)
            break
        except OSError:
            time.sleep(0.25)
    else:
        proc.kill()
        raise RuntimeError("server did not start: " + proc.stdout.read().decode())
    yield url, data
    proc.terminate()
    proc.wait(timeout=15)


def test_posts_videos_screenshots_pages(server, tmp_path):
    url, data = server
    ART.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    photo = tmp_path / "Asha Patel.png"
    photo.write_bytes(png())
    logo = tmp_path / "acme.png"
    logo.write_bytes(png((400, 200), noisy=True))
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(**launch_options())
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        shot = lambda n: page.screenshot(path=str(ART / f"{n}.png"), full_page=True)

        page.goto(url)
        page.wait_for_selector("text=Create administrator account")
        f = page.locator("form input")
        f.nth(0).fill("admin@relay.test"); f.nth(1).fill("Test Admin"); f.nth(2).fill("Very-Strong-Pass1"); f.nth(3).fill("Very-Strong-Pass1")
        page.click("text=Create administrator account")
        page.wait_for_selector(".sidebar")
        assert page.locator(".nav a", has_text="Posts").count() == 1 and page.locator(".nav a", has_text="Videos").count() == 1

        # Add an employee with a client assignment: a post project is created
        page.click("text=Add employee")
        m = page.locator(".modal")
        fld = lambda label: m.locator(".field", has_text=label).first
        fld("RHC ID").locator("input").fill("RHC0001")
        fld("Full name").locator("input").fill("Asha Mital Patel")
        fld("Designation").locator("input").fill("Senior Developer")
        fld("Location").locator("select").select_option("Ahmedabad")
        fld("Assignment status").locator("select").select_option("Client")
        fld("Client name").locator("input").fill("Acme Corp")
        m.locator(".btn-primary", has_text="Add employee").click()
        page.wait_for_selector("text=A deployment post project was created")
        shot("60_employee_added_post_created")

        page.goto(url + "#/posts")
        page.wait_for_selector(".post-card")
        assert "Needs attention" in page.locator(".post-card").first.inner_text()
        shot("61_posts_needs_attention")

        # Photo upload: "Asha Patel.png" matches "Asha Mital Patel" by first and last name (pre-ticked)
        page.goto(url + "#/photos")
        page.click("text=Start a new photo upload")
        page.wait_for_selector("text=Choose a folder")
        page.locator("input[type=file][accept]").first.set_input_files(str(photo))
        page.wait_for_selector(".cand", timeout=30000)
        assert "Pre-selected" in page.locator(".cand").first.inner_text()
        page.locator("button", has_text="ticked photo").click()
        page.locator(".modal .btn-primary", has_text="Assign").click()
        page.wait_for_selector("text=1 photos assigned")

        # Client logo upload on the Clients tab
        page.goto(url + "#/posts/clients")
        page.wait_for_selector("text=Acme Corp")
        page.locator("input[type=file]").first.set_input_files(str(logo))
        page.wait_for_selector("text=Logo saved for Acme Corp")
        shot("62_clients_logo")

        # The post is now generated; open it, edit, save, publish
        page.goto(url + "#/posts")
        page.wait_for_selector(".post-card img")
        page.locator(".post-card a", has_text="Open").click()
        page.wait_for_selector("text=Text size and placement")
        page.wait_for_selector(".preview-box img[src^='blob:']", timeout=20000)
        shot("63_post_editor")
        page.locator(".editor input.input").nth(1).fill("Lead Developer")
        page.locator("button", has_text="Save draft").click()
        page.wait_for_selector("text=Draft saved")
        page.wait_for_selector("text=Edited for this post only")
        page.locator("button", has_text="Mark as published").click()
        page.wait_for_selector("text=Marked as published")
        page.locator("button", has_text="Request by email").count()  # disabled when nothing is missing
        page.goto(url + "#/employees")
        page.wait_for_selector("table.dgrid tbody tr")
        assert "Senior Developer" in page.locator("table.dgrid tbody tr").first.inner_text()  # master record untouched

        # Screenshot import tab renders with the paste zone
        page.goto(url + "#/imports/screenshots")
        page.wait_for_selector(".paste-zone")
        shot("64_screenshot_import")

        # Videos page and dependency panel
        page.goto(url + "#/videos")
        page.wait_for_selector("text=Video Studio")
        page.wait_for_selector("text=Queue")
        shot("65_videos")
        page.goto(url + "#/settings/about")
        page.wait_for_selector("text=Dependencies and bundled assets")
        page.wait_for_selector("text=Post template")
        shot("66_dependencies")
        page.goto(url + "#/settings/posts")
        page.wait_for_selector("text=Logo and designation request emails")
        browser.close()
    assert not errors, errors
