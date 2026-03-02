"""
Follow-up detection + tracking engine — Phase 5.

Two entry points:
  process_sent_email(user_id, sent_email)
      Called after a sent email is approved by the user (POST /emails/{id}/send).
      Uses Claude Haiku to detect whether a follow-up is needed, then inserts a
      follow_ups row with status='waiting'.

  check_overdue(user_id)
      Returns follow_ups rows past their follow_up_by deadline.
      Called by the hourly follow_up_checker job.

All DB writes include user_id — no multi-user assumptions.
"""

import logging
from datetime import datetime, timedelta, timezone

from app import db
from app.services.ai_service import ai_service

logger = logging.getLogger(__name__)


class FollowUpEngine:

    async def process_sent_email(
        self,
        user_id: str,
        sent_email: dict,
    ) -> dict | None:
        """
        Analyse a sent email with Claude Haiku and, if a follow-up is warranted,
        create a follow_ups row.

        sent_email should be the dict representation of the email as stored in
        the emails table (with keys: id, subject, body, to_email / to, received_at).

        Returns the created follow_ups row, or None if no follow-up is needed.
        """
        email_id = sent_email.get("id")

        # Idempotency — only one follow-up per sent email
        if email_id:
            existing = await db.query_one(
                "SELECT id FROM follow_ups WHERE user_id = $1 AND email_id = $2",
                user_id, email_id,
            )
            if existing:
                return None

        # AI detection
        result = await ai_service.detect_follow_ups(sent_email)
        if result is None or not result.get("needs_follow_up"):
            return None

        # Build follow-up deadline
        days = result.get("suggested_follow_up_days")
        try:
            days = int(days) if days is not None else 3
        except (TypeError, ValueError):
            days = 3
        days = max(1, min(days, 30))  # clamp to 1–30 days

        follow_up_by = datetime.now(timezone.utc) + timedelta(days=days)

        # Recipient: for a sent email the "to" is who we sent to; the dict
        # may carry it as "to_email" or "to" depending on origin.
        to_email: str = (
            sent_email.get("to_email")
            or sent_email.get("to")
            or ""
        )

        row = await db.insert(
            "follow_ups",
            {
                "user_id":      user_id,
                "email_id":     email_id,
                "to_email":     to_email,
                "subject":      sent_email.get("subject") or "",
                "topic":        result.get("topic") or sent_email.get("subject") or "",
                "sent_at":      sent_email.get("received_at") or datetime.now(timezone.utc),
                "follow_up_by": follow_up_by,
                "urgency":      result.get("urgency") or "medium",
                "status":       "waiting",
            },
        )

        logger.info(
            "Follow-up created for user %s — topic: %s, due: %s",
            user_id, result.get("topic"), follow_up_by.date()
        )
        return row

    async def check_overdue(self, user_id: str) -> list[dict]:
        """
        Return all follow_ups for this user that are past their deadline
        and still in 'waiting' status, ordered by most overdue first.
        """
        return await db.query(
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

    async def mark_replied(self, user_id: str, thread_id: str) -> None:
        """
        Called when a new inbound email is processed and it belongs to a thread
        that has a tracked follow-up. Marks the follow-up as 'replied'.

        thread_id is matched against email_id stored on the follow-up (Gmail
        thread IDs are stable for the lifetime of a conversation).
        """
        if not thread_id:
            return
        # Look for follow_ups whose email_id appears in the thread
        await db.execute(
            """
            UPDATE follow_ups
            SET status = 'replied'
            WHERE user_id = $1
              AND status = 'waiting'
              AND email_id IN (
                SELECT id FROM emails WHERE user_id = $1 AND thread_id = $2
              )
            """,
            user_id, thread_id,
        )

    async def draft_follow_up_text(
        self,
        user_id: str,
        follow_up_id: str,
    ) -> str | None:
        """
        Generate a draft follow-up message for an existing follow_ups row.
        Stores the result in follow_ups.auto_draft.

        Returns the draft text or None on failure.
        """
        fu = await db.query_one(
            "SELECT * FROM follow_ups WHERE id = $1 AND user_id = $2",
            follow_up_id, user_id,
        )
        if not fu:
            return None

        # Reconstruct a minimal email dict for the AI detection prompt
        mock_email = {
            "to":      fu.get("to_email") or "",
            "subject": fu.get("subject") or "",
            "body":    f"Following up on: {fu.get('topic') or fu.get('subject') or 'our previous conversation'}",
        }

        # Use detect_follow_ups as a simple check — but we really just want
        # a short polite follow-up text. Ask the draft model directly.
        from app.services.ai_service import ai_service as _ai

        # We reuse the draft_reply stream but feed it a synthesized email
        full_text = ""
        try:
            async for chunk in _ai.draft_reply(
                email=mock_email,
                thread_history=[],
                contact={},
                style_profile={},
                user_name="",
                user_intent=(
                    f"Write a short, polite follow-up email. "
                    f"I sent an email to {fu.get('to_email')} about "
                    f"{fu.get('topic') or fu.get('subject') or 'a previous matter'} "
                    f"and haven't heard back. Keep it to 2-3 sentences."
                ),
            ):
                full_text += chunk
        except Exception:
            logger.exception("Draft follow-up generation failed for follow_up %s", follow_up_id)
            return None

        if full_text.strip():
            await db.execute(
                "UPDATE follow_ups SET auto_draft = $1 WHERE id = $2 AND user_id = $3",
                full_text.strip(), follow_up_id, user_id,
            )
            return full_text.strip()

        return None


follow_up_engine = FollowUpEngine()
