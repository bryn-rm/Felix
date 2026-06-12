"""
Rate limiting for expensive endpoints (AI draft/polish, voice).

Uses slowapi with per-user-id limits. Also provides a dependency that
checks monthly AI call usage against the configured cap.
"""

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app import db
from app.config import settings
from app.errors import error_envelope


def _get_user_id_from_request(request: Request) -> str:
    """Extract user_id from the Authorization JWT *before* the route body runs.

    SlowAPI calls this key function when evaluating the decorator, which is
    before Depends(get_current_user) populates request.state. We verify the
    JWT signature locally to extract a trusted subject claim for the rate-limit
    key. If verification fails (secret not configured, bad signature), we fall
    back to IP address so an attacker cannot forge a token to burn another
    user's rate-limit budget.
    """
    from app.middleware.auth import _verify_jwt_locally

    auth_header = request.headers.get("authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if token:
        try:
            payload = _verify_jwt_locally(token)
            if payload:
                sub = payload.get("sub")
                if sub:
                    return sub
        except Exception:
            pass
    return get_remote_address(request)


limiter = Limiter(key_func=_get_user_id_from_request)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Return a JSON 429 response.

    Exception handlers must return a Response — raising HTTPException here
    would bypass FastAPI's error translation and surface as a 500.
    """
    return JSONResponse(
        status_code=429,
        content=error_envelope(429, "Rate limit exceeded. Please slow down."),
    )


def _is_admin_email(email: str | None) -> bool:
    if not email:
        return False
    raw = settings.ADMIN_EMAILS or ""
    admins = {e.strip().lower() for e in raw.split(",") if e.strip()}
    legacy = (settings.ADMIN_EMAIL or "").strip().lower()
    if legacy:
        admins.add(legacy)
    return email.lower() in admins


async def check_monthly_ai_budget(user_id: str, email: str | None = None) -> None:
    """Raise 429 if the user has exceeded their monthly AI call cap.

    Admins (email in ADMIN_EMAILS) get the higher ADMIN_MONTHLY_AI_CALL_LIMIT cap.
    """
    cap = (
        settings.ADMIN_MONTHLY_AI_CALL_LIMIT
        if _is_admin_email(email)
        else settings.MONTHLY_AI_CALL_LIMIT
    )
    if cap <= 0:
        return

    row = await db.query_one(
        """
        SELECT COUNT(*) AS cnt
        FROM ai_calls
        WHERE user_id = $1
          AND created_at >= date_trunc('month', NOW())
        """,
        user_id,
    )
    if row and row["cnt"] >= cap:
        raise HTTPException(
            status_code=429,
            detail="Monthly AI usage limit reached. Contact support to increase your quota.",
        )
