"""
Eval / observability routes.

Endpoints (mounted at /eval):
  POST /eval/feedback           — store a user rating for an AI response
  GET  /eval/feedback/summary   — aggregated stats per feature (last 7 days)

Endpoints (mounted at /admin):
  GET  /admin/parse-errors      — last 20 parse errors from ai_calls (admin only)
  GET  /admin/prompt-versions   — performance grouped by prompt_version + feature (admin only)

Admin access is gated by the ADMIN_EMAIL environment variable.
"""

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.middleware.auth import get_current_user

router = APIRouter()        # mounted at /eval   in main.py
admin_router = APIRouter()  # mounted at /admin  in main.py


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_admin(current_user: dict) -> None:
    """Raise 403 unless the current user matches ADMIN_EMAIL."""
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    if not admin_email or current_user["email"] != admin_email:
        raise HTTPException(status_code=403, detail="Admin access required")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class FeedbackCreate(BaseModel):
    ai_call_id: str
    feature: str
    rating: int           # 1 = good, 2 = edited, 0 = wrong / corrected
    correction: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# /eval routes
# ---------------------------------------------------------------------------


@router.post("/feedback")
async def submit_feedback(
    body: FeedbackCreate,
    current_user: dict = Depends(get_current_user),
):
    """Store a user rating for an AI-generated response (best-effort)."""
    row = await db.insert(
        "eval_feedback",
        {
            "user_id": current_user["id"],
            "ai_call_id": body.ai_call_id,
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
    Aggregated AI performance stats per feature for the last 7 days.
    Returns one row per feature with call counts, latency, success/parse-error
    rates, and user-rating breakdowns.
    """
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
        LEFT JOIN eval_feedback ef
               ON ef.ai_call_id = ac.id
              AND ef.user_id = $1
        WHERE ac.created_at >= NOW() - INTERVAL '7 days'
        GROUP BY ac.feature
        ORDER BY ac.feature
        """,
        current_user["id"],
    )
    return rows


# ---------------------------------------------------------------------------
# /admin routes
# ---------------------------------------------------------------------------


@admin_router.get("/parse-errors")
async def get_parse_errors(
    current_user: dict = Depends(get_current_user),
):
    """
    Last 20 rows from ai_calls where parse_error = true, across ALL users.
    Useful for identifying which prompt version is producing malformed JSON.
    Requires ADMIN_EMAIL match.
    """
    _require_admin(current_user)
    rows = await db.query(
        """
        SELECT id, feature, prompt_version, error_message, created_at
        FROM   ai_calls
        WHERE  parse_error = true
        ORDER  BY created_at DESC
        LIMIT  20
        """
    )
    return rows


@admin_router.get("/prompt-versions")
async def get_prompt_versions(
    current_user: dict = Depends(get_current_user),
):
    """
    Performance breakdown grouped by prompt_version + feature, across all users.
    Use this to compare prompt versions and decide which to promote.
    Requires ADMIN_EMAIL match.
    """
    _require_admin(current_user)
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
