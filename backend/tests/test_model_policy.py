"""Tests for the per-model thinking policy and cost weighting.

Claude 5-series models think by default when `thinking` is omitted, and
max_tokens is one ceiling over thinking + reply. Every call in this codebase
budgets max_tokens for the reply alone, so thinking must be disabled
explicitly. Older models are sent no thinking parameter at all.
"""
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai_service import (
    AIService,
    _estimate_billable_units,
    thinking_kwarg,
)


# ---------------------------------------------------------------------------
# thinking_kwarg
# ---------------------------------------------------------------------------


def test_thinking_disabled_for_models_that_think_by_default():
    for model in ("claude-sonnet-5", "claude-opus-5", "claude-fable-5"):
        assert thinking_kwarg(model) == {"thinking": {"type": "disabled"}}


def test_no_thinking_param_for_older_models():
    """Haiku 4.5 and Sonnet 4.6 never think unless asked — send nothing."""
    for model in ("claude-haiku-4-5-20251001", "claude-sonnet-4-6", ""):
        assert thinking_kwarg(model) == {}


# ---------------------------------------------------------------------------
# The policy actually reaches the wire
# ---------------------------------------------------------------------------


def _claude_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.usage = MagicMock(input_tokens=10, output_tokens=5)
    return resp


async def test_smart_call_disables_thinking_on_a_5_series_model():
    """A tight max_tokens budget must not be eaten by adaptive thinking."""
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _claude_response('{"summary": "ok", "actions": [], "decisions": []}')

    with patch("app.services.ai_service.client") as mock_client, \
         patch("app.services.ai_service.SMART_THINKING",
               {"thinking": {"type": "disabled"}}), \
         patch("app.db.insert", new=AsyncMock(return_value={"id": "ai-call-1"})):
        mock_client.messages.create = AsyncMock(side_effect=fake_create)
        await AIService().summarize_meeting(
            transcript="me: hi\nthem: hello",
            user_notes="",
            template="general",
            memory_context="",
        )

    assert captured["thinking"] == {"type": "disabled"}


# ---------------------------------------------------------------------------
# Cost weighting — an Opus experiment must not be metered as an unknown model
# ---------------------------------------------------------------------------


def test_estimate_units_opus_weighting():
    # 5 * input + 25 * output
    assert _estimate_billable_units("claude-opus-5", 100, 10) == 100 * 5 + 10 * 25


def test_opus_output_costs_more_than_sonnet_output():
    assert (
        _estimate_billable_units("claude-opus-5", 0, 100)
        > _estimate_billable_units("claude-sonnet-5", 0, 100)
    )
