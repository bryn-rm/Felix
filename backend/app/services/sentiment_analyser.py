"""
Email sentiment analysis — Phase 2 (basic) / Phase 6 (trend tracking).
"""

import logging

from app import db
from app.services.ai_service import ai_service

logger = logging.getLogger(__name__)

_SENTIMENT_SCORE = {
    "positive":   1,
    "neutral":    0,
    "stressed":  -1,
    "urgent":    -1,
    "frustrated":-2,
}


class SentimentAnalyser:

    async def analyse(self, email: dict) -> dict:
        """Return sentiment metadata for a single email."""
        return await ai_service.analyse_sentiment(email)

    async def update_contact_trend(self, user_id: str, contact_email: str) -> None:
        """
        Compute a sentiment trend for a contact from their last 10 inbound emails
        and update contacts.sentiment_trend.

        Trend logic: compare the average sentiment of the most-recent half of
        emails against the older half.
          • recent avg > older avg by >0.4 → "improving"
          • recent avg < older avg by >0.4 → "deteriorating"
          • otherwise                       → "stable"
        """
        rows = await db.query(
            """
            SELECT sentiment
            FROM emails
            WHERE user_id = $1
              AND from_email = $2
              AND sentiment IS NOT NULL
            ORDER BY received_at DESC
            LIMIT 10
            """,
            user_id, contact_email,
        )

        if not rows:
            return

        scores = [_SENTIMENT_SCORE.get(r["sentiment"], 0) for r in rows]

        if len(scores) < 2:
            trend = "stable"
        else:
            mid = len(scores) // 2
            recent_avg = sum(scores[:mid]) / mid
            older_avg = sum(scores[mid:]) / (len(scores) - mid)
            diff = recent_avg - older_avg
            if diff > 0.4:
                trend = "improving"
            elif diff < -0.4:
                trend = "deteriorating"
            else:
                trend = "stable"

        await db.execute(
            "UPDATE contacts SET sentiment_trend = $1 WHERE email = $2 AND user_id = $3",
            trend, contact_email, user_id,
        )
        logger.debug(
            "Sentiment trend for %s / user %s → %s", contact_email, user_id, trend
        )


sentiment_analyser = SentimentAnalyser()
