"""
Daily briefing routes — Phase 4.

Endpoints:
  GET  /briefing/today           — today's briefing (text + audio_url)
  GET  /briefing/history         — last N briefings for replay
  POST /briefing/generate        — manually trigger briefing (async, 202)
  POST /briefing/{id}/listened   — mark a briefing as played
"""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app import db
from app.middleware.auth import get_current_user
from app.services.briefing_service import briefing_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# GET /briefing/today
# ---------------------------------------------------------------------------

@router.get("/today")
async def get_today_briefing(current_user: dict = Depends(get_current_user)):
    """
    Return today's briefing (text + audio_url) for this user.
    Returns 404 if no briefing has been generated yet today.
    """
    row = await db.query_one(
        "SELECT * FROM briefings WHERE user_id = $1 AND date = CURRENT_DATE",
        current_user["id"],
    )
    if not row:
        return {
            "briefing": None,
            "message": "No briefing generated yet today. Use POST /briefing/generate to create one.",
        }
    return {"briefing": row}


# ---------------------------------------------------------------------------
# GET /briefing/history
# ---------------------------------------------------------------------------

@router.get("/history")
async def get_briefing_history(
    limit: int = Query(7, ge=1, le=30),
    current_user: dict = Depends(get_current_user),
):
    """Return the last N daily briefings for this user (for replay in the app)."""
    rows = await db.query(
        """
        SELECT id, date, text, audio_url, generated_at, listened_at
        FROM briefings
        WHERE user_id = $1
        ORDER BY date DESC
        LIMIT $2
        """,
        current_user["id"],
        limit,
    )
    return {"briefings": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# POST /briefing/generate
# ---------------------------------------------------------------------------

@router.post("/generate", status_code=202)
async def trigger_briefing(current_user: dict = Depends(get_current_user)):
    """
    Manually trigger briefing generation for this user.

    Returns 202 Accepted immediately and runs generation in the background.
    If today's briefing already exists it will be overwritten (re-generated).
    """
    user_id = current_user["id"]

    async def _run():
        try:
            from app.services.briefing_service import briefing_service
            await briefing_service.generate_for_user(user_id)
            logger.info("Manual briefing generation complete for user %s", user_id)
        except Exception:
            logger.exception("Manual briefing generation failed for user %s", user_id)

    asyncio.create_task(_run())

    return {
        "status": "generating",
        "message": "Briefing generation started. Check GET /briefing/today in a few seconds.",
    }


# ---------------------------------------------------------------------------
# POST /briefing/{briefing_id}/listened
# ---------------------------------------------------------------------------

@router.post("/{briefing_id}/listened")
async def mark_listened(
    briefing_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Mark a briefing as listened to (sets listened_at timestamp)."""
    briefing = await db.query_one(
        "SELECT id FROM briefings WHERE id = $1 AND user_id = $2",
        briefing_id, current_user["id"],
    )
    if not briefing:
        raise HTTPException(status_code=404, detail="Briefing not found")

    await db.execute(
        "UPDATE briefings SET listened_at = $1 WHERE id = $2 AND user_id = $3",
        datetime.now(timezone.utc),
        briefing_id,
        current_user["id"],
    )
    return {"listened": True}
