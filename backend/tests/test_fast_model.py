"""Provider contract tests use real HTTP serialization with an in-memory transport."""
import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.prompts.fast_schemas import FAST_SCHEMAS
from app.services import ai_service as ai


def response_body(text='{"card":null}', **overrides):
    return {
        "status": "completed", "usage": {"input_tokens": 3, "output_tokens": 1},
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        **overrides,
    }


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setattr(settings, "AI_MODEL_FAST", "gpt-5.6-luna")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-openai-key")
    original_client = httpx.AsyncClient
    requests = []

    def install(body=None, handler=None):
        def respond(request):
            requests.append(request)
            return handler(request) if handler else httpx.Response(200, json=body or response_body())
        monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond), **kwargs,
        ))
        return requests
    return install


async def test_luna_request_and_fractional_metering(wire, monkeypatch):
    requests = wire()
    insert = AsyncMock(return_value={"id": "call-1"})
    monkeypatch.setattr("app.db.insert", insert)
    result = await ai.call_fast(
        feature="live_assist_watch", max_tokens=350, system="Trusted instructions",
        messages=[{"role": "user", "content": "<transcript>untrusted</transcript>"}],
    )
    request = requests[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["Authorization"] == "Bearer test-openai-key"
    assert body["model"] == "gpt-5.6-luna"
    assert body["reasoning"] == {"effort": "none"}
    assert body["store"] is False
    assert body["max_output_tokens"] == 350
    assert body["instructions"] == "Trusted instructions"
    assert body["input"][0]["role"] == "user"
    assert body["text"]["format"]["strict"] is True
    assert body["text"]["format"]["schema"] == FAST_SCHEMAS["live_assist_watch"]
    assert "thinking" not in body and "max_tokens" not in body
    assert result.text == '{"card":null}'
    await ai.log_ai_call(feature="live_assist_watch", model=settings.AI_MODEL_FAST,
                         response=result, started_at=time.monotonic(), user_id="u-1")
    row = insert.await_args.args[1]
    assert (row["input_tokens"], row["output_tokens"]) == (3, 1)
    assert row["billable_tokens"] == 4
    assert row["billable_units"] == pytest.approx(1.8)
    assert row["quota_scope"] == "interactive"
    assert row["success"] is True


async def test_plain_text_voice_does_not_request_json(wire):
    requests = wire(response_body("Hello"))
    result = await ai.call_fast(feature="voice_general", max_tokens=300,
                                messages=[{"role": "user", "content": "Hi"}])
    assert result.text == "Hello"
    assert "text" not in json.loads(requests[0].content)


@pytest.mark.parametrize("feature,payload", [
    ("triage", {"category": "fyi", "urgency": "low"}),
    ("voice_intent", {"intent": "general_question", "raw_transcript": "Hello"}),
    ("voice_general", "Hello"),
    ("follow_up_detect", {"needs_follow_up": True, "topic": "Invoice"}),
    ("commitment_detect", {"commitments": []}),
    ("sentiment", {"sentiment_of_sender": "neutral"}),
    ("session_summary", {"summary": "Discussed an invoice.", "open_items": []}),
    ("episode_distil", {"summary": "Discussed an invoice.", "entities": [], "importance": 0.5}),
    ("profile_extract", {"profile": {"name": None}, "preferences": {"email_tone": None}}),
    ("live_assist_watch", {"card": None}),
    ("live_assist_interview_watch", {"card": None, "interview_question": None}),
])
async def test_every_fast_caller_reaches_openai_and_logs(wire, monkeypatch, feature, payload):
    from app.services import memory_service, session_manager, live_assist_service as las

    requests = wire(response_body(payload if isinstance(payload, str) else json.dumps(payload)))
    insert = AsyncMock(return_value={"id": "call-1"})
    monkeypatch.setattr("app.db.insert", insert)
    monkeypatch.setattr(ai, "_auto_memory", AsyncMock(return_value=None))
    # Any accidental Anthropic routing is a hard test failure.
    monkeypatch.setattr(ai.client.messages, "create", AsyncMock(side_effect=AssertionError("Wrong provider")))
    svc = ai.AIService()
    if feature == "triage":
        result = await svc.triage_email({}, [], "User", user_id="u-1")
    elif feature == "voice_intent":
        result = await svc.parse_voice_intent("Hello", user_id="u-1")
    elif feature == "voice_general":
        result = await svc.answer_general_voice_question("Hello", "User", user_id="u-1")
    elif feature == "follow_up_detect":
        result = await svc.detect_follow_ups({}, user_id="u-1")
    elif feature == "commitment_detect":
        result = await svc.detect_commitments({}, source_kind="inbound", user_name="User",
                                              user_email="u@example.com", counterparty_email="c@example.com",
                                              user_id="u-1")
    elif feature == "sentiment":
        result = await svc.analyse_sentiment({}, user_id="u-1")
    elif feature == "session_summary":
        result = await session_manager._summarise_conversation("u-1", "Hello")
    elif feature in ("profile_extract", "episode_distil"):
        result = await memory_service._claude_json(feature=feature, prompt="Activity", user_id="u-1")
    else:
        watcher = object.__new__(las.LiveAssistWatcher)
        watcher.user_id = "u-1"
        watcher.meeting_id = "m-1"
        result = await watcher._json_ai_call(feature=feature, model=settings.AI_MODEL_FAST,
                                            max_tokens=350, prompt="Transcript")
    assert result == ([] if feature == "commitment_detect" else payload)
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    if feature != "voice_general":
        assert body["text"]["format"]["schema"] == FAST_SCHEMAS[feature]
    row = insert.await_args.args[1]
    assert row["model"] == "gpt-5.6-luna" and row["feature"] == feature
    assert row["success"] is True and row["parse_error"] is False
    assert row["billable_units"] == pytest.approx(1.8)
    assert row["quota_scope"] == ("interactive" if feature.startswith(("voice_", "live_assist_")) else "background")


async def test_live_watch_truncation_never_emits_even_complete_json(wire, monkeypatch):
    from app.services.live_assist_service import LiveAssistWatcher
    wire(response_body(status="incomplete", incomplete_details={"reason": "max_output_tokens"}))
    monkeypatch.setattr("app.db.insert", AsyncMock(return_value={"id": "call-1"}))
    watcher = object.__new__(LiveAssistWatcher)
    watcher.user_id = "u-1"
    watcher.meeting_id = "m-1"
    result = await watcher._json_ai_call(feature="live_assist_watch", model=settings.AI_MODEL_FAST,
                                        max_tokens=350, prompt="Transcript")
    assert result is None
    assert watcher._last_call_truncated is True


@pytest.mark.parametrize("body,error", [
    (response_body(status="incomplete", incomplete_details={"reason": "max_output_tokens"}), "truncated"),
    (response_body(output=[{"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}]), "refused"),
    (response_body(status="failed"), "status"),
    (response_body(output=[]), "no text"),
])
async def test_unusable_output_keeps_usage_but_cannot_succeed(wire, monkeypatch, body, error):
    wire(body)
    result = await ai.call_fast(feature="live_assist_watch", max_tokens=350, messages=[])
    with pytest.raises(ValueError, match=error):
        _ = result.text
    insert = AsyncMock(return_value={"id": "call-1"})
    monkeypatch.setattr("app.db.insert", insert)
    await ai.log_ai_call(feature="live_assist_watch", model=settings.AI_MODEL_FAST,
                         response=result, started_at=time.monotonic())
    row = insert.await_args.args[1]
    assert row["success"] is False
    assert row["parse_error"] is True
    assert row["billable_units"] == pytest.approx(1.8)


async def test_retries_provider_errors_within_deadline(wire, monkeypatch):
    replies = [httpx.Response(429), httpx.Response(503), httpx.Response(200, json=response_body())]
    requests = wire(handler=lambda request: replies.pop(0))
    monkeypatch.setattr(ai.asyncio, "sleep", AsyncMock())
    result = await ai.call_fast(feature="live_assist_watch", max_tokens=350, messages=[])
    assert result.error is None
    assert len(requests) == 3


async def test_bad_key_is_not_retried(wire):
    requests = wire(handler=lambda request: httpx.Response(401))
    with pytest.raises(httpx.HTTPStatusError):
        await ai.call_fast(feature="live_assist_watch", max_tokens=350, messages=[])
    assert len(requests) == 1


async def test_exhausted_openai_quota_trips_inbox_circuit_breaker(wire, monkeypatch):
    from app.jobs.inbox_sync import _is_provider_quota_error
    requests = wire(handler=lambda request: httpx.Response(429))
    monkeypatch.setattr(ai.asyncio, "sleep", AsyncMock())
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await ai.call_fast(feature="triage", max_tokens=500, messages=[])
    assert _is_provider_quota_error(exc.value)
    assert len(requests) == 3


async def test_total_timeout_cancels_http_request(wire, monkeypatch):
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=response_body())
    original_client = httpx.AsyncClient
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(slow), **kwargs,
    ))
    with pytest.raises(TimeoutError):
        await ai.call_fast(feature="live_assist_watch", max_tokens=350, messages=[], timeout=0.01)


async def test_claude_rollback_preserves_system_and_thinking(monkeypatch):
    create = AsyncMock(return_value=SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"card":null}')],
        usage=SimpleNamespace(input_tokens=10, output_tokens=2), stop_reason="end_turn",
    ))
    monkeypatch.setattr(ai.client.messages, "create", create)
    result = await ai.call_fast(feature="live_assist_watch", model="claude-sonnet-5",
                                messages=[], system="Trusted", max_tokens=350)
    assert create.await_args.kwargs["thinking"] == {"type": "disabled"}
    assert create.await_args.kwargs["system"] == "Trusted"
    assert result.usage.input_tokens == 10
    assert result.text == '{"card":null}'


def test_model_weights():
    assert ai._estimate_billable_units("claude-sonnet-5", 100, 10) == 300
    assert ai._estimate_billable_units("claude-sonnet-4-6", 100, 10) == 450
    assert ai._estimate_billable_units("gpt-5.6-luna", 100, 10) == 32


def test_settings_default_and_key_validation(monkeypatch):
    monkeypatch.delenv("AI_MODEL_FAST", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL_FAST", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert Settings(_env_file=None).AI_MODEL_FAST == "gpt-5.6-luna"
    monkeypatch.setenv("OPENAI_API_KEY", " ")
    with pytest.raises(ValidationError, match="OPENAI_API_KEY is required"):
        Settings(_env_file=None)
    monkeypatch.setenv("AI_MODEL_FAST", "claude-haiku-4-5-20251001")
    assert Settings(_env_file=None).AI_MODEL_FAST.startswith("claude-")
    monkeypatch.setenv("AI_MODEL_FAST", "unknown-provider")
    with pytest.raises(ValidationError, match="AI_MODEL_FAST must be"):
        Settings(_env_file=None)


def test_new_setting_overrides_legacy_env_and_dotenv(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_MODEL_FAST", "claude-haiku-4-5-20251001")
    monkeypatch.delenv("AI_MODEL_FAST", raising=False)
    assert Settings(_env_file=None).AI_MODEL_FAST.startswith("claude-")
    monkeypatch.setenv("AI_MODEL_FAST", "gpt-5.6-luna")
    assert Settings(_env_file=None).AI_MODEL_FAST == "gpt-5.6-luna"
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_MODEL_FAST=claude-haiku-4-5-20251001\n")
    assert Settings(_env_file=env_file).AI_MODEL_FAST == "gpt-5.6-luna"


@pytest.mark.parametrize("feature", FAST_SCHEMAS)
def test_schemas_obey_strict_object_rules(feature):
    def check(schema):
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])
            for child in schema["properties"].values():
                check(child)
        if "items" in schema:
            check(schema["items"])
        for child in schema.get("anyOf", []):
            check(child)
    assert FAST_SCHEMAS[feature]["type"] == "object"
    check(FAST_SCHEMAS[feature])
