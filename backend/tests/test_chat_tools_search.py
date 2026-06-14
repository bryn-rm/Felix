"""Tests for the chat agent's email-search tools.

Covers tokenization, cross-source dedup, result formatting, the index-friendly
local query shape, the get_email prefix dispatch, and the Gmail live fallback
trigger. All DB / Gmail access is mocked — no network or database calls.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import chat_tools
from app.services.chat_tools import (
    _dedupe_search_results,
    _format_local_results,
    _get_email,
    _like_escape,
    _search_emails,
    _search_local_email_cache,
    _search_terms,
)


# ---------------------------------------------------------------------------
# _search_terms — tokenization
# ---------------------------------------------------------------------------


def test_search_terms_strips_stopwords_and_filler():
    terms = _search_terms("find the emails from acme about invoice")
    # 'find', 'the', 'emails', 'from', 'about' are stop words.
    assert terms == ["acme", "invoice"]


def test_search_terms_dedupes_and_keeps_sender_tokens():
    terms = _search_terms("acme acme bob@acme.com")
    assert terms == ["acme", "bob@acme.com"]


def test_search_terms_drops_short_tokens_and_strips_punctuation():
    terms = _search_terms("a re: acme!!")
    # 'a' is below the 2-char minimum; 'acme!!' is punctuation-stripped to 'acme'.
    assert "acme" in terms
    assert "a" not in terms


def test_search_terms_caps_at_eight():
    terms = _search_terms("alpha bravo charlie delta echo foxtrot golf hotel india juliet")
    assert len(terms) == 8


def test_search_terms_empty_query():
    assert _search_terms("   ") == []


# ---------------------------------------------------------------------------
# _like_escape — LIKE metacharacters matched literally
# ---------------------------------------------------------------------------


def test_like_escape():
    assert _like_escape("100%_off") == "100\\%\\_off"
    assert _like_escape("a\\b") == "a\\\\b"
    assert _like_escape("plain") == "plain"


# ---------------------------------------------------------------------------
# _dedupe_search_results — collapse the same Gmail message across sources
# ---------------------------------------------------------------------------


def test_dedupe_collapses_local_and_gmail_same_id_local_first():
    results = [
        {"id": "abc", "source": "local_cache"},
        {"id": "gmail:abc", "source": "gmail_live"},
    ]
    deduped = _dedupe_search_results(results)
    assert len(deduped) == 1
    # Local listed first → local wins.
    assert deduped[0]["source"] == "local_cache"


def test_dedupe_collapses_sent_and_gmail_same_id():
    results = [
        {"id": "sent:xyz", "source": "local_cache"},
        {"id": "gmail:xyz", "source": "gmail_live"},
    ]
    deduped = _dedupe_search_results(results)
    assert len(deduped) == 1
    assert deduped[0]["id"] == "sent:xyz"


def test_dedupe_keeps_distinct_ids():
    results = [{"id": "a"}, {"id": "sent:b"}, {"id": "gmail:c"}]
    assert len(_dedupe_search_results(results)) == 3


# ---------------------------------------------------------------------------
# _format_local_results — sent rows get a sent: prefix
# ---------------------------------------------------------------------------


def test_format_local_results_prefixes_sent_ids():
    sort_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        {"mailbox": "inbound", "id": "i1", "sort_at": sort_at, "subject": "hi"},
        {"mailbox": "sent", "id": "s1", "sort_at": sort_at, "subject": "re: hi"},
    ]
    out = _format_local_results(rows)
    assert out[0]["id"] == "i1"
    assert out[0]["mailbox"] == "inbound"
    assert out[0]["source"] == "local_cache"
    assert out[1]["id"] == "sent:s1"
    assert out[1]["mailbox"] == "sent"
    assert out[1]["received_at"] == sort_at.isoformat()


# ---------------------------------------------------------------------------
# _search_local_email_cache — index-friendly query shape
# ---------------------------------------------------------------------------


async def test_local_cache_query_is_index_friendly():
    captured = {}

    async def fake_query(sql, *args):
        captured["sql"] = sql
        captured["args"] = args
        return []

    with patch.object(chat_tools.db, "query", new=AsyncMock(side_effect=fake_query)):
        await _search_local_email_cache("u1", "acme invoice", ["acme", "invoice"], 5)

    sql = captured["sql"]
    # Positive LIKE predicates against the exact indexed expressions, AND-ed.
    assert "LIKE $2" in sql
    assert "LIKE $3" in sql
    assert "UNION ALL" in sql
    assert chat_tools._INBOUND_HAYSTACK in sql
    assert chat_tools._SENT_HAYSTACK in sql
    # The old anti-join that defeated the index must be gone.
    assert "NOT EXISTS" not in sql
    assert "NOT LIKE" not in sql
    # Params: user_id, one wrapped+escaped pattern per term, then limit.
    assert captured["args"] == ("u1", "%acme%", "%invoice%", 5)


async def test_local_cache_escapes_like_metacharacters():
    captured = {}

    async def fake_query(sql, *args):
        captured["args"] = args
        return []

    with patch.object(chat_tools.db, "query", new=AsyncMock(side_effect=fake_query)):
        await _search_local_email_cache("u1", "50%_off", ["50%_off"], 5)

    assert captured["args"] == ("u1", "%50\\%\\_off%", 5)


async def test_local_cache_empty_terms_short_circuits():
    query = AsyncMock(side_effect=AssertionError("DB should not be queried"))
    with patch.object(chat_tools.db, "query", new=query):
        assert await _search_local_email_cache("u1", "", [], 5) == []
    query.assert_not_called()


# ---------------------------------------------------------------------------
# _get_email — prefix dispatch
# ---------------------------------------------------------------------------


async def test_get_email_dispatches_gmail_prefix():
    with patch.object(
        chat_tools, "_get_gmail_email", new=AsyncMock(return_value={"id": "gmail:abc"})
    ) as gmail_get:
        out = await _get_email("u1", "gmail:abc")
    gmail_get.assert_awaited_once_with("u1", "abc")
    assert out["id"] == "gmail:abc"


async def test_get_email_dispatches_sent_prefix():
    with patch.object(
        chat_tools, "_get_sent_email", new=AsyncMock(return_value={"id": "sent:xyz"})
    ) as sent_get:
        out = await _get_email("u1", "sent:xyz")
    sent_get.assert_awaited_once_with("u1", "xyz")
    assert out["id"] == "sent:xyz"


async def test_get_email_bare_id_queries_emails_table():
    row = {
        "id": "bare1", "from_name": "A", "from_email": "a@b.com", "to_email": "me@x.com",
        "subject": "Hi", "body": "body", "snippet": "s", "received_at": None,
        "category": "fyi", "topic": "t", "thread_id": "th1",
    }
    with patch.object(chat_tools.db, "query_one", new=AsyncMock(return_value=row)):
        out = await _get_email("u1", "bare1")
    assert out["id"] == "bare1"
    assert out["body"] == "body"


async def test_get_email_missing_id():
    out = await _get_email("u1", "")
    assert "error" in out


# ---------------------------------------------------------------------------
# _search_emails — Gmail fallback fires only when local is empty
# ---------------------------------------------------------------------------


async def test_search_skips_gmail_when_local_has_hits():
    local = [{"mailbox": "inbound", "id": "i1", "sort_at": None, "subject": "acme"}]
    with patch.object(
        chat_tools, "_search_local_email_cache", new=AsyncMock(return_value=local)
    ), patch.object(
        chat_tools, "_search_gmail_live", new=AsyncMock()
    ) as gmail_live:
        out = await _search_emails("u1", "acme", limit=5)

    gmail_live.assert_not_awaited()
    assert out["searched"]["gmail_live"] is False
    assert out["count"] == 1


async def test_search_falls_back_to_gmail_when_local_empty():
    live = [{"id": "gmail:g1", "source": "gmail_live", "mailbox": "inbound"}]
    with patch.object(
        chat_tools, "_search_local_email_cache", new=AsyncMock(return_value=[])
    ), patch.object(
        chat_tools, "_search_gmail_live", new=AsyncMock(return_value=live)
    ) as gmail_live:
        out = await _search_emails("u1", "acme", limit=5)

    gmail_live.assert_awaited_once()
    assert out["searched"]["gmail_live"] is True
    assert out["count"] == 1
    assert out["results"][0]["id"] == "gmail:g1"


async def test_search_empty_query_returns_no_results():
    out = await _search_emails("u1", "   ", limit=5)
    assert out["results"] == []
