"""
Nightly relationship refresh job — Phase 6.
"""

import logging

from app.services.relationship_engine import relationship_engine

logger = logging.getLogger(__name__)


async def refresh_user_relationships(user_id: str) -> None:
    """Rebuild all relationship profiles for one user."""
    try:
        await relationship_engine.refresh_user(user_id)
    except Exception:
        logger.exception("Failed relationship refresh for user %s", user_id)
