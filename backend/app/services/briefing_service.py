"""
Morning briefing generator — Phase 4.
"""

from app.services.ai_service import ai_service
from app.services.voice_service import voice_service


class BriefingService:

    async def gather_context(self, user_id: str) -> dict:
        """
        TODO Phase 4: collect priority emails, today's calendar, overdue
        follow-ups, and relationship alerts for the briefing prompt.
        """
        raise NotImplementedError

    async def generate_for_user(self, user_id: str) -> dict:
        """
        TODO Phase 4: build context, call Claude for text, convert to audio
        via ElevenLabs, store in briefings table.
        """
        raise NotImplementedError


briefing_service = BriefingService()
