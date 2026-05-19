"""
Unified error envelope for the Felix HTTP API.

All non-2xx responses go out as:
    {"code": "<machine_code>", "message": "<human readable>"}

This replaces the inconsistent mix of FastAPI's default ``{"detail": ...}``,
SlowAPI's ``{"detail": "Rate limit..."}``, and Pydantic 422's
``{"detail": [{...}]}`` — all of which the frontend used to surface either
opaquely or as ``[object Object]``.
"""

from typing import Any

_STATUS_TO_CODE: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    410: "gone",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
}


def status_to_code(status: int) -> str:
    """Map an HTTP status code to a stable machine-readable code string."""
    if status in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status]
    if 500 <= status < 600:
        return "internal_error"
    return "http_error"


def error_envelope(status: int, message: str, code: str | None = None) -> dict[str, Any]:
    """Build the unified ``{code, message}`` body used by every error handler."""
    return {"code": code or status_to_code(status), "message": message}
