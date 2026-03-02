"""
Calendar routes — Phase 4.

All endpoints load Google credentials per-request and construct
CalendarService(creds) — never a singleton.

Endpoints:
  GET  /calendar/events          — upcoming events (configurable window)
  GET  /calendar/today           — today's schedule + energy profile context
  POST /calendar/events          — create a new calendar event
  GET  /calendar/free-slots      — AI-suggested meeting times
  POST /calendar/focus-block     — create a focus-block event
"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import db
from app.middleware.auth import get_current_user, get_google_credentials
from app.services.calendar_service import CalendarService

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class CreateEventRequest(BaseModel):
    title: str
    start: str              # RFC3339 datetime string, e.g. "2026-03-03T10:00:00+00:00"
    end: str                # RFC3339 datetime string
    attendees: list[str] = []
    location: str = ""
    description: str = ""
    timezone: str = "UTC"


class FocusBlockRequest(BaseModel):
    date: str = ""          # "YYYY-MM-DD"; defaults to today if omitted


# ---------------------------------------------------------------------------
# GET /calendar/events
# ---------------------------------------------------------------------------

@router.get("/events")
async def list_events(
    days_ahead: int = Query(7, ge=1, le=30),
    current_user: dict = Depends(get_current_user),
):
    """
    Return upcoming calendar events for the next days_ahead days.
    Makes a live call to Google Calendar — not cached.
    """
    creds = await get_google_credentials(current_user["id"])
    cal = CalendarService(creds)

    try:
        events = await cal.get_upcoming_events(days_ahead=days_ahead)
    except Exception as exc:
        logger.exception("Calendar fetch failed for user %s", current_user["id"])
        raise HTTPException(status_code=502, detail=f"Calendar API error: {exc}")

    return {"events": events, "count": len(events)}


# ---------------------------------------------------------------------------
# GET /calendar/today
# ---------------------------------------------------------------------------

@router.get("/today")
async def today_summary(current_user: dict = Depends(get_current_user)):
    """
    Return today's meetings, any scheduling conflicts, and the user's
    energy profile (focus windows) so the frontend can annotate the timeline.
    """
    user_id = current_user["id"]

    # Load timezone + energy_profile from settings
    settings = await db.query_one(
        "SELECT timezone, energy_profile FROM settings WHERE user_id = $1",
        user_id,
    )
    user_tz: str = (settings or {}).get("timezone") or "UTC"
    energy_profile: dict = (settings or {}).get("energy_profile") or {}

    creds = await get_google_credentials(user_id)
    cal = CalendarService(creds)

    try:
        events = await cal.get_today_events(user_timezone=user_tz)
        conflicts = await cal.detect_conflicts(user_timezone=user_tz)
    except Exception as exc:
        logger.exception("Today summary failed for user %s", user_id)
        raise HTTPException(status_code=502, detail=f"Calendar API error: {exc}")

    return {
        "date": date.today().isoformat(),
        "timezone": user_tz,
        "events": events,
        "event_count": len(events),
        "conflicts": conflicts,
        "energy_profile": energy_profile,
    }


# ---------------------------------------------------------------------------
# POST /calendar/events
# ---------------------------------------------------------------------------

@router.post("/events", status_code=201)
async def create_event(
    body: CreateEventRequest,
    current_user: dict = Depends(get_current_user),
):
    """Create a new event in the user's primary Google Calendar."""
    creds = await get_google_credentials(current_user["id"])
    cal = CalendarService(creds)

    event_body = {
        "summary": body.title,
        "start": {"dateTime": body.start, "timeZone": body.timezone},
        "end":   {"dateTime": body.end,   "timeZone": body.timezone},
        "attendees": [{"email": e} for e in body.attendees],
        "location": body.location or "",
        "description": body.description or "",
    }

    try:
        created = await cal.create_event(event_body)
    except Exception as exc:
        logger.exception("Create event failed for user %s", current_user["id"])
        raise HTTPException(status_code=502, detail=f"Calendar API error: {exc}")

    return {"event": created}


# ---------------------------------------------------------------------------
# GET /calendar/free-slots
# ---------------------------------------------------------------------------

@router.get("/free-slots")
async def get_free_slots(
    duration_minutes: int = Query(30, ge=15, le=480),
    days_ahead: int = Query(5, ge=1, le=14),
    current_user: dict = Depends(get_current_user),
):
    """
    Suggest available meeting slots, respecting the user's focus blocks.

    Reads energy_profile.deep_work windows from settings and excludes them.
    Only proposes slots inside energy_profile.meetings windows (or 9am–6pm default).
    """
    creds = await get_google_credentials(current_user["id"])
    cal = CalendarService(creds)

    try:
        slots = await cal.find_free_slots(
            user_id=current_user["id"],
            duration_minutes=duration_minutes,
            days_ahead=days_ahead,
        )
    except Exception as exc:
        logger.exception("Free slots failed for user %s", current_user["id"])
        raise HTTPException(status_code=502, detail=f"Calendar API error: {exc}")

    return {"slots": slots, "duration_minutes": duration_minutes}


# ---------------------------------------------------------------------------
# POST /calendar/focus-block
# ---------------------------------------------------------------------------

@router.post("/focus-block", status_code=201)
async def create_focus_block(
    body: FocusBlockRequest = FocusBlockRequest(),
    current_user: dict = Depends(get_current_user),
):
    """
    Create focus-block events for the user's configured deep_work windows on a given date.
    Defaults to today if no date is provided.
    """
    date_str = body.date or date.today().isoformat()
    creds = await get_google_credentials(current_user["id"])
    cal = CalendarService(creds)

    try:
        result = await cal.protect_focus_block(
            user_id=current_user["id"],
            date_str=date_str,
        )
    except Exception as exc:
        logger.exception("Focus block creation failed for user %s", current_user["id"])
        raise HTTPException(status_code=502, detail=f"Calendar API error: {exc}")

    return {"date": date_str, "blocks_created": result.get("created", 0)}
