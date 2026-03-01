"""
Email sentiment analysis — Phase 2 (basic) / Phase 6 (trend tracking).
"""

from app.services.ai_service import ai_service


class SentimentAnalyser:

    async def analyse(self, email: dict) -> dict:
        """Return sentiment metadata for a single email."""
        return await ai_service.analyse_sentiment(email)

    async def update_contact_trend(self, user_id: str, contact_email: str) -> None:
        """
        TODO Phase 6: compute sentiment trend for a contact from their last N
        emails and update contacts.sentiment_trend.
        """
        raise NotImplementedError


sentiment_analyser = SentimentAnalyser()
