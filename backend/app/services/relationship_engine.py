"""
Relationship intelligence engine — Phase 6.

Maintains a ContactProfile per (user_id, contact_email) pair.
"""

from datetime import datetime, timezone

from app import db
from app.services.sentiment_analyser import sentiment_analyser


class RelationshipEngine:

    async def refresh_user(self, user_id: str) -> None:
        """Rebuild contact profiles from email and meetings for one user."""
        contacts = await db.query(
            """
            SELECT from_email AS email, MAX(from_name) AS name
            FROM emails
            WHERE user_id = $1 AND from_email IS NOT NULL AND from_email != ''
            GROUP BY from_email
            """,
            user_id,
        )

        for c in contacts:
            email = c.get("email")
            if not email:
                continue

            stats = await db.query_one(
                """
                SELECT
                  COUNT(*) AS total_emails,
                  MAX(received_at) AS last_contacted,
                  ARRAY_REMOVE(ARRAY_AGG(DISTINCT topic), NULL) AS topics
                FROM emails
                WHERE user_id = $1 AND from_email = $2
                """,
                user_id,
                email,
            ) or {}

            meeting_stats = await db.query_one(
                """
                SELECT
                  COUNT(*) AS meeting_count,
                  MAX(date) AS last_meeting
                FROM meetings
                WHERE user_id = $1
                  AND $2 = ANY(attendees)
                """,
                user_id,
                email,
            ) or {}

            total_emails = int(stats.get("total_emails") or 0)
            meeting_count = int(meeting_stats.get("meeting_count") or 0)
            relationship_strength = min(1.0, (total_emails / 50.0) * 0.7 + (meeting_count / 20.0) * 0.3)

            existing = await db.query_one(
                "SELECT vip, vip_rules, personal_notes, tags, company, role, known_facts, style_profile FROM contacts WHERE user_id = $1 AND email = $2",
                user_id,
                email,
            ) or {}

            await db.upsert(
                "contacts",
                {
                    "user_id": user_id,
                    "email": email,
                    "name": c.get("name"),
                    "company": existing.get("company"),
                    "role": existing.get("role"),
                    "vip": bool(existing.get("vip") or False),
                    "vip_rules": existing.get("vip_rules"),
                    "relationship_strength": relationship_strength,
                    "total_emails": total_emails,
                    "last_contacted": stats.get("last_contacted"),
                    "meeting_count": meeting_count,
                    "last_meeting": meeting_stats.get("last_meeting"),
                    "topics_discussed": (stats.get("topics") or [])[:20],
                    "open_commitments": [],
                    "their_open_commitments": [],
                    "sentiment_trend": None,
                    "known_facts": existing.get("known_facts"),
                    "personal_notes": existing.get("personal_notes"),
                    "tags": existing.get("tags") or [],
                    "style_profile": existing.get("style_profile"),
                    "updated_at": datetime.now(timezone.utc),
                },
                conflict_columns=["email", "user_id"],
            )

            await sentiment_analyser.update_contact_trend(user_id, email)

    async def update_contact(self, user_id: str, email: dict) -> None:
        """Incrementally upsert one contact from a newly processed email."""
        contact_email = (email.get("from_email") or "").strip().lower()
        if not contact_email:
            return

        existing = await db.query_one(
            "SELECT * FROM contacts WHERE user_id = $1 AND email = $2",
            user_id,
            contact_email,
        )

        total_emails = int((existing or {}).get("total_emails") or 0) + 1
        meeting_count = int((existing or {}).get("meeting_count") or 0)
        relationship_strength = min(1.0, (total_emails / 50.0) * 0.7 + (meeting_count / 20.0) * 0.3)

        topics = list((existing or {}).get("topics_discussed") or [])
        topic = email.get("topic")
        if topic and topic not in topics:
            topics.append(topic)

        await db.upsert(
            "contacts",
            {
                "user_id": user_id,
                "email": contact_email,
                "name": email.get("from_name") or (existing or {}).get("name"),
                "company": (existing or {}).get("company"),
                "role": (existing or {}).get("role"),
                "vip": bool((existing or {}).get("vip") or False),
                "vip_rules": (existing or {}).get("vip_rules"),
                "relationship_strength": relationship_strength,
                "total_emails": total_emails,
                "last_contacted": email.get("received_at") or datetime.now(timezone.utc),
                "meeting_count": meeting_count,
                "last_meeting": (existing or {}).get("last_meeting"),
                "topics_discussed": topics[:20],
                "open_commitments": (existing or {}).get("open_commitments") or [],
                "their_open_commitments": (existing or {}).get("their_open_commitments") or [],
                "sentiment_trend": (existing or {}).get("sentiment_trend"),
                "known_facts": (existing or {}).get("known_facts"),
                "personal_notes": (existing or {}).get("personal_notes"),
                "tags": (existing or {}).get("tags") or [],
                "style_profile": (existing or {}).get("style_profile"),
                "updated_at": datetime.now(timezone.utc),
            },
            conflict_columns=["email", "user_id"],
        )

        await sentiment_analyser.update_contact_trend(user_id, contact_email)


relationship_engine = RelationshipEngine()
