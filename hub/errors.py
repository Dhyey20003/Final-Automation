from __future__ import annotations

from typing import Any


class HubError(Exception):
    """An error with a plain-English message that is safe to show to the operator."""

    status = 400
    code = "bad_request"

    def __init__(self, message: str, details: Any = None, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code


class NotFound(HubError):
    status = 404
    code = "not_found"


class Forbidden(HubError):
    status = 403
    code = "forbidden"


class Unauthorized(HubError):
    status = 401
    code = "unauthorized"


class Conflict(HubError):
    status = 409
    code = "conflict"


class ValidationFailed(HubError):
    status = 422
    code = "validation_failed"
