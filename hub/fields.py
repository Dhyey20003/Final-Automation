"""Field registry (core + administrator-defined custom fields) and value normalisation."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .db import get_setting
from .security import valid_email
from .timeutil import iso, parse_date

EMPLOYMENT_STATUSES = ["Active", "Left", "Pending Review"]
ASSIGNMENT_STATUSES = ["Client", "Bench", "Internal", "Unknown"]
DEFAULT_LOCATIONS = ["Ahmedabad", "Vadodara"]

EMPLOYMENT_ALIASES = {
    "active": "Active", "working": "Active", "current": "Active", "employed": "Active",
    "left": "Left", "resigned": "Left", "exited": "Left", "exit": "Left", "inactive": "Left",
    "terminated": "Left", "relieved": "Left", "absconded": "Left", "separated": "Left",
    "pending review": "Pending Review", "pending": "Pending Review", "review": "Pending Review",
}
ASSIGNMENT_ALIASES = {
    "client": "Client", "on client": "Client", "billable": "Client", "deployed": "Client", "assigned": "Client",
    "bench": "Bench", "on bench": "Bench", "benched": "Bench",
    "internal": "Internal", "in-house": "Internal", "inhouse": "Internal",
    "unknown": "Unknown", "": "Unknown",
}
DEFAULT_LOCATION_ALIASES = {
    "ahmedabad": "Ahmedabad", "amd": "Ahmedabad", "ahd": "Ahmedabad", "ahmd": "Ahmedabad", "ahmadabad": "Ahmedabad",
    "vadodara": "Vadodara", "baroda": "Vadodara", "vdr": "Vadodara", "brd": "Vadodara",
}

DATE_FIELDS_CORE = [
    "date_of_joining", "date_of_birth", "last_working_date", "assignment_start",
    "assignment_end", "bench_start", "bench_end",
]


@dataclass
class FieldDef:
    key: str
    label: str
    type: str
    options: list[str] = field(default_factory=list)
    custom: bool = False
    editable: bool = True
    importable: bool = True
    sensitive: bool = False
    required: bool = False
    show_in_table: bool = True
    group: str = "Details"
    help: str = ""

    def public(self) -> dict:
        return {
            "key": self.key, "label": self.label, "type": self.type, "options": self.options,
            "custom": self.custom, "editable": self.editable, "importable": self.importable,
            "sensitive": self.sensitive, "required": self.required, "show_in_table": self.show_in_table,
            "group": self.group, "help": self.help,
        }


def core_fields(locations: list[str]) -> list[FieldDef]:
    F = FieldDef
    return [
        F("id", "Internal ID", "id", editable=False, importable=False, group="Identity",
          help="Permanent internal number. Never changes, never reused."),
        F("rhc_id", "RHC ID", "rhc_id", required=True, group="Identity",
          help="Unique company ID, stored as text so leading zeros are kept."),
        F("full_name", "Full name", "text", required=True, group="Identity"),
        F("designation", "Designation", "text", group="Role"),
        F("department", "Department", "text", group="Role"),
        F("official_email", "Official email", "email", group="Identity"),
        F("location", "Location", "dropdown", options=list(locations), group="Role"),
        F("date_of_joining", "Date of joining", "date", group="Dates"),
        F("date_of_birth", "Date of birth", "date", sensitive=True, group="Dates",
          help="Restricted field. Users without full access see day and month only, or nothing."),
        F("employment_status", "Employment status", "dropdown", options=EMPLOYMENT_STATUSES, group="Status"),
        F("last_working_date", "Last working date", "date", group="Status"),
        F("assignment_status", "Assignment status", "dropdown", options=ASSIGNMENT_STATUSES, group="Assignment"),
        F("client_name", "Client name", "text", group="Assignment"),
        F("assignment_start", "Assignment start", "date", group="Assignment"),
        F("assignment_end", "Assignment end", "date", group="Assignment"),
        F("bench_start", "Bench start", "date", group="Assignment"),
        F("bench_end", "Bench end", "date", group="Assignment"),
        F("bench_days", "Current bench days", "calculated", editable=False, importable=False, group="Assignment",
          help="Calendar days from bench start to today (0 on the start date). Blank unless status is Bench."),
        F("photo", "Photo", "photo", editable=False, importable=False, group="Identity"),
        F("notes", "Notes", "longtext", group="Other", show_in_table=False),
        F("created_by", "Created by", "meta", editable=False, importable=False, group="Record", show_in_table=False),
        F("created_at", "Created on", "meta", editable=False, importable=False, group="Record", show_in_table=False),
        F("updated_by", "Updated by", "meta", editable=False, importable=False, group="Record", show_in_table=False),
        F("updated_at", "Updated on", "meta", editable=False, importable=False, group="Record", show_in_table=False),
    ]


CORE_COLUMNS = {
    "rhc_id", "full_name", "designation", "department", "official_email", "location", "date_of_joining",
    "date_of_birth", "employment_status", "last_working_date", "assignment_status", "client_name",
    "assignment_start", "assignment_end", "bench_start", "bench_end", "notes",
}


def get_locations(conn: sqlite3.Connection) -> list[str]:
    return get_setting(conn, "locations", DEFAULT_LOCATIONS) or DEFAULT_LOCATIONS


def registry(conn: sqlite3.Connection, include_inactive: bool = False) -> list[FieldDef]:
    fields = core_fields(get_locations(conn))
    q = "SELECT * FROM field_defs WHERE is_custom = 1"
    if not include_inactive:
        q += " AND active = 1"
    q += " ORDER BY sort_order, id"
    for r in conn.execute(q).fetchall():
        fields.append(
            FieldDef(
                key=r["key"], label=r["label"], type=r["field_type"], options=json.loads(r["options"] or "[]"),
                custom=True, sensitive=bool(r["sensitive"]), required=bool(r["required"]),
                show_in_table=bool(r["show_in_table"]), group="Custom fields", help=r["help_text"] or "",
                editable=bool(r["active"]), importable=bool(r["active"]),
            )
        )
    return fields


def registry_map(conn: sqlite3.Connection) -> dict[str, FieldDef]:
    return {f.key: f for f in registry(conn)}


def slugify_key(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return "cf_" + (s[:40] or "field")


class FieldError(ValueError):
    pass


def normalize_rhc_id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = re.sub(r"\s+", "", str(value)).upper()
    if not text:
        return None
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_\-/.]{0,39}", text):
        raise FieldError(f"RHC ID '{value}' contains characters that are not allowed")
    return text


def _text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def normalize(fd: FieldDef, value: Any, *, date_order: str = "DMY", location_aliases: dict | None = None) -> Any:
    """Convert a raw input value into the stored representation. Blank becomes None."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    t = fd.type
    if t == "rhc_id":
        return normalize_rhc_id(value)
    if t in ("text",):
        s = _text(value)
        if len(s) > 500:
            raise FieldError(f"{fd.label} is longer than 500 characters")
        return s
    if t == "longtext":
        s = str(value).strip()
        if len(s) > 5000:
            raise FieldError(f"{fd.label} is longer than 5000 characters")
        return s
    if t == "email":
        s = _text(value).lower()
        if not valid_email(s):
            raise FieldError(f"'{value}' is not a valid email address")
        return s
    if t == "date":
        try:
            return iso(parse_date(value, date_order))
        except ValueError as exc:
            raise FieldError(str(exc)) from None
    if t == "number":
        if isinstance(value, bool):
            raise FieldError(f"{fd.label} must be a number")
        if isinstance(value, (int, float)):
            n = value
        else:
            try:
                n = float(str(value).replace(",", "").strip())
            except ValueError:
                raise FieldError(f"'{value}' is not a number") from None
        return int(n) if float(n).is_integer() else float(n)
    if t == "yesno":
        s = str(value).strip().lower()
        if s in ("yes", "y", "true", "1", "1.0"):
            return "Yes"
        if s in ("no", "n", "false", "0", "0.0"):
            return "No"
        raise FieldError(f"'{value}' must be Yes or No")
    if t == "dropdown":
        s = _text(value)
        low = s.lower()
        if fd.key == "employment_status":
            if low in EMPLOYMENT_ALIASES:
                return EMPLOYMENT_ALIASES[low]
        elif fd.key == "assignment_status":
            if low in ASSIGNMENT_ALIASES:
                return ASSIGNMENT_ALIASES[low]
        elif fd.key == "location":
            aliases = {**DEFAULT_LOCATION_ALIASES, **(location_aliases or {})}
            if low in aliases and aliases[low] in fd.options:
                return aliases[low]
        for opt in fd.options:
            if opt.lower() == low:
                return opt
        raise FieldError(f"'{value}' is not an allowed {fd.label} (allowed: {', '.join(fd.options)})")
    raise FieldError(f"{fd.label} cannot be set directly")
