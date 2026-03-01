"""
Google Calendar API wrapper — Phase 4.
"""

import asyncio
from datetime import datetime, timezone

from googleapiclient.discovery import build


class CalendarService:
    def __init__(self, credentials):
        self.service = build("calendar", "v3", credentials=credentials, cache_discovery=True)

    async def get_events(self, time_min: str, time_max: str) -> list[dict]:
        request = self.service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        )
        result = await asyncio.to_thread(request.execute)
        return [self._parse_event(item) for item in result.get("items", [])]

    async def create_event(self, event: dict) -> dict:
        request = self.service.events().insert(calendarId="primary", body=event)
        created = await asyncio.to_thread(request.execute)
        return self._parse_event(created)

    async def get_free_busy(self, time_min: str, time_max: str) -> dict:
        request = self.service.freebusy().query(
            body={
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": "primary"}],
            }
        )
        result = await asyncio.to_thread(request.execute)
        busy = (
            result.get("calendars", {})
            .get("primary", {})
            .get("busy", [])
        )
        return {"time_min": time_min, "time_max": time_max, "busy": busy}

    @staticmethod
    def _parse_event(event: dict) -> dict:
        start = (event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date")
        end = (event.get("end") or {}).get("dateTime") or (event.get("end") or {}).get("date")

        return {
            "id": event.get("id"),
            "status": event.get("status"),
            "summary": event.get("summary") or "(no title)",
            "description": event.get("description") or "",
            "location": event.get("location") or "",
            "hangout_link": event.get("hangoutLink"),
            "html_link": event.get("htmlLink"),
            "creator": event.get("creator", {}).get("email"),
            "organizer": event.get("organizer", {}).get("email"),
            "attendees": [a.get("email") for a in event.get("attendees", []) if a.get("email")],
            "start": start,
            "end": end,
            "is_all_day": bool((event.get("start") or {}).get("date")),
            "updated_at": event.get("updated") or datetime.now(timezone.utc).isoformat(),
        }
