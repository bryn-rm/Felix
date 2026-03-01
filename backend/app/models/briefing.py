from pydantic import BaseModel
from datetime import datetime, date


class Briefing(BaseModel):
    id: str
    user_id: str
    date: date
    text: str | None = None
    audio_url: str | None = None
    priority_emails: list[dict] | None = None
    calendar_summary: dict | None = None
    follow_ups_summary: dict | None = None
    generated_at: datetime | None = None
    listened_at: datetime | None = None
