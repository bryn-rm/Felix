"""Tests for unit-based AI quota accounting.

Covers the move from COUNT(*) row-based limits to SUM(billable_units) on
interactive-scope calls, plus the inbox-sync provider-quota circuit breaker.

All Anthropic / DB access is mocked — no real network or database calls.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services.ai_service import _estimate_billable_units, log_ai_call


# ---------------------------------------------------------------------------
# _estimate_billable_units — cost-weighted, output far heavier than input
# ---------------------------------------------------------------------------


def test_estimate_units_sonnet_weighting():
    # 3 * input + 15 * output
    assert _estimate_billable_units("claude-sonnet-4-6", 100, 10) == 100 * 3 + 10 * 15


def test_estimate_units_haiku_weighting():
    # 1 * input + 5 * output
    assert _estimate_billable_units("claude-haiku-4-5-20251001", 100, 10) == 100 + 10 * 5


def test_estimate_units_unknown_model_default():
    assert _estimate_billable_units("some-other-model", 100, 10) == 100 + 10 * 4


def test_sonnet_output_costs_more_than_haiku_output():
    sonnet = _estimate_billable_units("claude-sonnet-4-6", 0, 100)
    haiku = _estimate_billable_units("claude-haiku-4-5-20251001", 0, 100)
    assert sonnet > haiku


# ---------------------------------------------------------------------------
# log_ai_call — populates billable fields + quota_scope, NULL on no usage
# ---------------------------------------------------------------------------


def _usage_response(input_tokens: int, output_tokens: int) -> MagicMock:
    resp = MagicMock()
    resp.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return resp


async def test_log_ai_call_computes_billable_and_scope():
    captured = {}

    async def fake_insert(table, data):
        captured["table"] = table
        captured["data"] = data
        return {"id": "row-1"}

    with patch("app.db.insert", new=AsyncMock(side_effect=fake_insert)):
        await log_ai_call(
            feature="triage",
            model="claude-haiku-4-5-20251001",
            response=_usage_response(100, 50),
            started_at=0.0,
            user_id="u1",
            quota_scope="background",
        )

    data = captured["data"]
    assert data["quota_scope"] == "background"
    assert data["billable_tokens"] == 150
    assert data["billable_units"] == 100 + 50 * 5


async def test_log_ai_call_no_usage_leaves_billable_null():
    """A failed call (no usage block) must not consume quota."""
    captured = {}

    async def fake_insert(table, data):
        captured["data"] = data
        return {"id": "row-2"}

    with patch("app.db.insert", new=AsyncMock(side_effect=fake_insert)):
        await log_ai_call(
            feature="draft",
            model="claude-sonnet-4-6",
            response=None,  # provider error → no usage
            started_at=0.0,
            user_id="u1",
            success=False,
            error_message="Your credit balance is too low",
        )

    data = captured["data"]
    assert data["billable_tokens"] is None
    assert data["billable_units"] is None
    # Default scope is interactive even on failure — but NULL units mean it
    # still won't be summed by the quota query.
    assert data["quota_scope"] == "interactive"


# ---------------------------------------------------------------------------
# check_monthly_ai_budget — SUM(billable_units), interactive scope only
# ---------------------------------------------------------------------------


async def test_budget_query_sums_interactive_billable_units(monkeypatch):
    monkeypatch.setattr(settings, "MONTHLY_AI_UNIT_LIMIT", 1_000.0)
    captured = {}

    async def fake_query_one(sql, *args):
        captured["sql"] = sql
        captured["args"] = args
        return {"used": 10}  # well under cap

    from app.middleware import rate_limit

    with patch.object(rate_limit.db, "query_one", new=AsyncMock(side_effect=fake_query_one)):
        await rate_limit.check_monthly_ai_budget("u1", "user@example.com")

    sql = captured["sql"]
    assert "SUM(billable_units)" in sql
    assert "quota_scope = 'interactive'" in sql
    assert "billable_units IS NOT NULL" in sql
    assert "COUNT(*)" not in sql


async def test_budget_blocks_when_units_exceed_cap(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "MONTHLY_AI_UNIT_LIMIT", 500.0)
    from app.middleware import rate_limit

    with patch.object(
        rate_limit.db, "query_one", new=AsyncMock(return_value={"used": 500})
    ):
        with pytest.raises(HTTPException) as exc_info:
            await rate_limit.check_monthly_ai_budget("u1", "user@example.com")
    assert exc_info.value.status_code == 429


async def test_budget_allows_when_under_cap(monkeypatch):
    monkeypatch.setattr(settings, "MONTHLY_AI_UNIT_LIMIT", 500.0)
    from app.middleware import rate_limit

    with patch.object(
        rate_limit.db, "query_one", new=AsyncMock(return_value={"used": 499})
    ):
        # Should not raise.
        await rate_limit.check_monthly_ai_budget("u1", "user@example.com")


async def test_budget_unlimited_cap_short_circuits(monkeypatch):
    """cap <= 0 means unlimited — the DB is never queried."""
    monkeypatch.setattr(settings, "MONTHLY_AI_UNIT_LIMIT", 0.0)
    from app.middleware import rate_limit

    query_one = AsyncMock(side_effect=AssertionError("DB should not be queried"))
    with patch.object(rate_limit.db, "query_one", new=query_one):
        await rate_limit.check_monthly_ai_budget("u1", "user@example.com")
    query_one.assert_not_called()


# ---------------------------------------------------------------------------
# Inbox-sync provider-quota circuit breaker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "Your credit balance is too low to access the Anthropic API",
        "credit balance exhausted",
        "monthly quota exceeded",
        "Rate limit reached for requests",
        "rate_limit_error",
    ],
)
def test_is_provider_quota_error_true(message):
    from app.jobs.inbox_sync import _is_provider_quota_error

    assert _is_provider_quota_error(Exception(message)) is True


@pytest.mark.parametrize(
    "message",
    ["JSONDecodeError: bad json", "connection reset", "some random failure"],
)
def test_is_provider_quota_error_false(message):
    from app.jobs.inbox_sync import _is_provider_quota_error

    assert _is_provider_quota_error(Exception(message)) is False


async def test_process_email_aborts_batch_on_provider_quota():
    """A provider credit error during triage must raise ProviderQuotaError so
    the batch loop stops instead of retrying every remaining email."""
    from app.jobs import inbox_sync

    email = {"id": "msg-1", "from_email": "a@b.com", "subject": "hi", "body": "x"}

    with patch.object(
        inbox_sync.memory_service, "build_memory_context",
        new=AsyncMock(return_value=None),
    ), patch.object(
        inbox_sync.ai_service, "triage_email",
        new=AsyncMock(side_effect=Exception("Your credit balance is too low")),
    ):
        with pytest.raises(inbox_sync.ProviderQuotaError):
            await inbox_sync._process_email(
                email=email,
                user_id="u1",
                gmail=AsyncMock(),
                vip_list=[],
                user_name="User",
                style_profile={},
                label_cache={},
            )


async def test_process_email_swallows_ordinary_failure():
    """A non-quota failure during triage is swallowed so the batch continues."""
    from app.jobs import inbox_sync

    email = {"id": "msg-2", "from_email": "a@b.com", "subject": "hi", "body": "x"}

    with patch.object(
        inbox_sync.memory_service, "build_memory_context",
        new=AsyncMock(return_value=None),
    ), patch.object(
        inbox_sync.ai_service, "triage_email",
        new=AsyncMock(side_effect=Exception("transient connection reset")),
    ):
        # Should NOT raise — ordinary per-email failures are logged and skipped.
        await inbox_sync._process_email(
            email=email,
            user_id="u1",
            gmail=AsyncMock(),
            vip_list=[],
            user_name="User",
            style_profile={},
            label_cache={},
        )
