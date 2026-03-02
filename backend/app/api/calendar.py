"""
Calendar routes — Phase 4.

Read events, scheduling suggestions, focus block management.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from app import db
from app.middleware.auth import get_google_credentials
from app.middleware.auth import get_current_user
from app.services.calendar_service import CalendarService
from pydantic import BaseModel

router = APIRouter()


class CreateEventRequest(BaseModel):
    summary: str
    start: str  # ISO datetime, e.g. 2026-01-02T14:00:00+00:00
    end: str    # ISO datetime
    description: str | None = None
    location: str | None = None
    attendees: list[str] | None = None


async def _get_user_tz(user_id: str) -> ZoneInfo:
    settings = await db.query_one("SELECT timezone FROM settings WHERE user_id = $1", user_id)
    tz_name = (settings or {}).get("timezone")
    try:
        return ZoneInfo(tz_name) if tz_name else ZoneInfo("Europe/London")
    except Exception:
        return ZoneInfo("Europe/London")


@router.get("/events")
async def list_events(
    days_ahead: int = 7,
    current_user: dict = Depends(get_current_user),
):
    creds = await get_google_credentials(current_user["id"])
    calendar = CalendarService(creds)

    days = max(1, min(days_ahead, 30))
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days)).isoformat()

    events = await calendar.get_events(time_min=time_min, time_max=time_max)
    return {
        "events": events,
        "time_min": time_min,
        "time_max": time_max,
        "days_ahead": days,
    }


@router.get("/today")
async def today_summary(current_user: dict = Depends(get_current_user)):
    creds = await get_google_credentials(current_user["id"])
    calendar = CalendarService(creds)

    user_tz = await _get_user_tz(current_user["id"])
    local_now = datetime.now(user_tz)
    start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)

    events = await calendar.get_events(
        time_min=start_local.astimezone(timezone.utc).isoformat(),
        time_max=end_local.astimezone(timezone.utc).isoformat(),
    )

    # Approximate focus blocks: free gaps of at least 60 min in working day.
    busy_windows: list[tuple[datetime, datetime]] = []
    for ev in events:
        if ev.get("is_all_day"):
            continue
        try:
            s = datetime.fromisoformat(ev["start"])
            e = datetime.fromisoformat(ev["end"])
            busy_windows.append((s, e))
        except Exception:
            continue

    busy_windows.sort(key=lambda x: x[0])
    focus_blocks: list[dict] = []
    work_start = start_local.replace(hour=9)
    work_end = start_local.replace(hour=17)
    cursor = work_start.astimezone(timezone.utc)

    for s, e in busy_windows:
        if s > cursor and (s - cursor).total_seconds() >= 3600:
            focus_blocks.append({"start": cursor.isoformat(), "end": s.isoformat()})
        if e > cursor:
            cursor = e

    work_end_utc = work_end.astimezone(timezone.utc)
    if work_end_utc > cursor and (work_end_utc - cursor).total_seconds() >= 3600:
        focus_blocks.append({"start": cursor.isoformat(), "end": work_end_utc.isoformat()})

    return {
        "date": local_now.date().isoformat(),
        "timezone": str(user_tz),
        "meeting_count": len(events),
        "events": events,
        "focus_blocks": focus_blocks,
    }


@router.post("/events")
async def create_event(
    body: CreateEventRequest,
    current_user: dict = Depends(get_current_user),
):
    creds = await get_google_credentials(current_user["id"])
    calendar = CalendarService(creds)

    event_body = {
        "summary": body.summary,
        "description": body.description or "",
        "location": body.location or "",
        "start": {"dateTime": body.start},
        "end": {"dateTime": body.end},
        "attendees": [{"email": email} for email in (body.attendees or [])],
    }
    created = await calendar.create_event(event_body)
    return {"created": True, "event": created}
