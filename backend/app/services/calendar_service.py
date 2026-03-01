"""
Google Calendar API wrapper — Phase 4.
"""

from googleapiclient.discovery import build


class CalendarService:
    def __init__(self, credentials):
        self.service = build("calendar", "v3", credentials=credentials)

    async def get_events(self, time_min: str, time_max: str) -> list[dict]:
        # TODO Phase 4: fetch events and return parsed list
        raise NotImplementedError

    async def create_event(self, event: dict) -> dict:
        # TODO Phase 4: create a calendar event
        raise NotImplementedError

    async def get_free_busy(self, time_min: str, time_max: str) -> dict:
        # TODO Phase 4: query freebusy for scheduling suggestions
        raise NotImplementedError
