"""
Google Calendar API wrapper — Phase 4.

Mirrors the GmailService pattern exactly:
- Constructed per-request with a user Credentials object (never a global)
- Every blocking execute() call runs via asyncio.to_thread()
- HttpError 401/403/429 handled and logged via _handle_http_error()
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytz
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


class CalendarService:
    def __init__(self, credentials):
        self.service = build(
            "calendar", "v3",
            credentials=credentials,
            cache_discovery=True,
        )

    # ------------------------------------------------------------------
    # Reading events
    # ------------------------------------------------------------------

    async def get_events(
        self,
        time_min: str,
        time_max: str,
        calendar_id: str = "primary",
    ) -> list[dict]:
        """
        Fetch calendar events between time_min and time_max (RFC3339 strings).
        Returns parsed event dicts sorted chronologically.
        """
        request = self.service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=100,
        )
        try:
            result = await asyncio.to_thread(request.execute)
        except HttpError as e:
            _handle_http_error(e, context="list calendar events")
            return []

        return [self._parse_event(e) for e in result.get("items", [])]

    async def get_today_events(self, user_timezone: str = "UTC") -> list[dict]:
        """
        Return all events for today in the user's local timezone.
        """
        try:
            tz = pytz.timezone(user_timezone)
        except pytz.UnknownTimeZoneError:
            tz = pytz.UTC

        now_local = datetime.now(tz)
        day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        return await self.get_events(
            time_min=day_start.isoformat(),
            time_max=day_end.isoformat(),
        )

    async def get_upcoming_events(self, days_ahead: int = 7) -> list[dict]:
        """Return events from now until days_ahead days in the future."""
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days_ahead)
        return await self.get_events(
            time_min=now.isoformat(),
            time_max=end.isoformat(),
        )

    # ------------------------------------------------------------------
    # Creating events
    # ------------------------------------------------------------------

    async def create_event(self, event: dict, calendar_id: str = "primary") -> dict:
        """
        Create a calendar event.

        event dict format (Google Calendar API v3):
        {
            "summary": "Meeting title",
            "start": {"dateTime": "2026-03-03T10:00:00+00:00", "timeZone": "Europe/London"},
            "end":   {"dateTime": "2026-03-03T11:00:00+00:00", "timeZone": "Europe/London"},
            "attendees": [{"email": "person@example.com"}],
            "description": "...",
            "location": "...",
        }
        """
        request = self.service.events().insert(
            calendarId=calendar_id,
            body=event,
            sendUpdates="all",  # send invite emails to attendees
        )
        try:
            created = await asyncio.to_thread(request.execute)
        except HttpError as e:
            _handle_http_error(e, context="create calendar event")
            raise
        return self._parse_event(created)

    # ------------------------------------------------------------------
    # Free/busy
    # ------------------------------------------------------------------

    async def get_free_busy(
        self,
        time_min: str,
        time_max: str,
        calendar_id: str = "primary",
    ) -> list[dict]:
        """
        Query freebusy for the given window.
        Returns a list of busy periods: [{start: str, end: str}].
        """
        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": calendar_id}],
        }
        request = self.service.freebusy().query(body=body)
        try:
            result = await asyncio.to_thread(request.execute)
        except HttpError as e:
            _handle_http_error(e, context="freebusy query")
            return []

        calendars = result.get("calendars", {})
        busy_periods = calendars.get(calendar_id, {}).get("busy", [])
        return busy_periods  # [{start, end}] already in the right shape

    async def find_free_slots(
        self,
        user_id: str,
        duration_minutes: int = 30,
        days_ahead: int = 5,
    ) -> list[dict]:
        """
        Suggest free meeting slots for the next days_ahead days.

        Reads the user's energy_profile from the settings table and
        excludes deep-work focus blocks from available times, only
        suggesting slots inside meeting windows.

        Returns a list of dicts: [{start: str, end: str, label: str}]
        """
        from app import db

        now = datetime.now(timezone.utc)
        window_end = now + timedelta(days=days_ahead)

        # Load user timezone + energy profile
        settings = await db.query_one(
            "SELECT timezone, energy_profile FROM settings WHERE user_id = $1",
            user_id,
        )
        user_tz_name: str = (settings or {}).get("timezone") or "UTC"
        energy_profile: dict = (settings or {}).get("energy_profile") or {}

        try:
            user_tz = pytz.timezone(user_tz_name)
        except pytz.UnknownTimeZoneError:
            user_tz = pytz.UTC

        # Fetch existing busy periods
        busy = await self.get_free_busy(
            time_min=now.isoformat(),
            time_max=window_end.isoformat(),
        )
        busy_intervals = [
            (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
            for b in busy
        ]

        # Parse meeting windows from energy_profile (exclude focus/deep-work blocks)
        # energy_profile example:
        #   {"deep_work": ["09:00-12:00"], "meetings": ["14:00-17:00"]}
        meeting_windows: list[tuple[int, int, int, int]] = []  # (h_start, m_start, h_end, m_end)
        for window_str in energy_profile.get("meetings", []):
            try:
                start_str, end_str = window_str.split("-")
                hs, ms = map(int, start_str.split(":"))
                he, me = map(int, end_str.split(":"))
                meeting_windows.append((hs, ms, he, me))
            except (ValueError, AttributeError):
                continue

        # If no meeting windows defined, use 9am-6pm as default
        if not meeting_windows:
            meeting_windows = [(9, 0, 18, 0)]

        slots: list[dict] = []
        candidate = now.replace(second=0, microsecond=0)
        # Round up to next quarter-hour
        minutes_over = candidate.minute % 15
        if minutes_over:
            candidate += timedelta(minutes=15 - minutes_over)

        while candidate < window_end and len(slots) < 10:
            slot_end = candidate + timedelta(minutes=duration_minutes)

            # Convert to user's local time for window comparison
            candidate_local = candidate.astimezone(user_tz)
            h, m = candidate_local.hour, candidate_local.minute

            # Check if this slot falls inside a meeting window
            in_window = any(
                (hs * 60 + ms) <= (h * 60 + m) < (he * 60 + me)
                and (hs * 60 + ms) <= ((h * 60 + m) + duration_minutes) <= (he * 60 + me)
                for hs, ms, he, me in meeting_windows
            )

            if in_window:
                # Check for conflicts with existing busy periods
                conflict = any(
                    not (slot_end <= bs or candidate >= be)
                    for bs, be in busy_intervals
                )
                if not conflict:
                    slots.append({
                        "start": candidate.isoformat(),
                        "end": slot_end.isoformat(),
                        "label": candidate_local.strftime("%A %d %b, %H:%M"),
                    })

            candidate += timedelta(minutes=15)

        return slots

    # ------------------------------------------------------------------
    # Conflicts
    # ------------------------------------------------------------------

    async def detect_conflicts(self, user_timezone: str = "UTC") -> list[dict]:
        """
        Return pairs of overlapping events for today.
        Useful for the briefing / morning summary.
        """
        events = await self.get_today_events(user_timezone)
        conflicts = []
        for i, ev_a in enumerate(events):
            for ev_b in events[i + 1:]:
                if ev_a.get("is_all_day") or ev_b.get("is_all_day"):
                    continue
                start_a = ev_a.get("start")
                end_a = ev_a.get("end")
                start_b = ev_b.get("start")
                end_b = ev_b.get("end")
                if start_a and end_a and start_b and end_b:
                    # Overlap if neither ends before the other starts
                    if not (end_a <= start_b or end_b <= start_a):
                        conflicts.append({"event_a": ev_a, "event_b": ev_b})
        return conflicts

    # ------------------------------------------------------------------
    # Focus blocks
    # ------------------------------------------------------------------

    async def protect_focus_block(
        self,
        user_id: str,
        date_str: str,
        calendar_id: str = "primary",
    ) -> dict:
        """
        Create a "Focus Time / Deep Work" all-day blocker event on the
        deep_work windows defined in the user's energy_profile.

        date_str: "YYYY-MM-DD"
        """
        from app import db

        settings = await db.query_one(
            "SELECT timezone, energy_profile FROM settings WHERE user_id = $1",
            user_id,
        )
        energy_profile: dict = (settings or {}).get("energy_profile") or {}
        user_tz_name: str = (settings or {}).get("timezone") or "UTC"

        focus_windows: list[str] = energy_profile.get("deep_work", [])
        if not focus_windows:
            return {"created": 0}

        created = 0
        for window_str in focus_windows:
            try:
                start_str, end_str = window_str.split("-")
                event_body = {
                    "summary": "🔒 Deep Work / Focus Time",
                    "description": "Protected focus block — no meetings please.",
                    "start": {
                        "dateTime": f"{date_str}T{start_str}:00",
                        "timeZone": user_tz_name,
                    },
                    "end": {
                        "dateTime": f"{date_str}T{end_str}:00",
                        "timeZone": user_tz_name,
                    },
                    "transparency": "opaque",  # marks time as busy
                }
                await self.create_event(event_body, calendar_id=calendar_id)
                created += 1
            except Exception:
                logger.exception("Failed to create focus block for window %s", window_str)

        return {"created": created}

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_event(self, raw: dict) -> dict:
        """Flatten a Google Calendar event into a consistent dict."""
        start_raw = raw.get("start", {})
        end_raw = raw.get("end", {})

        # All-day events use "date" instead of "dateTime"
        is_all_day = "date" in start_raw and "dateTime" not in start_raw
        start = start_raw.get("dateTime") or start_raw.get("date") or ""
        end = end_raw.get("dateTime") or end_raw.get("date") or ""

        attendees = [
            a.get("email", "")
            for a in raw.get("attendees", [])
            if a.get("email")
        ]

        return {
            "id": raw.get("id", ""),
            "title": raw.get("summary", "(no title)"),
            "start": start,
            "end": end,
            "is_all_day": is_all_day,
            "attendees": attendees,
            "attendee_count": len(attendees),
            "location": raw.get("location", ""),
            "description": raw.get("description", ""),
            "hangout_link": raw.get("hangoutLink", ""),
            "status": raw.get("status", ""),
            "organizer": raw.get("organizer", {}).get("email", ""),
            "html_link": raw.get("htmlLink", ""),
        }


# ---------------------------------------------------------------------------
# Error handling (matches gmail_service._handle_http_error pattern)
# ---------------------------------------------------------------------------

def _handle_http_error(error: HttpError, context: str = "") -> None:
    code = error.resp.status if hasattr(error, "resp") else None
    prefix = f"Calendar API error [{context}]" if context else "Calendar API error"
    if code == 401:
        logger.warning("%s: 401 Unauthorized — token expired or revoked.", prefix)
    elif code == 403:
        logger.warning("%s: 403 Forbidden — insufficient scope or quota.", prefix)
    elif code == 429:
        logger.warning("%s: 429 Too Many Requests — rate limit hit.", prefix)
    else:
        logger.warning("%s: HTTP %s — %s", prefix, code, error)
