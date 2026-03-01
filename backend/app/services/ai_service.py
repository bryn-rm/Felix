"""
All Claude API calls live here — Phase 2+.

Models:
  claude-sonnet-4-6  → drafts, style analysis, meeting notes, briefing
  claude-haiku-4-5   → triage, voice intent routing, follow-up detection
"""

import json
from typing import AsyncIterator

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

client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


class AIService:

    # ------------------------------------------------------------------
    # Triage (Haiku — fast + cheap)
    # ------------------------------------------------------------------

    async def triage_email(
        self, email: dict, vip_list: list[str], user_name: str
    ) -> dict:
        """Classify an email and extract metadata. Returns parsed JSON."""
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL_FAST,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": TRIAGE_PROMPT.format(
                    user_name=user_name,
                    sender=email.get("from", ""),
                    subject=email.get("subject", ""),
                    body=email.get("body", "")[:3000],
                    vip_list=", ".join(vip_list) if vip_list else "none",
                ),
            }],
        )
        return json.loads(response.content[0].text)

    # ------------------------------------------------------------------
    # Draft reply (Sonnet — streaming)
    # ------------------------------------------------------------------

    async def draft_reply(
        self,
        email: dict,
        thread_history: list[dict],
        contact: dict,
        style_profile: dict,
        user_name: str,
        user_intent: str = "",
    ) -> AsyncIterator[str]:
        """Stream a draft reply token by token."""
        async with client.messages.stream(
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": DRAFT_PROMPT.format(
                    user_name=user_name,
                    formality=style_profile.get("formality_score", 0.5),
                    avg_words=style_profile.get("avg_words_per_email", 100),
                    greeting=style_profile.get("greeting_patterns", ["Hi"])[0],
                    sign_off=style_profile.get("sign_off_patterns", ["Thanks"])[0],
                    bullet_tendency=style_profile.get("bullet_point_tendency", 0),
                    phrases=", ".join(style_profile.get("hedging_language", [])),
                    relationship_context=contact.get("their_communication_style", ""),
                    thread_history=_format_thread(thread_history),
                    meeting_context="",
                    calendar_context="",
                    email_content=_format_email(email),
                    user_intent=user_intent or "Reply appropriately",
                ),
            }],
        ) as stream:
            async for text in stream.text_stream:
                yield text

    # ------------------------------------------------------------------
    # Style analysis (Sonnet)
    # ------------------------------------------------------------------

    async def analyse_writing_style(self, sent_emails: list[dict]) -> dict:
        """Build a style profile from up to 50 sent emails."""
        sample = sent_emails[:50]
        email_text = "\n\n---\n\n".join(
            f"To: {e['to']}\nSubject: {e['subject']}\n{e['body'][:500]}"
            for e in sample
        )
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=1000,
            messages=[{"role": "user", "content": STYLE_ANALYSIS_PROMPT.format(emails=email_text)}],
        )
        return json.loads(response.content[0].text)

    # ------------------------------------------------------------------
    # Meeting notes (Sonnet)
    # ------------------------------------------------------------------

    async def generate_meeting_notes(
        self, transcript: str, attendees: list[str], title: str
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
        return json.loads(response.content[0].text)

    # ------------------------------------------------------------------
    # Daily briefing (Sonnet)
    # ------------------------------------------------------------------

    async def generate_daily_briefing(self, context: dict) -> str:
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=400,
            messages=[{"role": "user", "content": BRIEFING_PROMPT.format(**context)}],
        )
        return response.content[0].text

    # ------------------------------------------------------------------
    # Voice intent routing (Haiku — latency critical)
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
        return json.loads(response.content[0].text)

    # ------------------------------------------------------------------
    # Follow-up detection (Haiku)
    # ------------------------------------------------------------------

    async def detect_follow_ups(self, sent_email: dict) -> dict | None:
        """Return follow-up metadata if the email needs one, else None."""
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
        result = json.loads(response.content[0].text)
        return result if result.get("needs_follow_up") else None

    # ------------------------------------------------------------------
    # Sentiment analysis (Haiku)
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
        return json.loads(response.content[0].text)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_thread(thread: list[dict]) -> str:
    if not thread:
        return "No prior thread."
    return "\n\n".join(
        f"From: {m.get('from', '')}\n{m.get('body', '')[:300]}"
        for m in thread[-3:]
    )


def _format_email(email: dict) -> str:
    return (
        f"From: {email.get('from', '')}\n"
        f"Subject: {email.get('subject', '')}\n\n"
        f"{email.get('body', '')}"
    )


ai_service = AIService()
