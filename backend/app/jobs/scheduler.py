"""
APScheduler setup — all background jobs registered here.

Jobs are designed to be multi-user from day one: every job calls
get_active_users() and iterates over all users with a connected Google
account. Users are completely independent — failures for one user must
never affect others.
"""

import asyncio
import logging
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import db

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


# ---------------------------------------------------------------------------
# Active user loader
# ---------------------------------------------------------------------------

async def get_active_users() -> list[dict]:
    """Return all users who have a connected Google account + their settings."""
    return await db.query(
        "SELECT s.user_id, s.timezone, s.briefing_time "
        "FROM settings s "
        "JOIN google_connections g USING (user_id)"
    )


# ---------------------------------------------------------------------------
# Inbox sync — every 2 minutes
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("interval", minutes=2, id="sync_all_inboxes")
async def sync_all_inboxes():
    """Poll Gmail for new emails for every connected user."""
    try:
        users = await get_active_users()
        await asyncio.gather(
            *[_sync_user_inbox(u["user_id"]) for u in users],
            return_exceptions=True,
        )
    except Exception:
        logger.exception("sync_all_inboxes failed")


async def _sync_user_inbox(user_id: str):
    # TODO Phase 2: import and call inbox_sync.sync_user_inbox
    pass


# ---------------------------------------------------------------------------
# Follow-up checker — every hour
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("interval", hours=1, id="check_all_follow_ups")
async def check_all_follow_ups():
    """Alert users about overdue follow-ups."""
    try:
        users = await get_active_users()
        await asyncio.gather(
            *[_check_user_follow_ups(u["user_id"]) for u in users],
            return_exceptions=True,
        )
    except Exception:
        logger.exception("check_all_follow_ups failed")


async def _check_user_follow_ups(user_id: str):
    # TODO Phase 5: import and call follow_up_checker.check_user_follow_ups
    pass


# ---------------------------------------------------------------------------
# Morning briefing — check every 5 minutes, fire at user's configured time
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("interval", minutes=5, id="check_morning_briefings")
async def check_morning_briefings():
    """Trigger morning briefing generation when a user's configured time arrives."""
    try:
        users = await get_active_users()
        for user in users:
            await _maybe_generate_briefing(user)
    except Exception:
        logger.exception("check_morning_briefings failed")


async def _maybe_generate_briefing(user: dict):
    tz = pytz.timezone(user.get("timezone") or "Europe/London")
    user_now = datetime.now(tz)
    briefing_time = user.get("briefing_time") or "07:30"  # stored as "HH:MM"

    if user_now.strftime("%H:%M") == briefing_time:
        already_done = await db.query_one(
            "SELECT id FROM briefings WHERE user_id = $1 AND date = CURRENT_DATE",
            user["user_id"],
        )
        if not already_done:
            asyncio.create_task(_generate_briefing_for_user(user["user_id"]))


async def _generate_briefing_for_user(user_id: str):
    # TODO Phase 4: import and call briefing_service.generate_for_user
    pass


# ---------------------------------------------------------------------------
# Nightly relationship refresh — 11pm every night
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("cron", hour=23, minute=0, id="refresh_all_relationships")
async def refresh_all_relationships():
    try:
        users = await get_active_users()
        await asyncio.gather(
            *[_refresh_user_relationships(u["user_id"]) for u in users],
            return_exceptions=True,
        )
    except Exception:
        logger.exception("refresh_all_relationships failed")


async def _refresh_user_relationships(user_id: str):
    # TODO Phase 6: import and call relationship_engine.refresh_user
    pass


# ---------------------------------------------------------------------------
# Weekly style re-analysis — Sunday 10pm
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("cron", day_of_week="sun", hour=22, id="refresh_all_style_profiles")
async def refresh_all_style_profiles():
    try:
        users = await get_active_users()
        for user in users:
            await _refresh_user_style(user["user_id"])
    except Exception:
        logger.exception("refresh_all_style_profiles failed")


async def _refresh_user_style(user_id: str):
    # TODO Phase 2: fetch sent emails + call style_profiler.build_profile + persist
    pass
