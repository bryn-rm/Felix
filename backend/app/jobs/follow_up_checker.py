"""
Follow-up checker — Phase 5.
"""

import logging

from app import db
from app.services.follow_up_engine import follow_up_engine

logger = logging.getLogger(__name__)


async def check_user_follow_ups(user_id: str) -> None:
    """Increment reminders for overdue follow-ups and stamp first-notified time."""
    overdue = await follow_up_engine.check_overdue(user_id)
    if not overdue:
        return

    for item in overdue:
        await db.execute(
            """
            UPDATE follow_ups
            SET reminder_count = reminder_count + 1
            WHERE id = $1 AND user_id = $2
            """,
            item["id"],
            user_id,
        )

    logger.info("User %s has %d overdue follow-up(s)", user_id, len(overdue))
