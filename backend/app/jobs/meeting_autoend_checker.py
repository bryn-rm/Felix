"""
Auto-end safety net for open meetings — Phase 7.

Finalizes meetings the client never explicitly stopped (tab closed, crash,
forgot to press stop). Driven by ``scheduler.check_stale_meetings`` every 5 min.
The invariant is that no meeting is left stuck ``'recording'``, and it holds for
**both** sources — each just measures idleness against what it actually produces.

Capture meetings — **silence timeout**: no new transcript segment for
``SILENCE_TIMEOUT_MINUTES``. A quiet meeting is an abandoned meeting; an active
one is left alone.

Manual assistant sessions produce no transcript at all, so silence would fire
instantly and guillotine a live in-person meeting. They are measured instead
against the things a manual session *does* write — a notes autosave (which
touches ``updated_at``) or an assist card — with the much longer
``MANUAL_IDLE_TIMEOUT_MINUTES``. Closing the tab or the sidebar leaves the row
open with no client that will ever call ``/end`` again, so without this branch
every abandoned session accumulated as a permanent "Open" row that stayed
ask-able forever.

    History: there was a second trigger — "past the linked calendar event's
    scheduled end". It ended meetings on ``now >= scheduled_end`` regardless of
    activity, which silently truncated every meeting that ran over its slot —
    exactly where the decisions and action items land. Gating it on idle (so it
    only acts once the meeting actually goes quiet) makes it a strict subset of
    the silence trigger: any idle-past-end meeting is already caught by silence
    alone. So it earned nothing and was removed — no per-sweep Calendar API call,
    and no way to guillotine a live overrun.

Before summarizing, the sweep runs the SAME ``check_monthly_ai_budget`` gate the
manual ``/meetings/{id}/end`` and ``/summarize`` routes run — the automatic path
is not a bypass. A user over their cap has the meeting finalized to ``'error'``
(terminal-but-retryable) WITHOUT summarization, rather than being silently
summarized or left stuck ``'recording'``.

Finalization otherwise goes through ``meeting_service.end_meeting`` — the SAME
path the REST ``/meetings/{id}/end`` route uses. That update is guarded to
``status='recording'``, so the sweep racing a real client ``/end`` (or a second
overlapping sweep) transitions the row — and spawns the summarizer — only once.

The sweep queries ``meetings WHERE status='recording'`` joined to ``settings``
for the gate flag **directly**. It deliberately does NOT use
``scheduler.get_active_users()``, which inner-joins ``google_connections`` and
would silently skip users who capture meetings without a connected Google account.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException

from app import db
from app.middleware.rate_limit import check_monthly_ai_budget
from app.models.meeting import MEETING_SOURCE_MANUAL
from app.services.meeting_service import meeting_service

logger = logging.getLogger(__name__)

# No new finalized segment for this long → assume the capture was abandoned.
SILENCE_TIMEOUT_MINUTES = 10

# A manual session's only activity signals are a notes autosave and an assist
# card, and an in-person meeting can easily run long stretches with neither
# (listening, not typing). Ending one early would drop the assistant mid-meeting,
# so this is deliberately far more patient than the capture timeout — it exists
# to reap genuinely abandoned sessions, not to bound live ones.
MANUAL_IDLE_TIMEOUT_MINUTES = 180


async def check_stale_meetings() -> int:
    """Finalize every stale recording meeting. Returns the count finalized."""
    rows = await db.query(
        # Scalar subqueries, not two LEFT JOINs: joining both child tables would
        # cross-multiply segments by assist items per meeting before the
        # aggregate collapsed them again.
        """
        SELECT m.id, m.user_id, m.started_at, m.source, m.updated_at,
               (SELECT MAX(created_at) FROM meeting_transcript_segments
                 WHERE meeting_id = m.id) AS last_segment_at,
               (SELECT MAX(created_at) FROM meeting_assist_items
                 WHERE meeting_id = m.id) AS last_assist_at
        FROM meetings m
        JOIN settings s ON s.user_id = m.user_id
        WHERE m.status = 'recording'
          AND s.meeting_capture_mode = TRUE
        """,
    )
    if not rows:
        return 0

    now = datetime.now(timezone.utc)
    finalized = 0
    for row in rows:
        try:
            if not _is_stale(row, now):
                continue
            user_id = row["user_id"]
            meeting_id = str(row["id"])

            # Same monthly-budget gate the manual /meetings/{id}/end and
            # /summarize routes run before spawning summarization. The automatic
            # path must NOT be a bypass — the invariant is that no path, manual
            # or automatic, spawns a summary without passing this gate.
            #
            # email=None on purpose: this sweep deliberately has no
            # google_connections dependency (so it still finalizes non-Google
            # users), so it can't resolve the address `_is_admin_email` needs. A
            # non-admin gets the identical check; an admin wrongly blocked here
            # just lands in 'error' and their one-click Retry (/summarize)
            # re-checks with their real email + admin cap and succeeds.
            try:
                await check_monthly_ai_budget(user_id, None)
            except HTTPException:
                # Over budget: finalize WITHOUT summarizing. Move out of
                # 'recording' (guarded → race-safe against a real /end) into
                # 'error' — a terminal-but-retryable state. Not 'processing'/
                # 'done' (no summary was produced), and not left 'recording'
                # forever. The detail page renders 'error' with a Retry that
                # re-runs /summarize once the cap resets.
                await db.execute(
                    "UPDATE meetings SET status = 'error', ended_at = NOW(), "
                    "updated_at = NOW() "
                    "WHERE id = $1 AND user_id = $2 AND status = 'recording'",
                    meeting_id, user_id,
                )
                logger.info(
                    "auto-end: user %s over budget; meeting %s finalized unsummarized",
                    user_id, meeting_id,
                )
                continue

            # end_meeting is idempotent (guarded to status='recording'); a client
            # /end that just landed simply makes this a no-op.
            result = await meeting_service.end_meeting(user_id, meeting_id)
            if result:
                finalized += 1
                logger.info("auto-ended stale meeting %s", meeting_id)
        except Exception:
            # One bad meeting must not abort the sweep for the rest.
            logger.warning(
                "auto-end check failed for meeting %s", row.get("id"), exc_info=True
            )
    return finalized


def _is_stale(row: dict, now: datetime) -> bool:
    """A recording meeting is stale once it goes quiet for its source's timeout.

    Capture meetings measure idleness from the last finalized transcript segment,
    so an actively-transcribing meeting — even one running past its scheduled
    slot — is never truncated. Manual sessions have no segments, so they measure
    from the newest of their own signals instead: a notes autosave (``updated_at``)
    or an assist card.

    Both fall back to ``started_at`` when nothing has landed yet, so a
    freshly-started meeting isn't ended on its first sweep."""
    if row.get("source") == MEETING_SOURCE_MANUAL:
        candidates = [row.get("last_assist_at"), row.get("updated_at")]
        timeout_minutes = MANUAL_IDLE_TIMEOUT_MINUTES
    else:
        candidates = [row.get("last_segment_at")]
        timeout_minutes = SILENCE_TIMEOUT_MINUTES

    stamps = [_as_utc(c) for c in candidates if c is not None]
    started = row.get("started_at")
    if not stamps and started is not None:
        stamps = [_as_utc(started)]
    if not stamps:
        return False
    idle_seconds = (now - max(stamps)).total_seconds()
    return idle_seconds >= timeout_minutes * 60


def _as_utc(value: datetime) -> datetime:
    """Coerce a naive or offset datetime to an aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
