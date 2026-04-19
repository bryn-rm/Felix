"""
Voice intent router — Phase 3.

Takes a parsed intent dict (from ai_service.parse_voice_intent) and resolves
it to a natural-language response string that Felix speaks back.

Handlers fetch real data from Gmail / Calendar / the local Supabase tables
and (where appropriate) take real actions — drafting replies, creating
calendar events, etc. — using the current user's Google credentials.
"""

import logging
import re
from datetime import date, datetime, time, timedelta, timezone

import pytz

from app import db
from app.middleware.auth import get_google_credentials
from app.services import memory_service
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
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_USER_TIMEZONE = "Europe/London"
DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTH_NAME_TO_NUMBER = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


async def _get_user_timezone(user_id: str) -> str:
    settings = await db.query_one(
        "SELECT timezone FROM settings WHERE user_id = $1", user_id
    )
    return (settings or {}).get("timezone") or DEFAULT_USER_TIMEZONE


def _parse_clock_time(value) -> time | None:
    """Parse 'HH:MM' (24h) or '3pm'/'3:30pm' style strings into a time object."""
    if value is None:
        return None
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip().lower().replace(" ", "")
    if not s:
        return None

    suffix = None
    if s.endswith("am") or s.endswith("pm"):
        suffix = s[-2:]
        s = s[:-2]

    try:
        if ":" in s:
            hh, mm = s.split(":", 1)
            hour = int(hh)
            minute = int(mm)
        else:
            hour = int(s)
            minute = 0
    except ValueError:
        return None

    if suffix == "am":
        if hour == 12:
            hour = 0
    elif suffix == "pm":
        if hour < 12:
            hour += 12

    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return time(hour=hour, minute=minute)


def _format_time_for_speech(dt_value) -> str:
    """Format an ISO string or datetime as a spoken time like '2pm' or '4:30pm'."""
    if isinstance(dt_value, str) and dt_value:
        try:
            dt = datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return ""
    elif isinstance(dt_value, datetime):
        dt = dt_value
    else:
        return ""

    minute_part = "" if dt.minute == 0 else f":{dt.minute:02d}"
    hour = dt.hour % 12 or 12
    suffix = "am" if dt.hour < 12 else "pm"
    return f"{hour}{minute_part}{suffix}"


# ---------------------------------------------------------------------------
# Intent handlers
# ---------------------------------------------------------------------------

async def _read_emails(intent, user_id, gmail, user_name) -> str:
    rows = await db.query(
        """
        SELECT subject, from_name, from_email, snippet, urgency
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
    for e in rows:
        sender = e.get("from_name") or e.get("from_email") or "someone"
        subject = (e.get("subject") or "no subject").strip()
        snippet = (e.get("snippet") or "").strip().replace("\n", " ")
        if snippet:
            # Trim long snippets so the spoken response stays brief
            if len(snippet) > 120:
                snippet = snippet[:117].rsplit(" ", 1)[0] + "…"
            lines.append(f"From {sender}: {subject} — {snippet}")
        else:
            lines.append(f"From {sender}: {subject}.")
    return " ".join(lines)


async def _whats_today(intent, user_id, gmail, user_name) -> str:
    user_tz = await _get_user_timezone(user_id)

    try:
        creds = await get_google_credentials(user_id)
    except Exception:
        return (
            f"{user_name}, I can't reach your calendar right now. "
            "Reconnect Google in Settings and ask me again."
        )

    cal = CalendarService(creds)
    try:
        events = await cal.get_today_events(user_tz)
    except Exception:
        logger.exception("Today's calendar fetch failed for user %s", user_id)
        return "I had trouble fetching today's calendar. Please try again."

    timed_events = [e for e in events if not e.get("is_all_day")]

    if not timed_events:
        return f"You have no meetings today, {user_name}. Your schedule is clear."

    count = len(timed_events)
    lines = [f"You have {count} meeting{'s' if count != 1 else ''} today."]
    for ev in timed_events[:5]:
        title = (ev.get("title") or "Untitled meeting").strip()
        time_str = _format_time_for_speech(ev.get("start"))
        attendee_count = ev.get("attendee_count", 0)
        attendee_phrase = ""
        if attendee_count > 1:
            attendee_phrase = f" with {attendee_count} attendees"
        if time_str:
            lines.append(f"{title} at {time_str}{attendee_phrase}.")
        else:
            lines.append(f"{title}{attendee_phrase}.")
    return " ".join(lines)


async def _whos_waiting(intent, user_id, gmail, user_name) -> str:
    rows = await db.query(
        """
        SELECT from_name, from_email, subject, topic, snippet, received_at
        FROM emails
        WHERE user_id = $1
          AND category = 'waiting_on'
        ORDER BY received_at DESC
        LIMIT 5
        """,
        user_id,
    )
    if not rows:
        return "No one is waiting on you right now. You're all caught up."

    count = len(rows)
    lines = [f"You owe {count} {'reply' if count == 1 else 'replies'}."]
    for r in rows:
        sender = r.get("from_name") or r.get("from_email") or "someone"
        topic = (r.get("topic") or r.get("subject") or "their message").strip()
        lines.append(f"{sender} — {topic}.")
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
    waiting = counts.get("waiting_on", 0)
    fyi = counts.get("fyi", 0)
    newsletter = counts.get("newsletter", 0)

    parts = [f"You have {total} processed email{'s' if total != 1 else ''} in Felix."]
    if action:
        parts.append(f"{action} need{'s' if action == 1 else ''} action.")
    if vip:
        parts.append(f"{vip} from VIP contacts.")
    if waiting:
        parts.append(f"{waiting} waiting on a reply from you.")
    if fyi:
        parts.append(f"{fyi} {'is' if fyi == 1 else 'are'} FYI.")
    if newsletter:
        parts.append(f"{newsletter} newsletter{'s' if newsletter != 1 else ''}.")
    return " ".join(parts)


async def _reply_to(intent, user_id, gmail, user_name) -> str:
    from app.services.ai_service import ai_service

    recipient = (intent.get("recipient") or "").strip()
    if not recipient:
        return "Who would you like to reply to? You can say their name or email address."

    # Find the most recent email from someone matching the recipient string.
    pattern = f"%{recipient.lower()}%"
    email = await db.query_one(
        """
        SELECT *
        FROM emails
        WHERE user_id = $1
          AND (
                LOWER(from_name) LIKE $2
             OR LOWER(from_email) LIKE $2
          )
        ORDER BY received_at DESC
        LIMIT 1
        """,
        user_id, pattern,
    )
    if not email:
        return f"I couldn't find a recent email from {recipient}. Try saying their full name or email."

    sender_label = email.get("from_name") or email.get("from_email") or recipient

    # Pull thread history if available so the draft has context.
    thread_history: list[dict] = []
    thread_id = email.get("thread_id")
    if thread_id and gmail is None:
        try:
            creds = await get_google_credentials(user_id)
            gmail = GmailService(creds)
        except Exception:
            logger.info("No Google credentials for reply_to (user %s)", user_id)
    if thread_id and gmail is not None:
        try:
            thread_history = await gmail.get_thread(thread_id)
        except Exception:
            logger.warning("Could not fetch thread for reply_to (user %s)", user_id)

    user_settings = await db.query_one(
        "SELECT display_name, style_profile FROM settings WHERE user_id = $1",
        user_id,
    )
    display_name: str = (user_settings or {}).get("display_name") or user_name
    style_profile: dict = (user_settings or {}).get("style_profile") or {}

    contact: dict = await db.query_one(
        "SELECT * FROM contacts WHERE email = $1 AND user_id = $2",
        email.get("from_email", ""), user_id,
    ) or {}

    user_intent = (intent.get("reply_content") or "").strip() or "Reply appropriately"

    draft_memory = await memory_service.build_memory_context(
        user_id=user_id,
        feature="draft",
        query=(
            f"{email.get('from_name') or email.get('from_email', '')} "
            f"{email.get('subject', '')}"
        ),
        include_episodes=True,
    )

    # Stream the draft to completion (we don't need partial chunks here).
    full_text = ""
    try:
        async for chunk in ai_service.draft_reply(
            email=dict(email),
            thread_history=thread_history,
            contact=contact,
            style_profile=style_profile,
            user_name=display_name,
            user_intent=user_intent,
            user_id=user_id,
            memory_context=draft_memory,
        ):
            full_text += chunk
    except Exception:
        logger.exception("draft_reply failed for user %s", user_id)
        return f"I found the email from {sender_label} but couldn't draft a reply. Please try again."

    if not full_text.strip():
        return f"I found the email from {sender_label} but the draft came back empty. Please try again."

    try:
        await db.upsert(
            "drafts",
            {
                "email_id":    email["id"],
                "user_id":     user_id,
                "draft_text":  full_text,
                "status":      "pending",
                "edited_text": None,
            },
            conflict_columns=["email_id", "user_id"],
        )
        await db.execute(
            "UPDATE emails SET draft_generated = TRUE WHERE id = $1 AND user_id = $2",
            email["id"], user_id,
        )
    except Exception:
        logger.exception("Failed to store voice-generated draft for user %s", user_id)
        return f"I drafted a reply to {sender_label}, but couldn't save it. Please try again."

    return (
        f"I've drafted a reply to {sender_label}. "
        "You can review and send it from your inbox."
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
    recipient = (intent.get("recipient") or "").strip()
    timeframe = (intent.get("timeframe") or "tomorrow").strip()
    topic = (intent.get("topic") or "").strip()
    duration = intent.get("duration_minutes") or 30
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        duration = 30

    user_tz_name = await _get_user_timezone(user_id)
    try:
        user_tz = pytz.timezone(user_tz_name)
    except pytz.UnknownTimeZoneError:
        user_tz_name = DEFAULT_USER_TIMEZONE
        user_tz = pytz.timezone(DEFAULT_USER_TIMEZONE)

    target_date, hint_hour, clarification = _resolve_schedule_date(intent, user_tz)
    if clarification:
        return clarification

    explicit_start = _parse_clock_time(intent.get("start_time"))
    explicit_end = _parse_clock_time(intent.get("end_time"))

    if explicit_start is not None:
        start_time_of_day = explicit_start
    else:
        start_time_of_day = time(hour=hint_hour, minute=0)

    start_local = user_tz.localize(datetime.combine(target_date, start_time_of_day))

    if explicit_end is not None:
        end_local = user_tz.localize(datetime.combine(target_date, explicit_end))
        if end_local <= start_local:
            end_local += timedelta(days=1)
        # Derive duration from the explicit end so the spoken response is consistent
        duration = max(int((end_local - start_local).total_seconds() // 60), 1)
    else:
        end_local = start_local + timedelta(minutes=duration)

    try:
        creds = await get_google_credentials(user_id)
    except Exception:
        return "I can't access your calendar right now. Reconnect Google in Settings and try again."

    cal = CalendarService(creds)

    summary = topic or (f"Meeting with {recipient}" if recipient else "Meeting")
    event_body: dict = {
        "summary": summary,
        "start": {
            "dateTime": start_local.isoformat(),
            "timeZone": user_tz_name,
        },
        "end": {
            "dateTime": end_local.isoformat(),
            "timeZone": user_tz_name,
        },
    }
    if recipient and "@" in recipient:
        event_body["attendees"] = [{"email": recipient}]

    try:
        await cal.create_event(event_body, user_timezone=user_tz_name)
    except Exception:
        logger.exception("create_event failed for user %s", user_id)
        return "I had trouble creating that calendar event. Please try again."

    when = start_local.strftime("%A %d %b at ") + _format_time_for_speech(start_local)
    attendee_phrase = f" with {recipient}" if recipient else ""
    return f"Done — I've added {summary}{attendee_phrase} to your calendar on {when}."


async def _follow_up_with(intent, user_id, gmail, user_name) -> str:
    recipient = (intent.get("recipient") or "").strip()
    if not recipient:
        return "Who would you like to follow up with?"

    pattern = f"%{recipient.lower()}%"
    rows = await db.query(
        """
        SELECT to_email, subject, topic, follow_up_by, status
        FROM follow_ups
        WHERE user_id = $1
          AND LOWER(to_email) LIKE $2
        ORDER BY follow_up_by ASC NULLS LAST
        LIMIT 5
        """,
        user_id, pattern,
    )

    if not rows:
        return f"I don't have any tracked follow-ups for {recipient}."

    waiting = [r for r in rows if r.get("status") == "waiting"]
    if not waiting:
        return f"You have no outstanding follow-ups with {recipient} — they're all closed."

    count = len(waiting)
    lines = [f"You have {count} open follow-up{'s' if count != 1 else ''} with {recipient}."]
    now = datetime.now(timezone.utc)
    for r in waiting:
        topic = (r.get("topic") or r.get("subject") or "your message").strip()
        due = r.get("follow_up_by")
        if isinstance(due, datetime):
            delta_days = (due - now).days
            if delta_days < 0:
                lines.append(f"{topic} — overdue by {abs(delta_days)} day{'s' if abs(delta_days) != 1 else ''}.")
            elif delta_days == 0:
                lines.append(f"{topic} — due today.")
            else:
                lines.append(f"{topic} — due in {delta_days} day{'s' if delta_days != 1 else ''}.")
        else:
            lines.append(f"{topic}.")
    return " ".join(lines)


async def _start_meeting_notes(intent, user_id, gmail, user_name) -> str:
    return (
        "Meeting notes mode is ready. "
        "Use the Felix app to start recording, and I'll transcribe and summarise everything when you're done."
    )


def _resolve_schedule_date(intent: dict, tz: pytz.BaseTzInfo) -> tuple[date, int, str | None]:
    """
    Resolve the schedule date for a create-event request.

    Preference order:
    1. explicit date_iso from the LLM
    2. explicit month/day parsed from the raw transcript or timeframe as a safety net
    3. relative timeframe phrases
    4. explicit weekday
    5. default fallback (tomorrow)
    """
    raw_transcript = (intent.get("raw_transcript") or "").strip()
    timeframe = (intent.get("timeframe") or "").strip()
    today = datetime.now(tz).date()
    hint_hour = _hint_hour_from_text(" ".join(part for part in (timeframe, raw_transcript) if part))

    explicit_date = _parse_iso_date(intent.get("date_iso"))
    if explicit_date is None:
        explicit_date = _extract_explicit_month_day(raw_transcript, today) or _extract_explicit_month_day(timeframe, today)

    explicit_weekday = _normalize_weekday(intent.get("weekday")) or _extract_weekday(raw_transcript)
    if explicit_date is None and explicit_weekday is None:
        explicit_weekday = _extract_weekday(timeframe)

    if explicit_date is not None and explicit_weekday is not None:
        actual_weekday = DAY_NAMES[explicit_date.weekday()]
        if actual_weekday != explicit_weekday:
            return (
                today,
                hint_hour,
                f"You said {explicit_date.strftime('%B %-d')} and {explicit_weekday.capitalize()}, but those don't match. Which date should I use?",
            )

    if explicit_date is not None:
        return explicit_date, hint_hour, None

    relative_date = _resolve_relative_timeframe(timeframe, today)
    if relative_date is not None:
        return relative_date, hint_hour, None

    if explicit_weekday is not None:
        return _next_weekday(today, explicit_weekday), hint_hour, None

    date_like_text = " ".join(part for part in (raw_transcript, timeframe) if part)
    if _looks_like_explicit_date_phrase(date_like_text):
        return today, hint_hour, "I’m not fully confident about the date you meant. Which date should I put on the calendar?"

    return today + timedelta(days=1), hint_hour, None


def _hint_hour_from_text(text: str) -> int:
    tf = (text or "").lower().strip()
    if "morning" in tf:
        return 10
    if "afternoon" in tf:
        return 14
    if "evening" in tf:
        return 18
    if "lunch" in tf or "midday" in tf or "noon" in tf:
        return 12
    return 10


def _parse_iso_date(value) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _normalize_weekday(value) -> str | None:
    if not value or not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    return candidate if candidate in DAY_NAMES else None


def _extract_weekday(text: str) -> str | None:
    tf = (text or "").lower()
    for name in DAY_NAMES:
        if re.search(rf"\b{name}\b", tf):
            return name
    return None


def _next_weekday(today: date, weekday_name: str) -> date:
    target_index = DAY_NAMES.index(weekday_name)
    days_ahead = (target_index - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def _extract_explicit_month_day(text: str, today: date) -> date | None:
    lower_text = (text or "").lower()
    if not lower_text:
        return None

    month_names = "|".join(MONTH_NAME_TO_NUMBER.keys())
    patterns = [
        rf"\b(?P<month>{month_names})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<year>\d{{4}}))?\b",
        rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month>{month_names})(?:,?\s+(?P<year>\d{{4}}))?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, lower_text)
        if not match:
            continue

        month = MONTH_NAME_TO_NUMBER[match.group("month")]
        day = int(match.group("day"))
        year = int(match.group("year")) if match.group("year") else today.year
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None

        if match.group("year") is None and candidate < today:
            try:
                candidate = date(today.year + 1, month, day)
            except ValueError:
                return None
        return candidate

    return None


def _looks_like_explicit_date_phrase(text: str) -> bool:
    lower_text = (text or "").lower()
    if not lower_text:
        return False
    if any(month in lower_text for month in MONTH_NAME_TO_NUMBER):
        return True
    return bool(re.search(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", lower_text))


def _resolve_relative_timeframe(timeframe: str, today: date) -> date | None:
    tf = (timeframe or "").lower().strip()
    if tf in ("", "today"):
        return today
    if "tomorrow" in tf:
        return today + timedelta(days=1)
    if "next week" in tf:
        return today + timedelta(days=7 - today.weekday())
    if "this week" in tf:
        return today
    return None


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
        monday = today - timedelta(days=today.weekday())
        start = datetime.combine(monday, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=7)
    elif tf in ("next week",):
        next_monday = today + timedelta(days=7 - today.weekday())
        start = datetime.combine(next_monday, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=7)
    else:
        target_day = None
        for i, name in enumerate(DAY_NAMES):
            if name in tf:
                target_day = i
                break

        if target_day is not None:
            days_ahead = (target_day - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            target_date = today + timedelta(days=days_ahead)
            start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
            end = start + timedelta(days=1)
        else:
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
    lines = [f"You have {count} {'meeting' if count == 1 else 'meetings'} {timeframe}."]

    for ev in events[:5]:
        title = ev.get("title") or "Untitled meeting"
        time_str = _format_time_for_speech(ev.get("start"))
        attendees = ev.get("attendees") or []
        # Pick a short attendee phrase if there's exactly one notable attendee
        if len(attendees) == 1:
            attendee_phrase = f" with {attendees[0]}"
        elif len(attendees) > 1:
            attendee_phrase = f" with {len(attendees)} attendees"
        else:
            attendee_phrase = ""

        if time_str:
            lines.append(f"{title} at {time_str}{attendee_phrase}.")
        else:
            lines.append(f"{title}{attendee_phrase}.")

    return " ".join(lines)


async def _general_question(intent, user_id, gmail, user_name) -> str:
    from app.services.ai_service import ai_service

    transcript = intent.get("raw_transcript", "")
    if not transcript:
        return "I didn't quite catch that. Could you say that again?"

    # Build a small "what Felix knows" snapshot so the answer can reference
    # the user's actual state instead of being purely generic.
    context_lines: list[str] = []
    try:
        category_rows = await db.query(
            "SELECT category, COUNT(*) AS n FROM emails WHERE user_id = $1 GROUP BY category",
            user_id,
        )
        counts = {r["category"]: r["n"] for r in category_rows if r.get("category")}
        if counts:
            summary = ", ".join(f"{n} {cat}" for cat, n in counts.items())
            context_lines.append(f"Inbox categories: {summary}.")
    except Exception:
        logger.warning("general_question: failed to load inbox counts for user %s", user_id)

    try:
        followup_row = await db.query_one(
            "SELECT COUNT(*) AS n FROM follow_ups WHERE user_id = $1 AND status = 'waiting'",
            user_id,
        )
        n = (followup_row or {}).get("n", 0)
        if n:
            context_lines.append(f"{n} open follow-up{'s' if n != 1 else ''}.")
    except Exception:
        pass

    try:
        contact_rows = await db.query(
            """
            SELECT name, email
            FROM contacts
            WHERE user_id = $1
            ORDER BY relationship_strength DESC NULLS LAST
            LIMIT 5
            """,
            user_id,
        )
        if contact_rows:
            top = ", ".join(
                (r.get("name") or r.get("email") or "").strip()
                for r in contact_rows
                if (r.get("name") or r.get("email"))
            )
            if top:
                context_lines.append(f"Top contacts: {top}.")
    except Exception:
        pass

    try:
        user_tz = await _get_user_timezone(user_id)
        creds = await get_google_credentials(user_id)
        cal = CalendarService(creds)
        events = await cal.get_today_events(user_tz)
        timed = [e for e in events if not e.get("is_all_day")]
        if timed:
            context_lines.append(f"{len(timed)} meeting{'s' if len(timed) != 1 else ''} on the calendar today.")
    except Exception:
        # No creds or calendar fetch failed — fine, just skip the calendar context.
        pass

    felix_context = "\n".join(context_lines) if context_lines else ""

    # Chat surface — pull Layer 1 profile, Layer 2 recent sessions, and
    # Layer 3 episodes relevant to the transcript.
    chat_memory = await memory_service.build_memory_context(
        user_id=user_id,
        feature="voice_general",
        query=transcript,
        include_sessions=True,
        include_episodes=True,
    )

    try:
        return await ai_service.answer_general_voice_question(
            transcript=transcript,
            user_name=user_name,
            felix_context=felix_context,
            user_id=user_id,
            memory_context=chat_memory,
        )
    except Exception:
        logger.exception("General question handler failed for user %s", user_id)
        return "I'm not sure how to help with that. You can ask me to check your emails, calendar, or follow-ups."
