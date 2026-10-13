"""Screenshot imports: OCR a picture, work out which employees it shows, update their records.

Everything reuses the spreadsheet import machinery so the result is an ordinary
import batch (purpose ``screenshot``) with per-field change records, which means
it appears in Import history and can be undone the same way.

Identity rules
--------------
1. RHC ID found in the text -> certain.
2. Official email that matches exactly one employee -> certain.
3. Full name (all name words, any order) that matches exactly one employee -> certain,
   unless the name itself is what would change.
4. First and last name only (middle names ignored) matching exactly one employee -> certain.
5. Anything else: shown for review with the candidates found. Nothing is guessed.

Only fields that were actually read from the screenshot are updated. Blank or
unreadable fields never erase existing values. Name and email changes always
need a review decision because they identify the person.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from .. import audit
from ..db import get_setting, jdump, jload, tx, utcnow
from ..errors import HubError, NotFound, ValidationFailed
from ..fields import normalize_rhc_id, registry
from ..permissions import CurrentUser
from . import clients as C
from . import employees as emps
from . import photos as PH

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff", ".gif"}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
LABELS = {
    "full_name": ["name", "employee", "employee name", "full name", "resource", "candidate"],
    "official_email": ["email", "e-mail", "mail", "email id", "official email", "work email"],
    "designation": ["designation", "title", "job title", "role", "position"],
    "client_name": ["client", "customer", "account", "deployed with", "deployed to", "placed with", "placed at", "project", "company"],
    "rhc_id": ["rhc id", "rhc", "emp id", "employee id", "emp code", "employee code", "id"],
    "department": ["department", "dept", "team"],
    "location": ["location", "office", "city"],
}
AUTO_FIELDS = {"designation", "client_name", "department", "location"}
REVIEW_FIELDS = {"full_name", "official_email", "rhc_id"}


# ---------------------------------------------------------------- OCR

def ocr_settings(conn) -> dict:
    return {"tesseract_path": "", "lang": "eng", "psm": 6, **(get_setting(conn, "ocr", {}) or {})}


def find_tesseract(configured: str | None = None) -> str | None:
    cands = []
    if configured:
        cands.append(Path(configured))
    env = os.environ.get("RELAY_TESSERACT")
    if env:
        cands.append(Path(env))
    from ..config import app_root

    cands.append(app_root() / "Tools" / "Tesseract-OCR" / "tesseract.exe")
    cands.append(app_root() / "tools" / "tesseract" / "tesseract.exe")
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            cands.append(Path(base) / "Tesseract-OCR" / "tesseract.exe")
            cands.append(Path(base) / "Programs" / "Tesseract-OCR" / "tesseract.exe")
    for c in cands:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    return shutil.which("tesseract")


def ocr_image(path: Path, settings: dict) -> str:
    exe = find_tesseract(settings.get("tesseract_path"))
    if not exe:
        raise HubError("Tesseract OCR was not found. Install Tesseract (for example from the UB Mannheim installer), then set its "
                       "location under Settings > Posts and OCR. Until then you can type the details by hand in the review screen.",
                       code="ocr_missing")
    # Light preprocessing helps a lot on screenshots: greyscale and 2x upscale.
    tmp = path.with_name(path.stem + "_ocr.png")
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("L")
            if im.width < 1800:
                scale = min(3.0, 1800 / max(1, im.width))
                im = im.resize((int(im.width * scale), int(im.height * scale)), Image.Resampling.LANCZOS)
            im.save(tmp, "PNG")
        cmd = [exe, str(tmp), "stdout", "--psm", str(int(settings.get("psm", 6))), "-l", settings.get("lang") or "eng"]
        flags = 0x08000000 if os.name == "nt" else 0
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, creationflags=flags, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            raise HubError("OCR took longer than two minutes and was stopped.") from None
        if r.returncode != 0:
            raise HubError("Tesseract could not read this image: " + (r.stderr or "").strip()[-300:])
        return r.stdout
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- parsing

def _clean(s: str) -> str:
    s = re.sub(r"[|_~`•●■–—\-]+$", "", s.strip())
    s = re.sub(r"^[|_~`•●■*\-\s]+", "", s)
    return re.sub(r"\s{2,}", " ", s).strip(" .,:;")


def _norm_tokens(s: str) -> list[str]:
    return [t for t in re.sub(r"[^a-z ]+", " ", (s or "").lower()).split() if t]


def _label_match(line: str) -> tuple[str | None, str]:
    """'Designation: Senior Developer' -> ('designation', 'Senior Developer')."""
    m = re.match(r"^\s*([A-Za-z][A-Za-z /\-]{1,30}?)\s*[:\-–—]\s*(.+)$", line)
    if not m:
        return None, line
    label = re.sub(r"[^a-z ]+", " ", m.group(1).lower()).strip()
    for key, names in LABELS.items():
        if label in names:
            return key, m.group(2).strip()
    return None, line


class Index:
    def __init__(self, conn):
        self.employees: list[dict] = []
        self.by_rhc: dict[str, dict] = {}
        self.by_email: dict[str, list[dict]] = {}
        self.by_name: dict[str, list[dict]] = {}
        self.by_first_last: dict[str, list[dict]] = {}
        self.designations: dict[str, str] = {}
        self.clients: dict[str, str] = {}
        for r in conn.execute("SELECT * FROM employees WHERE archived = 0 AND record_scope = 'directory'").fetchall():
            rec = emps.row_record(r)
            self.employees.append(rec)
            self.by_rhc[re.sub(r"[^A-Z0-9]", "", rec["rhc_id"].upper())] = rec
            if rec.get("official_email"):
                self.by_email.setdefault(rec["official_email"].lower(), []).append(rec)
            toks = _norm_tokens(rec["full_name"])
            self.by_name.setdefault(" ".join(sorted(toks)), []).append(rec)
            if len(toks) >= 2:
                self.by_first_last.setdefault(f"{toks[0]} {toks[-1]}", []).append(rec)
            if rec.get("designation"):
                self.designations[_norm_tokens_key(rec["designation"])] = rec["designation"]
            if rec.get("client_name"):
                self.clients[C.norm_client(rec["client_name"])] = rec["client_name"]
        for r in conn.execute("SELECT name FROM clients").fetchall():
            self.clients.setdefault(C.norm_client(r["name"]), r["name"])

    def match_name(self, text: str) -> tuple[list[dict], str]:
        toks = _norm_tokens(text)
        if not toks:
            return [], ""
        key = " ".join(sorted(toks))
        if key in self.by_name:
            return self.by_name[key], "full name"
        if len(toks) >= 2:
            fl = f"{toks[0]} {toks[-1]}"
            if fl in self.by_first_last:
                return self.by_first_last[fl], "first and last name"
            # the screenshot may hold the full name with a middle name the record lacks, or the reverse
            hits = [e for e in self.employees if set(toks) >= set(_norm_tokens(e["full_name"])) and len(_norm_tokens(e["full_name"])) >= 2]
            if hits:
                return hits, "name words"
        return [], ""

    def names_in(self, text: str) -> list[tuple[dict, str]]:
        """Employees whose first and last name both appear in the text."""
        words = set(_norm_tokens(text))
        out = []
        for e in self.employees:
            toks = _norm_tokens(e["full_name"])
            if len(toks) >= 2 and toks[0] in words and toks[-1] in words:
                out.append((e, "first and last name in text"))
        return out


def _norm_tokens_key(s: str) -> str:
    return " ".join(_norm_tokens(s))


def parse_text(text: str, idx: Index) -> list[dict]:
    """Turn OCR text into records: {field: value, ...} plus 'unparsed' lines."""
    lines = [_clean(l) for l in (text or "").splitlines()]
    lines = [l for l in lines if l and len(re.sub(r"[^A-Za-z0-9@]", "", l)) >= 2]
    records: list[dict] = []
    cur: dict[str, Any] = {}

    def start_new():
        nonlocal cur
        if any(k in cur for k in ("full_name", "official_email", "rhc_id")):
            records.append(cur)
        cur = {}

    for line in lines:
        key, value = _label_match(line)
        if key:
            if key == "rhc_id":
                try:
                    value = normalize_rhc_id(value.split()[0]) if value.split() else ""
                except Exception:
                    value = ""
                if not value:
                    continue
            if key in cur and cur[key] != value and key in ("full_name", "official_email", "rhc_id"):
                start_new()
            cur[key] = value
            continue
        # "Client - Employee Name" (the format the old Post Studio screenshots used)
        parts = re.split(r"\s+[-–—]\s+", line, maxsplit=1)
        if len(parts) == 2 and not EMAIL_RE.search(line):
            a, b = parts
            ma, _ = idx.match_name(a)
            mb, _ = idx.match_name(b)
            if mb and not ma:
                if "full_name" in cur:
                    start_new()
                cur["client_name"], cur["full_name"] = a.strip(), b.strip()
                continue
            if ma and not mb:
                if "full_name" in cur:
                    start_new()
                cur["client_name"], cur["full_name"] = b.strip(), a.strip()
                continue
        emails = EMAIL_RE.findall(line)
        rest = line
        if emails:
            if "official_email" in cur and cur["official_email"].lower() != emails[0].lower():
                start_new()
            cur["official_email"] = emails[0].lower()
            rest = EMAIL_RE.sub(" ", line)
            # a table row: words around the email may hold the name and designation
            hits = idx.names_in(rest)
            if len(hits) == 1 and "full_name" not in cur:
                cur["full_name"] = hits[0][0]["full_name"]
                rest = re.sub("|".join(re.escape(t) for t in _norm_tokens(hits[0][0]["full_name"])), " ", rest, flags=re.IGNORECASE)
            if "designation" not in cur and "full_name" in cur:
                leftover = _clean(re.sub(r"\s{2,}", " ", rest))
                dkey = _norm_tokens_key(leftover)
                if dkey in idx.designations:
                    cur["designation"] = idx.designations[dkey]
                elif 2 <= len(leftover) <= 60 and re.search(r"[A-Za-z]", leftover) and not re.search(r"\d{3,}", leftover):
                    cur["designation"] = leftover
            continue
        if re.fullmatch(r"[A-Z0-9][A-Z0-9_\-/.]{2,}", line.upper()) and re.sub(r"[^A-Z0-9]", "", line.upper()) in idx.by_rhc:
            if "rhc_id" in cur:
                start_new()
            cur["rhc_id"] = idx.by_rhc[re.sub(r"[^A-Z0-9]", "", line.upper())]["rhc_id"]
            continue
        hits, how = idx.match_name(line)
        if hits and len(_norm_tokens(line)) <= 5:
            if "full_name" in cur:
                start_new()
            cur["full_name"] = line
            continue
        dk = _norm_tokens_key(line)
        if dk in idx.designations and "designation" not in cur:
            cur["designation"] = idx.designations[dk]
            continue
        ck = C.norm_client(line)
        if ck and ck in idx.clients and "client_name" not in cur:
            cur["client_name"] = idx.clients[ck]
            continue
        if any(k in cur for k in ("full_name", "official_email")) and "designation" not in cur and 2 <= len(dk.split()) <= 5 \
                and not re.search(r"\d{3,}", line) and not re.search(r"\b(active|bench|client|internal|joined|since|date)\b", dk):
            cur["designation_guess"] = line
            continue
        cur.setdefault("unparsed", []).append(line)
    start_new()
    if not records and cur:
        records.append(cur)
    for r in records:
        if "designation" not in r and r.get("designation_guess"):
            r["designation"] = r.pop("designation_guess")
            r["designation_guessed"] = True
        r.pop("designation_guess", None)
    return records


# ---------------------------------------------------------------- batches

def upload(conn, ctx, user: CurrentUser, files: list[tuple[str, bytes]], ocr_text_override: str | None = None) -> dict:
    """Store screenshots, OCR them, analyse rows. Returns the batch detail."""
    if not files:
        raise ValidationFailed("Choose or paste at least one screenshot.")
    limits = get_setting(conn, "limits", {})
    settings = ocr_settings(conn)
    now = utcnow()
    with tx(conn):
        first_name = Path(files[0][0] or "screenshot.png").name
        digest = hashlib.sha256(b"".join(d for _, d in files)).hexdigest()
        folder = ctx.paths.screenshot_staging / uuid.uuid4().hex
        folder.mkdir(parents=True, exist_ok=True)
        bid = conn.execute(
            "INSERT INTO import_batches(filename, file_hash, staged_path, purpose, operator, created_at, status, params) VALUES (?,?,?,?,?,?, 'uploaded', ?)",
            (first_name if len(files) == 1 else f"{first_name} (+{len(files) - 1} more)", digest, str(folder.relative_to(ctx.paths.data)),
             "screenshot", user.email, now, jdump({"files": [f for f, _ in files]}))).lastrowid
        audit.log(conn, user.email, "screenshot.upload", "import_batch", bid, {"files": [f for f, _ in files][:50]}, user.ip)
    texts, problems, saved = [], [], []
    for i, (fname, data) in enumerate(files):
        ext = Path(fname or "").suffix.lower() or ".png"
        if ext not in IMAGE_EXTS:
            problems.append(f"{fname}: not an image file.")
            continue
        try:
            PH.image_info(data, limits)
        except HubError as exc:
            problems.append(f"{fname}: {exc.message}")
            continue
        dest = folder / f"{i:02d}_{uuid.uuid4().hex[:6]}{ext}"
        dest.write_bytes(data)
        saved.append({"name": Path(fname).name, "path": str(dest.relative_to(ctx.paths.data))})
        if ocr_text_override is not None and ctx.testing:
            texts.append(ocr_text_override)
            continue
        try:
            texts.append(ocr_image(dest, settings))
        except HubError as exc:
            problems.append(f"{fname}: {exc.message}")
            texts.append("")
    text = "\n\n".join(texts)
    with tx(conn):
        conn.execute("UPDATE import_batches SET params = ?, status = 'previewed' WHERE id = ?",
                     (jdump({"files": saved, "ocr_text": text, "problems": problems}), bid))
    analyze(conn, ctx, user, bid, text)
    return detail(conn, ctx, user, bid)


def analyze(conn, ctx, user: CurrentUser, bid: int, text: str | None = None, rows_override: list[dict] | None = None) -> None:
    """(Re)build the review rows for a batch from its OCR text or from operator-edited rows."""
    b = _batch(conn, bid)
    params = jload(b["params"], {})
    if text is None:
        text = params.get("ocr_text") or ""
    idx = Index(conn)
    records = rows_override if rows_override is not None else parse_text(text, idx)
    with tx(conn):
        conn.execute("DELETE FROM screenshot_rows WHERE batch_id = ?", (bid,))
        seen: dict[int, int] = {}
        for seq, rec in enumerate(records, start=1):
            row = _analyze_record(conn, user, idx, rec)
            if row.get("employee_id"):
                if row["employee_id"] in seen:
                    row["status"] = "review"
                    row["messages"].append(f"Another row (#{seen[row['employee_id']]}) matched the same employee; check which values are right.")
                seen.setdefault(row["employee_id"], seq)
            conn.execute("INSERT INTO screenshot_rows(batch_id, seq, extracted, employee_id, match_method, match_certain, candidates, changes, status, "
                         "messages, decision) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (bid, seq, jdump(row["extracted"]), row.get("employee_id"), row.get("match_method"), 1 if row.get("match_certain") else 0,
                          jdump(row["candidates"]), jdump(row["changes"]), row["status"], jdump(row["messages"]), jdump(row.get("decision") or {})))


def _analyze_record(conn, user: CurrentUser, idx: Index, rec: dict) -> dict:
    extracted = {k: v for k, v in rec.items() if k in LABELS and v not in (None, "")}
    messages: list[str] = []
    if rec.get("unparsed"):
        messages.append("Text not understood: " + " | ".join(rec["unparsed"][:4]))
    if rec.get("designation_guessed"):
        messages.append(f"'{extracted.get('designation')}' was read as the designation; confirm it.")
    out: dict[str, Any] = {"extracted": extracted, "candidates": [], "changes": [], "messages": messages, "status": "review",
                           "employee_id": None, "match_method": None, "match_certain": False}
    forced = rec.get("_employee_id")
    emp, method, certain = None, None, False
    if forced:
        emp = next((e for e in idx.employees if e["id"] == int(forced)), None)
        if emp:
            method, certain = "chosen by operator", True
    if emp is None and extracted.get("rhc_id"):
        emp = idx.by_rhc.get(re.sub(r"[^A-Z0-9]", "", str(extracted["rhc_id"]).upper()))
        if emp:
            method, certain = "RHC ID", True
    if emp is None and extracted.get("official_email"):
        cands = idx.by_email.get(extracted["official_email"].lower(), [])
        if len(cands) == 1:
            emp, method, certain = cands[0], "official email", True
        elif len(cands) > 1:
            messages.append("That email belongs to more than one employee.")
            out["candidates"] = [_cand(c) for c in cands]
    if emp is None and extracted.get("full_name"):
        cands, how = idx.match_name(extracted["full_name"])
        if len(cands) == 1:
            emp, method, certain = cands[0], how, True
        elif len(cands) > 1:
            messages.append(f"'{extracted['full_name']}' matches {len(cands)} employees; choose the right one.")
            out["candidates"] = [_cand(c) for c in cands]
    if emp is None:
        if not out["candidates"] and extracted.get("full_name"):
            words = set(_norm_tokens(extracted["full_name"]))
            loose = [e for e in idx.employees if words & set(_norm_tokens(e["full_name"])) and (words & set(_norm_tokens(e["full_name"])) != {"the"})]
            loose = [e for e in loose if len(words & set(_norm_tokens(e["full_name"]))) >= max(1, len(words) - 1) or
                     (len(words) == 1 and _norm_tokens(e["full_name"])[0] in words)][:8]
            out["candidates"] = [_cand(c) for c in loose]
        out["status"] = "unmatched" if not out["candidates"] else "review"
        if out["status"] == "unmatched":
            messages.append("No employee matched. Choose one, or skip this row.")
        return out
    if not user.in_scope(emp.get("location")):
        out["status"] = "review"
        messages.append("This employee is outside your location access.")
        return out
    out.update(employee_id=emp["id"], match_method=method, match_certain=certain,
               employee={"id": emp["id"], "rhc_id": emp["rhc_id"], "full_name": emp["full_name"], "version": emp["version"]})
    out["extracted"]["_employee"] = {"id": emp["id"], "rhc_id": emp["rhc_id"], "full_name": emp["full_name"], "version": emp["version"],
                                      "current": {k: emp.get(k) for k in ("full_name", "official_email", "designation", "client_name",
                                                                           "assignment_status", "department", "location")}}
    needs_review = False
    fields = {f.key: f for f in registry(conn)}
    for k, v in extracted.items():
        if k == "rhc_id" or k.startswith("_"):
            continue
        cur = emp.get(k)
        if k == "client_name":
            if C.norm_client(cur) == C.norm_client(v) and cur:
                continue
        elif (cur or "").strip().lower() == str(v).strip().lower():
            continue
        if k in ("full_name", "official_email") and method and method.startswith(("full name", "first and last", "name words")) and k == "full_name":
            # the name is what identified the person; a different spelling is not a change we make silently
            needs_review = True
            out["messages"].append(f"Name read as '{v}' but the record says '{cur}'. Matched by name, so this is only shown, not changed.")
            continue
        change = {"field": k, "label": fields[k].label if k in fields else k, "old": cur, "new": v}
        if k in REVIEW_FIELDS:
            change["review"] = True
            needs_review = True
        out["changes"].append(change)
        if k == "client_name" and emp.get("assignment_status") != "Client":
            out["changes"].append({"field": "assignment_status", "label": "Assignment status", "old": emp.get("assignment_status"), "new": "Client"})
    if not out["changes"]:
        out["status"] = "unchanged"
    elif needs_review or not certain or rec.get("designation_guessed"):
        out["status"] = "review"
    else:
        out["status"] = "auto"
    return out


def _cand(e: dict) -> dict:
    return {"id": e["id"], "rhc_id": e["rhc_id"], "full_name": e["full_name"], "designation": e.get("designation"), "client_name": e.get("client_name"),
            "location": e.get("location")}


def _batch(conn, bid: int) -> dict:
    r = conn.execute("SELECT * FROM import_batches WHERE id = ? AND purpose = 'screenshot'", (bid,)).fetchone()
    if r is None:
        raise NotFound("That screenshot import does not exist.")
    return dict(r)


def detail(conn, ctx, user: CurrentUser, bid: int) -> dict:
    b = _batch(conn, bid)
    params = jload(b["params"], {})
    rows = []
    for r in conn.execute("SELECT * FROM screenshot_rows WHERE batch_id = ? ORDER BY seq", (bid,)).fetchall():
        d = dict(r)
        for k in ("extracted", "candidates", "changes", "messages", "decision", "applied"):
            d[k] = jload(d[k], [] if k in ("candidates", "changes", "messages") else {})
        d["employee"] = d["extracted"].pop("_employee", None)
        rows.append(d)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"batch": {"id": b["id"], "filename": b["filename"], "status": b["status"], "operator": b["operator"], "created_at": b["created_at"],
                      "committed_at": b["committed_at"], "summary": jload(b["summary"], {})},
            "files": params.get("files", []), "ocr_text": params.get("ocr_text", ""), "problems": params.get("problems", []),
            "rows": rows, "counts": counts}


def list_batches(conn, user: CurrentUser, limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT id, filename, operator, created_at, status, committed_at, undone_at, summary FROM import_batches "
                        "WHERE purpose = 'screenshot' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        if not user.is_admin and r["operator"] != user.email:
            continue
        d = dict(r)
        d["summary"] = jload(d["summary"], {})
        out.append(d)
    return out


def file_path(conn, ctx, bid: int, index: int) -> Path:
    b = _batch(conn, bid)
    files = jload(b["params"], {}).get("files", [])
    try:
        rel = files[int(index)]["path"]
    except (IndexError, KeyError, ValueError):
        raise NotFound("Screenshot not found.") from None
    p = ctx.paths.data / rel
    if not p.exists():
        raise NotFound("The screenshot file has been removed.")
    return p


def update_rows(conn, ctx, user: CurrentUser, bid: int, rows: list[dict]) -> dict:
    """Operator edits (corrected text, chosen employee, added/removed rows) are re-analysed."""
    b = _batch(conn, bid)
    if b["status"] not in ("uploaded", "previewed"):
        raise HubError("This import has already been committed or discarded.")
    records = []
    for r in rows:
        rec = {k: str(v).strip() for k, v in (r.get("extracted") or {}).items() if k in LABELS and str(v or "").strip()}
        if r.get("employee_id"):
            rec["_employee_id"] = int(r["employee_id"])
        if r.get("skip"):
            rec["_skip"] = True
        records.append(rec)
    analyze(conn, ctx, user, bid, rows_override=records)
    with tx(conn):
        for seq, rec in enumerate(records, start=1):
            if rec.get("_skip"):
                conn.execute("UPDATE screenshot_rows SET status = 'skipped', decision = ? WHERE batch_id = ? AND seq = ?", (jdump({"skip": True}), bid, seq))
    return detail(conn, ctx, user, bid)


def set_text(conn, ctx, user: CurrentUser, bid: int, text: str) -> dict:
    b = _batch(conn, bid)
    params = jload(b["params"], {})
    params["ocr_text"] = text
    with tx(conn):
        conn.execute("UPDATE import_batches SET params = ? WHERE id = ?", (jdump(params), bid))
    analyze(conn, ctx, user, bid, text)
    return detail(conn, ctx, user, bid)


def commit(conn, ctx, user: CurrentUser, bid: int, approve_rows: list[int] | None = None) -> dict:
    """Apply the auto rows plus any review rows the operator approved. Everything else is left alone."""
    from . import posts as POSTS

    with tx(conn):
        b = _batch(conn, bid)
        if b["status"] != "previewed":
            raise HubError("This import has already been committed or discarded.")
        approve = set(int(x) for x in (approve_rows or []))
        rows = [dict(r) for r in conn.execute("SELECT * FROM screenshot_rows WHERE batch_id = ? ORDER BY seq", (bid,)).fetchall()]
        applied = skipped = unchanged = 0
        posts_created = 0
        results = []
        for r in rows:
            changes = jload(r["changes"], [])
            status = r["status"]
            if status == "unchanged":
                unchanged += 1
                results.append({"seq": r["seq"], "result": "unchanged"})
                continue
            if status != "auto" and r["id"] not in approve:
                skipped += 1
                results.append({"seq": r["seq"], "result": "skipped", "status": status})
                continue
            if not r["employee_id"] or not changes:
                skipped += 1
                results.append({"seq": r["seq"], "result": "skipped", "status": status})
                continue
            emp_before = emps.fetch_record(conn, r["employee_id"])
            ch = {c["field"]: c["new"] for c in changes}
            try:
                res = emps.apply_update(conn, user, r["employee_id"], None, ch, source="screenshot", batch_id=bid)
            except (ValidationFailed, HubError) as exc:
                results.append({"seq": r["seq"], "result": "error", "error": exc.message})
                conn.execute("UPDATE screenshot_rows SET status = 'error', messages = ? WHERE id = ?",
                             (jdump(jload(r["messages"], []) + [exc.message]), r["id"]))
                continue
            for f, old, new, hid in res["changed"]:
                conn.execute("INSERT INTO import_changes(batch_id, employee_id, action, field, old_value, new_value, history_id) VALUES (?,?,?,?,?,?,?)",
                             (bid, r["employee_id"], "update", f, emps.hv(old), emps.hv(new), hid))
            sync = POSTS.sync_assignment(conn, ctx, user.email, emp_before, res["record"], source="screenshot")
            if sync.get("outcome") == "created":
                posts_created += 1
            applied += 1
            conn.execute("UPDATE screenshot_rows SET status = 'applied', applied = ? WHERE id = ?",
                         (jdump({"fields": [c[0] for c in res["changed"]], "post": sync}), r["id"]))
            results.append({"seq": r["seq"], "result": "applied", "fields": [c[0] for c in res["changed"]], "post": sync.get("outcome")})
        summary = {"applied": applied, "skipped": skipped, "unchanged": unchanged, "posts_created": posts_created, "rows": len(rows),
                   "created": 0, "updated": applied, "deactivated": 0}
        conn.execute("UPDATE import_batches SET status = 'committed', committed_at = ?, summary = ?, result_rows = ? WHERE id = ?",
                     (utcnow(), jdump(summary), jdump(results), bid))
        audit.log(conn, user.email, "screenshot.commit", "import_batch", bid, summary, user.ip)
    return {"batch_id": bid, "summary": summary, "results": results}


def discard(conn, user: CurrentUser, bid: int) -> None:
    with tx(conn):
        b = _batch(conn, bid)
        if b["status"] not in ("uploaded", "previewed"):
            raise HubError("Only imports that were not committed can be discarded.")
        conn.execute("UPDATE import_batches SET status = 'discarded' WHERE id = ?", (bid,))
        audit.log(conn, user.email, "screenshot.discard", "import_batch", bid, None, user.ip)
