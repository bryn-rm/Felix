from pydantic import BaseModel
from datetime import datetime


class FollowUp(BaseModel):
    id: str
    user_id: str
    email_id: str | None = None
    to_email: str | None = None
    subject: str | None = None
    topic: str | None = None
    sent_at: datetime | None = None
    follow_up_by: datetime | None = None
    status: str = "waiting"             # waiting | replied | followed_up | closed
    # Note: rows inserted by follow_up_engine always use status='waiting'.
    # The schema default is also 'waiting'. 'pending' is not used in this table.
    urgency: str | None = None
    auto_draft: str | None = None
    reminder_count: int = 0
    created_at: datetime | None = None
