"""FastAPI dependencies: database connection, current user, permission checks."""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Callable, Iterator

from fastapi import Depends, Request

from .db import get_setting, utcnow, utcnow_dt
from .errors import Forbidden, Unauthorized
from .permissions import CurrentUser, user_from_row
from .security import token_hash
from .timeutil import parse_iso_dt

SESSION_COOKIE = "rehub_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_ctx(request: Request):
    return request.app.state.ctx


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    ctx = request.app.state.ctx
    with ctx.db() as conn:
        yield conn


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def load_session_user(conn: sqlite3.Connection, raw_token: str | None, ip: str) -> CurrentUser | None:
    if not raw_token:
        return None
    sid = token_hash(raw_token)
    row = conn.execute(
        "SELECT s.id AS sid, s.csrf_token, s.expires_at, s.last_seen_at, u.* FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.id = ?",
        (sid,),
    ).fetchone()
    if row is None:
        return None
    now = utcnow_dt()
    idle = int(get_setting(conn, "session_idle_minutes", 60))
    if parse_iso_dt(row["expires_at"]) < now or parse_iso_dt(row["last_seen_at"]) + timedelta(minutes=idle) < now:
        conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        return None
    if not row["is_active"]:
        conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        return None
    # Touch at most once a minute to limit writes.
    if parse_iso_dt(row["last_seen_at"]) + timedelta(seconds=60) < now:
        conn.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (utcnow(), sid))
    return user_from_row(row, session_id=sid, csrf=row["csrf_token"], ip=ip)


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> CurrentUser:
    user = load_session_user(conn, request.cookies.get(SESSION_COOKIE), client_ip(request))
    if user is None:
        raise Unauthorized("Your session has ended. Please sign in again.")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token")
        if not sent or sent != user.csrf_token:
            raise Forbidden("The request was blocked by cross-site request protection. Reload the page and try again.")
    if user.must_change_password and not request.url.path.startswith("/api/auth/"):
        raise Forbidden("You must change your password before continuing.", code="password_change_required")
    return user


def require(*actions: str) -> Callable[..., CurrentUser]:
    def dep(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        missing = [a for a in actions if not user.can(a)]
        if missing:
            raise Forbidden("You do not have permission to do this. Ask an administrator for access.",
                            details={"missing": missing})
        return user

    return dep


def require_any(*actions: str) -> Callable[..., CurrentUser]:
    def dep(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if not any(user.can(a) for a in actions):
            raise Forbidden("You do not have permission to do this.", details={"any_of": list(actions)})
        return user

    return dep
