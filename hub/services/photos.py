"""Photo storage, upload staging, matching, review and assignment.

Storage layout (inside the data folder):
    images/<employee id>/<employee id>_p<photo id>_v<version>.<ext>   original bytes, never modified
    thumbnails/<employee id>/<photo id>.jpg                             generated preview
Original filenames are kept as metadata only. Previous versions are kept for
audit and package history.

Matching (never facial recognition):
1. Explicit mapping supplied by the operator, or an RHC ID found in the filename -> pre-selected.
2. Unique name match, or an RHC ID found in PDF text next to an image -> suggestion only, needs confirmation.
3. Sequence mapping -> only with an explicit ordered employee list and a confirmed preview.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from .. import audit
from ..db import get_setting, jdump, jload, tx, utcnow
from ..errors import Forbidden, HubError, NotFound, ValidationFailed
from ..permissions import CurrentUser
from . import employees as emps
from . import pdfproc

IMAGE_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp", "TIFF": ".tif"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
IGNORED_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
THUMB_SIZE = 320


# ---------------------------------------------------------------- validation + storage

def image_info(data: bytes, limits: dict) -> tuple[str, int, int]:
    max_bytes = int(limits.get("max_image_mb", 25)) * 1024 * 1024
    if len(data) > max_bytes:
        raise HubError(f"The image is larger than {limits.get('max_image_mb', 25)} MB.")
    Image.MAX_IMAGE_PIXELS = int(limits.get("max_image_pixels", 60_000_000))
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = im.format
            w, h = im.size
            im.verify()
    except Image.DecompressionBombError:
        raise HubError("The image has too many pixels to process safely.") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise HubError("This file is not a readable image (supported: JPG, PNG, WEBP, BMP, TIFF).") from None
    if fmt not in IMAGE_FORMATS:
        raise HubError(f"{fmt} images are not supported. Use JPG, PNG, WEBP, BMP or TIFF.")
    if w < 32 or h < 32:
        raise HubError("The image is too small to be a headshot.")
    return fmt, w, h


def make_thumbnail(data: bytes, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((THUMB_SIZE, THUMB_SIZE))
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            if im.mode in ("RGBA", "LA") or "transparency" in im.info:
                im = im.convert("RGBA")
                bg.paste(im, mask=im.split()[-1])
            else:
                bg.paste(im.convert("RGB"))
            im = bg
        tmp = dest.with_suffix(".tmp")
        im.convert("RGB").save(tmp, "JPEG", quality=85)
        os.replace(tmp, dest)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def store_photo(conn: sqlite3.Connection, ctx, user: CurrentUser, emp_id: int, data: bytes, original_name: str | None,
                source: str, source_ref: str | None = None, allow_same_as_current: bool = False) -> int:
    """Store a new photo version and make it current. Caller owns the transaction."""
    limits = get_setting(conn, "limits", {})
    fmt, w, h = image_info(data, limits)
    rec = emps.fetch_record(conn, emp_id)
    emps.check_scope(user, rec)
    digest = hashlib.sha256(data).hexdigest()
    if rec.get("current_photo_id") and not allow_same_as_current:
        cur = conn.execute("SELECT sha256 FROM photos WHERE id = ?", (rec["current_photo_id"],)).fetchone()
        if cur and cur["sha256"] == digest:
            raise HubError(f"This image is already the current photo for {rec['full_name']}.", code="duplicate_current")
    ext = IMAGE_FORMATS[fmt]
    vno = (conn.execute("SELECT COALESCE(MAX(version_no), 0) FROM photos WHERE employee_id = ?", (emp_id,)).fetchone()[0]) + 1
    now = utcnow()
    pid = conn.execute(
        "INSERT INTO photos(employee_id, version_no, original_filename, stored_path, sha256, bytes, width, height, image_format, "
        "source, source_ref, uploaded_by, uploaded_at, is_current) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
        (emp_id, vno, original_name, "", digest, len(data), w, h, fmt, source, source_ref, user.email, now),
    ).lastrowid
    rel = Path("images") / str(emp_id) / f"{emp_id}_p{pid}_v{vno}{ext}"
    trel = Path("thumbnails") / str(emp_id) / f"{pid}.jpg"
    _atomic_write(ctx.paths.data / rel, data)
    make_thumbnail(data, ctx.paths.data / trel)
    conn.execute("UPDATE photos SET is_current = 0, superseded_at = ? WHERE employee_id = ? AND is_current = 1", (now, emp_id))
    conn.execute("UPDATE photos SET stored_path = ?, thumb_path = ?, is_current = 1 WHERE id = ?",
                 (rel.as_posix(), trel.as_posix(), pid))
    emps.apply_update(conn, user, emp_id, None, {}, source=f"photo:{source}", enforce_permissions=False,
                      extra_columns={"current_photo_id": pid})
    audit.log(conn, user.email, "photo.assign", "employee", emp_id,
              {"photo_id": pid, "version": vno, "original_filename": original_name, "source": source, "sha256": digest}, user.ip)
    try:
        from . import posts as POSTS

        POSTS.refresh_employee_projects(conn, ctx, user.email, emp_id)
    except Exception:  # a post problem must never block storing the photo
        import logging

        logging.getLogger("hub.posts").exception("post refresh after photo failed")
    return pid


def photo_row(conn, photo_id: int) -> dict:
    r = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if r is None:
        raise NotFound("Photo not found.")
    return dict(r)


def authorised_photo_path(conn, ctx, user: CurrentUser, photo_id: int, thumb: bool) -> Path:
    if not user.can_see_field("photo"):
        raise Forbidden("Photos are hidden for your account.")
    p = photo_row(conn, photo_id)
    rec = emps.fetch_record(conn, p["employee_id"])
    emps.check_scope(user, rec)
    rel = p["thumb_path"] if thumb else p["stored_path"]
    path = ctx.paths.data / rel
    if not path.exists():
        raise NotFound("The photo file is missing from the data folder.")
    return path


def versions(conn, emp_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, version_no, original_filename, sha256, bytes, width, height, image_format, source, uploaded_by, "
        "uploaded_at, is_current, superseded_at FROM photos WHERE employee_id = ? ORDER BY version_no DESC", (emp_id,))]


def missing_photos(conn, user: CurrentUser) -> list[dict]:
    rows = conn.execute(
        "SELECT id, rhc_id, full_name, location, designation, department FROM employees "
        "WHERE record_scope = 'directory' AND archived = 0 AND employment_status = 'Active' AND current_photo_id IS NULL "
        "ORDER BY location, rhc_id").fetchall()
    out = []
    for r in rows:
        if not user.in_scope(r["location"]):
            continue
        d = dict(r)
        for k in ("designation", "department", "location"):
            if not user.can_see_field(k):
                d[k] = None
        out.append(d)
    return out


# ---------------------------------------------------------------- staging batches

def create_batch(conn, user: CurrentUser, label: str | None) -> int:
    with tx(conn):
        bid = conn.execute("INSERT INTO photo_batches(label, created_by, created_at) VALUES (?,?,?)",
                           (label or "Photo upload", user.email, utcnow())).lastrowid
        audit.log(conn, user.email, "photo.batch_created", "photo_batch", bid, {"label": label}, user.ip)
    return bid


def get_batch(conn, batch_id: int) -> dict:
    r = conn.execute("SELECT * FROM photo_batches WHERE id = ?", (batch_id,)).fetchone()
    if r is None:
        raise NotFound("Upload batch not found.")
    return dict(r)


def _stage_dir(ctx, batch_id: int) -> Path:
    d = ctx.paths.photo_staging / str(batch_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _add_candidate(conn, ctx, batch_id: int, seq: int, data: bytes | None, original_name: str, source_kind: str,
                   source_ref: str, limits: dict, *, pdf_id=None, page_no=None, bbox=None, context_text=None,
                   flags=None, error=None) -> int:
    flags = list(flags or [])
    row: dict[str, Any] = {"batch_id": batch_id, "seq": seq, "original_name": original_name, "source_kind": source_kind,
                           "source_ref": source_ref, "pdf_id": pdf_id, "page_no": page_no,
                           "bbox": jdump(bbox) if bbox else None, "context_text": context_text}
    if error is None and data is not None:
        try:
            fmt, w, h = image_info(data, limits)
            d = _stage_dir(ctx, batch_id)
            name = uuid.uuid4().hex
            staged = d / f"{name}{IMAGE_FORMATS[fmt]}"
            staged.write_bytes(data)
            make_thumbnail(data, d / f"{name}_t.jpg")
            row.update(staged_path=str(staged.relative_to(ctx.paths.data)),
                       thumb_path=str((d / f"{name}_t.jpg").relative_to(ctx.paths.data)),
                       sha256=hashlib.sha256(data).hexdigest(), width=w, height=h, image_format=fmt, status="pending")
        except HubError as exc:
            error = exc.message
    if error:
        row.update(status="error", message=error)
    row["flags"] = jdump(flags)
    cols = list(row)
    return conn.execute(f"INSERT INTO photo_candidates({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                        [row[c] for c in cols]).lastrowid


def add_files(conn, ctx, user: CurrentUser, batch_id: int, files: list[tuple[str, bytes]]) -> dict:
    get_batch(conn, batch_id)
    limits = get_setting(conn, "limits", {})
    added, errors = 0, 0
    with tx(conn):
        seq = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM photo_candidates WHERE batch_id = ?", (batch_id,)).fetchone()[0]
        for fname, data in files:
            rel = fname.replace("\\", "/")
            base = PurePosixPath(rel).name
            ext = Path(base).suffix.lower()
            if base.lower() in IGNORED_NAMES or base.startswith("._"):
                continue
            if ext == ".zip":
                for inner_name, inner_data, err in safe_zip_entries(data, limits):
                    inner_base = PurePosixPath(inner_name).name
                    iext = Path(inner_base).suffix.lower()
                    if err:
                        seq += 1
                        _add_candidate(conn, ctx, batch_id, seq, None, inner_base, "zip", f"{base}:{inner_name}", limits, error=err)
                        errors += 1
                    elif iext == ".pdf":
                        seq = _add_pdf(conn, ctx, batch_id, seq, inner_data, inner_base, f"{base}:{inner_name}", limits)
                    elif iext in IMAGE_EXTS:
                        seq += 1
                        _add_candidate(conn, ctx, batch_id, seq, inner_data, inner_base, "zip", f"{base}:{inner_name}", limits)
                    else:
                        seq += 1
                        _add_candidate(conn, ctx, batch_id, seq, None, inner_base, "zip", f"{base}:{inner_name}", limits,
                                       error=f"Unsupported file type '{iext or 'none'}' inside the ZIP.")
                        errors += 1
            elif ext == ".pdf":
                seq = _add_pdf(conn, ctx, batch_id, seq, data, base, rel, limits)
            elif ext in IMAGE_EXTS:
                seq += 1
                _add_candidate(conn, ctx, batch_id, seq, data, base, "folder" if "/" in rel else "upload", rel, limits)
            elif ext in (".heic", ".heif"):
                seq += 1
                _add_candidate(conn, ctx, batch_id, seq, None, base, "upload", rel, limits,
                               error="HEIC photos are not supported. Convert to JPG first (for example with the Photos app).")
            else:
                seq += 1
                _add_candidate(conn, ctx, batch_id, seq, None, base, "upload", rel, limits,
                               error=f"Unsupported file type '{ext or 'none'}'. Use JPG, PNG, WEBP, BMP, TIFF, ZIP or PDF.")
        auto_match(conn, user, batch_id)
        audit.log(conn, user.email, "photo.files_added", "photo_batch", batch_id, {"files": [f for f, _ in files][:200]}, user.ip)
    return batch_detail(conn, user, batch_id)


def safe_zip_entries(data: bytes, limits: dict):
    """Yield (name, bytes, error). Nothing is written to disk by name, so path traversal is impossible;
    unsafe names are still reported and rejected."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        yield ("archive.zip", b"", "The ZIP file is damaged or not a ZIP file.")
        return
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > int(limits.get("max_zip_entries", 3000)):
        yield ("archive.zip", b"", f"The ZIP has {len(infos)} files, above the limit of {limits.get('max_zip_entries')}.")
        return
    total = 0
    max_total = int(limits.get("max_zip_total_mb", 1500)) * 1024 * 1024
    max_file = int(limits.get("max_image_mb", 25)) * 1024 * 1024
    max_ratio = int(limits.get("max_zip_ratio", 200))
    for info in infos:
        name = info.filename.replace("\\", "/")
        base = PurePosixPath(name).name
        if name.startswith("__MACOSX/") or base.lower() in IGNORED_NAMES or base.startswith("._"):
            continue
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or re.match(r"^[A-Za-z]:", name):
            yield (name, b"", "Unsafe path inside the ZIP was rejected.")
            continue
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            yield (name, b"", "Symbolic links inside the ZIP are not allowed.")
            continue
        if info.flag_bits & 0x1:
            yield (name, b"", "Encrypted ZIP entries are not supported.")
            continue
        is_pdf = base.lower().endswith(".pdf")
        limit = max_file * (8 if is_pdf else 1)
        if info.file_size > limit:
            yield (name, b"", "File inside the ZIP is larger than the size limit.")
            continue
        if info.compress_size and info.file_size / info.compress_size > max_ratio:
            yield (name, b"", "File inside the ZIP is suspiciously compressed and was rejected.")
            continue
        total += info.file_size
        if total > max_total:
            yield (name, b"", "The ZIP contents exceed the total size limit; remaining files were skipped.")
            return
        if base.lower().endswith(".zip"):
            yield (name, b"", "ZIP files inside a ZIP are not opened. Extract the inner ZIP and upload it separately.")
            continue
        with zf.open(info) as f:
            content = f.read(limit + 1)
        if len(content) > limit:
            yield (name, b"", "File inside the ZIP is larger than the size limit.")
            continue
        yield (name, content, None)


def _add_pdf(conn, ctx, batch_id: int, seq: int, data: bytes, name: str, ref: str, limits: dict) -> int:
    d = _stage_dir(ctx, batch_id)
    staged = d / f"{uuid.uuid4().hex}.pdf"
    staged.write_bytes(data)
    try:
        pages, images = pdfproc.analyse(staged, int(limits.get("max_pdf_pages", 300)))
    except HubError as exc:
        seq += 1
        _add_candidate(conn, ctx, batch_id, seq, None, name, "pdf", ref, limits, error=exc.message)
        return seq
    pdf_id = conn.execute("INSERT INTO pdf_docs(batch_id, staged_path, original_name, page_count, pages) VALUES (?,?,?,?,?)",
                          (batch_id, str(staged.relative_to(ctx.paths.data)), name, len(pages), jdump(pages))).lastrowid
    for i, img in enumerate(images, start=1):
        seq += 1
        _add_candidate(conn, ctx, batch_id, seq, img["bytes"], f"{Path(name).stem}_p{img['page_no']}_img{i}{img['ext']}",
                       "pdf", f"{name} page {img['page_no']}", limits, pdf_id=pdf_id, page_no=img["page_no"],
                       bbox=img["bbox"], context_text=img["context_text"], flags=img["flags"])
    return seq


def pdf_crop(conn, ctx, user: CurrentUser, batch_id: int, pdf_id: int, page_no: int, rect: list[float]) -> dict:
    doc = conn.execute("SELECT * FROM pdf_docs WHERE id = ? AND batch_id = ?", (pdf_id, batch_id)).fetchone()
    if doc is None:
        raise NotFound("PDF not found in this batch.")
    limits = get_setting(conn, "limits", {})
    png, text = pdfproc.crop_region(ctx.paths.data / doc["staged_path"], page_no, rect, int(limits.get("max_pdf_pages", 300)))
    with tx(conn):
        seq = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM photo_candidates WHERE batch_id = ?", (batch_id,)).fetchone()[0] + 1
        cid = _add_candidate(conn, ctx, batch_id, seq, png, f"{Path(doc['original_name']).stem}_p{page_no}_crop{seq}.png",
                             "pdf_crop", f"{doc['original_name']} page {page_no} (manual crop)", limits, pdf_id=pdf_id,
                             page_no=page_no, bbox=rect, context_text=text, flags=["Manual crop from PDF page"])
        auto_match(conn, user, batch_id, only_ids=[cid])
        audit.log(conn, user.email, "photo.pdf_crop", "photo_batch", batch_id, {"pdf": doc["original_name"], "page": page_no}, user.ip)
    return candidate(conn, cid)


def page_image(conn, ctx, batch_id: int, pdf_id: int, page_no: int) -> Path:
    doc = conn.execute("SELECT * FROM pdf_docs WHERE id = ? AND batch_id = ?", (pdf_id, batch_id)).fetchone()
    if doc is None:
        raise NotFound("PDF not found in this batch.")
    out = ctx.paths.data / Path(doc["staged_path"]).with_name(f"pdf{pdf_id}_p{page_no}.png")
    if not out.exists():
        limits = get_setting(conn, "limits", {})
        img = pdfproc.render_page(ctx.paths.data / doc["staged_path"], page_no, 1.6, int(limits.get("max_pdf_pages", 300)))
        img.save(out, "PNG")
    return out


# ---------------------------------------------------------------- matching

def _norm_tokens(s: str) -> str:
    return " ".join(sorted(re.sub(r"[^a-z]+", " ", s.lower()).split()))


def _employee_pool(conn, user: CurrentUser) -> list[dict]:
    rows = conn.execute("SELECT id, rhc_id, full_name, location, current_photo_id FROM employees "
                        "WHERE archived = 0 AND record_scope = 'directory'").fetchall()
    return [dict(r) for r in rows if user.in_scope(r["location"])]


def find_rhc_ids(text: str, rhc_set: dict[str, dict]) -> list[dict]:
    if not text:
        return []
    up = text.upper()
    found: dict[int, dict] = {}
    tokens = set(re.findall(r"[A-Z0-9]+(?:[-_ ][0-9]+)?", up)) | set(re.findall(r"[A-Z0-9]+", up))
    for t in list(tokens):
        tokens.add(re.sub(r"[-_ ]", "", t))
    for t in tokens:
        if t in rhc_set:
            found[rhc_set[t]["id"]] = rhc_set[t]
    stem_compact = re.sub(r"[^A-Z0-9]", "", up)
    if stem_compact in rhc_set:
        found[rhc_set[stem_compact]["id"]] = rhc_set[stem_compact]
    return list(found.values())


def auto_match(conn, user: CurrentUser, batch_id: int, only_ids: list[int] | None = None) -> None:
    pool = _employee_pool(conn, user)
    rhc_set = {e["rhc_id"].upper(): e for e in pool}
    for e in pool:
        rhc_set.setdefault(re.sub(r"[^A-Z0-9]", "", e["rhc_id"].upper()), e)
    by_name: dict[str, list[dict]] = {}
    by_first_last: dict[str, list[dict]] = {}
    for e in pool:
        by_name.setdefault(_norm_tokens(e["full_name"]), []).append(e)
        toks = re.sub(r"[^a-z]+", " ", e["full_name"].lower()).split()
        if len(toks) >= 2:
            by_first_last.setdefault(f"{toks[0]} {toks[-1]}", []).append(e)
    q = "SELECT * FROM photo_candidates WHERE batch_id = ? AND status = 'pending'"
    cands = [dict(r) for r in conn.execute(q, (batch_id,)).fetchall()]
    if only_ids:
        cands = [c for c in cands if c["id"] in only_ids]
    seen_sha: dict[str, int] = {}
    for c in sorted([dict(r) for r in conn.execute(q, (batch_id,)).fetchall()], key=lambda x: x["seq"]):
        if c["sha256"] and c["sha256"] not in seen_sha:
            seen_sha[c["sha256"]] = c["id"]
    for c in cands:
        if c["match_method"] == "explicit mapping":
            continue
        flags = [f for f in jload(c["flags"], []) if not f.startswith(("Duplicate", "Same image", "Several", "Name matches"))]
        sug, method, certain = None, None, 0
        stem = Path(c["original_name"] or "").stem
        hits = find_rhc_ids(stem, rhc_set) if c["source_kind"] not in ("pdf", "pdf_crop") else []
        if len(hits) == 1:
            rid = re.sub(r"[^A-Z0-9]", "", hits[0]["rhc_id"].upper())
            if re.search(r"[A-Z]", rid) or re.sub(r"[^A-Z0-9]", "", stem.upper()) == rid:
                sug, method, certain = hits[0]["id"], "RHC ID in filename", 1
            else:
                # A bare number inside a longer filename (e.g. IMG_1234) is only a suggestion.
                sug, method = hits[0]["id"], "Number in filename matches an RHC ID (confirm)"
        elif len(hits) > 1:
            flags.append("Several RHC IDs found in the filename; choose the employee manually.")
        if sug is None and c["context_text"]:
            th = find_rhc_ids(c["context_text"], rhc_set)
            if len(th) == 1:
                sug, method = th[0]["id"], "RHC ID in PDF text near image (confirm)"
            elif len(th) > 1:
                flags.append("Several RHC IDs appear near this image; choose the employee manually.")
        if sug is None:
            for text, label in ((stem, "Name in filename"), (c["context_text"] or "", "Name in PDF text (confirm)")):
                key = _norm_tokens(text)
                if key and key in by_name:
                    if len(by_name[key]) == 1:
                        sug, method = by_name[key][0]["id"], label
                        toks = re.sub(r"[^a-z]+", " ", text.lower()).split()
                        others = [e for e in by_first_last.get(f"{toks[0]} {toks[-1]}" if len(toks) >= 2 else "", []) if e["id"] != sug]
                        if label == "Name in filename" and not others:
                            certain = 1  # the whole name matches exactly one employee and nobody else shares first and last name
                        elif others:
                            method = label + " (confirm)"
                            flags.append(f"'{text}' could also be {', '.join(o['full_name'] for o in others[:4])}; confirm the choice.")
                    else:
                        flags.append("Name matches more than one employee; choose manually.")
                    break
                if label == "Name in filename" and text:
                    # "John Smith.jpg" matches "John Michael Smith": first and last name, ignoring middle names and separators.
                    toks = re.sub(r"[^a-z]+", " ", text.lower()).split()
                    fl_key = f"{toks[0]} {toks[-1]}" if len(toks) >= 2 else ""
                    hits = by_first_last.get(fl_key, [])
                    if len(hits) == 1 and set(toks) <= set(re.sub(r"[^a-z]+", " ", hits[0]["full_name"].lower()).split()):
                        sug, method, certain = hits[0]["id"], "First and last name in filename", 1
                        break
                    if len(hits) > 1:
                        flags.append(f"'{text}' could be {len(hits)} employees ({', '.join(h['full_name'] for h in hits[:4])}); choose manually.")
                        break
                if label.startswith("Name in PDF") and text:
                    matches = [e for k, lst in by_name.items() for e in lst if k and _contains_name(text, e["full_name"])]
                    if len(matches) == 1:
                        sug, method = matches[0]["id"], label
                    elif len(matches) > 1:
                        flags.append("Name matches more than one employee; choose manually.")
        if c["sha256"]:
            if seen_sha.get(c["sha256"]) != c["id"]:
                flags.append("Duplicate of another file in this upload.")
            dup = conn.execute("SELECT p.employee_id, p.is_current, e.full_name, e.rhc_id FROM photos p JOIN employees e "
                               "ON e.id = p.employee_id WHERE p.sha256 = ? ORDER BY p.is_current DESC LIMIT 1",
                               (c["sha256"],)).fetchone()
            if dup:
                state = "current photo" if dup["is_current"] else "an earlier photo"
                flags.append(f"Same image is already stored as {state} of {dup['full_name']} ({dup['rhc_id']}).")
        conn.execute("UPDATE photo_candidates SET suggested_employee_id = ?, match_method = ?, match_certain = ?, flags = ? WHERE id = ?",
                     (sug, method, certain, jdump(flags), c["id"]))


def _contains_name(text: str, name: str) -> bool:
    words = [w for w in re.sub(r"[^a-z ]+", " ", name.lower()).split() if len(w) > 1]
    if len(words) < 2:
        return False
    hay = " " + re.sub(r"[^a-z ]+", " ", text.lower()) + " "
    return all(f" {w} " in hay for w in words)


def apply_explicit_mapping(conn, user: CurrentUser, batch_id: int, text: str) -> dict:
    """Mapping lines: <filename>,<RHC ID>  (comma, tab or semicolon separated)."""
    pool = {e["rhc_id"].upper(): e for e in _employee_pool(conn, user)}
    cands = {(c["original_name"] or "").lower(): dict(c) for c in conn.execute(
        "SELECT * FROM photo_candidates WHERE batch_id = ? AND status = 'pending'", (batch_id,)).fetchall()}
    stems = {}
    for k, c in cands.items():
        stems.setdefault(Path(k).stem, []).append(c)
    applied, problems = 0, []
    with tx(conn):
        for ln, line in enumerate((text or "").splitlines(), start=1):
            if not line.strip():
                continue
            parts = [p.strip() for p in re.split(r"[,\t;]", line) if p.strip()]
            if len(parts) < 2:
                problems.append(f"Line {ln}: expected 'filename, RHC ID'.")
                continue
            fname, rhc = parts[0].lower(), parts[1].upper()
            if ln == 1 and ("file" in fname and "rhc" in rhc.lower()):
                continue
            emp = pool.get(rhc)
            c = cands.get(fname) or (stems.get(fname, [None])[0] if len(stems.get(fname, [])) == 1 else None)
            if emp is None:
                problems.append(f"Line {ln}: RHC ID {parts[1]} not found (or outside your access).")
                continue
            if c is None:
                problems.append(f"Line {ln}: no pending file named {parts[0]} in this upload.")
                continue
            conn.execute("UPDATE photo_candidates SET suggested_employee_id = ?, match_method = 'explicit mapping', "
                         "match_certain = 1 WHERE id = ?", (emp["id"], c["id"]))
            applied += 1
        audit.log(conn, user.email, "photo.mapping_applied", "photo_batch", batch_id, {"applied": applied}, user.ip)
    return {"applied": applied, "problems": problems}


def sequence_preview(conn, user: CurrentUser, batch_id: int, candidate_ids: list[int], rhc_lines: str) -> dict:
    pool = {e["rhc_id"].upper(): e for e in _employee_pool(conn, user)}
    ids = [ln.strip().upper() for ln in (rhc_lines or "").splitlines() if ln.strip()]
    problems = []
    cands = []
    for cid in candidate_ids:
        c = conn.execute("SELECT * FROM photo_candidates WHERE id = ? AND batch_id = ?", (cid, batch_id)).fetchone()
        if c is None or c["status"] != "pending":
            problems.append(f"File #{cid} is not a pending image in this upload.")
        else:
            cands.append(dict(c))
    if len(ids) != len(cands):
        problems.append(f"The employee list has {len(ids)} entries but {len(cands)} images were selected. "
                        "Counts must match exactly.")
    if len(set(ids)) != len(ids):
        problems.append("The employee list contains the same RHC ID more than once.")
    pairs = []
    for i in range(max(len(ids), len(cands))):
        c = cands[i] if i < len(cands) else None
        rid = ids[i] if i < len(ids) else None
        emp = pool.get(rid) if rid else None
        if rid and emp is None:
            problems.append(f"Position {i + 1}: RHC ID {rid} not found (or outside your access).")
        pairs.append({"position": i + 1, "candidate_id": c["id"] if c else None, "original_name": c["original_name"] if c else None,
                      "rhc_id": rid, "employee_id": emp["id"] if emp else None, "employee_name": emp["full_name"] if emp else None})
    return {"pairs": pairs, "problems": problems, "ok": not problems}


def assign(conn, ctx, user: CurrentUser, batch_id: int, assignments: list[dict], method_note: str | None = None) -> dict:
    done, problems = [], []
    for a in assignments:
        cid, eid = int(a["candidate_id"]), int(a["employee_id"])
        with tx(conn):
            c = conn.execute("SELECT * FROM photo_candidates WHERE id = ? AND batch_id = ?", (cid, batch_id)).fetchone()
            if c is None or c["status"] != "pending":
                problems.append({"candidate_id": cid, "reason": "This file is not pending any more."})
                continue
            conn.execute("SAVEPOINT assign_one")
            try:
                data = (ctx.paths.data / c["staged_path"]).read_bytes()
                ref = f"{c['source_ref']} | batch {batch_id} | {method_note or c['match_method'] or 'manual selection'}"
                pid = store_photo(conn, ctx, user, eid, data, c["original_name"], c["source_kind"] or "upload", ref,
                                  allow_same_as_current=bool(a.get("allow_duplicate")))
                conn.execute("RELEASE assign_one")
            except (HubError, OSError) as exc:
                conn.execute("ROLLBACK TO assign_one")
                conn.execute("RELEASE assign_one")
                problems.append({"candidate_id": cid, "reason": getattr(exc, "message", str(exc))})
                continue
            conn.execute("UPDATE photo_candidates SET status = 'assigned', assigned_employee_id = ?, assigned_photo_id = ? WHERE id = ?",
                         (eid, pid, cid))
            done.append({"candidate_id": cid, "employee_id": eid, "photo_id": pid})
    return {"assigned": done, "problems": problems}


def set_candidate_status(conn, user: CurrentUser, batch_id: int, ids: list[int], status: str) -> int:
    if status not in ("skipped", "pending"):
        raise ValidationFailed("Unknown status.")
    with tx(conn):
        n = 0
        for cid in ids:
            n += conn.execute("UPDATE photo_candidates SET status = ? WHERE id = ? AND batch_id = ? AND status IN ('pending','skipped')",
                              (status, cid, batch_id)).rowcount
        audit.log(conn, user.email, f"photo.candidates_{status}", "photo_batch", batch_id, {"ids": ids}, user.ip)
    return n


def candidate(conn, cid: int) -> dict:
    r = conn.execute(
        "SELECT c.*, e.rhc_id AS s_rhc, e.full_name AS s_name, e.location AS s_loc, e.current_photo_id AS s_photo, "
        "a.rhc_id AS a_rhc, a.full_name AS a_name FROM photo_candidates c "
        "LEFT JOIN employees e ON e.id = c.suggested_employee_id LEFT JOIN employees a ON a.id = c.assigned_employee_id "
        "WHERE c.id = ?", (cid,)).fetchone()
    return _cand_out(dict(r))


def _cand_out(d: dict) -> dict:
    return {
        "id": d["id"], "seq": d["seq"], "original_name": d["original_name"], "source_kind": d["source_kind"],
        "source_ref": d["source_ref"], "pdf_id": d["pdf_id"], "page_no": d["page_no"], "bbox": jload(d["bbox"]),
        "context_text": d["context_text"], "width": d["width"], "height": d["height"], "format": d["image_format"],
        "status": d["status"], "message": d["message"], "flags": jload(d["flags"], []),
        "suggestion": ({"employee_id": d["suggested_employee_id"], "rhc_id": d.get("s_rhc"), "name": d.get("s_name"),
                        "location": d.get("s_loc"), "has_photo": bool(d.get("s_photo")), "method": d["match_method"],
                        "certain": bool(d["match_certain"])} if d["suggested_employee_id"] else None),
        "assigned": ({"employee_id": d["assigned_employee_id"], "rhc_id": d.get("a_rhc"), "name": d.get("a_name"),
                      "photo_id": d["assigned_photo_id"]} if d["assigned_employee_id"] else None),
        "has_image": bool(d["staged_path"]),
    }


def batch_detail(conn, user: CurrentUser, batch_id: int) -> dict:
    b = get_batch(conn, batch_id)
    rows = conn.execute(
        "SELECT c.*, e.rhc_id AS s_rhc, e.full_name AS s_name, e.location AS s_loc, e.current_photo_id AS s_photo, "
        "a.rhc_id AS a_rhc, a.full_name AS a_name FROM photo_candidates c "
        "LEFT JOIN employees e ON e.id = c.suggested_employee_id LEFT JOIN employees a ON a.id = c.assigned_employee_id "
        "WHERE c.batch_id = ? ORDER BY c.seq", (batch_id,)).fetchall()
    cands = [_cand_out(dict(r)) for r in rows]
    pdfs = [{"id": r["id"], "name": r["original_name"], "page_count": r["page_count"], "pages": jload(r["pages"], [])}
            for r in conn.execute("SELECT * FROM pdf_docs WHERE batch_id = ?", (batch_id,)).fetchall()]
    counts: dict[str, int] = {}
    for c in cands:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    counts["suggested_certain"] = sum(1 for c in cands if c["status"] == "pending" and c["suggestion"] and c["suggestion"]["certain"])
    counts["suggested_uncertain"] = sum(1 for c in cands if c["status"] == "pending" and c["suggestion"] and not c["suggestion"]["certain"])
    counts["unmatched"] = sum(1 for c in cands if c["status"] == "pending" and not c["suggestion"])
    return {"batch": b, "candidates": cands, "pdfs": pdfs, "counts": counts}


def list_batches(conn, limit: int = 30) -> list[dict]:
    out = []
    for r in conn.execute("SELECT b.*, (SELECT COUNT(*) FROM photo_candidates c WHERE c.batch_id = b.id) AS n, "
                          "(SELECT COUNT(*) FROM photo_candidates c WHERE c.batch_id = b.id AND c.status = 'pending') AS pending "
                          "FROM photo_batches b WHERE b.status != 'closed' ORDER BY b.id DESC LIMIT ?", (limit,)).fetchall():
        out.append(dict(r))
    return out


def close_batch(conn, ctx, user: CurrentUser, batch_id: int) -> None:
    with tx(conn):
        get_batch(conn, batch_id)
        conn.execute("UPDATE photo_batches SET status = 'closed' WHERE id = ?", (batch_id,))
        audit.log(conn, user.email, "photo.batch_closed", "photo_batch", batch_id, None, user.ip)
    d = ctx.paths.photo_staging / str(batch_id)
    if d.exists():
        import shutil

        shutil.rmtree(d, ignore_errors=True)


def candidate_file(conn, ctx, batch_id: int, cid: int, thumb: bool) -> Path:
    c = conn.execute("SELECT staged_path, thumb_path FROM photo_candidates WHERE id = ? AND batch_id = ?", (cid, batch_id)).fetchone()
    if c is None or not c["staged_path"]:
        raise NotFound("No image for this entry.")
    p = ctx.paths.data / (c["thumb_path"] if thumb else c["staged_path"])
    if not p.exists():
        raise NotFound("The staged image has been removed. Upload it again.")
    return p
