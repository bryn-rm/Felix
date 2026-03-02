"""
All Claude API calls live here.

Models:
  claude-sonnet-4-6        → drafts, style analysis, meeting notes, briefing
  claude-haiku-4-5-20251001 → triage, voice intent routing, follow-up detection

draft_reply() is an async generator — consume with:
  async for chunk in ai_service.draft_reply(...):
      ...
"""

import json
import logging
from typing import AsyncGenerator

from anthropic import AsyncAnthropic

from app.config import settings
from app.prompts.briefing import BRIEFING_PROMPT
from app.prompts.draft import DRAFT_PROMPT
from app.prompts.follow_up_detection import FOLLOW_UP_DETECTION_PROMPT
from app.prompts.meeting_notes import MEETING_NOTES_PROMPT
from app.prompts.sentiment import SENTIMENT_PROMPT
from app.prompts.style_analysis import STYLE_ANALYSIS_PROMPT
from app.prompts.triage import TRIAGE_PROMPT
from app.prompts.voice_intent import VOICE_INTENT_PROMPT

logger = logging.getLogger(__name__)

client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


class AIService:

    # ------------------------------------------------------------------
    # Triage — Haiku (fast + cheap, called for every incoming email)
    # ------------------------------------------------------------------

    async def triage_email(
        self,
        email: dict,
        vip_list: list[str],
        user_name: str,
    ) -> dict:
        """
        Classify an email and extract metadata.
        Returns parsed triage JSON dict.
        """
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL_FAST,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": TRIAGE_PROMPT.format(
                    user_name=user_name,
                    sender=email.get("from", ""),
                    subject=email.get("subject", ""),
                    body=email.get("body", "")[:3000],  # cap tokens
                    vip_list=", ".join(vip_list) if vip_list else "none",
                ),
            }],
        )
        try:
            return json.loads(response.content[0].text)
        except json.JSONDecodeError:
            logger.warning("Triage response was not valid JSON: %s", response.content[0].text)
            return {
                "category": "fyi",
                "urgency": "low",
                "topic": email.get("subject", ""),
                "sentiment_of_sender": "neutral",
                "requires_response_by": None,
                "key_entities": [],
            }

    # ------------------------------------------------------------------
    # Draft reply — Sonnet, streaming
    # ------------------------------------------------------------------

    async def draft_reply(
        self,
        email: dict,
        thread_history: list[dict],
        contact: dict,
        style_profile: dict,
        user_name: str,
        user_intent: str = "",
    ) -> AsyncGenerator[str, None]:
        """
        Stream a draft reply token by token.

        Usage:
            async for chunk in ai_service.draft_reply(...):
                full_text += chunk
        """
        async with client.messages.stream(
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": DRAFT_PROMPT.format(
                    user_name=user_name,
                    formality=style_profile.get("formality_score", 0.5),
                    avg_words=int(style_profile.get("avg_words_per_email", 100)),
                    greeting=_first(style_profile.get("greeting_patterns"), "Hi"),
                    sign_off=_first(style_profile.get("sign_off_patterns"), "Thanks"),
                    bullet_tendency=style_profile.get("bullet_point_tendency", 0),
                    phrases=", ".join(style_profile.get("hedging_language") or []) or "none",
                    relationship_context=contact.get("their_communication_style") or "unknown",
                    thread_history=_format_thread(thread_history),
                    meeting_context="none",
                    calendar_context="none",
                    email_content=_format_email(email),
                    user_intent=user_intent or "Reply appropriately",
                ),
            }],
        ) as stream:
            async for text in stream.text_stream:
                yield text

    # ------------------------------------------------------------------
    # Style analysis — Sonnet (runs once on onboarding, weekly refresh)
    # ------------------------------------------------------------------

    async def analyse_writing_style(self, sent_emails: list[dict]) -> dict:
        """
        Build a StyleProfile from the user's sent email history.
        Samples up to 50 emails to stay within token limits.
        """
        sample = sent_emails[:50]
        if not sample:
            return _default_style_profile()

        email_text = "\n\n---\n\n".join(
            f"To: {e.get('to', '')}\nSubject: {e.get('subject', '')}\n{e.get('body', '')[:500]}"
            for e in sample
        )
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": STYLE_ANALYSIS_PROMPT.format(emails=email_text),
            }],
        )
        try:
            return json.loads(response.content[0].text)
        except json.JSONDecodeError:
            logger.warning("Style analysis response was not valid JSON")
            return _default_style_profile()

    # ------------------------------------------------------------------
    # Meeting notes — Sonnet
    # ------------------------------------------------------------------

    async def generate_meeting_notes(
        self,
        transcript: str,
        attendees: list[str],
        title: str,
    ) -> dict:
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": MEETING_NOTES_PROMPT.format(
                    meeting_title=title,
                    attendees=", ".join(attendees),
                    transcript=transcript,
                ),
            }],
        )
        try:
            return json.loads(response.content[0].text)
        except json.JSONDecodeError:
            logger.warning("Meeting notes response was not valid JSON")
            return {"summary": response.content[0].text, "action_items": [], "decisions": [], "open_questions": []}

    # ------------------------------------------------------------------
    # Daily briefing — Sonnet
    # ------------------------------------------------------------------

    async def generate_daily_briefing(self, context: dict) -> str:
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": BRIEFING_PROMPT.format(**context),
            }],
        )
        return response.content[0].text

    # ------------------------------------------------------------------
    # Voice intent routing — Haiku (latency critical)
    # ------------------------------------------------------------------

    async def parse_voice_intent(self, transcript: str) -> dict:
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL_FAST,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": VOICE_INTENT_PROMPT.format(transcript=transcript),
            }],
        )
        try:
            return json.loads(response.content[0].text)
        except json.JSONDecodeError:
            logger.warning("Voice intent response was not valid JSON: %s", response.content[0].text)
            return {"intent": "general_question", "raw_transcript": transcript}

    # ------------------------------------------------------------------
    # Follow-up detection — Haiku
    # ------------------------------------------------------------------

    async def detect_follow_ups(self, sent_email: dict) -> dict | None:
        """Return follow-up metadata if the sent email needs tracking, else None."""
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL_FAST,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": FOLLOW_UP_DETECTION_PROMPT.format(
                    to=sent_email.get("to", ""),
                    subject=sent_email.get("subject", ""),
                    body=sent_email.get("body", "")[:2000],
                ),
            }],
        )
        try:
            result = json.loads(response.content[0].text)
        except json.JSONDecodeError:
            return None
        return result if result.get("needs_follow_up") else None

    # ------------------------------------------------------------------
    # Sentiment analysis — Haiku
    # ------------------------------------------------------------------

    async def analyse_sentiment(self, email: dict) -> dict:
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL_FAST,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": SENTIMENT_PROMPT.format(
                    sender=email.get("from", ""),
                    subject=email.get("subject", ""),
                    body=email.get("body", "")[:2000],
                ),
            }],
        )
        try:
            return json.loads(response.content[0].text)
        except json.JSONDecodeError:
            return {"sentiment": "neutral", "urgency_signals": [], "pressure_level": "none", "notable_phrases": []}


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere
# ---------------------------------------------------------------------------

ai_service = AIService()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _first(lst: list | None, default: str) -> str:
    if lst:
        return lst[0]
    return default


def _format_thread(thread: list[dict]) -> str:
    if not thread:
        return "No prior thread."
    # Show last 3 messages for context, oldest first
    recent = thread[-3:]
    return "\n\n".join(
        f"From: {m.get('from', '')}\nSubject: {m.get('subject', '')}\n{m.get('body', '')[:400]}"
        for m in recent
    )


def _format_email(email: dict) -> str:
    return (
        f"From: {email.get('from', '')}\n"
        f"Subject: {email.get('subject', '')}\n\n"
        f"{email.get('body', '')}"
    )


def _default_style_profile() -> dict:
    return {
        "avg_words_per_email": 100,
        "formality_score": 0.5,
        "greeting_patterns": ["Hi"],
        "sign_off_patterns": ["Thanks"],
        "bullet_point_tendency": 0.2,
        "emoji_frequency": 0.0,
        "question_tendency": 0.3,
        "hedging_language": [],
        "directness_score": 0.5,
        "avg_response_time_hours": None,
        "style_notes": "No style profile built yet.",
    }
