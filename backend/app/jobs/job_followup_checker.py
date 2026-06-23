"""
Job follow-up checker — Job Search Mode.

Called hourly by the scheduler for every active user. Gated + fail-closed:
does nothing unless the user enabled ``settings.job_search_mode``.

v1 is board-badge (pull), not push: the badge surfaces on the /jobs board the
next time the user opens it (list_board derives ``is_due`` from next_action_at).
This checker maintains/observes that due state and logs overdue actions so the
signal is visible in server logs; a push surface can come later.
"""

import logging

from app import db

logger = logging.getLogger(__name__)


async def check_user_job_followups(user_id: str) -> None:
    # Fail closed — skip entirely unless the flag is explicitly on.
    settings_row = await db.query_one(
        "SELECT job_search_mode FROM settings WHERE user_id = $1", user_id,
    )
    if not (settings_row and settings_row.get("job_search_mode")):
        return

    due = await db.query(
        """
        SELECT id, company, role_title, next_action, next_action_at, status
        FROM job_applications
        WHERE user_id = $1
          AND status NOT IN ('rejected','withdrawn','accepted')
          AND next_action_at IS NOT NULL
          AND next_action_at < NOW()
        ORDER BY next_action_at ASC
        """,
        user_id,
    )
    if not due:
        return

    for job in due:
        logger.info(
            "User %s: job action due — %s @ %s: %s",
            user_id,
            job.get("role_title") or "?",
            job.get("company") or "?",
            job.get("next_action") or "follow up",
        )
    logger.info("User %s: %d job action(s) due", user_id, len(due))
