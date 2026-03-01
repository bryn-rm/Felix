"""
Calendar routes — Phase 4.

Read events, scheduling suggestions, focus block management.
"""

from fastapi import APIRouter, Depends
from app.middleware.auth import get_current_user

router = APIRouter()


@router.get("/events")
async def list_events(
    days_ahead: int = 7,
    current_user: dict = Depends(get_current_user),
):
    # TODO Phase 4: fetch events from Google Calendar for this user
    raise NotImplementedError


@router.get("/today")
async def today_summary(current_user: dict = Depends(get_current_user)):
    # TODO Phase 4: return today's meetings + focus blocks
    raise NotImplementedError


@router.post("/events")
async def create_event(current_user: dict = Depends(get_current_user)):
    # TODO Phase 4: create a calendar event via Google Calendar API
    raise NotImplementedError
