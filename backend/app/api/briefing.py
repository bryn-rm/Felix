"""
Daily briefing routes — Phase 4.
"""

from fastapi import APIRouter, Depends
from app.middleware.auth import get_current_user

router = APIRouter()


@router.get("/today")
async def get_today_briefing(current_user: dict = Depends(get_current_user)):
    # TODO Phase 4: return today's briefing (text + audio_url) for this user
    raise NotImplementedError


@router.get("/history")
async def get_briefing_history(
    limit: int = 7,
    current_user: dict = Depends(get_current_user),
):
    # TODO Phase 4: return last N briefings for replay
    raise NotImplementedError


@router.post("/generate")
async def trigger_briefing(current_user: dict = Depends(get_current_user)):
    # TODO Phase 4: manually trigger briefing generation for this user
    raise NotImplementedError
