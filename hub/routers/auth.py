from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Body, Depends, Request, Response

from .. import APP_NAME, APP_VERSION
from ..db import get_setting
from ..deps import SESSION_COOKIE, client_ip, current_user, get_conn, get_ctx, load_session_user
from ..errors import Forbidden
from ..main import _is_loopback
from ..permissions import CurrentUser
from ..services import users as U

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _cookie(response: Response, token: str, conn) -> None:
    hours = int(get_setting(conn, "session_absolute_hours", 12))
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", max_age=hours * 3600, path="/")


@router.get("/state")
def state(request: Request, conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    user = load_session_user(conn, request.cookies.get(SESSION_COOKIE), client_ip(request))
    return {
        "app": APP_NAME, "version": APP_VERSION,
        "setup_required": U.setup_required(conn),
        "setup_allowed_here": _is_loopback(client_ip(request)),
        "user": user.public() if user else None,
        "maintenance": ctx.maintenance,
    }


@router.post("/setup")
def setup(request: Request, response: Response, body: dict = Body(...), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    ip = client_ip(request)
    if not _is_loopback(ip):
        raise Forbidden("First-run setup can only be done on the computer where the application is installed.")
    U.create_first_admin(conn, body.get("email", ""), body.get("display_name", ""), body.get("password", ""), ip)
    token, extra = U.login(conn, body.get("email", ""), body.get("password", ""), ip, request.headers.get("user-agent", ""), ctx.throttle)
    _cookie(response, token, conn)
    return {"ok": True, **extra}


@router.post("/login")
def login(request: Request, response: Response, body: dict = Body(...), conn: sqlite3.Connection = Depends(get_conn), ctx=Depends(get_ctx)):
    token, extra = U.login(conn, body.get("email", ""), body.get("password", ""), client_ip(request),
                           request.headers.get("user-agent", ""), ctx.throttle)
    _cookie(response, token, conn)
    user = load_session_user(conn, token, client_ip(request))
    return {"ok": True, "user": user.public() if user else None}


@router.post("/logout")
def logout(response: Response, user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    U.logout(conn, user)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: CurrentUser = Depends(current_user)):
    return user.public()


@router.post("/change-password")
def change_password(body: dict = Body(...), user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    U.change_password(conn, user, body.get("current_password", ""), body.get("new_password", ""))
    return {"ok": True}
