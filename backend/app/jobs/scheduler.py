"""
APScheduler setup — all background jobs registered here.

Every job calls get_active_users() and iterates over all users who have a
connected Google account. Users are completely independent — an exception for
one user is caught and logged, never allowed to cancel the run for others.
"""

import asyncio
import functools
import logging
from datetime import datetime, time as dt_time

import pytz

from app.services.timezone_utils import local_date_for_user
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import db
from app.utils.background import spawn

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def locked_job(fn):
    """Serialize a job across processes with a Postgres advisory lock.

    APScheduler runs in-process: a second Railway replica (or an overlapping
    old/new instance during a deploy) would otherwise double-run every job —
    including ones with user-visible external effects (digest emails, weekly
    reviews, meeting-prep emails, Gmail labeling). The lock is keyed on the
    job's function name; a concurrent holder makes this run a no-op.

    Apply BELOW @scheduler.scheduled_job so the scheduler registers the
    locked wrapper. Locks are taken on db's dedicated lock connection, never
    the pool: interval jobs share one process-start anchor, so ~10 fire
    together at the hourly tick — holding a pooled connection per lock for
    each job's duration would exhaust max_size=10 and deadlock every job
    body waiting for a second connection.
    """
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        key = f"felix_job:{fn.__name__}"
        if not await db.try_advisory_lock(key):
            logger.info(
                "Job %s skipped — another instance holds the lock", fn.__name__
            )
            return
        try:
            return await fn(*args, **kwargs)
        finally:
            try:
                await db.advisory_unlock(key)
            except Exception:
                # Don't mask the job's own outcome; a dropped lock session
                # released server-side anyway.
                logger.exception("advisory unlock failed for %s", fn.__name__)
    return wrapper


# ---------------------------------------------------------------------------
# Active user loader
# ---------------------------------------------------------------------------

async def get_active_users() -> list[dict]:
    """
    Return every user who has a connected Google account, along with the
    settings fields needed for job scheduling (incl. digest_mode/digest_times).
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
@locked_job
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
@locked_job
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
# Job follow-up checker — every hour (Job Search Mode; per-user gated)
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("interval", hours=1, id="check_all_job_followups")
@locked_job
async def check_all_job_followups() -> None:
    """Flag due job-board actions for users with Job Search Mode on."""
    try:
        users = await get_active_users()
        if not users:
            return
        results = await asyncio.gather(
            *[_check_user_job_followups(u["user_id"]) for u in users],
            return_exceptions=True,
        )
        for user, result in zip(users, results):
            if isinstance(result, Exception):
                logger.error("Job follow-up check failed for user %s: %s",
                             user["user_id"], result)
    except Exception:
        logger.exception("check_all_job_followups outer error")


async def _check_user_job_followups(user_id: str) -> None:
    from app.jobs.job_followup_checker import check_user_job_followups
    await check_user_job_followups(user_id)


# ---------------------------------------------------------------------------
# Morning briefing — check every 5 minutes, fire at each user's configured time
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("interval", minutes=5, id="check_morning_briefings")
@locked_job
async def check_morning_briefings() -> None:
    """Trigger morning briefing generation when a user's configured time arrives."""
    try:
        users = await get_active_users()
        if not users:
            return
        results = await asyncio.gather(
            *[_maybe_generate_briefing(u) for u in users],
            return_exceptions=True,
        )
        for user, result in zip(users, results):
            if isinstance(result, Exception):
                logger.error(
                    "Briefing check failed for user %s: %s", user["user_id"], result
                )
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

    # Interval jobs fire at process_start + n×5min, so the minute they land on
    # is arbitrary — an exact HH:MM equality would only ever match by luck.
    # Instead treat the briefing as due when the target time fell within the
    # last 5 minutes (the job interval). Modulo a day so a target just before
    # midnight still matches a run just after. The briefings (user_id, date)
    # dedup below prevents double-fires within the window.
    target_minutes = int(target[:2]) * 60 + int(target[3:5])
    now_minutes = user_now.hour * 60 + user_now.minute
    if (now_minutes - target_minutes) % 1440 < 5:
        local_today = local_date_for_user(tz_name)
        already_done = await db.query_one(
            "SELECT id FROM briefings WHERE user_id = $1 AND date = $2",
            user["user_id"],
            local_today,
        )
        if not already_done:
            spawn(_generate_briefing_for_user(user["user_id"]), name="briefing_generate")


async def _generate_briefing_for_user(user_id: str) -> None:
    from app.jobs.briefing_generator import generate_briefing_for_user
    await generate_briefing_for_user(user_id)


# ---------------------------------------------------------------------------
# Nightly relationship refresh — 11pm every night
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("cron", hour=23, minute=0, id="refresh_all_relationships")
@locked_job
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
@locked_job
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
# Digest mode — every 30 minutes
# Fire for users where digest_mode=True and their local time matches one of
# their configured digest_times (e.g. ["08:00", "12:00", "18:00"]).
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("interval", minutes=30, id="check_digest_mode")
@locked_job
async def check_digest_mode() -> None:
    """
    Send an email digest to users who have digest_mode enabled when their
    configured digest time arrives (compared in their local timezone).
    """
    try:
        users = await get_active_users()
        digest_users = [u for u in users if u.get("digest_mode")]
        if not digest_users:
            return
        results = await asyncio.gather(
            *[_maybe_send_digest(u) for u in digest_users],
            return_exceptions=True,
        )
        for user, result in zip(digest_users, results):
            if isinstance(result, Exception):
                logger.error("Digest check failed for user %s: %s", user["user_id"], result)
    except Exception:
        logger.exception("check_digest_mode outer error")


async def _maybe_send_digest(user: dict) -> None:
    """
    Check whether any of this user's digest_times matches their current local time
    (within the 30-minute job window). If so, send a digest notification.
    """
    tz_name: str = user.get("timezone") or "Europe/London"
    try:
        tz = pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        tz = pytz.UTC

    user_now = datetime.now(tz)
    current_hhmm = user_now.strftime("%H:%M")

    digest_times: list[str] = user.get("digest_times") or []
    if not digest_times:
        return

    # Round current time down to the nearest 30-minute mark for comparison
    # e.g. 08:14 → "08:00", 12:31 → "12:30"
    rounded_minute = (user_now.minute // 30) * 30
    rounded_hhmm = f"{user_now.hour:02d}:{rounded_minute:02d}"

    # Check if any configured digest time falls in this 30-minute slot
    should_send = any(t.strip()[:5] == rounded_hhmm for t in digest_times)
    if not should_send:
        return

    # Claim the (user, date, slot) marker before sending. Interval spacing alone
    # is not idempotent: a redeploy mid-slot resets the interval anchor, so the
    # new process can land in the same 30-min slot and send a second digest.
    # ON CONFLICT DO NOTHING returns no row when another run already claimed it.
    claimed = await db.query_one(
        "INSERT INTO digest_sends (user_id, slot_date, slot_time) "
        "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING RETURNING user_id",
        user["user_id"],
        local_date_for_user(tz_name),
        rounded_hhmm,
    )
    if not claimed:
        return

    logger.info(
        "Digest time reached for user %s at %s (local: %s)",
        user["user_id"], rounded_hhmm, current_hhmm,
    )
    spawn(_send_digest_for_user(user["user_id"]), name="digest_send")


async def _send_digest_for_user(user_id: str) -> None:
    """Send an email digest to the user via Gmail (digest_sender)."""
    try:
        from app.jobs.digest_sender import send_digest_for_user
        await send_digest_for_user(user_id)
        logger.info("Digest sent for user %s", user_id)
    except Exception:
        logger.exception("Failed to send digest for user %s", user_id)


# ---------------------------------------------------------------------------
# Weekly review email — Sunday at 6pm in UTC (each user's own TZ varies but
# the review window is a rolling 7 days so exact timing is not critical).
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("cron", day_of_week="sun", hour=18, minute=0, id="send_weekly_reviews")
@locked_job
async def send_weekly_reviews() -> None:
    """Send weekly review emails to all active users."""
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
    """Send a weekly review email for a single user."""
    try:
        from app.jobs.digest_sender import send_weekly_review_for_user
        await send_weekly_review_for_user(user_id)
        logger.info("Weekly review sent for user %s", user_id)
    except Exception:
        logger.exception("Failed to send weekly review for user %s", user_id)


# ---------------------------------------------------------------------------
# Meeting prep — every 5 minutes
# Generates pre-meeting context cards for events starting in the next 20 min.
# Idempotent on (user_id, event_id) so the overlapping window doesn't dupe.
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("interval", minutes=5, id="generate_meeting_preps")
@locked_job
async def generate_meeting_preps() -> None:
    try:
        users = await get_active_users()
        if not users:
            return
        results = await asyncio.gather(
            *[_generate_meeting_preps_for_user(u["user_id"]) for u in users],
            return_exceptions=True,
        )
        for user, result in zip(users, results):
            if isinstance(result, Exception):
                logger.error("Meeting prep generation failed for user %s: %s", user["user_id"], result)
    except Exception:
        logger.exception("generate_meeting_preps outer error")


async def _generate_meeting_preps_for_user(user_id: str) -> None:
    """Generate prep cards for any events starting in the next 20 minutes.

    Reads `settings.meeting_prep_mode` to decide whether to also push the card
    via email. Always writes to `meeting_preps` so the in-app surface has it.
    """
    try:
        # Skip users who opted out
        prefs = await db.query_one(
            "SELECT meeting_prep_mode FROM settings WHERE user_id = $1",
            user_id,
        ) or {}
        mode = prefs.get("meeting_prep_mode") or "in_app_only"
        if mode == "off":
            return

        from datetime import timedelta as _td
        from app.middleware.auth import get_google_credentials
        from app.services.calendar_service import CalendarService
        from app.services.meeting_prep_service import (
            meeting_prep_service, _eligible_for_prep, _subject_for,
        )

        try:
            creds = await get_google_credentials(user_id)
        except Exception:
            return
        cal = CalendarService(creds)

        now = datetime.now(pytz.UTC)
        window_end = now + _td(minutes=20)
        events = await cal.get_events(
            time_min=now.isoformat(), time_max=window_end.isoformat(),
        )
        for event in events:
            if not _eligible_for_prep(event):
                continue
            existing = await db.query_one(
                "SELECT status, delivery_modes, event_title, event_start, "
                "content_html, content_text "
                "FROM meeting_preps WHERE user_id = $1 AND event_id = $2",
                user_id, event["id"],
            )
            if existing and existing.get("status") != "failed":
                # An existing row still needs an email retry if the user is in
                # an email mode and the email side hasn't been delivered yet
                # (transient Gmail send failure or in_app_only → email mode
                # upgrade after the row was generated). status='failed' falls
                # through to regenerate so a recovered Anthropic outage doesn't
                # leave the event stuck on the fallback stub.
                already_emailed = "email" in (existing.get("delivery_modes") or [])
                wants_email = mode in ("email_only", "both")
                can_retry = (
                    wants_email
                    and not already_emailed
                    and existing.get("status") == "generated"
                    and existing.get("content_html")
                )
                if can_retry:
                    spawn(_send_meeting_prep_email(user_id, {
                        "event_id":    event["id"],
                        "subject":     _subject_for(
                            existing.get("event_title"), existing.get("event_start"),
                        ),
                        "html":        existing.get("content_html"),
                        "text":        existing.get("content_text") or "",
                    }), name="meeting_prep_email_retry")
                continue  # don't regenerate — cached row is still authoritative

            prep = await meeting_prep_service.generate_for_event(user_id, event)

            # Optional email push — controlled by per-user setting
            if mode in ("email_only", "both"):
                spawn(_send_meeting_prep_email(user_id, prep), name="meeting_prep_email")
    except Exception:
        logger.exception("Failed to generate meeting preps for user %s", user_id)


async def _send_meeting_prep_email(user_id: str, prep: dict) -> None:
    """Push a prep card via Gmail. Fire-and-forget."""
    try:
        from app.config import settings as _app_settings
        from app.middleware.auth import get_google_credentials
        from app.services.gmail_service import GmailService
        from app.services.polish_service import _wrap_html_shell

        recipient = await db.query_one(
            "SELECT google_email FROM google_connections WHERE user_id = $1",
            user_id,
        )
        to_email = (recipient or {}).get("google_email")
        if not to_email or not prep.get("html"):
            return

        # The cached prep.html is the body only — wrap with the email shell
        # at send time so the in-app surface stays free of email chrome.
        email_html = _wrap_html_shell(prep["html"], _app_settings.FRONTEND_URL)

        creds = await get_google_credentials(user_id)
        gmail = GmailService(creds)
        await gmail.send_email(
            to=to_email,
            subject=prep["subject"],
            body=prep["text"] or "Pre-meeting prep — open Felix to view.",
            html_body=email_html,
        )
        await db.execute(
            "UPDATE meeting_preps SET status = 'sent', "
            "delivery_modes = ARRAY['email','in_app'] "
            "WHERE user_id = $1 AND event_id = $2",
            user_id, prep["event_id"],
        )
    except Exception:
        logger.exception("Failed to send meeting prep email for user %s", user_id)


# ---------------------------------------------------------------------------
# Meeting capture — auto-end stale recordings every 5 minutes
# Finalizes meetings the client never stopped (tab closed / crash / forgot).
# The checker queries status='recording' DIRECTLY (NOT get_active_users, which
# skips non-Google users); see meeting_autoend_checker for the trigger logic.
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("interval", minutes=5, id="check_stale_meetings")
@locked_job
async def check_stale_meetings() -> None:
    try:
        from app.jobs.meeting_autoend_checker import check_stale_meetings as _sweep
        n = await _sweep()
        if n:
            logger.info("Auto-ended %d stale meeting(s)", n)
    except Exception:
        logger.exception("check_stale_meetings failed")


# ---------------------------------------------------------------------------
# Expired OAuth nonce sweep — hourly
# Nonces are keyed per-attempt so abandoned ones accumulate unless swept.
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("interval", hours=1, id="sweep_expired_oauth_nonces")
@locked_job
async def sweep_expired_oauth_nonces() -> None:
    try:
        await db.execute("DELETE FROM oauth_nonces WHERE expires_at < NOW()")
        # Digest dedup markers only matter within their 30-min slot; keep a
        # couple of weeks for debugging, then drop so the table stays tiny.
        await db.execute(
            "DELETE FROM digest_sends WHERE slot_date < CURRENT_DATE - INTERVAL '14 days'"
        )
    except Exception:
        logger.exception("sweep_expired_oauth_nonces failed")


# ---------------------------------------------------------------------------
# Memory — session sweep every 5 minutes
# Idle chat sessions get summarised even without an explicit close.
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("interval", minutes=5, id="sweep_stale_sessions")
@locked_job
async def sweep_stale_sessions_job() -> None:
    try:
        from app.services.session_manager import sweep_stale_sessions
        n = await sweep_stale_sessions()
        if n:
            logger.info("Swept %d stale chat session(s)", n)
    except Exception:
        logger.exception("sweep_stale_sessions_job failed")


# ---------------------------------------------------------------------------
# Memory — hourly embedding backfill
# Episodes are created without embeddings when OpenAI is slow/unavailable;
# this pass fills them in so retrieval stays semantic over time.
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("interval", hours=1, id="backfill_episode_embeddings")
@locked_job
async def backfill_episode_embeddings() -> None:
    try:
        from app.services.memory_service import backfill_missing_embeddings
        filled = await backfill_missing_embeddings(limit=200)
        if filled:
            logger.info("Backfilled embeddings for %d episodes", filled)
    except Exception:
        logger.exception("backfill_episode_embeddings failed")


# ---------------------------------------------------------------------------
# Memory — daily pruning (03:15 UTC)
# Removes low-importance episodes older than 60 days.
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("cron", hour=3, minute=15, id="prune_memory_episodes")
@locked_job
async def prune_memory_episodes_job() -> None:
    try:
        from app.services.memory_service import prune_low_value_episodes
        removed = await prune_low_value_episodes()
        if removed:
            logger.info("Pruned %d low-value memory episodes", removed)
    except Exception:
        logger.exception("prune_memory_episodes_job failed")


# ---------------------------------------------------------------------------
# Memory — nightly profile extraction (02:30 UTC)
# Looks at each user's recent email activity and merges extracted facts
# into their profile. Never overwrites manual keys.
# ---------------------------------------------------------------------------

@scheduler.scheduled_job("cron", hour=2, minute=30, id="extract_user_profiles")
@locked_job
async def extract_user_profiles_job() -> None:
    try:
        users = await get_active_users()
        if not users:
            return
        results = await asyncio.gather(
            *[_extract_profile_for_user(u["user_id"]) for u in users],
            return_exceptions=True,
        )
        for user, result in zip(users, results):
            if isinstance(result, Exception):
                logger.warning(
                    "Profile extraction failed for user %s: %s",
                    user["user_id"], result,
                )
    except Exception:
        logger.exception("extract_user_profiles_job failed")


async def _extract_profile_for_user(user_id: str) -> None:
    """Build a recent-activity snippet and ask the extractor to merge facts."""
    try:
        rows = await db.query(
            """
            SELECT from_name, from_email, subject, snippet, topic
            FROM emails
            WHERE user_id = $1
              AND received_at > NOW() - INTERVAL '7 days'
            ORDER BY received_at DESC
            LIMIT 30
            """,
            user_id,
        )
        if not rows:
            return
        lines = []
        for r in rows:
            sender = r.get("from_name") or r.get("from_email") or "someone"
            subject = r.get("subject") or "(no subject)"
            snippet = (r.get("snippet") or "")[:240]
            topic = r.get("topic") or ""
            lines.append(f"- From {sender} | {subject} | topic={topic} | {snippet}")
        snippet = "\n".join(lines)

        from app.services.memory_service import extract_and_merge_profile
        await extract_and_merge_profile(user_id=user_id, activity_snippet=snippet)
    except Exception:
        logger.exception("profile extraction failed for user %s", user_id)
