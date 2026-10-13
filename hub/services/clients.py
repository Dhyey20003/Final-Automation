"""Client records and client logos.

Clients get a permanent internal ID. Employee records keep their free-text
``client_name`` (nothing in the existing data changes); a client row is linked
by the normalised name. Logos are stored like photos: every version is kept,
the current one is replaced only after the new file is written and validated.

Storage layout (inside the data folder):
    images/clients/<client id>/<client id>_l<logo id>_v<version>.<ext>
    thumbnails/clients/<client id>/<logo id>.jpg
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from .. import audit
from ..db import utcnow
from ..errors import HubError, NotFound, ValidationFailed
from . import photos as PH


def norm_client(name: str | None) -> str:
    """Ignore case, accents, spaces and punctuation when comparing client names."""
    text = unicodedata.normalize("NFKD", str(name or "")).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\b(pvt|private|ltd|limited|llc|inc|incorporated|corp|corporation|co|company|llp|plc|gmbh)\b\.?", " ", text)
    return "".join(ch for ch in text if ch.isalnum())


def similar(a: str | None, b: str | None) -> float:
    na, nb = norm_client(a), norm_client(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.9
    return SequenceMatcher(None, na, nb).ratio()


def row(conn: sqlite3.Connection, client_id: int) -> dict:
    r = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if r is None:
        raise NotFound("That client does not exist.")
    return dict(r)


def find_by_name(conn: sqlite3.Connection, name: str | None) -> dict | None:
    key = norm_client(name)
    if not key:
        return None
    r = conn.execute("SELECT * FROM clients WHERE norm_name = ?", (key,)).fetchone()
    return dict(r) if r else None


def get_or_create(conn: sqlite3.Connection, name: str, actor: str = "system") -> dict:
    """Caller owns the transaction."""
    name = (name or "").strip()
    if not name:
        raise ValidationFailed("A client name is required.")
    existing = find_by_name(conn, name)
    if existing:
        return existing
    now = utcnow()
    cid = conn.execute(
        "INSERT INTO clients(name, norm_name, display_name, created_at, created_by, updated_at, updated_by) VALUES (?,?,?,?,?,?,?)",
        (name, norm_client(name), name, now, actor, now, actor)).lastrowid
    audit.log(conn, actor, "client.create", "client", cid, {"name": name})
    return row(conn, cid)


def rename(conn: sqlite3.Connection, actor: str, client_id: int, new_name: str) -> dict:
    """Rename a client (spelling correction). Merges into an existing client with the same normalised name."""
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValidationFailed("Enter the client name.")
    cur = row(conn, client_id)
    other = find_by_name(conn, new_name)
    now = utcnow()
    if other and other["id"] != client_id:
        # Same client spelled differently already exists: move references and keep the other record.
        conn.execute("UPDATE post_projects SET client_id = ? WHERE client_id = ?", (other["id"], client_id))
        conn.execute("UPDATE post_requests SET client_id = ? WHERE client_id = ?", (other["id"], client_id))
        if not other.get("current_logo_id") and cur.get("current_logo_id"):
            conn.execute("UPDATE client_logos SET client_id = ? WHERE client_id = ?", (other["id"], client_id))
            conn.execute("UPDATE clients SET current_logo_id = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                         (cur["current_logo_id"], now, actor, other["id"]))
        conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))
        audit.log(conn, actor, "client.merge", "client", other["id"], {"from": cur["name"], "into": other["name"]})
        return row(conn, other["id"])
    conn.execute("UPDATE clients SET name = ?, norm_name = ?, display_name = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                 (new_name, norm_client(new_name), new_name, now, actor, client_id))
    audit.log(conn, actor, "client.rename", "client", client_id, {"from": cur["name"], "to": new_name})
    return row(conn, client_id)


# ---------------------------------------------------------------- logos

def logo_row(conn, logo_id: int) -> dict:
    r = conn.execute("SELECT * FROM client_logos WHERE id = ?", (logo_id,)).fetchone()
    if r is None:
        raise NotFound("Logo not found.")
    return dict(r)


def validate_logo(data: bytes, limits: dict) -> tuple[str, int, int]:
    """Explain clearly why an attachment cannot be a logo."""
    if not data:
        raise HubError("The file is empty.")
    fmt, w, h = PH.image_info(data, limits)
    if w < 60 and h < 60:
        raise HubError(f"The image is only {w} x {h} pixels, too small to be a logo (it looks like an icon or a tracking pixel).")
    return fmt, w, h


def store_logo(conn: sqlite3.Connection, ctx, actor: str, client_id: int, data: bytes, original_name: str | None,
               source: str, source_ref: str | None = None, limits: dict | None = None, allow_same: bool = False) -> int:
    """Store a new logo version and make it current. Caller owns the transaction.

    The previous logo stays current until the new file is fully written and validated.
    """
    from ..db import get_setting

    limits = limits if limits is not None else get_setting(conn, "limits", {})
    fmt, w, h = validate_logo(data, limits)
    client = row(conn, client_id)
    digest = hashlib.sha256(data).hexdigest()
    if client.get("current_logo_id") and not allow_same:
        cur = conn.execute("SELECT sha256 FROM client_logos WHERE id = ?", (client["current_logo_id"],)).fetchone()
        if cur and cur["sha256"] == digest:
            raise HubError(f"This image is already the current logo for {client['name']}.", code="duplicate_current")
    ext = PH.IMAGE_FORMATS[fmt]
    vno = conn.execute("SELECT COALESCE(MAX(version_no), 0) FROM client_logos WHERE client_id = ?", (client_id,)).fetchone()[0] + 1
    now = utcnow()
    lid = conn.execute(
        "INSERT INTO client_logos(client_id, version_no, stored_path, sha256, bytes, width, height, image_format, source, source_ref, "
        "uploaded_by, uploaded_at, is_current, original_filename) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
        (client_id, vno, "", digest, len(data), w, h, fmt, source, source_ref, actor, now, original_name)).lastrowid
    rel = Path("images") / "clients" / str(client_id) / f"{client_id}_l{lid}_v{vno}{ext}"
    trel = Path("thumbnails") / "clients" / str(client_id) / f"{lid}.jpg"
    PH._atomic_write(ctx.paths.data / rel, data)
    try:
        PH.make_thumbnail(data, ctx.paths.data / trel)
    except Exception:
        trel = rel  # a logo with an odd colour mode still works; show the original as its preview
    conn.execute("UPDATE client_logos SET is_current = 0, superseded_at = ? WHERE client_id = ? AND is_current = 1", (now, client_id))
    conn.execute("UPDATE client_logos SET stored_path = ?, thumb_path = ?, is_current = 1 WHERE id = ?", (rel.as_posix(), trel.as_posix(), lid))
    conn.execute("UPDATE clients SET current_logo_id = ?, updated_at = ?, updated_by = ? WHERE id = ?", (lid, now, actor, client_id))
    audit.log(conn, actor, "client.logo_stored", "client", client_id,
              {"logo_id": lid, "version": vno, "original_filename": original_name, "source": source, "sha256": digest})
    return lid


def logo_path(conn, ctx, logo_id: int, thumb: bool = False) -> Path:
    lg = logo_row(conn, logo_id)
    rel = (lg["thumb_path"] if thumb else None) or lg["stored_path"]
    p = ctx.paths.data / rel
    if not p.exists():
        raise NotFound("The logo file is missing from the data folder.")
    return p


def logo_versions(conn, client_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, version_no, original_filename, sha256, bytes, width, height, image_format, source, source_ref, uploaded_by, "
        "uploaded_at, is_current, superseded_at FROM client_logos WHERE client_id = ? ORDER BY version_no DESC", (client_id,))]


def set_current_logo(conn, actor: str, client_id: int, logo_id: int) -> None:
    lg = logo_row(conn, logo_id)
    if lg["client_id"] != client_id:
        raise ValidationFailed("That logo belongs to another client.")
    now = utcnow()
    conn.execute("UPDATE client_logos SET is_current = 0, superseded_at = COALESCE(superseded_at, ?) WHERE client_id = ? AND is_current = 1",
                 (now, client_id))
    conn.execute("UPDATE client_logos SET is_current = 1, superseded_at = NULL WHERE id = ?", (logo_id,))
    conn.execute("UPDATE clients SET current_logo_id = ?, updated_at = ?, updated_by = ? WHERE id = ?", (logo_id, now, actor, client_id))
    audit.log(conn, actor, "client.logo_current", "client", client_id, {"logo_id": logo_id})


def list_clients(conn, q: str = "") -> list[dict]:
    like = f"%{(q or '').strip()}%"
    rows = conn.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM post_projects p WHERE p.client_id = c.id) AS projects, "
        "(SELECT COUNT(*) FROM employees e WHERE e.client_name IS NOT NULL AND e.assignment_status = 'Client' "
        " AND e.archived = 0 AND e.record_scope = 'directory' AND lower(e.client_name) = lower(c.name)) AS employees "
        "FROM clients c WHERE c.name LIKE ? ORDER BY c.name", (like,)).fetchall()
    return [dict(r) for r in rows]


def sync_from_employees(conn, actor: str = "system") -> int:
    """Create client rows for every distinct client name used by employees (no posts are created)."""
    n = 0
    for r in conn.execute("SELECT DISTINCT client_name FROM employees WHERE client_name IS NOT NULL AND TRIM(client_name) <> ''").fetchall():
        if find_by_name(conn, r["client_name"]) is None:
            get_or_create(conn, r["client_name"], actor)
            n += 1
    return n
