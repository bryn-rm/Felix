"""
Morning briefing generator — Phase 4.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import db

from app.services.ai_service import ai_service
from app.services.calendar_service import CalendarService
from app.services.voice_service import voice_service
from app.middleware.auth import get_google_credentials


class BriefingService:

    async def gather_context(self, user_id: str) -> dict:
        settings_row = await db.query_one(
            "SELECT display_name, timezone FROM settings WHERE user_id = $1",
            user_id,
        ) or {}

        user_name = settings_row.get("display_name") or "there"
        tz_name = settings_row.get("timezone") or "Europe/London"
        try:
            user_tz = ZoneInfo(tz_name)
        except Exception:
            user_tz = ZoneInfo("Europe/London")

        now_local = datetime.now(user_tz)
        day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        priority_emails = await db.query(
            """
            SELECT from_name, from_email, subject, urgency, received_at
            FROM emails
            WHERE user_id = $1
              AND category IN ('action_required', 'vip')
            ORDER BY
              CASE urgency
                WHEN 'critical' THEN 4
                WHEN 'high' THEN 3
                WHEN 'medium' THEN 2
                ELSE 1
              END DESC,
              received_at DESC
            LIMIT 5
            """,
            user_id,
        )

        overdue_followups = await db.query(
            """
            SELECT to_email, subject, follow_up_by
            FROM follow_ups
            WHERE user_id = $1
              AND status = 'waiting'
              AND follow_up_by < NOW()
            ORDER BY follow_up_by ASC
            LIMIT 5
            """,
            user_id,
        )

        relationship_alert_rows = await db.query(
            """
            SELECT name, email, sentiment_trend, relationship_strength
            FROM contacts
            WHERE user_id = $1
              AND (sentiment_trend = 'deteriorating' OR relationship_strength < 0.35)
            ORDER BY relationship_strength ASC NULLS LAST
            LIMIT 3
            """,
            user_id,
        )

        meetings_today: list[dict] = []
        try:
            creds = await get_google_credentials(user_id)
            calendar = CalendarService(creds)
            meetings_today = await calendar.get_events(
                day_start.astimezone(timezone.utc).isoformat(),
                day_end.astimezone(timezone.utc).isoformat(),
            )
        except Exception:
            meetings_today = []

        priority_emails_summary = _summarise_priority_emails(priority_emails)
        calendar_summary = _summarise_calendar(meetings_today)
        follow_ups_summary = _summarise_followups(overdue_followups)
        relationship_alerts = _summarise_relationship_alerts(relationship_alert_rows)

        return {
            "user_name": user_name,
            "briefing_date": now_local.date().isoformat(),
            "priority_email_count": len(priority_emails),
            "priority_emails": priority_emails,
            "priority_emails_summary": priority_emails_summary,
            "meeting_count": len(meetings_today),
            "meetings_today": meetings_today,
            "calendar_summary": calendar_summary,
            "follow_up_count": len(overdue_followups),
            "overdue_followups": overdue_followups,
            "follow_ups_summary": follow_ups_summary,
            "relationship_alerts": relationship_alerts,
        }

    async def generate_for_user(self, user_id: str) -> dict:
        context = await self.gather_context(user_id)
        text = await ai_service.generate_daily_briefing(context)

        audio_url = None
        try:
            audio_url = await voice_service.generate_and_store(text=text, user_id=user_id)
        except Exception:
            audio_url = None

        row = await db.upsert(
            "briefings",
            {
                "user_id": user_id,
                "date": date.fromisoformat(context["briefing_date"]),
                "text": text,
                "audio_url": audio_url,
                "priority_emails": context["priority_emails"],
                "calendar_summary": {
                    "meeting_count": context["meeting_count"],
                    "summary": context["calendar_summary"],
                    "events": context["meetings_today"],
                },
                "follow_ups_summary": {
                    "overdue_count": context["follow_up_count"],
                    "summary": context["follow_ups_summary"],
                    "items": context["overdue_followups"],
                },
            },
            conflict_columns=["user_id", "date"],
        )

        return {
            "id": row["id"] if row else None,
            "user_id": user_id,
            "date": context["briefing_date"],
            "text": text,
            "audio_url": audio_url,
            "priority_email_count": context["priority_email_count"],
            "meeting_count": context["meeting_count"],
            "follow_up_count": context["follow_up_count"],
        }


briefing_service = BriefingService()


def _summarise_priority_emails(rows: list[dict]) -> str:
    if not rows:
        return "No priority emails need your attention right now."
    parts = []
    for idx, email in enumerate(rows[:3], 1):
        sender = email.get("from_name") or email.get("from_email") or "someone"
        subj = email.get("subject") or "no subject"
        urgency = email.get("urgency") or "low"
        parts.append(f"{idx}) {sender} about '{subj}' ({urgency} urgency)")
    return "; ".join(parts)


def _summarise_calendar(events: list[dict]) -> str:
    if not events:
        return "You have no meetings scheduled today."
    first = events[0]
    first_title = first.get("summary") or "first meeting"
    return f"{len(events)} meetings today. First is '{first_title}'."


def _summarise_followups(rows: list[dict]) -> str:
    if not rows:
        return "No overdue follow-ups."
    parts = []
    for item in rows[:3]:
        to = item.get("to_email") or "someone"
        subject = item.get("subject") or "no subject"
        parts.append(f"{to} on '{subject}'")
    return "Overdue follow-ups with " + ", ".join(parts) + "."


def _summarise_relationship_alerts(rows: list[dict]) -> str:
    if not rows:
        return "No relationship alerts right now."
    parts = []
    for row in rows:
        name = row.get("name") or row.get("email") or "a contact"
        trend = row.get("sentiment_trend") or "stable"
        parts.append(f"{name} trend is {trend}")
    return "; ".join(parts)
