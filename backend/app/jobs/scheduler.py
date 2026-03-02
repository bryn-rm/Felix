"""
APScheduler setup — all background jobs registered here.

Every job calls get_active_users() and iterates over all users who have a
connected Google account. Users are completely independent — an exception for
one user is caught and logged, never allowed to cancel the run for others.
"""

import asyncio
import logging
from datetime import datetime, time as dt_time

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import db

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


# ---------------------------------------------------------------------------
# Active user loader
# ---------------------------------------------------------------------------

async def get_active_users() -> list[dict]:
    """
    Return every user who has a connected Google account, along with the
    settings fields needed for job scheduling.
    """
    return await db.query(
        "SELECT s.user_id, s.timezone, s.briefing_time, s.digest_mode, s.digest_times "
        "FROM settings s "
        "JOIN google_connections g USING (user_id)"
    )


# ---------------------------------------------------------------------------
# Inbox sync — every 2 minutes
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("interval", minutes=2, id="sync_all_inboxes")
async def sync_all_inboxes() -> None:
    """Poll Gmail for new emails for every connected user in parallel."""
    try:
        users = await get_active_users()
        if not users:
            return
        results = await asyncio.gather(
            *[_sync_user_inbox(u["user_id"]) for u in users],
            return_exceptions=True,
        )
        for user, result in zip(users, results):
            if isinstance(result, Exception):
                logger.error("Inbox sync failed for user %s: %s", user["user_id"], result)
    except Exception:
        logger.exception("sync_all_inboxes outer error")


async def _sync_user_inbox(user_id: str) -> None:
    from app.jobs.inbox_sync import sync_user_inbox
    await sync_user_inbox(user_id)


# ---------------------------------------------------------------------------
# Follow-up checker — every hour
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("interval", hours=1, id="check_all_follow_ups")
async def check_all_follow_ups() -> None:
    """Alert users about overdue follow-ups."""
    try:
        users = await get_active_users()
        if not users:
            return
        results = await asyncio.gather(
            *[_check_user_follow_ups(u["user_id"]) for u in users],
            return_exceptions=True,
        )
        for user, result in zip(users, results):
            if isinstance(result, Exception):
                logger.error("Follow-up check failed for user %s: %s", user["user_id"], result)
    except Exception:
        logger.exception("check_all_follow_ups outer error")


async def _check_user_follow_ups(user_id: str) -> None:
    from app.jobs.follow_up_checker import check_user_follow_ups

    await check_user_follow_ups(user_id)


# ---------------------------------------------------------------------------
# Morning briefing — check every 5 minutes, fire at each user's configured time
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("interval", minutes=5, id="check_morning_briefings")
async def check_morning_briefings() -> None:
    """Trigger morning briefing generation when a user's configured time arrives."""
    try:
        users = await get_active_users()
        for user in users:
            await _maybe_generate_briefing(user)
    except Exception:
        logger.exception("check_morning_briefings outer error")


async def _maybe_generate_briefing(user: dict) -> None:
    tz_name: str = user.get("timezone") or "Europe/London"
    try:
        tz = pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        tz = pytz.UTC

    user_now = datetime.now(tz)

    # briefing_time comes back from asyncpg as a datetime.time object
    briefing_time = user.get("briefing_time")
    if isinstance(briefing_time, dt_time):
        target = briefing_time.strftime("%H:%M")
    elif briefing_time:
        target = str(briefing_time)[:5]  # "HH:MM:SS" → "HH:MM"
    else:
        target = "07:30"

    if user_now.strftime("%H:%M") == target:
        already_done = await db.query_one(
            "SELECT id FROM briefings WHERE user_id = $1 AND date = CURRENT_DATE",
            user["user_id"],
        )
        if not already_done:
            asyncio.create_task(_generate_briefing_for_user(user["user_id"]))


async def _generate_briefing_for_user(user_id: str) -> None:
    from app.jobs.briefing_generator import generate_briefing_for_user

    await generate_briefing_for_user(user_id)


# ---------------------------------------------------------------------------
# Nightly relationship refresh — 11pm every night
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("cron", hour=23, minute=0, id="refresh_all_relationships")
async def refresh_all_relationships() -> None:
    try:
        users = await get_active_users()
        results = await asyncio.gather(
            *[_refresh_user_relationships(u["user_id"]) for u in users],
            return_exceptions=True,
        )
        for user, result in zip(users, results):
            if isinstance(result, Exception):
                logger.error("Relationship refresh failed for user %s: %s", user["user_id"], result)
    except Exception:
        logger.exception("refresh_all_relationships outer error")


async def _refresh_user_relationships(user_id: str) -> None:
    from app.jobs.relationship_updater import refresh_user_relationships

    await refresh_user_relationships(user_id)


# ---------------------------------------------------------------------------
# Weekly style re-analysis — Sunday 10pm
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("cron", day_of_week="sun", hour=22, id="refresh_all_style_profiles")
async def refresh_all_style_profiles() -> None:
    try:
        users = await get_active_users()
        results = await asyncio.gather(
            *[_refresh_user_style(u["user_id"]) for u in users],
            return_exceptions=True,
        )
        for user, result in zip(users, results):
            if isinstance(result, Exception):
                logger.error("Style refresh failed for user %s: %s", user["user_id"], result)
    except Exception:
        logger.exception("refresh_all_style_profiles outer error")


async def _refresh_user_style(user_id: str) -> None:
    from app.jobs.inbox_sync import refresh_user_style_profile
    await refresh_user_style_profile(user_id)


# ---------------------------------------------------------------------------
# Digest mode sender — every 15 minutes (per-user configured digest times)
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("interval", minutes=15, id="send_scheduled_digests")
async def send_scheduled_digests() -> None:
    try:
        users = await get_active_users()
        if not users:
            return
        results = await asyncio.gather(
            *[_maybe_send_digest_for_user(u) for u in users],
            return_exceptions=True,
        )
        for user, result in zip(users, results):
            if isinstance(result, Exception):
                logger.error("Digest send failed for user %s: %s", user["user_id"], result)
    except Exception:
        logger.exception("send_scheduled_digests outer error")


async def _maybe_send_digest_for_user(user: dict) -> None:
    if not user.get("digest_mode"):
        return

    times = user.get("digest_times") or []
    if not times:
        return

    tz_name: str = user.get("timezone") or "Europe/London"
    try:
        tz = pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        tz = pytz.UTC

    now = datetime.now(tz).strftime("%H:%M")
    targets = [str(t)[:5] for t in times]
    if now in targets:
        from app.jobs.digest_sender import send_digest_for_user

        await send_digest_for_user(user["user_id"])


# ---------------------------------------------------------------------------
# Weekly review sender — Sunday evening
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("cron", day_of_week="sun", hour=18, minute=0, id="send_weekly_reviews")
async def send_weekly_reviews() -> None:
    try:
        users = await get_active_users()
        if not users:
            return
        results = await asyncio.gather(
            *[_send_weekly_review_for_user(u["user_id"]) for u in users],
            return_exceptions=True,
        )
        for user, result in zip(users, results):
            if isinstance(result, Exception):
                logger.error("Weekly review failed for user %s: %s", user["user_id"], result)
    except Exception:
        logger.exception("send_weekly_reviews outer error")


async def _send_weekly_review_for_user(user_id: str) -> None:
    from app.jobs.digest_sender import send_weekly_review_for_user

    await send_weekly_review_for_user(user_id)
