"""
Morning briefing generation job — Phase 4.

Called by the scheduler via asyncio.create_task() when a user's configured
briefing time arrives. Must never raise — exceptions are caught and logged.
"""

import logging

logger = logging.getLogger(__name__)


async def generate_briefing_for_user(user_id: str) -> None:
    """
    Orchestrate morning briefing generation for a single user.

    Calls briefing_service.generate_for_user() which:
      1. Gathers context (emails + calendar + follow-ups + relationship alerts)
      2. Generates text via Claude Sonnet
      3. Converts to audio via ElevenLabs
      4. Stores in Supabase Storage
      5. Upserts into the briefings table (idempotent)

    Any exception is caught and logged — this is a fire-and-forget task.
    """
    try:
        from app.services.briefing_service import briefing_service
        await briefing_service.generate_for_user(user_id)
        logger.info("Morning briefing generated for user %s", user_id)
    except Exception:
        logger.exception("Failed to generate briefing for user %s", user_id)
