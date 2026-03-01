from pydantic import BaseModel
from datetime import datetime


class ContactProfile(BaseModel):
    email: str
    user_id: str
    name: str | None = None
    company: str | None = None
    role: str | None = None

    # Relationship signals
    vip: bool = False
    vip_rules: dict | None = None
    relationship_strength: float | None = None  # 0.0 → 1.0
    total_emails: int = 0
    last_contacted: datetime | None = None
    meeting_count: int = 0
    last_meeting: datetime | None = None

    # Communication patterns
    topics_discussed: list[str] = []
    open_commitments: list[str] = []
    their_open_commitments: list[str] = []
    sentiment_trend: str | None = None
    known_facts: dict | None = None
    personal_notes: str | None = None
    tags: list[str] = []
    style_profile: dict | None = None

    updated_at: datetime | None = None
