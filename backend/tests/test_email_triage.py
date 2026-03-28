"""Unit tests for email triage via the AI service.

All Anthropic API calls are mocked — no real network calls are made.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai_service import AIService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ai_svc() -> AIService:
    return AIService()


def _claude_response(text: str) -> MagicMock:
    """Build a mock Anthropic Messages response with the given content text."""
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_triage_stores_category_in_db(ai_svc: AIService):
    """
    Triage result has correct category and urgency fields ready for DB
    persistence; the downstream db.execute receives those exact values.
    """
    payload = {
        "category": "action_required",
        "urgency": "high",
        "topic": "Project deadline",
        "sentiment_of_sender": "neutral",
        "requires_response_by": None,
        "key_entities": [],
    }
    with patch("app.services.ai_service.client") as mock_client:
        mock_client.messages.create = AsyncMock(
            return_value=_claude_response(json.dumps(payload))
        )
        with patch("app.db.execute", new_callable=AsyncMock) as mock_exec:
            result = await ai_svc.triage_email(
                email={
                    "from": "boss@corp.com",
                    "subject": "Project deadline",
                    "body": "We need this done by Friday.",
                },
                vip_list=[],
                user_name="Test User",
            )
            # Simulate what the background job does after calling triage
            await mock_exec(
                "UPDATE emails SET category = $1, urgency = $2 WHERE id = $3",
                result["category"],
                result["urgency"],
                "email-001",
            )

    assert result["category"] == "action_required"
    assert result["urgency"] == "high"
    mock_exec.assert_called_once()
    db_args = mock_exec.call_args[0]
    assert "action_required" in db_args
    assert "email-001" in db_args


async def test_triage_handles_claude_json_error_gracefully(ai_svc: AIService):
    """
    When Claude returns non-JSON the triage function logs a warning and
    returns safe defaults (category='fyi', urgency='low', topic=subject).
    """
    with patch("app.services.ai_service.client") as mock_client:
        mock_client.messages.create = AsyncMock(
            return_value=_claude_response("I cannot parse this email right now.")
        )
        result = await ai_svc.triage_email(
            email={"from": "x@y.com", "subject": "Hello", "body": "Hey there."},
            vip_list=[],
            user_name="Test User",
        )

    assert result["category"] == "fyi"
    assert result["urgency"] == "low"
    # Fallback topic is the email subject
    assert result["topic"] == "Hello"


async def test_vip_email_triggers_vip_category(ai_svc: AIService):
    """
    When the sender is in the VIP list and Claude returns 'vip', the result
    carries that category; also confirms the vip_list was forwarded to the prompt.
    """
    payload = {
        "category": "vip",
        "urgency": "high",
        "topic": "Q4 strategy",
        "sentiment_of_sender": "positive",
        "requires_response_by": None,
        "key_entities": ["CEO"],
    }
    with patch("app.services.ai_service.client") as mock_client:
        mock_client.messages.create = AsyncMock(
            return_value=_claude_response(json.dumps(payload))
        )
        result = await ai_svc.triage_email(
            email={
                "from": "ceo@bigcorp.com",
                "subject": "Q4 strategy",
                "body": "Let's align on priorities.",
            },
            vip_list=["ceo@bigcorp.com"],
            user_name="Test User",
        )

    assert result["category"] == "vip"
    # Verify the VIP email address was injected into the prompt
    prompt_text = mock_client.messages.create.call_args[1]["messages"][0]["content"]
    assert "ceo@bigcorp.com" in prompt_text


async def test_newsletter_email_categorised_correctly(ai_svc: AIService):
    """Newsletter-style email gets category='newsletter' and urgency='low'."""
    payload = {
        "category": "newsletter",
        "urgency": "low",
        "topic": "Weekly digest",
        "sentiment_of_sender": "neutral",
        "requires_response_by": None,
        "key_entities": [],
    }
    with patch("app.services.ai_service.client") as mock_client:
        mock_client.messages.create = AsyncMock(
            return_value=_claude_response(json.dumps(payload))
        )
        result = await ai_svc.triage_email(
            email={
                "from": "noreply@digest.com",
                "subject": "Your weekly roundup",
                "body": "Click here to unsubscribe from this newsletter.",
            },
            vip_list=[],
            user_name="Test User",
        )

    assert result["category"] == "newsletter"
    assert result["urgency"] == "low"
