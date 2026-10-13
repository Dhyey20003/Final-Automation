"""Headshot package preparation.

A package is one ZIP per location per task period, plus a manifest. Prepared
packages are immutable: files are written once, their SHA-256 is recorded and
checked before sending, and regenerating creates a new revision instead of
changing an existing one.

Only exported copies are renamed. Stored originals are never modified. When
an output format is chosen (JPG/PNG) images are genuinely re-encoded.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

from .. import APP_VERSION, audit
from ..db import jdump, jload, tx, utcnow
from ..errors import HubError, ValidationFailed
from ..fsutil import force_rmtree
from ..permissions import CurrentUser
from ..timeutil import zone
from . import tasks as T

FORMAT_EXT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp", "TIFF": ".tif"}
EXT_ALIASES = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP", ".bmp": "BMP", ".tif": "TIFF", ".tiff": "TIFF"}
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
LOCKED_STATUSES = ("Submitted to Outlook", "Confirmed in Sent Items", "Manually Completed", "Delivery Uncertain")


def sanitize_filename(stem: str, max_len: int = 120) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem or "")
    s = re.sub(r"\s+", " ", s).strip().strip(".")
    if not s:
        s = "photo"
    if s.split(".")[0].upper() in RESERVED:
        s = "_" + s
    return s[:max_len]


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render_name(template: str, values: dict) -> str:
    return template.format_map(_SafeDict(values))


def _split_name(full: str) -> tuple[str, str]:
    parts = (full or "").split()
    if not parts:
        return "", ""
    return parts[0], parts[-1] if len(parts) > 1 else ""


def plan_filenames(items: list[dict], template: str, output_format: str) -> None:
    """Assign item['filename'] deterministically. Duplicates get _<RHC ID> added."""
    for it in items:
        orig = it["source_filename"] or f"{it['rhc_id']}{FORMAT_EXT.get(it['photo_format'], '.jpg')}"
        ostem, oext = os.path.splitext(orig)
        if output_format == "jpg":
            ext = ".jpg"
        elif output_format == "png":
            ext = ".png"
        else:
            actual = it["photo_format"]
            if EXT_ALIASES.get(oext.lower()) == actual:
                ext = oext.lower()
            else:
                ext = FORMAT_EXT.get(actual, ".jpg")
                if oext:
                    it["notes"].append(f"Extension corrected from '{oext}' to '{ext}' to match the actual image format.")
        first, last = _split_name(it["full_name"])
        values = {"original_name": ostem, "original_stem": ostem, "rhc_id": it["rhc_id"], "full_name": it["full_name"],
                  "first_name": first, "last_name": last, "location": it["location"], "designation": it.get("designation") or ""}
        stem = sanitize_filename(render_name(template, values))
        it["_stem"], it["_ext"] = stem, ext
        it["filename"] = stem + ext
    items.sort(key=lambda i: (i["rhc_id"], i["photo_id"]))
    for _ in range(3):
        groups: dict[str, list[dict]] = {}
        for it in items:
            groups.setdefault(it["filename"].lower(), []).append(it)
        dup = [g for g in groups.values() if len(g) > 1]
        if not dup:
            break
        for g in dup:
            for it in g:
                if it.get("_dedup") == 1:
                    it["filename"] = f"{it['_stem']}_{it['rhc_id']}_p{it['photo_id']}{it['_ext']}"
                    it["_dedup"] = 2
                elif not it.get("_dedup"):
                    it["filename"] = f"{it['_stem']}_{it['rhc_id']}{it['_ext']}"
                    it["_dedup"] = 1
                it["renamed_for_duplicate"] = True


def convert_image(data: bytes, output_format: str) -> tuple[bytes, str | None]:
    """Return (bytes, converted_from). JPEG->JPG keeps original bytes; other formats are re-encoded."""
    with Image.open(io.BytesIO(data)) as im:
        src = im.format
        target = {"jpg": "JPEG", "png": "PNG"}.get(output_format)
        if target is None or src == target:
            return data, None
        im = ImageOps.exif_transpose(im)
        out = io.BytesIO()
        if target == "JPEG":
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")
            im.save(out, "JPEG", quality=92, optimize=True)
        else:
            im.save(out, "PNG", optimize=True)
        return out.getvalue(), src


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def current_packages(conn, period_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM packages WHERE period_id = ? AND status != 'superseded' ORDER BY location", (period_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["manifest"] = jload(d["manifest"], {})
        out.append(d)
    return out


def collect(conn, ctx, task: dict, location: str) -> dict:
    """Decide which employees go into a location package, and why others are left out."""
    cfg = task["config"]
    inclusion = cfg.get("inclusion")
    if inclusion not in T.INCLUSIONS:
        raise ValidationFailed("Choose the inclusion setting for this task before preparing packages.")
    baseline = {r["employee_id"]: dict(r) for r in conn.execute(
        "SELECT * FROM export_baseline WHERE task_id = ?", (task["id"],)).fetchall()}
    rows = conn.execute(
        "SELECT e.id, e.rhc_id, e.full_name, e.location, e.designation, e.employment_status, e.current_photo_id, "
        "p.id AS pid, p.version_no, p.stored_path, p.sha256, p.original_filename, p.image_format "
        "FROM employees e LEFT JOIN photos p ON p.id = e.current_photo_id "
        "WHERE e.record_scope = 'directory' AND e.archived = 0 AND e.location = ? "
        "AND e.employment_status IN ('Active', 'Pending Review') ORDER BY e.rhc_id", (location,)).fetchall()
    items, omissions, unchanged = [], [], 0
    for r in rows:
        who = {"employee_id": r["id"], "rhc_id": r["rhc_id"], "name": r["full_name"]}
        if r["employment_status"] != "Active":
            omissions.append({**who, "reason": "Employment status is Pending Review (not active)"})
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-.]*", r["rhc_id"] or ""):
            omissions.append({**who, "reason": "RHC ID is missing or cannot be used in a filename"})
            continue
        if not r["pid"]:
            omissions.append({**who, "reason": "No photo on record"})
            continue
        b = baseline.get(r["id"])
        if inclusion == "changed" and b and b["photo_id"] == r["pid"] and b["location"] == location:
            unchanged += 1
            continue
        path = ctx.paths.data / r["stored_path"]
        if not path.exists():
            omissions.append({**who, "reason": "Photo file is missing from the data folder"})
            continue
        if _file_sha(path) != r["sha256"]:
            omissions.append({**who, "reason": "Photo file is damaged (checksum does not match)"})
            continue
        reason = "new" if not b else ("location changed" if b["location"] != location else "photo changed")
        items.append({**who, "full_name": r["full_name"], "location": location, "designation": r["designation"],
                      "photo_id": r["pid"], "photo_version": r["version_no"], "photo_sha256": r["sha256"],
                      "photo_format": r["image_format"], "source_filename": r["original_filename"], "path": path,
                      "notes": [], "change_reason": reason if inclusion == "changed" else None})
    return {"items": items, "omissions": omissions, "unchanged": unchanged}


def unlocated_active(conn) -> list[dict]:
    return [{"employee_id": r["id"], "rhc_id": r["rhc_id"], "name": r["full_name"], "reason": "No location recorded"}
            for r in conn.execute("SELECT id, rhc_id, full_name FROM employees WHERE record_scope = 'directory' AND archived = 0 "
                                  "AND employment_status = 'Active' AND (location IS NULL OR location = '')").fetchall()]


def prepare_period(conn, ctx, user: CurrentUser, period_id: int, regenerate: bool = False) -> dict:
    period = T.period_row(conn, period_id)
    task = T.load_task(conn, period["task_id"])
    if period["status"] in LOCKED_STATUSES:
        raise HubError(f"This period is '{period['status']}'. Packages cannot be regenerated after sending.")
    active = conn.execute("SELECT 1 FROM dispatches WHERE period_id = ? AND active_send_lock = 1", (period_id,)).fetchone()
    if active:
        raise HubError("A send for this period is in progress or unresolved.")
    with tx(conn):
        T.set_period_status(conn, period_id, "Preparing", None)
    try:
        result = _build_all(conn, ctx, user, task, period, regenerate)
    except Exception as exc:
        with tx(conn):
            T.set_period_status(conn, period_id, "Needs Attention", {"message": f"Preparation failed: {getattr(exc, 'message', exc)}"})
            audit.log(conn, user.email, "package.prepare_failed", "task_period", period_id, {"error": str(exc)}, user.ip)
        raise
    return result


def _build_all(conn, ctx, user, task, period, regenerate) -> dict:
    cfg = task["config"]
    tz = zone(T.tzname(conn))
    existing = {p["location"]: p for p in current_packages(conn, period["id"])}
    unlocated = unlocated_active(conn)
    built = []
    for loc in cfg["locations"]:
        if loc in existing and not regenerate:
            continue
        data = collect(conn, ctx, task, loc)
        plan_filenames(data["items"], cfg["naming_template"], cfg["output_format"])
        built.append(_write_package(conn, ctx, user, task, period, loc, data, unlocated, tz))
    with tx(conn):
        status, detail = evaluate_period(conn, task, period["id"])
        T.set_period_status(conn, period["id"], status, detail)
        audit.log(conn, user.email, "package.prepare", "task_period", period["id"],
                  {"task": task["task_key"], "period": period["period_key"], "regenerate": regenerate,
                   "built": [{"location": b["location"], "revision": b["revision"], "count": b["included_count"],
                              "omitted": b["omitted_count"]} for b in built], "status": status}, user.ip)
    return {"status": status, "detail": detail, "packages": current_packages(conn, period["id"])}


def _write_package(conn, ctx, user, task, period, location, data, unlocated, tz) -> dict:
    cfg = task["config"]
    items, omissions = data["items"], data["omissions"]
    period_key = period["period_key"]
    names = {"location": location, "period_date": period_key if len(period_key) == 10 else period_key + "-01",
             "period_month": period_key[:7], "task": task["name"], "kind": task["kind"]}
    zip_name = sanitize_filename(render_name(cfg["archive_name_template"], names).removesuffix(".zip")) + ".zip"
    work = ctx.paths.tmp / f"pkg-{uuid.uuid4().hex}"
    work.mkdir(parents=True)
    now_local = datetime.now(tz)
    manifest = {
        "application": f"Relay Employee Hub {APP_VERSION}",
        "task": task["name"], "task_key": task["task_key"], "kind": task["kind"],
        "period_key": period_key, "reporting_period": period["period_label"], "location": location,
        "generated_at_utc": utcnow(), "generated_at_local": now_local.isoformat(timespec="seconds"),
        "generated_by": user.email, "inclusion": cfg["inclusion"], "inclusion_label": T.INCLUSIONS[cfg["inclusion"]],
        "naming_template": cfg["naming_template"], "output_format": cfg["output_format"], "zip_name": zip_name,
        "employees": [], "exclusions": omissions, "employees_without_location": unlocated,
        "unchanged_not_included": data["unchanged"],
    }
    try:
        zpath = work / zip_name
        if items or cfg.get("include_manifest_in_zip"):
            with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for it in items:
                    raw = it["path"].read_bytes()
                    out, converted = convert_image(raw, cfg["output_format"])
                    zinfo = zipfile.ZipInfo(it["filename"], date_time=now_local.timetuple()[:6])
                    zinfo.compress_type = zipfile.ZIP_DEFLATED
                    zf.writestr(zinfo, out)
                    manifest["employees"].append({
                        "employee_id": it["employee_id"], "rhc_id": it["rhc_id"], "name": it["full_name"],
                        "photo_id": it["photo_id"], "photo_version": it["photo_version"],
                        "photo_sha256": it["photo_sha256"], "source_filename": it["source_filename"],
                        "filename": it["filename"], "renamed_for_duplicate": bool(it.get("renamed_for_duplicate")),
                        "converted_from": converted, "notes": it["notes"], "change_reason": it.get("change_reason"),
                        "exported_sha256": hashlib.sha256(out).hexdigest(),
                    })
                if cfg.get("include_manifest_in_zip"):
                    zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        manifest["complete"] = not omissions
        manifest["included_count"] = len(items)
        manifest["omitted_count"] = len(omissions)
        with tx(conn):
            rev = conn.execute("SELECT COALESCE(MAX(revision), 0) + 1 FROM packages WHERE task_id = ? AND period_key = ? AND location = ?",
                               (task["id"], period_key, location)).fetchone()[0]
            manifest["revision"] = rev
            dest = ctx.paths.packages / task["task_key"] / period_key / location / f"rev{rev}"
            if dest.exists():
                force_rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            zip_rel, sha, size = None, None, 0
            has_zip = zpath.exists()
            if has_zip:
                sha, size = _file_sha(zpath), zpath.stat().st_size
                manifest["zip_sha256"] = sha
                manifest["zip_bytes"] = size
            (work / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
            shutil.move(str(work), str(dest))
            if has_zip:
                zip_rel = (dest / zip_name).relative_to(ctx.paths.data).as_posix()
            man_rel = (dest / "manifest.json").relative_to(ctx.paths.data).as_posix()
            _make_readonly(dest)
            status = "empty" if not items else ("ready" if not omissions else "needs_attention")
            conn.execute("UPDATE packages SET status = 'superseded' WHERE task_id = ? AND period_key = ? AND location = ? AND status != 'superseded'",
                         (task["id"], period_key, location))
            pkg_id = conn.execute(
                "INSERT INTO packages(task_id, period_id, period_key, location, revision, status, zip_path, zip_name, manifest_path, "
                "manifest, sha256, bytes, included_count, omitted_count, is_complete, created_at, created_by) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task["id"], period["id"], period_key, location, rev, status, zip_rel, zip_name if zip_rel else None, man_rel,
                 jdump(manifest), sha, size, len(items), len(omissions), 1 if not omissions else 0, utcnow(), user.email)).lastrowid
            conn.executemany(
                "INSERT INTO package_items(package_id, employee_id, rhc_id, photo_id, location, filename, source_filename) VALUES (?,?,?,?,?,?,?)",
                [(pkg_id, e["employee_id"], e["rhc_id"], e["photo_id"], location, e["filename"], e["source_filename"])
                 for e in manifest["employees"]])
        return {"id": pkg_id, "location": location, "revision": rev, "included_count": len(items), "omitted_count": len(omissions)}
    finally:
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)


def _make_readonly(folder: Path) -> None:
    for p in folder.rglob("*"):
        if p.is_file():
            os.chmod(p, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


def _make_writable(folder: Path) -> None:
    for p in folder.rglob("*"):
        if p.is_file():
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)


def evaluate_period(conn, task: dict, period_id: int) -> tuple[str, dict]:
    pkgs = current_packages(conn, period_id)
    cfg = task["config"]
    problems = []
    if not pkgs:
        return "Due", {"message": "Packages have not been prepared yet."}
    unaccepted = [p for p in pkgs if p["omitted_count"] and not p["omissions_accepted_by"]]
    for p in unaccepted:
        problems.append(f"{p['location']}: {p['omitted_count']} employees left out (see exclusions). Fix them and regenerate, or accept the omissions.")
    unlocated = pkgs[0]["manifest"].get("employees_without_location") or []
    if unlocated and not all(p["omissions_accepted_by"] for p in pkgs):
        problems.append(f"{len(unlocated)} active employees have no location and are in no package.")
    non_empty = [p for p in pkgs if p["included_count"]]
    if not non_empty:
        problems.append("There are no photos to send in this period (nothing new or changed). Mark the period complete, or regenerate later.")
    total = sum(p["bytes"] or 0 for p in non_empty)
    limit = float(cfg.get("attachment_limit_mb", 18)) * 1024 * 1024
    if total > limit:
        problems.append(f"Attachments total {total / 1048576:.1f} MB, above the {cfg.get('attachment_limit_mb')} MB limit.")
    detail = {"message": " ".join(problems) if problems else "Packages are ready to send.", "problems": problems,
              "attachment_bytes": total}
    return ("Needs Attention" if problems else "Ready"), detail


def accept_omissions(conn, user: CurrentUser, period_id: int, note: str) -> dict:
    if not (note or "").strip():
        raise ValidationFailed("Add a short note explaining why the omissions are acceptable.")
    period = T.period_row(conn, period_id)
    task = T.load_task(conn, period["task_id"])
    if period["status"] in LOCKED_STATUSES:
        raise HubError("This period has already been sent.")
    with tx(conn):
        conn.execute("UPDATE packages SET omissions_accepted_by = ?, omissions_accepted_at = ?, omissions_accept_note = ? "
                     "WHERE period_id = ? AND status != 'superseded'", (user.email, utcnow(), note.strip(), period_id))
        status, detail = evaluate_period(conn, task, period_id)
        T.set_period_status(conn, period_id, status, detail)
        audit.log(conn, user.email, "package.omissions_accepted", "task_period", period_id, {"note": note}, user.ip)
    return {"status": status, "detail": detail}


def verify_package_files(ctx, pkgs: list[dict]) -> list[str]:
    problems = []
    for p in pkgs:
        if not p["zip_path"]:
            continue
        path = ctx.paths.data / p["zip_path"]
        if not path.exists():
            problems.append(f"{p['zip_name']} is missing from the data folder.")
        elif _file_sha(path) != p["sha256"]:
            problems.append(f"{p['zip_name']} has been changed on disk since it was prepared.")
    return problems
