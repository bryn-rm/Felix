"""
Follow-up checker — Phase 5.
"""


async def check_user_follow_ups(user_id: str) -> None:
    """
    TODO Phase 5:
    1. Query follow_ups WHERE user_id = $1 AND follow_up_by < NOW() AND status = 'waiting'
    2. For each overdue item: notify user + increment reminder_count
    """
    raise NotImplementedError
