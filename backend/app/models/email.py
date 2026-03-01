from pydantic import BaseModel
from datetime import datetime


class EmailRecord(BaseModel):
    id: str
    user_id: str
    thread_id: str | None = None
    from_email: str | None = None
    from_name: str | None = None
    to_email: str | None = None
    subject: str | None = None
    body: str | None = None
    snippet: str | None = None
    received_at: datetime | None = None
    category: str | None = None          # action_required | fyi | waiting_on | newsletter | automated | vip
    urgency: str | None = None           # low | medium | high | critical
    sentiment: str | None = None         # neutral | positive | stressed | frustrated | urgent
    topic: str | None = None
    triage_json: dict | None = None
    processed_at: datetime | None = None
    draft_generated: bool = False


class Draft(BaseModel):
    id: str
    user_id: str
    email_id: str | None = None
    draft_text: str | None = None
    status: str = "pending"              # pending | approved | sent | discarded
    edited_text: str | None = None
    generated_at: datetime | None = None
    sent_at: datetime | None = None
