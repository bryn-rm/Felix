"""
Voice intent router — Phase 3.

Takes a parsed intent dict (from ai_service.parse_voice_intent) and resolves
it to a natural-language response string that Felix speaks back.

All DB reads come from the local Supabase tables — no live Gmail calls for
read-only intents, keeping latency low.
"""

import logging
from datetime import date, datetime, timedelta, timezone

from app import db
from app.middleware.auth import get_google_credentials
from app.services.calendar_service import CalendarService
from app.services.gmail_service import GmailService

logger = logging.getLogger(__name__)


async def route_intent(
    intent: dict,
    user_id: str,
    gmail: GmailService | None,
    user_name: str,
) -> str:
    """
    Dispatch a parsed voice intent to the right handler.
    Returns a spoken-language sentence (or short paragraph) for TTS.
    """
    name = intent.get("intent", "")

    handlers = {
        "read_emails":        _read_emails,
        "whats_today":        _whats_today,
        "whos_waiting":       _whos_waiting,
        "summarise_inbox":    _summarise_inbox,
        "reply_to":           _reply_to,
        "compose_new":        _compose_new,
        "schedule_meeting":   _schedule_meeting,
        "follow_up_with":     _follow_up_with,
        "start_meeting_notes": _start_meeting_notes,
        "check_calendar":     _check_calendar,
        "general_question":   _general_question,
    }

    handler = handlers.get(name)
    if handler is None:
        return "I didn't quite catch that. Could you rephrase?"

    try:
        return await handler(intent, user_id, gmail, user_name)
    except Exception:
        logger.exception("Intent handler '%s' failed for user %s", name, user_id)
        return "I ran into a problem handling that. Please try again."


# ---------------------------------------------------------------------------
# Intent handlers
# ---------------------------------------------------------------------------

async def _read_emails(intent, user_id, gmail, user_name) -> str:
    rows = await db.query(
        """
        SELECT subject, from_name, from_email, category, urgency
        FROM emails
        WHERE user_id = $1
          AND category IN ('action_required', 'vip')
        ORDER BY received_at DESC
        LIMIT 5
        """,
        user_id,
    )
    if not rows:
        return "Your priority inbox is clear. No action-required emails right now."

    count = len(rows)
    lines = [f"You have {count} priority email{'s' if count != 1 else ''}."]
    for i, e in enumerate(rows, 1):
        sender = e.get("from_name") or e.get("from_email") or "someone"
        subject = e.get("subject") or "no subject"
        lines.append(f"{i}. From {sender}: {subject}.")
    return " ".join(lines)


async def _whats_today(intent, user_id, gmail, user_name) -> str:
    pending_drafts = await db.query_one(
        "SELECT COUNT(*) AS n FROM drafts WHERE user_id = $1 AND status = 'pending'",
        user_id,
    )
    action_emails = await db.query_one(
        "SELECT COUNT(*) AS n FROM emails WHERE user_id = $1 AND category = 'action_required'",
        user_id,
    )
    overdue_followups = await db.query_one(
        "SELECT COUNT(*) AS n FROM follow_ups WHERE user_id = $1 AND status = 'waiting' AND follow_up_by < NOW()",
        user_id,
    )

    drafts = (pending_drafts or {}).get("n", 0)
    actions = (action_emails or {}).get("n", 0)
    followups = (overdue_followups or {}).get("n", 0)

    if not drafts and not actions and not followups:
        return f"Good news, {user_name}. You're all caught up — nothing pending right now."

    parts = []
    if drafts:
        parts.append(f"{drafts} draft {'reply' if drafts == 1 else 'replies'} awaiting approval")
    if actions:
        parts.append(f"{actions} email{'s' if actions != 1 else ''} requiring action")
    if followups:
        parts.append(f"{followups} overdue follow-up{'s' if followups != 1 else ''}")

    return f"{user_name}, here's your status: " + ", and ".join(parts) + "."


async def _whos_waiting(intent, user_id, gmail, user_name) -> str:
    rows = await db.query(
        """
        SELECT to_email, subject, follow_up_by
        FROM follow_ups
        WHERE user_id = $1 AND status = 'waiting'
        ORDER BY follow_up_by ASC NULLS LAST
        LIMIT 5
        """,
        user_id,
    )
    if not rows:
        return "No one is waiting on you right now. You're all caught up."

    count = len(rows)
    lines = [f"You have {count} outstanding {'item' if count == 1 else 'items'}."]
    for r in rows:
        to = r.get("to_email") or "someone"
        subject = r.get("subject") or "no subject"
        lines.append(f"{to}, regarding {subject}.")
    return " ".join(lines)


async def _summarise_inbox(intent, user_id, gmail, user_name) -> str:
    rows = await db.query(
        "SELECT category, COUNT(*) AS n FROM emails WHERE user_id = $1 GROUP BY category",
        user_id,
    )
    if not rows:
        return "Your inbox hasn't been synced yet. Check back shortly."

    counts = {r["category"]: r["n"] for r in rows if r.get("category")}
    total = sum(counts.values())
    action = counts.get("action_required", 0)
    vip = counts.get("vip", 0)
    fyi = counts.get("fyi", 0)
    newsletter = counts.get("newsletter", 0)

    parts = [f"You have {total} processed email{'s' if total != 1 else ''} in Felix."]
    if action:
        parts.append(f"{action} need{'s' if action == 1 else ''} action.")
    if vip:
        parts.append(f"{vip} from VIP contacts.")
    if fyi:
        parts.append(f"{fyi} {'is' if fyi == 1 else 'are'} FYI.")
    if newsletter:
        parts.append(f"{newsletter} newsletter{'s' if newsletter != 1 else ''}.")
    return " ".join(parts)


async def _reply_to(intent, user_id, gmail, user_name) -> str:
    recipient = intent.get("recipient")
    if not recipient:
        return "Who would you like to reply to? You can say their name or email address."
    return (
        f"I'll prepare a reply to {recipient}. "
        "Check your drafts in the Felix app to review and send it."
    )


async def _compose_new(intent, user_id, gmail, user_name) -> str:
    recipient = intent.get("recipient", "")
    topic = intent.get("topic", "")
    if not recipient:
        return "Who would you like to email? Just say their name or email address."
    msg = f"I'll draft a new email to {recipient}"
    if topic:
        msg += f" about {topic}"
    return msg + ". You can review and send it from the Felix app."


async def _schedule_meeting(intent, user_id, gmail, user_name) -> str:
    recipient = intent.get("recipient", "")
    timeframe = intent.get("timeframe", "")
    duration = intent.get("duration_minutes")

    parts = ["I'll help you schedule a meeting"]
    if recipient:
        parts.append(f"with {recipient}")
    if timeframe:
        parts.append(timeframe)
    if duration:
        parts.append(f"for {duration} minutes")

    return " ".join(parts) + ". Full calendar scheduling is coming in the next update."


async def _follow_up_with(intent, user_id, gmail, user_name) -> str:
    recipient = intent.get("recipient", "")
    topic = intent.get("topic", "")
    if not recipient:
        return "Who would you like to follow up with?"
    msg = f"I'll create a follow-up reminder for {recipient}"
    if topic:
        msg += f" about {topic}"
    return msg + "."


async def _start_meeting_notes(intent, user_id, gmail, user_name) -> str:
    return (
        "Meeting notes mode is ready. "
        "Use the Felix app to start recording, and I'll transcribe and summarise everything when you're done."
    )


def _resolve_timeframe(timeframe: str) -> tuple[datetime, datetime]:
    """Parse a natural-language timeframe into (start, end) datetimes in UTC."""
    today = date.today()
    tf = (timeframe or "").lower().strip()

    if tf in ("today", ""):
        start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=1)
    elif tf in ("tomorrow",):
        start = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=1)
    elif tf in ("this week",):
        # Monday of current week through Sunday
        monday = today - timedelta(days=today.weekday())
        start = datetime.combine(monday, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=7)
    elif tf in ("next week",):
        next_monday = today + timedelta(days=7 - today.weekday())
        start = datetime.combine(next_monday, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=7)
    else:
        # Try to match a weekday name (e.g. "tuesday", "on wednesday")
        day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        target_day = None
        for i, name in enumerate(day_names):
            if name in tf:
                target_day = i
                break

        if target_day is not None:
            days_ahead = (target_day - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7  # next occurrence if today
            target_date = today + timedelta(days=days_ahead)
            start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
            end = start + timedelta(days=1)
        else:
            # Default to today
            start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
            end = start + timedelta(days=1)

    return start, end


async def _check_calendar(intent, user_id, gmail, user_name) -> str:
    timeframe = intent.get("timeframe") or "today"

    try:
        creds = await get_google_credentials(user_id)
    except Exception:
        return "I can't access your calendar right now. Make sure your Google account is connected in Settings."

    cal = CalendarService(creds)
    start, end = _resolve_timeframe(timeframe)

    try:
        events = await cal.get_events(
            time_min=start.isoformat(),
            time_max=end.isoformat(),
        )
    except Exception:
        logger.exception("Calendar fetch failed for user %s", user_id)
        return "I had trouble fetching your calendar. Please try again."

    if not events:
        return f"You have no meetings {timeframe}. Your schedule is clear."

    count = len(events)
    label = timeframe if timeframe != "today" else "today"
    lines = [f"You have {count} {'meeting' if count == 1 else 'meetings'} {label}."]

    for i, ev in enumerate(events, 1):
        title = ev.get("title") or ev.get("summary") or "Untitled meeting"
        start_time = ev.get("start_time") or ev.get("start", "")
        # Format time for speech
        if isinstance(start_time, str) and "T" in start_time:
            try:
                dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                time_str = dt.strftime("%-I:%M %p").lower()
            except (ValueError, TypeError):
                time_str = ""
        elif isinstance(start_time, datetime):
            time_str = start_time.strftime("%-I:%M %p").lower()
        else:
            time_str = ""

        if time_str:
            lines.append(f"{i}. {title} at {time_str}.")
        else:
            lines.append(f"{i}. {title}.")

    return " ".join(lines)


async def _general_question(intent, user_id, gmail, user_name) -> str:
    from app.services.ai_service import ai_service

    transcript = intent.get("raw_transcript", "")
    if not transcript:
        return "I didn't quite catch that. Could you say that again?"

    try:
        return await ai_service.answer_general_voice_question(transcript, user_name)
    except Exception:
        logger.exception("General question handler failed for user %s", user_id)
        return "I'm not sure how to help with that. You can ask me to check your emails, calendar, or follow-ups."
