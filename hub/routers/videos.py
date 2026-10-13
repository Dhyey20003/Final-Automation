"""Videos page: status of the Video Studio service and its queue."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from ..deps import get_ctx, require
from ..errors import HubError
from ..permissions import CurrentUser

router = APIRouter(prefix="/api/videos", tags=["videos"])


def _svc(ctx):
    svc = getattr(ctx, "video", None)
    if svc is None:
        raise HubError("The Video Studio service is not configured in this process.")
    return svc


@router.get("/status")
def status(user: CurrentUser = Depends(require("videos.view")), ctx=Depends(get_ctx)):
    return _svc(ctx).status()


@router.post("/start")
def start(user: CurrentUser = Depends(require("videos.operate")), ctx=Depends(get_ctx)):
    svc = _svc(ctx)
    svc.start()
    return svc.status()


@router.post("/jobs/{action}")
def job_action(action: str, body: dict = Body(...), user: CurrentUser = Depends(require("videos.operate")), ctx=Depends(get_ctx)):
    code, text = _svc(ctx).job_action(action, str(body.get("id") or ""))
    if code >= 400:
        raise HubError(text or "The Video Studio refused the request.")
    return JSONResponse({"ok": True, "message": text})
