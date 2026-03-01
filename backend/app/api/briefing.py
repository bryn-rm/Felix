"""
Daily briefing routes — Phase 4.
"""

from datetime import date

from fastapi import APIRouter, Depends

from app import db
from app.middleware.auth import get_current_user
from app.services.briefing_service import briefing_service

router = APIRouter()


@router.get("/today")
async def get_today_briefing(current_user: dict = Depends(get_current_user)):
    row = await db.query_one(
        "SELECT * FROM briefings WHERE user_id = $1 AND date = $2",
        current_user["id"],
        date.today(),
    )
    if row:
        return row
    return await briefing_service.generate_for_user(current_user["id"])


@router.get("/history")
async def get_briefing_history(
    limit: int = 7,
    current_user: dict = Depends(get_current_user),
):
    n = max(1, min(limit, 30))
    rows = await db.query(
        """
        SELECT *
        FROM briefings
        WHERE user_id = $1
        ORDER BY date DESC
        LIMIT $2
        """,
        current_user["id"],
        n,
    )
    return {"briefings": rows, "limit": n}


@router.post("/generate")
async def trigger_briefing(current_user: dict = Depends(get_current_user)):
    briefing = await briefing_service.generate_for_user(current_user["id"])
    return {"generated": True, "briefing": briefing}
