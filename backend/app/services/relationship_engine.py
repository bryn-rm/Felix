"""
Relationship intelligence engine — Phase 6.

Maintains a ContactProfile per (user_id, contact_email) pair —
the same sender email can exist for multiple Felix users as fully
separate rows (PK is (email, user_id)).

Two modes:
  refresh_user(user_id)       — nightly full rebuild from email + meeting history
  update_contact(user_id, email) — lightweight inline update per new email
                                   called from inbox_sync during each sync run

All DB reads are scoped with AND user_id = $user_id — defence in depth
even though the backend already uses a service key.
"""

import logging
from datetime import datetime, timezone

from app import db

logger = logging.getLogger(__name__)

# Sentiment → numeric score for trend calculation
_SENTIMENT_SCORE = {
    "positive":   1,
    "neutral":    0,
    "stressed":  -1,
    "urgent":    -1,
    "frustrated":-2,
}


class RelationshipEngine:

    # ------------------------------------------------------------------
    # Nightly full rebuild
    # ------------------------------------------------------------------

    async def refresh_user(self, user_id: str) -> None:
        """
        Rebuild all contact profiles for a user from their email + meeting history.
        Called nightly at 11pm. One exception per contact never aborts the whole run.
        """
        # Gather all distinct contact email addresses from this user's emails
        sent_contacts = await db.query(
            "SELECT DISTINCT to_email AS email FROM emails "
            "WHERE user_id = $1 AND to_email IS NOT NULL AND to_email != ''",
            user_id,
        )
        received_contacts = await db.query(
            "SELECT DISTINCT from_email AS email FROM emails "
            "WHERE user_id = $1 AND from_email IS NOT NULL AND from_email != ''",
            user_id,
        )

        all_emails = {r["email"] for r in sent_contacts + received_contacts}
        all_emails.discard("")

        logger.info("Rebuilding %d contact profiles for user %s", len(all_emails), user_id)

        for contact_email in all_emails:
            try:
                await self._rebuild_contact(user_id, contact_email)
            except Exception:
                logger.exception(
                    "Failed to rebuild contact %s for user %s", contact_email, user_id
                )

    async def _rebuild_contact(self, user_id: str, contact_email: str) -> None:
        """Full rebuild of a single contact profile."""
        # Fetch the last 50 emails with this contact (both directions)
        emails = await db.query(
            """
            SELECT id, from_email, from_name, subject, body, received_at, sentiment
            FROM emails
            WHERE user_id = $1
              AND (from_email = $2 OR to_email = $2)
            ORDER BY received_at DESC
            LIMIT 50
            """,
            user_id, contact_email,
        )

        if not emails:
            return

        total_emails = len(emails)

        # Most recent email from this contact (not sent by user)
        inbound = [e for e in emails if e.get("from_email") == contact_email]
        last_contacted = None
        from_name: str | None = None
        if inbound:
            last_contacted = inbound[0].get("received_at")
            from_name = inbound[0].get("from_name") or None
        elif emails:
            last_contacted = emails[0].get("received_at")

        # Relationship strength: frequency + recency
        relationship_strength = self._compute_strength(total_emails, last_contacted)

        # Sentiment trend from last 10 inbound emails
        sentiment_trend = self._compute_sentiment_trend(inbound[:10])

        # Topic extraction — collect non-null, non-empty topics
        recent_subjects = [
            e.get("subject") or "" for e in inbound[:10]
            if e.get("subject")
        ]

        # Meeting history
        meeting_row = await db.query_one(
            """
            SELECT COUNT(*) AS cnt, MAX(date) AS last_meeting
            FROM meetings
            WHERE user_id = $1 AND $2 = ANY(attendees)
            """,
            user_id, contact_email,
        )
        meeting_count = int((meeting_row or {}).get("cnt") or 0)
        last_meeting = (meeting_row or {}).get("last_meeting")

        # Upsert the contact — preserve existing manual fields (personal_notes,
        # tags, open_commitments, vip, vip_rules) if they exist
        existing = await db.query_one(
            "SELECT personal_notes, tags, open_commitments, their_open_commitments, "
            "vip, vip_rules, known_facts "
            "FROM contacts WHERE email = $1 AND user_id = $2",
            contact_email, user_id,
        )

        await db.upsert(
            "contacts",
            {
                "email":                contact_email,
                "user_id":              user_id,
                "name":                 from_name,
                "total_emails":         total_emails,
                "last_contacted":       last_contacted,
                "sentiment_trend":      sentiment_trend,
                "relationship_strength": relationship_strength,
                "meeting_count":        meeting_count,
                "last_meeting":         last_meeting,
                # Preserve manual fields from existing row
                "personal_notes":       (existing or {}).get("personal_notes"),
                "tags":                 (existing or {}).get("tags") or [],
                "open_commitments":     (existing or {}).get("open_commitments") or [],
                "their_open_commitments": (existing or {}).get("their_open_commitments") or [],
                "vip":                  bool((existing or {}).get("vip")),
                "vip_rules":            (existing or {}).get("vip_rules"),
                "known_facts":          (existing or {}).get("known_facts"),
                "updated_at":           datetime.now(timezone.utc),
            },
            conflict_columns=["email", "user_id"],
        )

        # Update sentiment trend via sentiment_analyser
        try:
            from app.services.sentiment_analyser import sentiment_analyser
            await sentiment_analyser.update_contact_trend(user_id, contact_email)
        except Exception:
            logger.warning(
                "Sentiment trend update failed for %s / user %s", contact_email, user_id
            )

    # ------------------------------------------------------------------
    # Lightweight inline update (called per new email during sync)
    # ------------------------------------------------------------------

    async def update_contact(self, user_id: str, email: dict) -> None:
        """
        Update a single contact's profile from a new inbound email.
        Only updates fields that can be derived from a single email.
        Called inline during inbox_sync — must be fast.
        """
        contact_email = email.get("from_email")
        if not contact_email:
            return

        existing = await db.query_one(
            "SELECT total_emails, relationship_strength FROM contacts "
            "WHERE email = $1 AND user_id = $2",
            contact_email, user_id,
        )

        from_name: str | None = email.get("from_name") or None
        received_at = email.get("received_at") or datetime.now(timezone.utc)
        new_total = int((existing or {}).get("total_emails") or 0) + 1
        new_strength = self._compute_strength(new_total, received_at)

        if existing:
            await db.execute(
                """
                UPDATE contacts
                SET total_emails = $1,
                    last_contacted = $2,
                    relationship_strength = $3,
                    name = COALESCE(name, $4),
                    updated_at = $5
                WHERE email = $6 AND user_id = $7
                """,
                new_total, received_at, new_strength, from_name,
                datetime.now(timezone.utc), contact_email, user_id,
            )
        else:
            # First time seeing this contact — create a minimal profile
            await db.upsert(
                "contacts",
                {
                    "email":                contact_email,
                    "user_id":              user_id,
                    "name":                 from_name,
                    "total_emails":         1,
                    "last_contacted":       received_at,
                    "relationship_strength": new_strength,
                    "meeting_count":        0,
                    "updated_at":           datetime.now(timezone.utc),
                },
                conflict_columns=["email", "user_id"],
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_strength(total_emails: int, last_contacted) -> float:
        """
        Relationship strength = frequency factor × recency factor.
        Capped at 1.0.
        """
        frequency = min(1.0, total_emails / 100)

        recency = 0.1  # default: >90 days
        if last_contacted:
            try:
                if not isinstance(last_contacted, datetime):
                    last_contacted = datetime.fromisoformat(str(last_contacted))
                if last_contacted.tzinfo is None:
                    last_contacted = last_contacted.replace(tzinfo=timezone.utc)
                days_since = (datetime.now(timezone.utc) - last_contacted).days
                if days_since <= 7:
                    recency = 1.0
                elif days_since <= 30:
                    recency = 0.7
                elif days_since <= 90:
                    recency = 0.4
            except Exception:
                pass

        return round(min(1.0, frequency * recency), 3)

    @staticmethod
    def _compute_sentiment_trend(recent_inbound: list[dict]) -> str:
        """
        Split recent emails into two halves; compare average sentiment scores.
        Returns "improving", "deteriorating", or "stable".
        """
        scores = []
        for e in recent_inbound:
            s = e.get("sentiment") or "neutral"
            scores.append(_SENTIMENT_SCORE.get(s, 0))

        if len(scores) < 2:
            return "stable"

        mid = len(scores) // 2
        recent_avg = sum(scores[:mid]) / mid
        older_avg = sum(scores[mid:]) / (len(scores) - mid)

        diff = recent_avg - older_avg
        if diff > 0.4:
            return "improving"
        if diff < -0.4:
            return "deteriorating"
        return "stable"


relationship_engine = RelationshipEngine()
