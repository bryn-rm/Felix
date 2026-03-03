"""
Morning briefing generator — Phase 4.

Pipeline per user:
  1. gather_context()  — query emails, follow-ups, calendar, contacts
  2. generate_daily_briefing() via Claude Sonnet
  3. generate_and_store() via ElevenLabs → Supabase Storage
  4. Upsert into briefings table (idempotent on user_id + date)
"""

import logging
from datetime import date, datetime, timezone

from app import db
from app.middleware.auth import get_google_credentials
from app.services.ai_service import ai_service
from app.services.calendar_service import CalendarService
from app.services.voice_service import voice_service

logger = logging.getLogger(__name__)


class BriefingService:

    async def gather_context(self, user_id: str) -> dict:
        """
        Collect all data sources required for the briefing prompt.

        Returns a dict whose keys match BRIEFING_PROMPT's .format() placeholders:
          user_name, priority_email_count, priority_emails_summary,
          meeting_count, calendar_summary, follow_up_count,
          follow_ups_summary, relationship_alerts

        Also carries raw list versions for JSONB storage:
          _priority_emails, _calendar_events, _follow_ups
        """
        # 1. User settings
        settings = await db.query_one(
            "SELECT display_name, timezone, energy_profile FROM settings WHERE user_id = $1",
            user_id,
        )
        user_name: str = (settings or {}).get("display_name") or "there"
        user_tz: str = (settings or {}).get("timezone") or "UTC"

        # 2. Priority emails from last 24 hours
        priority_emails = await db.query(
            """
            SELECT id, from_name, from_email, subject, snippet, urgency, sentiment, topic
            FROM emails
            WHERE user_id = $1
              AND received_at > NOW() - INTERVAL '24 hours'
              AND category IN ('action_required', 'vip')
            ORDER BY
              CASE urgency
                WHEN 'critical' THEN 1
                WHEN 'high'     THEN 2
                WHEN 'medium'   THEN 3
                ELSE 4
              END,
              received_at DESC
            LIMIT 5
            """,
            user_id,
        )
        priority_email_count = len(priority_emails)
        if priority_emails:
            email_lines = []
            for e in priority_emails:
                sender = e.get("from_name") or e.get("from_email") or "someone"
                subject = e.get("subject") or "(no subject)"
                urgency = e.get("urgency") or "medium"
                email_lines.append(f"• [{urgency.upper()}] From {sender}: {subject}")
            priority_emails_summary = "\n".join(email_lines)
        else:
            priority_emails_summary = "No priority emails in the last 24 hours."

        # 3. Overdue follow-ups
        follow_ups = await db.query(
            """
            SELECT topic, to_email, follow_up_by, urgency
            FROM follow_ups
            WHERE user_id = $1
              AND status = 'waiting'
              AND follow_up_by < NOW()
            ORDER BY follow_up_by ASC
            LIMIT 5
            """,
            user_id,
        )
        follow_up_count = len(follow_ups)
        if follow_ups:
            fu_lines = []
            for fu in follow_ups:
                to = fu.get("to_email") or "someone"
                topic = fu.get("topic") or "your email"
                fu_lines.append(f"• {to} — {topic}")
            follow_ups_summary = "\n".join(fu_lines)
        else:
            follow_ups_summary = "No overdue follow-ups."

        # 4. Today's calendar events (live Google Calendar call)
        calendar_events: list[dict] = []
        try:
            creds = await get_google_credentials(user_id)
            cal = CalendarService(creds)
            calendar_events = await cal.get_today_events(user_tz)
        except Exception:
            logger.warning("Could not fetch calendar events for briefing (user %s)", user_id)

        meeting_count = sum(1 for e in calendar_events if not e.get("is_all_day"))
        if calendar_events:
            cal_lines = []
            for ev in calendar_events[:5]:
                title = ev.get("title") or "(no title)"
                start = ev.get("start") or ""
                # Format start time if it's an ISO string
                try:
                    dt = datetime.fromisoformat(start)
                    start_fmt = dt.strftime("%H:%M")
                except (ValueError, TypeError):
                    start_fmt = start
                attendee_count = ev.get("attendee_count", 0)
                suffix = f" ({attendee_count} attendees)" if attendee_count > 1 else ""
                cal_lines.append(f"• {start_fmt} — {title}{suffix}")
            calendar_summary = "\n".join(cal_lines)
        else:
            calendar_summary = "No meetings scheduled today."

        # 5. Relationship alerts — VIPs not contacted recently or deteriorating sentiment
        relationship_alerts_rows = await db.query(
            """
            SELECT name, email, last_contacted, sentiment_trend, relationship_strength
            FROM contacts
            WHERE user_id = $1
              AND (
                sentiment_trend = 'deteriorating'
                OR last_contacted < NOW() - INTERVAL '21 days'
              )
              AND relationship_strength > 0.3
            ORDER BY relationship_strength DESC
            LIMIT 3
            """,
            user_id,
        )
        if relationship_alerts_rows:
            alert_lines = []
            for r in relationship_alerts_rows:
                name = r.get("name") or r.get("email") or "a contact"
                trend = r.get("sentiment_trend")
                last = r.get("last_contacted")
                if trend == "deteriorating":
                    alert_lines.append(f"• {name} — sentiment deteriorating")
                elif last:
                    try:
                        days_ago = (datetime.now(timezone.utc) - last).days
                        alert_lines.append(f"• {name} — not contacted in {days_ago} days")
                    except Exception:
                        alert_lines.append(f"• {name} — overdue for contact")
            relationship_alerts = "\n".join(alert_lines)
        else:
            relationship_alerts = "No relationship alerts today."

        return {
            # Keys for BRIEFING_PROMPT.format()
            "user_name": user_name,
            "priority_email_count": priority_email_count,
            "priority_emails_summary": priority_emails_summary,
            "meeting_count": meeting_count,
            "calendar_summary": calendar_summary,
            "follow_up_count": follow_up_count,
            "follow_ups_summary": follow_ups_summary,
            "relationship_alerts": relationship_alerts,
            # Raw data for JSONB snapshot in briefings table
            "_priority_emails": priority_emails,
            "_calendar_events": calendar_events,
            "_follow_ups": follow_ups,
        }

    async def generate_for_user(self, user_id: str) -> dict:
        """
        Full pipeline: gather context → Claude text → ElevenLabs audio →
        upsert into briefings table (idempotent on user_id + date).

        Returns the briefings row dict.
        """
        context = await self.gather_context(user_id)

        # Strip the raw data keys before passing to Claude (they're not in the prompt template)
        prompt_context = {k: v for k, v in context.items() if not k.startswith("_")}

        # Generate spoken text via Claude Sonnet
        briefing_text = await ai_service.generate_daily_briefing(prompt_context)

        # Generate TTS audio and upload to Supabase Storage
        audio_url: str = ""
        try:
            audio_url = await voice_service.generate_and_store(briefing_text, user_id)
        except Exception:
            logger.warning(
                "ElevenLabs TTS failed for briefing (user %s) — storing text only", user_id
            )

        # Upsert into briefings (UNIQUE on user_id + date → safe to call multiple times)
        today = date.today()
        row = await db.upsert(
            "briefings",
            {
                "user_id": user_id,
                "date": today.isoformat(),
                "text": briefing_text,
                "audio_url": audio_url or None,
                "priority_emails": context.get("_priority_emails", []),
                "calendar_summary": context.get("_calendar_events", []),
                "follow_ups_summary": context.get("_follow_ups", []),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            conflict_columns=["user_id", "date"],
        )

        logger.info("Briefing generated for user %s (audio: %s)", user_id, bool(audio_url))
        return row or {}


briefing_service = BriefingService()
