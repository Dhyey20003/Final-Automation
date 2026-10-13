"""FastAPI application factory."""
from __future__ import annotations

import ipaddress
import logging
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import APP_NAME, APP_VERSION
from .config import resource_dir
from .context import AppContext
from .db import get_setting
from .errors import HubError

log = logging.getLogger("hub")

LOOPBACK_NAMES = {"127.0.0.1", "::1", "localhost", "testclient"}


def _is_loopback(host: str) -> bool:
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _allowed_client(ctx: AppContext, host: str) -> bool:
    if _is_loopback(host):
        return True
    cached = getattr(ctx, "_net_cache", None)
    if cached and time.time() - cached[0] < 5:
        net = cached[1]
    else:
        with ctx.db() as conn:
            net = get_setting(conn, "network", {}) or {}
        ctx._net_cache = (time.time(), net)
    if not net.get("lan_enabled"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if not ip.is_private:
        return False
    for cidr in net.get("allowed_networks") or []:
        try:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def _allowed_host_header(ctx: AppContext, host_header: str) -> bool:
    host = (host_header or "").rsplit(":", 1)[0].strip("[]").lower()
    if host in ("localhost", "127.0.0.1", "::1", "testserver"):
        return True
    names = {ctx.hostname.lower()}
    try:
        names.add(socket.getfqdn().lower())
    except OSError:
        pass
    if host in names:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


def create_app(data_dir: Path, outlook_mode: str | None = None, start_background: bool = True, testing: bool = False) -> FastAPI:
    ctx = AppContext(Path(data_dir), outlook_mode=outlook_mode, testing=testing)
    ctx.initialise()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from .services import dispatch
        from .services.scheduler import Scheduler

        with ctx.db() as conn:
            info = dispatch.recover_after_restart(conn)
            if info["dispatches_flagged"] or info["preparing_reset"]:
                ctx.startup_warnings.append(
                    f"Recovered after restart: {info['dispatches_flagged']} interrupted sends were marked 'Delivery Uncertain' "
                    f"for review, {info['preparing_reset']} interrupted preparations will be retried.")
        ctx.scheduler = Scheduler(ctx)
        from .services.videosvc import VideoService

        ctx.video = VideoService(ctx)
        if start_background:
            ctx.scheduler.start()
            ctx.video.start()
        try:
            yield
        finally:
            if ctx.scheduler:
                ctx.scheduler.stop()
            if getattr(ctx, "video", None):
                ctx.video.stop()
            ctx.release_instance_lock()

    app = FastAPI(title=APP_NAME, version=APP_VERSION, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.ctx = ctx

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.client.host if request.client else ""
        if not _allowed_client(ctx, host):
            return JSONResponse({"error": {"code": "network_blocked", "message":
                                 "Access from other computers is not enabled. An administrator can enable office network access in Settings."}},
                                status_code=403)
        if not _allowed_host_header(ctx, request.headers.get("host", "")):
            return JSONResponse({"error": {"code": "bad_host", "message": "Unrecognised host name."}}, status_code=400)
        path = request.url.path
        if ctx.maintenance and path.startswith("/api/") and path not in ("/api/auth/state",):
            return JSONResponse({"error": {"code": "maintenance", "message": ctx.maintenance_reason or
                                           "The application is busy with a restore. Try again in a minute."}}, status_code=503)
        ctx.request_started()
        try:
            response = await call_next(request)
        finally:
            ctx.request_finished()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'")
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HubError)
    async def hub_error(request: Request, exc: HubError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message, "details": exc.details}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        problems = [f"{'.'.join(str(p) for p in e.get('loc', [])[1:])}: {e.get('msg')}" for e in exc.errors()]
        return JSONResponse({"error": {"code": "invalid_request", "message": "The request was not valid. " + "; ".join(problems[:5]),
                                       "details": problems}}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("Unexpected error on %s", request.url.path)
        return JSONResponse({"error": {"code": "internal", "message": f"Something went wrong: {exc}. The details were written to the log."}},
                            status_code=500)

    from .routers import auth, backups, employees, fetch, imports, photos, posts, screenshots, settings, tasks, videos

    for r in (auth, employees, imports, photos, tasks, settings, backups, fetch, posts, screenshots, videos):
        app.include_router(r.router)

    static = resource_dir() / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(static / "img" / "favicon.png")

    return app
