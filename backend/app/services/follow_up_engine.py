"""
Follow-up detection + tracking engine — Phase 5.
"""

from datetime import datetime, timedelta, timezone

from app import db

from app.services.ai_service import ai_service


class FollowUpEngine:

    async def process_sent_email(self, user_id: str, sent_email: dict) -> dict | None:
        """Create a follow-up tracking row when a sent email needs monitoring."""
        detection = await ai_service.detect_follow_ups(sent_email)
        if not detection:
            return None

        days = detection.get("suggested_follow_up_days")
        try:
            days_int = int(days)
        except (TypeError, ValueError):
            days_int = 3
        days_int = max(1, min(days_int, 30))

        sent_at = sent_email.get("sent_at")
        if isinstance(sent_at, str):
            try:
                sent_dt = datetime.fromisoformat(sent_at)
            except ValueError:
                sent_dt = datetime.now(timezone.utc)
        elif isinstance(sent_at, datetime):
            sent_dt = sent_at
        else:
            sent_dt = datetime.now(timezone.utc)

        if sent_dt.tzinfo is None:
            sent_dt = sent_dt.replace(tzinfo=timezone.utc)

        follow_up_by = sent_dt + timedelta(days=days_int)

        auto_draft = (
            f"Hi — just following up on {detection.get('topic') or sent_email.get('subject') or 'my earlier email'}. "
            "Would appreciate a quick update when you have a moment. Thanks!"
        )

        email_id = sent_email.get("email_id") or sent_email.get("id")
        if email_id:
            existing = await db.query_one(
                "SELECT id FROM follow_ups WHERE user_id = $1 AND email_id = $2 LIMIT 1",
                user_id,
                email_id,
            )
            if existing:
                return None

        row = await db.insert(
            "follow_ups",
            {
                "user_id": user_id,
                "email_id": email_id,
                "to_email": sent_email.get("to") or sent_email.get("to_email"),
                "subject": sent_email.get("subject") or "",
                "topic": detection.get("topic"),
                "sent_at": sent_dt,
                "follow_up_by": follow_up_by,
                "status": "waiting",
                "urgency": detection.get("urgency") or "medium",
                "auto_draft": auto_draft,
            },
        )
        return row

    async def check_overdue(self, user_id: str) -> list[dict]:
        """Return overdue follow-ups for a user and mark replied ones as closed."""
        # If inbound email has arrived from recipient since the original sent_at,
        # mark as replied so it no longer appears as overdue.
        waiting = await db.query(
            """
            SELECT id, to_email, subject, sent_at
            FROM follow_ups
            WHERE user_id = $1 AND status = 'waiting'
            """,
            user_id,
        )

        for item in waiting:
            if not item.get("to_email") or not item.get("sent_at"):
                continue
            replied = await db.query_one(
                """
                SELECT id
                FROM emails
                WHERE user_id = $1
                  AND from_email = $2
                  AND received_at > $3
                  AND (
                    subject = $4
                    OR subject = CONCAT('Re: ', $4)
                    OR subject = CONCAT('RE: ', $4)
                  )
                LIMIT 1
                """,
                user_id,
                item["to_email"],
                item["sent_at"],
                item.get("subject") or "",
            )
            if replied:
                await db.execute(
                    "UPDATE follow_ups SET status = 'replied' WHERE id = $1 AND user_id = $2",
                    item["id"],
                    user_id,
                )

        overdue = await db.query(
            """
            SELECT *
            FROM follow_ups
            WHERE user_id = $1
              AND status = 'waiting'
              AND follow_up_by < NOW()
            ORDER BY follow_up_by ASC
            """,
            user_id,
        )
        return overdue


follow_up_engine = FollowUpEngine()
