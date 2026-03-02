"""
Nightly relationship refresh job — Phase 6.

Called by the scheduler at 11pm for every active user.
Delegates to relationship_engine.refresh_user() which rebuilds all
contact profiles from email + meeting history.
"""

import logging

logger = logging.getLogger(__name__)


async def refresh_user_relationships(user_id: str) -> None:
    """
    Rebuild all contact profiles for a user from their email + meeting history.
    Any exception is caught and logged — never propagated to the scheduler.
    """
    try:
        from app.services.relationship_engine import relationship_engine
        await relationship_engine.refresh_user(user_id)
        logger.info("Relationship profiles refreshed for user %s", user_id)
    except Exception:
        logger.exception("Failed to refresh relationships for user %s", user_id)
