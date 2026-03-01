"""
Morning briefing generation job — Phase 4.
"""

import logging

from app.services.briefing_service import briefing_service

logger = logging.getLogger(__name__)


async def generate_briefing_for_user(user_id: str) -> None:
    try:
        await briefing_service.generate_for_user(user_id)
    except Exception:
        logger.exception("Failed to generate briefing for user %s", user_id)
