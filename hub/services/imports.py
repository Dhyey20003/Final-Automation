"""Spreadsheet imports: mapping, preview, atomic commit, error reports and safe undo.

Matching rules
--------------
1. RHC ID (exact, case-insensitive, leading zeros kept).
2. Only when explicitly enabled: an exact, unique official email.
3. Name matches are never applied automatically. They are shown as
   suggestions, and the operator may explicitly link a row to a suggested
   employee.

Blank cells never erase existing values unless the operator selects the
field under "Blank cells clear these fields".
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .. import audit
from ..db import get_setting, jdump, jload, tx, utcnow
from ..errors import Conflict, Forbidden, HubError, NotFound, ValidationFailed
from ..fields import CORE_COLUMNS, FieldDef, FieldError, normalize, normalize_rhc_id, registry
from ..permissions import CurrentUser
from . import employees as emps
from .sheets import check_extension, display, headers_from, list_sheets, read_raw_rows, write_xlsx

PURPOSES = {
    "historical": "Historical employee data",
    "roster": "Complete current employee roster",
    "partial": "Partial employee update",
    "designation": "Designation update",
    "selected": "Selected field update",
}

SYNONYMS = {
    "rhc_id": ["rhc id", "rhc code", "rhc no", "rhc number", "emp code", "employee code", "employee id", "emp id",
               "emp no", "employee no", "employee number", "code", "id", "rhcid", "staff id"],
    "full_name": ["name", "full name", "employee name", "emp name", "staff name", "employee full name"],
    "designation": ["designation", "title", "job title", "position", "role"],
    "department": ["department", "dept", "team", "function"],
    "official_email": ["email", "official email", "email id", "work email", "office email", "official email id",
                       "company email", "email address"],
    "location": ["location", "office", "city", "branch", "office location", "work location"],
    "date_of_joining": ["doj", "date of joining", "joining date", "join date", "joined on", "start date"],
    "date_of_birth": ["dob", "date of birth", "birth date", "birthday"],
    "employment_status": ["status", "employment status", "emp status", "employee status"],
    "last_working_date": ["lwd", "last working date", "last working day", "exit date", "relieving date",
                          "date of leaving", "leaving date"],
    "assignment_status": ["assignment status", "assignment", "deployment status", "billing status"],
    "client_name": ["client", "client name", "customer", "account"],
    "assignment_start": ["assignment start", "assignment start date", "client start date", "deployment date"],
    "assignment_end": ["assignment end", "assignment end date", "client end date"],
    "bench_start": ["bench start", "bench start date", "bench from", "on bench since"],
    "bench_end": ["bench end", "bench end date", "bench to"],
    "notes": ["notes", "remarks", "comments", "comment"],
}

DEFAULT_ENRICH = ["date_of_joining", "date_of_birth", "official_email", "department", "designation"]
NEVER_CLEAR = {"rhc_id", "full_name", "employment_status", "location"}


def norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(h).lower()).strip()


def norm_name(n: str | None) -> str:
    if not n:
        return ""
    return " ".join(sorted(re.sub(r"[^a-z ]+", " ", n.lower()).split()))


def suggest_mapping(headers: list[str], fields: list[FieldDef]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for fd in fields:
        if not fd.importable:
            continue
        lookup[norm_header(fd.label)] = fd.key
        lookup[norm_header(fd.key)] = fd.key
        for s in SYNONYMS.get(fd.key, []):
            lookup.setdefault(norm_header(s), fd.key)
    out, used = {}, set()
    for i, h in enumerate(headers):
        key = lookup.get(norm_header(h))
        if key and key not in used:
            out[str(i)] = key
            used.add(key)
    return out


# ---------------------------------------------------------------- upload

def upload(conn: sqlite3.Connection, ctx, user: CurrentUser, filename: str, data: bytes) -> dict:
    ext = check_extension(filename)
    limits = get_setting(conn, "limits", {})
    if len(data) > int(limits.get("max_upload_mb", 300)) * 1024 * 1024:
        raise HubError("The file is larger than the upload limit set in Settings.")
    if not data:
        raise HubError("The file is empty.")
    digest = hashlib.sha256(data).hexdigest()
    dest = ctx.paths.sheet_staging / f"{uuid.uuid4().hex}{ext}"
    dest.write_bytes(data)
    sheets = list_sheets(dest)
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO import_batches(filename, file_hash, staged_path, operator, created_at, status) VALUES (?,?,?,?,?, 'uploaded')",
            (Path(filename).name, digest, str(dest.relative_to(ctx.paths.data)), user.email, utcnow()),
        )
        batch_id = cur.lastrowid
        audit.log(conn, user.email, "import.upload", "import_batch", batch_id, {"filename": filename, "sha256": digest}, user.ip)
    return {"batch_id": batch_id, "sheets": sheets, "file_hash": digest, "previous_imports": previous_imports(conn, digest, batch_id)}


def previous_imports(conn: sqlite3.Connection, digest: str, exclude: int | None = None) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, filename, purpose, operator, committed_at, status FROM import_batches "
        "WHERE file_hash = ? AND status IN ('committed','partially_undone','undone') AND id IS NOT ? ORDER BY id DESC",
        (digest, exclude)).fetchall()]


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict:
    row = conn.execute("SELECT * FROM import_batches WHERE id = ?", (batch_id,)).fetchone()
    if row is None:
        raise NotFound("That import does not exist.")
    return dict(row)


def _rows(ctx, conn, batch: dict, sheet: str | None) -> dict:
    limits = get_setting(conn, "limits", {})
    path = ctx.paths.data / batch["staged_path"]
    if not path.exists():
        raise HubError("The uploaded file is no longer available. Upload it again.")
    return read_raw_rows(path, sheet, int(limits.get("max_import_rows", 50000)))


def sheet_info(conn, ctx, user, batch_id: int, sheet: str | None, header_row: int, template_id: int | None) -> dict:
    batch = get_batch(conn, batch_id)
    path = ctx.paths.data / batch["staged_path"]
    sheets = list_sheets(path)
    data = _rows(ctx, conn, batch, sheet)
    rows = data["rows"]
    preview = [[display(v) for v in r] for r in rows[:15]]
    headers = headers_from(rows, header_row) if rows else []
    fields = [f for f in registry(conn) if f.importable]
    mapping = suggest_mapping(headers, fields)
    template = None
    if template_id:
        template = get_template(conn, template_id)
        mapping = apply_template_mapping(template, headers)
    sample = [[display(v) for v in r] for r in rows[header_row: header_row + 5]]
    formulas_in_data = [f for f in data["formulas"] if f[0] > header_row]
    return {
        "batch_id": batch_id, "filename": batch["filename"], "sheets": sheets, "sheet": data["sheet"],
        "header_row": header_row, "raw_preview": preview, "headers": headers, "sample_rows": sample,
        "data_row_count": max(0, sum(1 for r in rows[header_row:] if any(v not in (None, "") for v in r))),
        "suggested_mapping": mapping, "formula_cells": len(formulas_in_data), "template": template,
        "fields": [f.public() for f in fields if user.can_edit_field(f.key)],
        "previous_imports": previous_imports(conn, batch["file_hash"], batch_id),
    }


# ---------------------------------------------------------------- templates

def get_template(conn, template_id: int) -> dict:
    r = conn.execute("SELECT * FROM import_templates WHERE id = ?", (template_id,)).fetchone()
    if r is None:
        raise NotFound("Mapping template not found.")
    d = dict(r)
    d["mapping"] = jload(d["mapping"], {})
    d["options"] = jload(d["options"], {})
    return d


def list_templates(conn) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM import_templates ORDER BY name").fetchall():
        d = dict(r)
        d["mapping"] = jload(d["mapping"], {})
        d["options"] = jload(d["options"], {})
        out.append(d)
    return out


def apply_template_mapping(template: dict, headers: list[str]) -> dict[str, str]:
    by_header = template["mapping"]
    out = {}
    for i, h in enumerate(headers):
        key = by_header.get(norm_header(h))
        if key:
            out[str(i)] = key
    return out


def save_template(conn, user: CurrentUser, name: str, purpose: str, sheet: str | None, header_row: int,
                  headers: list[str], mapping: dict[str, str], options: dict, template_id: int | None = None) -> int:
    name = (name or "").strip()
    if not name:
        raise ValidationFailed("Enter a name for the mapping template.")
    by_header = {norm_header(headers[int(i)]): k for i, k in mapping.items() if k and k != "__ignore__" and int(i) < len(headers)}
    opts = {k: v for k, v in options.items() if k in ("email_fallback", "scope", "blank_clears", "selected_fields",
                                                      "create_unmatched", "enrich_fields")}
    now = utcnow()
    existing = conn.execute("SELECT id FROM import_templates WHERE name = ?", (name,)).fetchone()
    if existing and existing["id"] != template_id and template_id is not None:
        raise ValidationFailed("Another template already has that name.")
    if existing:
        conn.execute("UPDATE import_templates SET purpose=?, sheet_name=?, header_row=?, mapping=?, options=?, updated_at=? WHERE id=?",
                     (purpose, sheet, header_row, jdump(by_header), jdump(opts), now, existing["id"]))
        tid = existing["id"]
    else:
        tid = conn.execute(
            "INSERT INTO import_templates(name, purpose, sheet_name, header_row, mapping, options, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (name, purpose, sheet, header_row, jdump(by_header), jdump(opts), user.email, now, now)).lastrowid
    audit.log(conn, user.email, "import.template_saved", "import_template", tid, {"name": name}, user.ip)
    return tid


# ---------------------------------------------------------------- analysis

class _Idx:
    def __init__(self, conn):
        self.by_id: dict[int, dict] = {}
        self.by_rhc: dict[str, dict] = {}
        self.by_email: dict[str, list[dict]] = {}
        self.by_name: dict[str, list[dict]] = {}
        for r in conn.execute("SELECT * FROM employees").fetchall():
            rec = emps.row_record(r)
            self.by_id[rec["id"]] = rec
            self.by_rhc[rec["rhc_id"].upper()] = rec
            if rec.get("official_email"):
                self.by_email.setdefault(rec["official_email"].lower(), []).append(rec)
            self.by_name.setdefault(norm_name(rec["full_name"]), []).append(rec)


def _label(rec: dict) -> str:
    return f"{rec['full_name']} ({rec['rhc_id']})"


def analyze(conn: sqlite3.Connection, ctx, user: CurrentUser, batch: dict, params: dict) -> dict:
    purpose = params.get("purpose")
    if purpose not in PURPOSES:
        raise ValidationFailed("Choose what this import is for.")
    opts = params.get("options") or {}
    header_row = int(params.get("header_row") or 1)
    data = _rows(ctx, conn, batch, params.get("sheet"))
    rows = data["rows"]
    headers = headers_from(rows, header_row) if rows else []
    all_fields = registry(conn)
    fmap = {f.key: f for f in all_fields}
    mapping = {int(i): k for i, k in (params.get("mapping") or {}).items() if k and k != "__ignore__"}
    locations = emps.locations(conn)
    date_order = get_setting(conn, "date_order", "DMY")
    loc_aliases = get_setting(conn, "location_aliases", {}) or {}
    email_fallback = bool(opts.get("email_fallback"))
    scope = [s for s in (opts.get("scope") or []) if s in locations]
    blank_clears = set(opts.get("blank_clears") or [])
    selected = set(opts.get("selected_fields") or [])
    enrich = set(opts.get("enrich_fields") if opts.get("enrich_fields") is not None else DEFAULT_ENRICH)
    create_unmatched = bool(opts.get("create_unmatched"))
    resolutions: dict[str, str] = {str(k): v for k, v in (params.get("resolutions") or {}).items()}
    today = emps.tz_today(conn)

    # ---- mapping checks
    mapping_errors = []
    seen = {}
    for i, k in mapping.items():
        if i >= len(headers):
            mapping_errors.append(f"Column {i + 1} does not exist in the sheet.")
            continue
        fd = fmap.get(k)
        if fd is None or not fd.importable:
            mapping_errors.append(f"'{headers[i]}' is mapped to a field that cannot be imported.")
            continue
        if k in seen:
            mapping_errors.append(f"{fd.label} is mapped from two columns ('{headers[seen[k]]}' and '{headers[i]}').")
        seen[k] = i
    mapped = set(seen)
    match_keys = {"rhc_id", "official_email"}
    if purpose == "designation":
        if "designation" not in mapped:
            mapping_errors.append("Map a column to Designation.")
        updatable = {"designation"}
    elif purpose == "selected":
        if not selected:
            mapping_errors.append("Choose which fields this import may update.")
        missing_sel = [fmap[k].label for k in selected if k in fmap and k not in mapped]
        if missing_sel:
            mapping_errors.append("These selected fields are not mapped: " + ", ".join(missing_sel))
        updatable = selected & mapped
    else:
        updatable = mapped
    if "rhc_id" not in mapped and not ("official_email" in mapped and email_fallback and purpose not in ("historical", "roster")):
        mapping_errors.append("Map a column to RHC ID (or map Official email and enable email matching).")
    if purpose in ("historical", "roster") and "full_name" not in mapped:
        mapping_errors.append("Map a column to Full name so new records can be created.")
    if purpose == "roster" and not scope:
        mapping_errors.append("Declare which locations this roster covers (for example Ahmedabad only, or both).")
    for k in updatable | (mapped & match_keys):
        if k in fmap and not user.can_edit_field(k):
            mapping_errors.append(f"You do not have permission to import {fmap[k].label}.")
    if scope and user.locations and not set(scope) <= set(user.locations):
        mapping_errors.append("The declared location scope includes locations outside your access.")
    for k in blank_clears:
        if k in NEVER_CLEAR:
            mapping_errors.append(f"{fmap[k].label if k in fmap else k} can never be cleared by an import.")
    if mapping_errors:
        return {"mapping_errors": mapping_errors, "rows": [], "summary": {}, "deactivations": [], "blocking": 1,
                "plan_hash": None, "headers": headers}

    idx = _Idx(conn)
    out_rows: list[dict] = []
    parsed: list[dict] = []
    formula_cells = {(r, c) for r, c in data["formulas"]}

    for offset, raw in enumerate(rows[header_row:], start=header_row + 1):
        if not any(v not in (None, "") for v in raw):
            continue
        row = {"row_no": offset, "raw": [display(v) for v in raw], "errors": [], "warnings": [], "values": {},
               "blank": set(), "changes": [], "suggestions": []}
        for i, k in mapping.items():
            v = raw[i] if i < len(raw) else None
            if (offset, i) in formula_cells:
                row["warnings"].append(f"'{headers[i]}' contains a formula; its stored value was used and the formula was not run.")
            try:
                nv = normalize(fmap[k], v, date_order=date_order, location_aliases=loc_aliases)
            except FieldError as exc:
                row["errors"].append(f"{fmap[k].label}: {exc}")
                continue
            if nv is None:
                row["blank"].add(k)
            else:
                row["values"][k] = nv
        row["rhc_id"] = row["values"].get("rhc_id")
        row["name"] = row["values"].get("full_name")
        parsed.append(row)

    # ---- duplicates within the file (ignoring rows the operator chose to skip)
    active_rows = [r for r in parsed if resolutions.get(str(r["row_no"])) != "skip"]
    counts: dict[str, list[int]] = {}
    email_counts: dict[str, list[int]] = {}
    for r in active_rows:
        if r["rhc_id"]:
            counts.setdefault(r["rhc_id"].upper(), []).append(r["row_no"])
        em = r["values"].get("official_email")
        if em:
            email_counts.setdefault(em, []).append(r["row_no"])

    matched_ids: set[int] = set()
    for r in parsed:
        res = resolutions.get(str(r["row_no"]))
        if res == "skip":
            r["status"] = "skipped"
            out_rows.append(r)
            continue
        if r["rhc_id"] and len(counts.get(r["rhc_id"].upper(), [])) > 1:
            others = [n for n in counts[r["rhc_id"].upper()] if n != r["row_no"]]
            r["errors"].append(f"RHC ID {r['rhc_id']} appears more than once in the file (also row {', '.join(map(str, others))}).")
            r["duplicate"] = True
        em = r["values"].get("official_email")
        if em and len(email_counts.get(em, [])) > 1:
            r["errors"].append(f"Email {em} appears more than once in the file.")
            r["duplicate"] = True

        # ---- match
        emp = None
        method = None
        if r["rhc_id"]:
            emp = idx.by_rhc.get(r["rhc_id"].upper())
            method = "RHC ID" if emp else None
            if emp is None and email_fallback and em:
                cands = idx.by_email.get(em, [])
                if len(cands) == 1:
                    r["errors"].append(
                        f"Email {em} belongs to {_label(cands[0])}, but the row has a different RHC ID ({r['rhc_id']}). "
                        "Records are never merged automatically; fix the file or skip the row.")
        elif email_fallback and em:
            cands = idx.by_email.get(em, [])
            if len(cands) == 1:
                emp, method = cands[0], "email"
            elif len(cands) > 1:
                r["errors"].append(f"Email {em} matches more than one employee.")
        elif "rhc_id" in mapped:
            r["errors"].append("RHC ID is blank.")
        if res and res.startswith("link:"):
            try:
                link_id = int(res.split(":", 1)[1])
            except ValueError:
                link_id = -1
            cand = idx.by_id.get(link_id)
            if cand is None:
                r["errors"].append("The linked employee no longer exists.")
            else:
                emp, method = cand, "linked by operator"
        if emp is None:
            nn = norm_name(r["name"]) if r["name"] else ""
            if nn and nn in idx.by_name:
                r["suggestions"] = [
                    {"id": c["id"], "rhc_id": c["rhc_id"], "name": c["full_name"], "location": c["location"],
                     "record_scope": c["record_scope"]}
                    for c in idx.by_name[nn] if user.in_scope(c.get("location"))
                ]
        if emp is not None:
            if emp["id"] in matched_ids:
                r["errors"].append(f"{_label(emp)} is matched by more than one row.")
            matched_ids.add(emp["id"])
            r["employee_id"] = emp["id"]
            r["employee_label"] = _label(emp)
            r["employee_version"] = emp["version"]
            r["match_method"] = method
            if em and em != (emp.get("official_email") or ""):
                other = [c for c in idx.by_email.get(em, []) if c["id"] != emp["id"]]
                if other:
                    r["errors"].append(f"Email {em} is already used by {_label(other[0])}.")

        # ---- scope
        row_loc = r["values"].get("location") or (emp or {}).get("location")
        r["location"] = row_loc
        if scope and row_loc not in scope:
            if purpose == "roster" or r["values"].get("location") or emp is not None:
                r["status"] = "outside_scope"
                r["warnings"].append(
                    f"Location '{row_loc or 'blank'}' is outside the declared scope ({', '.join(scope)}); row not imported.")
                out_rows.append(r)
                continue
        if not user.in_scope(row_loc):
            r["errors"].append("This row is outside your location access.")

        _plan_row(r, emp, purpose, updatable, mapped, blank_clears, enrich, create_unmatched, res, fmap, today, conn, all_fields, user)
        out_rows.append(r)

    # ---- proposed deactivations (complete roster only)
    deactivations = []
    if purpose == "roster":
        for rec in idx.by_id.values():
            if (rec["record_scope"] == "directory" and not rec["archived"] and rec["employment_status"] in ("Active", "Pending Review")
                    and rec.get("location") in scope and rec["id"] not in matched_ids and user.in_scope(rec.get("location"))):
                deactivations.append({"employee_id": rec["id"], "rhc_id": rec["rhc_id"], "name": rec["full_name"],
                                      "location": rec["location"], "version": rec["version"],
                                      "status": rec["employment_status"]})
        deactivations.sort(key=lambda d: d["rhc_id"])

    for r in out_rows:
        if r.get("status") not in ("skipped", "outside_scope") and r["errors"]:
            r["status"] = "duplicate" if r.get("duplicate") else "error"
        r.pop("blank", None)
    summary: dict[str, int] = {}
    for r in out_rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    summary["deactivations"] = len(deactivations)
    summary["rows"] = len(out_rows)
    blocking = sum(1 for r in out_rows if r["status"] in ("error", "duplicate"))
    plan_src = [
        (r["row_no"], r.get("action"), r.get("employee_id"), r.get("employee_version"),
         sorted((c["field"], jdump(c["new"])) for c in r["changes"]))
        for r in out_rows if r.get("action")
    ] + [("deact", d["employee_id"], d["version"]) for d in deactivations]
    plan_hash = hashlib.sha256(jdump(plan_src).encode()).hexdigest()
    return {"mapping_errors": [], "rows": out_rows, "summary": summary, "deactivations": deactivations,
            "blocking": blocking, "plan_hash": plan_hash, "headers": headers, "purpose": purpose,
            "sheet": data["sheet"]}


def _plan_row(r, emp, purpose, updatable, mapped, blank_clears, enrich, create_unmatched, res, fmap, today, conn, all_fields, user):
    vals = r["values"]
    fields_by_key = {f.key: f for f in all_fields}
    if emp is None:
        can_create = purpose in ("historical", "roster") or (purpose == "partial" and (create_unmatched or res == "create"))
        if not can_create:
            r["status"] = "unmatched"
            if r["suggestions"]:
                r["warnings"].append("No RHC ID match. A name match is shown as a suggestion; link it only if you are sure.")
            return
        if not r["rhc_id"]:
            r["errors"].append("RHC ID is required to create a new record.")
        if not vals.get("full_name"):
            r["errors"].append("Full name is required to create a new record.")
        new_vals = {k: v for k, v in vals.items() if k in updatable or k in ("rhc_id", "full_name")}
        scope = "historical" if purpose == "historical" else "directory"
        if purpose == "roster" and "employment_status" not in new_vals:
            new_vals["employment_status"] = "Active"
        if purpose == "historical" and "employment_status" not in new_vals:
            new_vals["employment_status"] = "Pending Review"
        rec = {k: None for k in CORE_COLUMNS}
        rec.update({"employment_status": "Active", "assignment_status": "Unknown", "custom": {}, "record_scope": scope})
        for k, v in new_vals.items():
            if k.startswith("cf_"):
                rec["custom"][k] = v
            else:
                rec[k] = v
        errs, warns = emps.validate_record(rec, today, fields_by_key)
        if rec.get("official_email"):
            errs.update({k: v for k, v in emps.check_unique(conn, rec).items() if k == "official_email"})
        r["errors"] += [f"{fmap[k].label if k in fmap else k}: {m}" for k, m in errs.items()]
        r["warnings"] += warns
        if r["suggestions"]:
            names = ", ".join(f"{s['name']} ({s['rhc_id']})" for s in r["suggestions"])
            r["warnings"].append(f"Possible duplicate of {names}: same name, different or missing RHC ID. "
                                 "It will be created as a new record unless you link it.")
        r["action"] = "create"
        r["record_scope"] = scope
        r["status"] = "historical_new" if scope == "historical" else "new"
        r["create_values"] = new_vals
        r["changes"] = [{"field": k, "label": fmap[k].label if k in fmap else k, "old": None, "new": v}
                        for k, v in new_vals.items()]
        return

    # ---- existing employee
    target: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    kept_differences = []
    if purpose == "historical" and emp["record_scope"] == "directory":
        # Enrich current employees: fill blanks only; never overwrite current data with historical data.
        for k, v in vals.items():
            if k in ("rhc_id",) or k not in updatable:
                continue
            cur = emps.flat_value(emp, k)
            if cur in (None, ""):
                target[k] = v
            elif cur != v:
                kept_differences.append(f"{fmap[k].label}: kept '{cur}' (file has '{v}')")
        r["status_hint"] = "enrich"
    else:
        for k in updatable:
            if k == "rhc_id" and r.get("match_method") != "linked by operator" and r.get("match_method") != "email":
                continue
            if k in vals:
                target[k] = vals[k]
            elif k in blank_clears and k in mapped:
                target[k] = None
        if purpose == "roster":
            if "employment_status" not in vals:
                if emp["employment_status"] != "Active":
                    target["employment_status"] = "Active"
                    r["warnings"].append(f"Listed in the current roster, so status changes from {emp['employment_status']} to Active.")
                    if emp.get("last_working_date") and "last_working_date" not in vals:
                        target["last_working_date"] = None
            if emp["archived"]:
                extra["archived"] = 0
                r["warnings"].append("This employee was archived and is back in the current roster; the record will be restored.")
            if emp["record_scope"] == "historical":
                extra["record_scope"] = "directory"
                r["status_hint"] = "promote"
                # Historical values not provided by the roster are kept only if approved for enrichment.
                for fd in fields_by_key.values():
                    k = fd.key
                    if (not fd.importable or k in NEVER_CLEAR or k in vals or k in enrich or k in target
                            or not user.can_edit_field(k)):
                        continue
                    if emps.flat_value(emp, k) not in (None, ""):
                        target[k] = "Unknown" if k == "assignment_status" else None
        elif purpose == "historical" and emp["record_scope"] == "historical":
            pass
    if kept_differences:
        r["warnings"].append("Current values kept: " + "; ".join(kept_differences))
    changes = []
    for k, v in target.items():
        cur = emps.flat_value(emp, k)
        if cur != v:
            changes.append({"field": k, "label": fmap[k].label if k in fmap else k, "old": cur, "new": v})
    for k, v in extra.items():
        if emp.get(k) != v:
            changes.append({"field": k, "label": {"record_scope": "Record type", "archived": "Archived"}[k],
                            "old": emp.get(k), "new": v, "system": True})
    if not changes:
        r["status"] = "unchanged"
        return
    new = dict(emp)
    new["custom"] = dict(emp["custom"])
    for c in changes:
        if c["field"].startswith("cf_"):
            new["custom"][c["field"]] = c["new"]
        else:
            new[c["field"]] = c["new"]
    errs, warns = emps.validate_record(new, today, fields_by_key)
    r["errors"] += [f"{fmap[k].label if k in fmap else k}: {m}" for k, m in errs.items()]
    r["warnings"] += warns
    r["action"] = "update"
    r["status"] = r.get("status_hint") or "update"
    r["changes"] = changes


# ---------------------------------------------------------------- preview / commit

def preview(conn, ctx, user: CurrentUser, batch_id: int, params: dict) -> dict:
    batch = get_batch(conn, batch_id)
    if batch["status"] not in ("uploaded", "previewed"):
        raise HubError("This import has already been committed or discarded.")
    plan = analyze(conn, ctx, user, batch, params)
    template_id = params.get("template_id")
    tname = None
    with tx(conn):
        if params.get("save_template_as"):
            template_id = save_template(conn, user, params["save_template_as"], params["purpose"], plan.get("sheet"),
                                        int(params.get("header_row") or 1), plan["headers"],
                                        params.get("mapping") or {}, params.get("options") or {})
        if template_id:
            tname = get_template(conn, int(template_id))["name"]
        conn.execute(
            "UPDATE import_batches SET purpose=?, template_id=?, template_name=?, params=?, scope=?, status='previewed', "
            "plan_hash=?, summary=?, result_rows=? WHERE id=?",
            (params["purpose"], template_id, tname, jdump(params), jdump((params.get("options") or {}).get("scope") or []),
             plan["plan_hash"], jdump(plan["summary"]), jdump(_report_rows(plan)), batch_id),
        )
    plan["template_id"] = template_id
    plan["previous_imports"] = previous_imports(conn, batch["file_hash"], batch_id)
    return plan


def _report_rows(plan: dict) -> list[dict]:
    return [{"row_no": r["row_no"], "status": r["status"], "rhc_id": r.get("rhc_id"), "name": r.get("name"),
             "employee": r.get("employee_label"), "errors": r["errors"], "warnings": r["warnings"],
             "changes": [f"{c['label']}: {c['old'] if c['old'] is not None else ''} -> {c['new'] if c['new'] is not None else ''}"
                         for c in r.get("changes", [])],
             "raw": r["raw"]} for r in plan["rows"]]


def commit(conn, ctx, user: CurrentUser, batch_id: int, body: dict) -> dict:
    with tx(conn):
        batch = get_batch(conn, batch_id)
        if batch["status"] != "previewed":
            raise HubError("Preview this import before committing it.")
        params = jload(batch["params"], {})
        plan = analyze(conn, ctx, user, batch, params)
        if plan["mapping_errors"]:
            raise ValidationFailed("The mapping has problems.", details={"mapping_errors": plan["mapping_errors"]})
        if plan["plan_hash"] != body.get("plan_hash") or plan["plan_hash"] != batch["plan_hash"]:
            raise Conflict("Employee data changed since you previewed this import. Review the new preview before committing.")
        if plan["blocking"]:
            raise ValidationFailed(f"{plan['blocking']} rows still have blocking errors. Fix the file or choose to skip those rows.")
        prev = previous_imports(conn, batch["file_hash"], batch_id)
        if prev and not body.get("acknowledge_repeat"):
            raise ValidationFailed("This exact file was imported before. Confirm that you want to import it again.",
                                   code="repeat_import")
        proposed = {d["employee_id"]: d for d in plan["deactivations"]}
        deact_ids = [int(x) for x in body.get("deactivate_ids") or []]
        bad = [i for i in deact_ids if i not in proposed]
        if bad:
            raise ValidationFailed("Only employees missing from a complete roster can be marked Left.")
        if deact_ids and not body.get("confirm_deactivations"):
            raise ValidationFailed("Confirm that the selected employees should be marked Left.")
        lwd = body.get("deactivation_last_working_date") or None
        created = updated = deactivated = 0
        for r in plan["rows"]:
            act = r.get("action")
            if act == "create":
                emp_id, _ = emps.create_employee(conn, user, r["create_values"], source="import", batch_id=batch_id,
                                                 record_scope=r["record_scope"], normalized=True, enforce_permissions=False)
                hid = conn.execute("SELECT MAX(id) FROM employee_history WHERE employee_id = ?", (emp_id,)).fetchone()[0]
                conn.execute("INSERT INTO import_changes(batch_id, employee_id, action, history_id) VALUES (?,?, 'create', ?)",
                             (batch_id, emp_id, hid))
                created += 1
            elif act == "update":
                ch = {c["field"]: c["new"] for c in r["changes"] if not c.get("system")}
                extra = {c["field"]: c["new"] for c in r["changes"] if c.get("system")}
                res = emps.apply_update(conn, user, r["employee_id"], r["employee_version"], ch, source="import",
                                        batch_id=batch_id, normalized=True, enforce_permissions=False, extra_columns=extra)
                for f, old, new, hid in res["changed"]:
                    conn.execute(
                        "INSERT INTO import_changes(batch_id, employee_id, action, field, old_value, new_value, history_id) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (batch_id, r["employee_id"], r["status"], f, emps.hv(old), emps.hv(new), hid))
                updated += 1
        for eid in deact_ids:
            d = proposed[eid]
            ch = {"employment_status": "Left"}
            rec = emps.fetch_record(conn, eid)
            if lwd and not rec.get("last_working_date"):
                ch["last_working_date"] = lwd
            res = emps.apply_update(conn, user, eid, d["version"], ch, source="import", batch_id=batch_id,
                                    enforce_permissions=False)
            for f, old, new, hid in res["changed"]:
                conn.execute(
                    "INSERT INTO import_changes(batch_id, employee_id, action, field, old_value, new_value, history_id) "
                    "VALUES (?,?, 'deactivate', ?,?,?,?)", (batch_id, eid, f, emps.hv(old), emps.hv(new), hid))
            deactivated += 1
        summary = dict(plan["summary"])
        summary.update({"created": created, "updated": updated, "deactivated": deactivated})
        conn.execute("UPDATE import_batches SET status='committed', committed_at=?, summary=?, result_rows=? WHERE id=?",
                     (utcnow(), jdump(summary), jdump(_report_rows(plan)), batch_id))
        audit.log(conn, user.email, "import.commit", "import_batch", batch_id,
                  {"filename": batch["filename"], "sha256": batch["file_hash"], "purpose": batch["purpose"],
                   "template": batch["template_name"], "scope": jload(batch["scope"], []), "summary": summary}, user.ip)
    return {"batch_id": batch_id, "summary": summary}


def discard(conn, user, batch_id: int) -> None:
    with tx(conn):
        b = get_batch(conn, batch_id)
        if b["status"] not in ("uploaded", "previewed"):
            raise HubError("Only imports that were not committed can be discarded.")
        conn.execute("UPDATE import_batches SET status='discarded' WHERE id=?", (batch_id,))
        audit.log(conn, user.email, "import.discard", "import_batch", batch_id, None, user.ip)


def error_report(conn, batch_id: int) -> tuple[str, bytes]:
    b = get_batch(conn, batch_id)
    rows = jload(b["result_rows"], []) or []
    params = jload(b["params"], {})
    width = max((len(r["raw"]) for r in rows), default=0)
    headers = ["Row", "Result", "RHC ID", "Name", "Matched employee", "Errors", "Warnings", "Changes"] + [
        f"Original column {i + 1}" for i in range(width)]
    out = []
    for r in rows:
        out.append([r["row_no"], r["status"], r.get("rhc_id"), r.get("name"), r.get("employee"),
                    "\n".join(r["errors"]), "\n".join(r["warnings"]), "\n".join(r["changes"])] + list(r["raw"]))
    problems = [o for o in out if o[1] in ("error", "duplicate", "unmatched", "outside_scope", "skipped")]
    data = write_xlsx(headers, problems, "Problems", extra_sheets={"All rows": (headers, out)})
    name = f"import-{batch_id}-report.xlsx"
    return name, data


def list_batches(conn, limit: int = 100) -> list[dict]:
    out = []
    for r in conn.execute("SELECT id, filename, file_hash, purpose, template_name, scope, operator, created_at, status, "
                          "summary, committed_at, undone_at, undone_by FROM import_batches ORDER BY id DESC LIMIT ?", (limit,)):
        d = dict(r)
        d["summary"] = jload(d["summary"], {})
        d["scope"] = jload(d["scope"], [])
        out.append(d)
    return out


# ---------------------------------------------------------------- undo

def undo_plan(conn, batch_id: int, user: CurrentUser | None = None) -> dict:
    b = get_batch(conn, batch_id)
    if b["status"] not in ("committed", "partially_undone"):
        raise HubError("Only committed imports can be undone.")
    revertible, conflicts = [], []
    changes = conn.execute("SELECT * FROM import_changes WHERE batch_id = ? AND undone = 0 ORDER BY id", (batch_id,)).fetchall()
    for c in changes:
        c = dict(c)
        rec_row = conn.execute("SELECT * FROM employees WHERE id = ?", (c["employee_id"],)).fetchone()
        if rec_row is None:
            conflicts.append({**c, "reason": "The employee record no longer exists."})
            continue
        rec = emps.row_record(rec_row)
        label = _label(rec)
        if user is not None and not user.in_scope(rec.get("location")):
            conflicts.append({**c, "employee": label, "reason": "This record is outside your location access."})
            continue
        if c["action"] == "create":
            later = conn.execute(
                "SELECT changed_by, changed_at, field FROM employee_history WHERE employee_id = ? AND id > ? "
                "AND (import_batch_id IS NOT ? ) ORDER BY id LIMIT 1", (c["employee_id"], c["history_id"], batch_id)).fetchone()
            photos = conn.execute("SELECT COUNT(*) FROM photos WHERE employee_id = ?", (c["employee_id"],)).fetchone()[0]
            if later:
                conflicts.append({**c, "employee": label, "reason":
                                  f"Record changed later by {later['changed_by']} on {later['changed_at']} ({later['field']})."})
            elif photos:
                conflicts.append({**c, "employee": label, "reason": "A photo has been added to this record since the import."})
            else:
                revertible.append({**c, "employee": label, "description": "Remove record created by this import"})
            continue
        field = c["field"]
        current = emps.flat_value(rec, field) if field not in ("record_scope", "archived") else rec.get(field)
        later = conn.execute(
            "SELECT changed_by, changed_at FROM employee_history WHERE employee_id = ? AND field = ? AND id > ? ORDER BY id LIMIT 1",
            (c["employee_id"], field, c["history_id"])).fetchone()
        if later or emps.hv(current) != c["new_value"]:
            who = f" by {later['changed_by']} on {later['changed_at']}" if later else ""
            conflicts.append({**c, "employee": label, "current": current,
                              "reason": f"{field} was changed again after this import{who}."})
        else:
            revertible.append({**c, "employee": label,
                               "description": f"{field}: {emps.hv_load(c['new_value'])} -> {emps.hv_load(c['old_value'])}"})
    return {"batch_id": batch_id, "revertible": revertible, "conflicts": conflicts}


def undo(conn, user: CurrentUser, batch_id: int, mode: str) -> dict:
    with tx(conn):
        plan = undo_plan(conn, batch_id, user)
        if plan["conflicts"] and mode != "non_conflicting":
            raise Conflict("Some imported values were changed afterwards, so the import cannot be fully undone. "
                           "Review the conflicts, or undo only the changes without conflicts.", details=plan)
        by_emp: dict[int, list[dict]] = {}
        for c in plan["revertible"]:
            by_emp.setdefault(c["employee_id"], []).append(c)
        reverted, failed = 0, []
        for emp_id, items in by_emp.items():
            conn.execute("SAVEPOINT undo_emp")
            try:
                creates = [i for i in items if i["action"] == "create"]
                if creates:
                    conn.execute("DELETE FROM employees WHERE id = ?", (emp_id,))
                else:
                    ch = {i["field"]: emps.hv_load(i["old_value"]) for i in items if i["field"] not in ("record_scope", "archived")}
                    extra = {i["field"]: emps.hv_load(i["old_value"]) for i in items if i["field"] in ("record_scope", "archived")}
                    emps.apply_update(conn, user, emp_id, None, ch, source=f"undo:{batch_id}", normalized=True,
                                      enforce_permissions=False, extra_columns=extra)
                conn.executemany("UPDATE import_changes SET undone = 1 WHERE id = ?", [(i["id"],) for i in items])
                conn.execute("RELEASE undo_emp")
                reverted += len(items)
            except (ValidationFailed, Conflict) as exc:
                conn.execute("ROLLBACK TO undo_emp")
                conn.execute("RELEASE undo_emp")
                failed.append({"employee_id": emp_id, "reason": exc.message, "details": exc.details})
        remaining = conn.execute("SELECT COUNT(*) FROM import_changes WHERE batch_id = ? AND undone = 0", (batch_id,)).fetchone()[0]
        status = "undone" if remaining == 0 else "partially_undone"
        conn.execute("UPDATE import_batches SET status=?, undone_at=?, undone_by=? WHERE id=?", (status, utcnow(), user.email, batch_id))
        audit.log(conn, user.email, "import.undo", "import_batch", batch_id,
                  {"mode": mode, "reverted": reverted, "conflicts": len(plan["conflicts"]), "failed": failed}, user.ip)
    return {"status": status, "reverted": reverted, "conflicts": plan["conflicts"], "failed": failed}
