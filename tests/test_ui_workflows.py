"""Browser end-to-end test of the main workflows (requires Playwright + Chromium).

Starts the real launcher on a free port with a temporary data folder in dry-run email mode,
then drives the interface: first-run setup, roster import, inline edit, paste preview,
profile, photo ZIP upload and assignment, task configuration, package preparation,
dry-run send, and backup. Screenshots are written to tests/_artifacts/.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
ART = ROOT / "tests" / "_artifacts"


def launch_options() -> dict:
    """Use a Chromium given by PLAYWRIGHT_CHROMIUM_PATH (or the one preinstalled in /opt/pw-browsers) when present."""
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH") or "/opt/pw-browsers/chromium"
    return {"executable_path": exe} if Path(exe).exists() else {}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data = tmp_path_factory.mktemp("uidata")
    port = free_port()
    # Pre-create the database with the chosen port so the launcher binds to it.
    import sqlite3
    import json

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
    proc = subprocess.Popen([sys.executable, str(ROOT / "launcher" / "run_hub.py"), "--data-dir", str(data), "--no-browser",
                             "--outlook-mode", "dryrun"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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
    yield url
    proc.terminate()
    proc.wait(timeout=15)


def test_main_workflows(server):
    ART.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(**launch_options())
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        shot = lambda n: page.screenshot(path=str(ART / f"{n}.png"), full_page=True)

        # First-run setup
        page.goto(server)
        page.wait_for_selector("text=Create administrator account")
        f = page.locator("form input")
        f.nth(0).fill("admin@relay.test"); f.nth(1).fill("Test Admin")
        f.nth(2).fill("Very-Strong-Pass1"); f.nth(3).fill("Very-Strong-Pass1")
        page.click("text=Create administrator account")
        page.wait_for_selector(".sidebar")

        # Import the initial roster as a complete roster for both locations
        page.click("text=Import Data")
        page.check("input[value=roster]")
        page.set_input_files("input[type=file]", str(SAMPLES / "synthetic_initial_roster.csv"))
        page.wait_for_selector("text=Match columns to fields")
        for loc in ("Ahmedabad", "Vadodara"):
            page.locator("label.check", has_text=loc).first.locator("input").check()
        shot("10_import_mapping")
        page.click("text=Preview changes")
        page.wait_for_selector("text=New records")
        shot("11_import_preview")
        assert page.locator(".stat", has_text="New records").locator(".stat-num").inner_text() == "44"
        page.click("text=Commit import")
        page.locator(".modal .btn-primary", has_text="Commit").click()
        page.wait_for_selector("text=Import complete")
        shot("12_import_results")

        # Employees grid, inline edit
        page.click("text=Employees")
        page.wait_for_selector("table.dgrid tbody tr")
        assert "44 records" in page.locator(".pager").inner_text()
        cell = page.locator("table.dgrid tbody tr").first.locator("td").nth(3)  # designation column
        cell.dblclick()
        page.keyboard.press("Control+A")
        page.keyboard.type("Principal Analyst")
        page.keyboard.press("Enter")
        page.wait_for_selector("text=Saved Designation")
        # Paste two rows of designations via a synthetic clipboard event
        page.locator("table.dgrid tbody tr").nth(1).locator("td").nth(3).click()
        page.evaluate("""() => { const dt = new DataTransfer(); dt.setData('text/plain', 'Lead A\\nLead B');
            document.querySelector('.dgrid-wrap').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true })); }""")
        page.wait_for_selector("text=Paste preview (2 rows)")
        shot("20_paste_preview")
        page.click("text=Save all changes")
        page.wait_for_selector("text=Saved 2 changes in 2 rows.")
        shot("21_employees")
        # Profile drawer
        page.locator("td.name-cell a").first.click()
        page.wait_for_selector(".drawer >> text=Change history")
        shot("22_profile")
        page.keyboard.press("Escape")

        # Photos: upload the sample ZIP and assign the pre-selected matches
        page.click("text=Photos")
        page.click("text=Start a new photo upload")
        page.wait_for_selector("text=Choose a folder")
        page.locator("input[type=file][accept]").first.set_input_files(str(SAMPLES / "synthetic_headshots.zip"))
        page.wait_for_selector(".cand", timeout=30000)
        shot("30_photo_review")
        btn = page.locator("button", has_text="ticked photo")
        m = re.search(r"Assign (\d+)", btn.inner_text())
        assert m and int(m.group(1)) >= 24  # 24 by RHC ID plus unique first-and-last-name matches
        btn.click()
        page.locator(".modal .btn-primary", has_text="Assign").click()
        page.wait_for_selector(f"text={m.group(1)} photos assigned")

        # Tasks: configure IT task, prepare, accept omissions, send (dry-run)
        page.click("text=Tasks and Exports")
        page.wait_for_selector("text=IT headshots")
        page.locator(".task-card", has_text="IT headshots").locator("text=Task settings").click()
        modal = page.locator(".modal")
        modal.locator("input[type=date]").fill("2026-09-15")
        modal.locator("input[value=all_active]").check()
        modal.locator("input[placeholder^=\"name@company\"]").fill("it-team@relay-sample.test")
        modal.locator("text=Save settings").click()
        page.wait_for_selector("text=Task settings saved.")
        page.locator(".task-card", has_text="IT headshots").locator("button", has_text="Prepare packages now").click()
        page.locator(".modal .btn-primary", has_text="Prepare").click()
        page.wait_for_selector("text=Packages", timeout=30000)
        page.wait_for_selector("text=Needs Attention")
        shot("40_period_needs_attention")
        page.click("text=Accept omissions")
        page.locator(".modal textarea").fill("Missing photos will follow in the next cycle.")
        page.locator(".modal .btn-primary", has_text="Accept").click()
        page.wait_for_selector(".card-title >> text=Ready")
        page.click("text=Send now")
        page.locator(".modal .btn-primary", has_text="Send").click()
        page.wait_for_selector(".card-title >> text=Confirmed in Sent Items", timeout=30000)
        shot("41_period_confirmed")

        # Backups
        page.goto(server + "#/settings/backups")
        page.click("text=Back up now")
        page.wait_for_selector("text=Backup created and verified", timeout=60000)
        shot("50_backups")
        browser.close()
    assert not errors, errors
