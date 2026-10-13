"""Read-only local integration API for fetch-only tokens.

    GET /api/fetch/v1/employees                 active employees (token's locations and fields only)
    GET /api/fetch/v1/employees/{rhc_id}/photo  current photo, only if the token allows photos

Tokens cannot sign in to the interface, cannot write anything, and can never read
restricted fields such as date of birth or notes.
"""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from ..deps import get_conn, get_ctx
from ..errors import Forbidden, NotFound
from ..fields import registry
from ..services import users as U
from ..services.employees import bench_days, row_record, tz_today

router = APIRouter(prefix="/api/fetch/v1", tags=["fetch"])


@router.get("/employees")
def employees(request: Request, location: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    tok = U.token_from_header(conn, request.headers.get("authorization"))
    scope = json.loads(tok["location_scope"] or "[]")
    current = {f.key: f for f in registry(conn)}  # fields marked sensitive or switched off later are dropped
    fields = [f for f in json.loads(tok["allowed_fields"] or "[]")
              if f not in U.SENSITIVE_FOR_TOKENS and f in current and not current[f].sensitive and f != "photo"]
    q = "SELECT * FROM employees WHERE record_scope = 'directory' AND archived = 0 AND employment_status = 'Active'"
    args: list = []
    if location:
        if scope and location not in scope:
            raise Forbidden("This token cannot read that location.")
        q += " AND location = ?"
        args.append(location)
    elif scope:
        q += f" AND location IN ({', '.join('?' for _ in scope)})"
        args += scope
    today = tz_today(conn)
    out = []
    for r in conn.execute(q + " ORDER BY rhc_id", args).fetchall():
        rec = row_record(r)
        item = {}
        for f in fields:
            if f == "bench_days":
                item[f] = bench_days(rec, today)
            elif f.startswith("cf_"):
                item[f] = rec["custom"].get(f)
            else:
                item[f] = rec.get(f)
        if tok["include_photos"]:
            item["has_photo"] = bool(rec["current_photo_id"])
        out.append(item)
    return {"employees": out, "count": len(out)}


@router.get("/employees/{rhc_id}/photo")
def photo(rhc_id: str, request: Request, conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    tok = U.token_from_header(conn, request.headers.get("authorization"))
    if not tok["include_photos"]:
        raise Forbidden("This token is not allowed to read photos.")
    scope = json.loads(tok["location_scope"] or "[]")
    r = conn.execute("SELECT e.location, p.stored_path FROM employees e JOIN photos p ON p.id = e.current_photo_id "
                     "WHERE e.rhc_id = ? AND e.record_scope = 'directory' AND e.archived = 0 AND e.employment_status = 'Active'",
                     (rhc_id,)).fetchone()
    if r is None or (scope and r["location"] not in scope):
        raise NotFound("No photo found.")
    return FileResponse(ctx.paths.data / r["stored_path"])
