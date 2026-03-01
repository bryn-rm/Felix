from pydantic import BaseModel
from datetime import datetime


class User(BaseModel):
    id: str
    email: str
    metadata: dict = {}


class UserSettings(BaseModel):
    user_id: str
    display_name: str | None = None
    timezone: str = "Europe/London"
    briefing_time: str = "07:30"
    style_profile: dict | None = None
    vip_contacts: list[str] = []
    digest_mode: bool = False
    digest_times: list[str] = []
    energy_profile: dict | None = None
    felix_voice_id: str | None = None
    updated_at: datetime | None = None


class GoogleConnection(BaseModel):
    user_id: str
    google_email: str
    connected_at: datetime
    last_sync: datetime | None = None
