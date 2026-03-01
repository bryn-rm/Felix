"""
Inbox sync job — Phase 2.

Called every 2 minutes per user by the scheduler.
"""


async def sync_user_inbox(user_id: str) -> None:
    """
    TODO Phase 2:
    1. Load Google credentials for user
    2. Fetch new unprocessed emails from Gmail
    3. Run each email through triage (Claude Haiku)
    4. Persist to emails table with triage metadata
    5. Apply Gmail labels (felix/action-required, etc.)
    6. For action_required: generate draft reply (Claude Sonnet, streaming)
    7. For vip: notify user
    8. Update contact profiles inline
    """
    raise NotImplementedError
