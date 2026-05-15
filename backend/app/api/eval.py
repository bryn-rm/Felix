"""
Eval / observability routes.

Endpoints (mounted at /eval):
  POST /eval/feedback           — store a user rating for an AI response
  GET  /eval/feedback/summary   — aggregated stats per feature, last 7 days (admin only)

Endpoints (mounted at /admin):
  GET  /admin/me                — verify current user has admin access
  GET  /admin/parse-errors      — last 20 parse errors from ai_calls (admin only)
  GET  /admin/prompt-versions   — performance grouped by prompt_version + feature (admin only)

Admin access is gated by the ADMIN_EMAILS environment variable (comma-separated).
"""

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.config import settings
from app.middleware.auth import get_current_user
from app.middleware.pii import redact_pii

logger = logging.getLogger(__name__)

router = APIRouter()        # mounted at /eval   in main.py
admin_router = APIRouter()  # mounted at /admin  in main.py


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_admin_emails() -> set[str]:
    """Parse the comma-separated ADMIN_EMAILS config into a set of lower-cased emails.

    Also includes the deprecated ADMIN_EMAIL (singular) for backward compat.
    """
    raw = settings.ADMIN_EMAILS or ""
    emails = {e.strip().lower() for e in raw.split(",") if e.strip()}
    # Backward compat: merge the old singular setting if present
    legacy = (settings.ADMIN_EMAIL or "").strip().lower()
    if legacy:
        emails.add(legacy)
    return emails


def _require_admin(current_user: dict) -> None:
    """Raise 403 unless the current user is in the ADMIN_EMAILS list."""
    admin_emails = _get_admin_emails()
    if not admin_emails or (current_user.get("email") or "").lower() not in admin_emails:
        raise HTTPException(status_code=403, detail="Admin access required")


async def _log_admin_access(user_id: str, email: str, endpoint: str) -> None:
    """Best-effort audit log for admin endpoint access."""
    try:
        await db.insert("admin_audit", {
            "user_id": user_id,
            "email": email,
            "endpoint": endpoint,
            "accessed_at": datetime.now(timezone.utc),
        })
    except Exception:
        # Table may not exist yet — log and continue
        logger.warning("Could not write admin audit log for %s (table may not exist)", endpoint)


def _redact_error_message(msg: str | None, max_len: int = 200) -> str:
    """Redact PII from error messages and truncate to max_len."""
    if not msg:
        return ""
    redacted = redact_pii(msg)
    if len(redacted) > max_len:
        return redacted[:max_len] + "..."
    return redacted


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class FeedbackCreate(BaseModel):
    ai_call_id: str | None = None
    feature: str
    rating: int           # 1 = good, 2 = edited, 0 = wrong / corrected
    correction: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# /eval routes
# ---------------------------------------------------------------------------


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@router.post("/feedback")
async def submit_feedback(
    body: FeedbackCreate,
    current_user: dict = Depends(get_current_user),
):
    """
    Store a user rating for an AI-generated response (best-effort).

    The frontend currently passes either a draft UUID or an email TEXT id as
    ai_call_id, neither of which is a real ai_calls.id. We validate the value
    is a UUID *and* exists in ai_calls before storing it; otherwise we drop
    it to NULL so the rating is still captured rather than failing the insert.
    """
    valid_ai_call_id: str | None = None
    if body.ai_call_id and _UUID_RE.match(body.ai_call_id):
        exists = await db.query_one(
            "SELECT id FROM ai_calls WHERE id = $1", body.ai_call_id
        )
        if exists:
            valid_ai_call_id = body.ai_call_id

    row = await db.insert(
        "ai_feedback",
        {
            "user_id": current_user["id"],
            "ai_call_id": valid_ai_call_id,
            "feature": body.feature,
            "rating": body.rating,
            "correction": body.correction,
            "notes": body.notes,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return row or {"ok": True}


@router.get("/feedback/summary")
async def get_feedback_summary(
    current_user: dict = Depends(get_current_user),
):
    """
    Aggregated AI performance stats per feature for the last 7 days, across
    ALL users. Returns one row per feature with call counts, latency,
    success/parse-error rates, and org-wide user-rating breakdowns.
    Requires ADMIN_EMAILS match.
    """
    _require_admin(current_user)
    await _log_admin_access(current_user["id"], current_user.get("email", ""), "/eval/feedback/summary")

    rows = await db.query(
        """
        SELECT
            ac.feature,
            COUNT(*)                                                      AS calls_7d,
            ROUND(AVG(ac.latency_ms))::int                               AS avg_latency_ms,
            ROUND(
                100.0 * SUM(CASE WHEN ac.success      THEN 1 ELSE 0 END)
                      / NULLIF(COUNT(*), 0),
                1
            )                                                             AS success_pct,
            ROUND(
                100.0 * SUM(CASE WHEN ac.parse_error  THEN 1 ELSE 0 END)
                      / NULLIF(COUNT(*), 0),
                1
            )                                                             AS parse_error_pct,
            ROUND(AVG(ef.rating), 2)                                      AS avg_user_rating,
            COUNT(ef.id)                                                  AS rated_count,
            SUM(CASE WHEN ef.rating = 1              THEN 1 ELSE 0 END)  AS good_count,
            SUM(CASE WHEN ef.correction IS NOT NULL  THEN 1 ELSE 0 END)  AS edited_count,
            SUM(CASE WHEN ef.rating = 0              THEN 1 ELSE 0 END)  AS wrong_count
        FROM ai_calls ac
        LEFT JOIN ai_feedback ef
               ON ef.ai_call_id = ac.id
        WHERE ac.created_at >= NOW() - INTERVAL '7 days'
        GROUP BY ac.feature
        ORDER BY ac.feature
        """,
    )
    return rows


# ---------------------------------------------------------------------------
# /admin routes
# ---------------------------------------------------------------------------


@admin_router.get("/me")
async def get_admin_me(
    current_user: dict = Depends(get_current_user),
):
    """
    Lightweight admin authorization probe for the frontend.
    Returns 200 only when the authenticated user matches ADMIN_EMAILS.
    """
    _require_admin(current_user)
    return {"admin": True}


@admin_router.get("/parse-errors")
async def get_parse_errors(
    current_user: dict = Depends(get_current_user),
):
    """
    Last 20 rows from ai_calls where parse_error = true, across ALL users.
    Useful for identifying which prompt version is producing malformed JSON.
    Requires ADMIN_EMAILS match.
    """
    _require_admin(current_user)
    await _log_admin_access(current_user["id"], current_user.get("email", ""), "/admin/parse-errors")

    rows = await db.query(
        """
        SELECT id, feature, prompt_version, error_message, created_at
        FROM   ai_calls
        WHERE  parse_error = true
        ORDER  BY created_at DESC
        LIMIT  20
        """
    )
    # Redact error_message to avoid leaking PII (email subjects/bodies)
    for row in rows:
        row["error_message"] = _redact_error_message(row.get("error_message"))
    return rows


@admin_router.get("/prompt-versions")
async def get_prompt_versions(
    current_user: dict = Depends(get_current_user),
):
    """
    Performance breakdown grouped by prompt_version + feature, across all users.
    Use this to compare prompt versions and decide which to promote.
    Requires ADMIN_EMAILS match.
    """
    _require_admin(current_user)
    await _log_admin_access(current_user["id"], current_user.get("email", ""), "/admin/prompt-versions")

    rows = await db.query(
        """
        SELECT
            COALESCE(prompt_version, 'unversioned')                       AS prompt_version,
            feature,
            COUNT(*)                                                       AS call_count,
            ROUND(AVG(latency_ms))::int                                   AS avg_latency_ms,
            ROUND(
                100.0 * SUM(CASE WHEN success THEN 1 ELSE 0 END)
                      / NULLIF(COUNT(*), 0),
                1
            )                                                              AS success_pct
        FROM   ai_calls
        GROUP  BY prompt_version, feature
        ORDER  BY prompt_version, feature
        """
    )
    return rows
