"""
Email sentiment analysis — Phase 2 (basic) / Phase 6 (trend tracking).
"""

from app import db
from app.services.ai_service import ai_service


class SentimentAnalyser:

    async def analyse(self, email: dict) -> dict:
        """Return sentiment metadata for a single email."""
        return await ai_service.analyse_sentiment(email)

    async def update_contact_trend(self, user_id: str, contact_email: str) -> None:
        """Compute and persist sentiment trend from recent incoming emails."""
        rows = await db.query(
            """
            SELECT sentiment
            FROM emails
            WHERE user_id = $1
              AND from_email = $2
              AND sentiment IS NOT NULL
            ORDER BY received_at DESC
            LIMIT 20
            """,
            user_id,
            contact_email,
        )
        if len(rows) < 4:
            return

        scores = []
        for row in rows:
            sentiment = (row.get("sentiment") or "").lower()
            if sentiment == "positive":
                scores.append(1.0)
            elif sentiment in ("neutral",):
                scores.append(0.0)
            elif sentiment in ("stressed", "urgent"):
                scores.append(-0.5)
            elif sentiment == "frustrated":
                scores.append(-1.0)

        if len(scores) < 4:
            return

        split = max(1, len(scores) // 2)
        recent_avg = sum(scores[:split]) / split
        prior_avg = sum(scores[split:]) / len(scores[split:])
        delta = recent_avg - prior_avg

        if delta >= 0.2:
            trend = "improving"
        elif delta <= -0.2:
            trend = "deteriorating"
        else:
            trend = "stable"

        await db.execute(
            "UPDATE contacts SET sentiment_trend = $1, updated_at = NOW() WHERE user_id = $2 AND email = $3",
            trend,
            user_id,
            contact_email,
        )


sentiment_analyser = SentimentAnalyser()
