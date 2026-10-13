"""Bring the old Post Studio data into the hub: logos, headshots, log.xlsx rows, publishing ticks.

Nothing in the old folder is changed. Matches are by exact name; anything that
matches several employees or nobody is reported for review instead of guessed.
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

from openpyxl import load_workbook

from .. import audit
from ..db import tx, utcnow
from ..errors import HubError, ValidationFailed
from ..permissions import CurrentUser
from . import clients as C
from . import employees as emps
from . import photos as PH
from . import posts as POSTS

IMG = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}


def _norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def _find(root: Path, *rels: str) -> Path | None:
    for rel in rels:
        p = root / rel
        if p.exists():
            return p
    return None


def locate(folder: str) -> dict:
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise ValidationFailed("That folder does not exist on this computer.")
    return {
        "root": root,
        "log": _find(root, "Data/Posts/log.xlsx", "log.xlsx", "Raw_Files/Posts/log.xlsx"),
        "state": _find(root, "Data/Posts/studio_state.json", "studio_state.json"),
        "logos": _find(root, "Raw_Files/Posts/saved_logos", "saved_logos"),
        "profiles": _find(root, "Raw_Files/saved_profile", "saved_profile"),
        "posts": _find(root, "Output/Posts", "generated_posts"),
        "replies": _find(root, "Raw_Files/Posts/processed_replies.json", "processed_replies.json"),
    }


def _read_log(path: Path | None) -> list[dict]:
    if not path:
        return []
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(c or "").strip().lower() for c in rows[0]]
    out = []
    for r in rows[1:]:
        d = {headers[i]: (str(v).strip() if v is not None else "") for i, v in enumerate(r) if i < len(headers)}
        if d.get("employee") or d.get("name"):
            d["employee"] = d.get("employee") or d.get("name")
            out.append(d)
    return out


def _name_index(conn) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT * FROM employees WHERE archived = 0").fetchall():
        rec = emps.row_record(r)
        idx.setdefault(_norm(rec["full_name"]), []).append(rec)
    return idx


def preview(conn, ctx, user: CurrentUser, folder: str) -> dict:
    loc = locate(folder)
    idx = _name_index(conn)
    plan = {"folder": str(loc["root"]), "found": {k: str(v) if v else None for k, v in loc.items() if k != "root"},
            "logos": [], "profiles": [], "posts": [], "review": []}
    if loc["logos"]:
        for f in sorted(loc["logos"].iterdir()):
            if f.suffix.lower() in IMG and f.is_file():
                existing = C.find_by_name(conn, f.stem)
                plan["logos"].append({"file": f.name, "client": f.stem, "exists": bool(existing), "has_logo": bool(existing and existing.get("current_logo_id"))})
    if loc["profiles"]:
        for f in sorted(loc["profiles"].iterdir()):
            if f.suffix.lower() in IMG and f.is_file():
                cands = idx.get(_norm(f.stem), [])
                item = {"file": f.name, "name": f.stem, "employee_id": cands[0]["id"] if len(cands) == 1 else None,
                        "employee": f"{cands[0]['full_name']} ({cands[0]['rhc_id']})" if len(cands) == 1 else None,
                        "has_photo": bool(cands[0].get("current_photo_id")) if len(cands) == 1 else None,
                        "candidates": [{"id": c["id"], "label": f"{c['full_name']} ({c['rhc_id']})"} for c in cands]}
                if len(cands) != 1:
                    plan["review"].append({"kind": "profile", "file": f.name, "reason": "No employee with exactly this name." if not cands else "Several employees have this name.",
                                           "candidates": item["candidates"]})
                plan["profiles"].append(item)
    state = {}
    if loc["state"]:
        try:
            state = json.loads(loc["state"].read_text(encoding="utf-8")).get("posts", {})
        except (OSError, ValueError):
            state = {}
    latest: dict[str, dict] = {}
    for r in _read_log(loc["log"]):
        key = _norm(r.get("employee"))
        if key:
            latest[key] = r
    keys = set(latest) | set(state)
    for key in sorted(keys):
        st = state.get(key, {})
        lg = latest.get(key, {})
        name = st.get("name") or lg.get("employee") or key
        client = st.get("client") or lg.get("client") or ""
        png = None
        if loc["posts"]:
            for f in loc["posts"].glob("*.png"):
                if _norm(f.stem) == key:
                    png = f
                    break
        cands = idx.get(key, [])
        item = {"name": name, "client": client, "designation": lg.get("designation") or "", "uploaded": bool(st.get("uploaded")),
                "pronoun": st.get("pronoun") or "", "png": png.name if png else None,
                "employee_id": cands[0]["id"] if len(cands) == 1 else None,
                "employee": f"{cands[0]['full_name']} ({cands[0]['rhc_id']})" if len(cands) == 1 else None,
                "candidates": [{"id": c["id"], "label": f"{c['full_name']} ({c['rhc_id']})"} for c in cands],
                "exists": bool(cands and len(cands) == 1 and client and POSTS.find_project(conn, cands[0]["id"], client, None))}
        if not client:
            item["skip_reason"] = "No client recorded for this post."
        if len(cands) != 1:
            plan["review"].append({"kind": "post", "file": name, "reason": "No employee with exactly this name." if not cands else "Several employees have this name.",
                                   "candidates": item["candidates"]})
        plan["posts"].append(item)
    return plan


def commit(conn, ctx, user: CurrentUser, folder: str, decisions: dict | None = None, mark_published: bool = True) -> dict:
    """decisions: {"profiles": {file: employee_id}, "posts": {name: employee_id}} for the uncertain ones."""
    decisions = decisions or {}
    plan = preview(conn, ctx, user, folder)
    loc = locate(folder)
    out = {"logos": 0, "profiles": 0, "posts": 0, "skipped": [], "errors": []}
    with tx(conn):
        for lg in plan["logos"]:
            try:
                client = C.get_or_create(conn, lg["client"], user.email)
                data = (loc["logos"] / lg["file"]).read_bytes()
                C.store_logo(conn, ctx, user.email, client["id"], data, lg["file"], "legacy import", str(loc["logos"] / lg["file"]), allow_same=True)
                out["logos"] += 1
            except HubError as exc:
                if exc.code == "duplicate_current":
                    out["skipped"].append(f"Logo {lg['file']}: already stored.")
                else:
                    out["errors"].append(f"Logo {lg['file']}: {exc.message}")
        for pr in plan["profiles"]:
            eid = pr.get("employee_id") or (decisions.get("profiles") or {}).get(pr["file"])
            if not eid:
                out["skipped"].append(f"Photo {pr['file']}: no single matching employee.")
                continue
            try:
                data = (loc["profiles"] / pr["file"]).read_bytes()
                PH.store_photo(conn, ctx, user, int(eid), data, pr["file"], "legacy import", str(loc["profiles"] / pr["file"]))
                out["profiles"] += 1
            except HubError as exc:
                if exc.code == "duplicate_current":
                    out["skipped"].append(f"Photo {pr['file']}: already the current photo.")
                else:
                    out["errors"].append(f"Photo {pr['file']}: {exc.message}")
        for po in plan["posts"]:
            eid = po.get("employee_id") or (decisions.get("posts") or {}).get(po["name"])
            if not eid or not po.get("client"):
                out["skipped"].append(f"Post {po['name']}: " + (po.get("skip_reason") or "no single matching employee."))
                continue
            try:
                proj, outcome = POSTS.ensure_project(conn, ctx, user.email, int(eid), po["client"], None, "legacy")
                if outcome != "created":
                    out["skipped"].append(f"Post {po['name']}: a project for this deployment already exists.")
                    continue
                changes = {}
                if po.get("designation"):
                    changes["designation"] = po["designation"]
                if po.get("pronoun") in POSTS.PRONOUNS:
                    changes["pronoun"] = po["pronoun"]
                if changes:
                    POSTS.update_project(conn, ctx, user, proj["id"], None, changes, note="legacy data", render=True)
                if po.get("png"):
                    src = loc["posts"] / po["png"]
                    dest = POSTS._export_dir(ctx, proj) / f"legacy_{po['png']}"
                    PH._atomic_write(dest, src.read_bytes())
                    rel = dest.relative_to(ctx.paths.data).as_posix()
                    conn.execute("INSERT INTO post_versions(project_id, version_no, kind, data, render_path, note, created_at, created_by) VALUES (?,?,?,?,?,?,?,?)",
                                 (proj["id"], POSTS._next_version(conn, proj["id"]), "legacy", "{}", rel, f"Imported from {src}", utcnow(), user.email))
                if po.get("uploaded") and mark_published:
                    proj = POSTS.project_row(conn, proj["id"])
                    conn.execute("UPDATE post_projects SET status = 'published', published_at = ?, published_by = ?, published_version = ? WHERE id = ?",
                                 (utcnow(), user.email + " (legacy Uploaded tick)", proj["draft_version"], proj["id"]))
                out["posts"] += 1
            except HubError as exc:
                out["errors"].append(f"Post {po['name']}: {exc.message}")
        audit.log(conn, user.email, "post.legacy_import", "legacy", None, {"folder": folder, **{k: v for k, v in out.items() if k != 'skipped'}}, user.ip)
    return out
