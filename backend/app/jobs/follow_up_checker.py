"""
Follow-up checker — Phase 5.

Called hourly by the scheduler for every active user.
Finds overdue follow-ups, increments their reminder count, and logs them.
(Push / voice alerts will be wired in Phase 7 polish.)
"""

import logging
from datetime import datetime, timezone

from app import db

logger = logging.getLogger(__name__)


async def check_user_follow_ups(user_id: str) -> None:
    """
    1. Query follow_ups WHERE user_id = $1 AND follow_up_by < NOW() AND status = 'waiting'
    2. Increment reminder_count for each
    3. Log a warning per overdue item (voice alerts added in Phase 7)
    """
    overdue = await db.query(
        """
        SELECT id, to_email, subject, topic, follow_up_by, urgency, reminder_count
        FROM follow_ups
        WHERE user_id = $1
          AND status = 'waiting'
          AND follow_up_by < NOW()
        ORDER BY follow_up_by ASC
        """,
        user_id,
    )

    if not overdue:
        return

    for fu in overdue:
        follow_up_id = fu["id"]
        topic = fu.get("topic") or fu.get("subject") or "unknown topic"
        to_email = fu.get("to_email") or "unknown recipient"
        reminder_count = fu.get("reminder_count", 0)

        days_overdue = 0
        try:
            due = fu.get("follow_up_by")
            if due:
                days_overdue = max(0, (datetime.now(timezone.utc) - due).days)
        except Exception:
            pass

        logger.warning(
            "User %s: follow-up overdue — to=%s, topic=%s, %d days overdue (reminder #%d)",
            user_id, to_email, topic, days_overdue, reminder_count + 1,
        )

        # Increment reminder_count
        try:
            await db.execute(
                "UPDATE follow_ups SET reminder_count = reminder_count + 1 WHERE id = $1 AND user_id = $2",
                follow_up_id, user_id,
            )
        except Exception:
            logger.exception("Failed to update reminder_count for follow_up %s", follow_up_id)

    logger.info("User %s: %d overdue follow-up(s) processed", user_id, len(overdue))
