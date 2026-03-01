"""
Follow-up detection + tracking engine — Phase 5.
"""

from app.services.ai_service import ai_service


class FollowUpEngine:

    async def process_sent_email(self, user_id: str, sent_email: dict) -> dict | None:
        """
        TODO Phase 5: scan a sent email for follow-up signals and create a
        follow_ups row if needed.
        """
        raise NotImplementedError

    async def check_overdue(self, user_id: str) -> list[dict]:
        """
        TODO Phase 5: return follow-ups past their follow_up_by deadline.
        """
        raise NotImplementedError


follow_up_engine = FollowUpEngine()
