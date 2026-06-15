"""Tests for the chat agent's email-search tools.

Covers tokenization, cross-source dedup, result formatting, the index-friendly
local query shape, the get_email prefix dispatch, and the Gmail live fallback
trigger. Most DB / Gmail access is mocked — no network or database calls.

The exception is ``test_local_cache_query_executes_against_postgres``, which
runs the real local-cache SQL against a live Postgres (when ``TEST_DATABASE_URL``
is set) so that a SQL semantic error — like the ``sort_at`` alias-resolution bug
the all-mocked tests could not see — fails the suite instead of crashing chat.
"""
import os
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


async def test_local_cache_fails_soft_on_db_error():
    """A DB / schema error must log and yield no rows, never raise into chat."""
    boom = AsyncMock(side_effect=RuntimeError("column \"sort_at\" does not exist"))
    with patch.object(chat_tools.db, "query", new=boom):
        out = await _search_local_email_cache("u1", "acme", ["acme"], 5)
    assert out == []
    boom.assert_awaited_once()


# ---------------------------------------------------------------------------
# _search_local_email_cache — the SQL actually prepares + executes on Postgres
#
# The mocked tests above capture the SQL string but never let Postgres parse it,
# so they cannot catch a query that is syntactically fine yet semantically
# invalid (e.g. referencing the `sort_at` SELECT-list alias from inside a UNION
# branch where it cannot be resolved). This test runs the real query against a
# live Postgres so that class of bug fails the suite. Skipped when no test DB is
# configured, so CI without Postgres still passes.
# ---------------------------------------------------------------------------

# Minimal schema mirroring the columns the local-cache query touches, plus the
# IMMUTABLE felix_array_text wrapper the sent-mail haystack/index depend on
# (see infra/migrations/014_email_search_indexes.sql).
_PG_SCHEMA = """
    CREATE OR REPLACE FUNCTION felix_array_text(text[])
        RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE
        AS $$ SELECT COALESCE(array_to_string($1, ' '), '') $$;

    CREATE TEMP TABLE emails (
        id          text PRIMARY KEY,
        user_id     text NOT NULL,
        from_name   text,
        from_email  text,
        to_email    text,
        subject     text,
        snippet     text,
        body        text,
        received_at timestamptz,
        category    text,
        thread_id   text
    );

    CREATE TEMP TABLE sent_emails (
        id         text PRIMARY KEY,
        user_id    text NOT NULL,
        from_email text,
        to_emails  text[],
        to_names   text[],
        subject    text,
        snippet    text,
        body       text,
        sent_at    timestamptz,
        thread_id  text
    );
"""


@pytest.fixture
async def pg_conn():
    """A connection to a live test Postgres with the minimal email schema.

    Uses TEST_DATABASE_URL (e.g. a throwaway Docker postgres). Temp tables and
    the function live only for the connection's lifetime, so nothing persists.
    """
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set; skipping live-Postgres SQL test")
    import asyncpg

    try:
        conn = await asyncpg.connect(dsn)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover - env dependent
        pytest.skip(f"could not reach TEST_DATABASE_URL: {exc}")
    try:
        await conn.execute(_PG_SCHEMA)
        yield conn
    finally:
        await conn.close()


async def test_local_cache_query_executes_against_postgres(pg_conn):
    now = datetime.now(timezone.utc)
    await pg_conn.execute(
        "INSERT INTO emails (id, user_id, from_name, from_email, to_email, "
        "subject, snippet, body, received_at, category, thread_id) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
        "i1", "u1", "Acme Sales", "sales@acme.com", "me@x.com",
        "Acme invoice March", "Your acme invoice", "Full acme invoice body",
        now, "fyi", "t1",
    )
    await pg_conn.execute(
        "INSERT INTO sent_emails (id, user_id, from_email, to_emails, to_names, "
        "subject, snippet, body, sent_at, thread_id) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
        "s1", "u1", "me@x.com", ["sales@acme.com"], ["Acme Sales"],
        "Re: Acme invoice", "thanks", "thanks for the acme invoice",
        now, "t1",
    )

    prepared: dict = {}

    async def real_query(sql, *args):
        # prepare() makes Postgres parse + plan the statement: an unresolved
        # column (the old sort_at-in-UNION bug) raises UndefinedColumnError here.
        stmt = await pg_conn.prepare(sql)
        prepared["sql"] = sql
        rows = await stmt.fetch(*args)
        return [dict(r) for r in rows]

    with patch.object(chat_tools.db, "query", new=AsyncMock(side_effect=real_query)):
        rows = await _search_local_email_cache("u1", "acme invoice", ["acme", "invoice"], 5)

    assert prepared.get("sql"), "query should have been prepared/executed"
    # Both the inbound and the sent match come back, with a real numeric rank
    # and a real sort_at timestamp resolved from the outer query.
    ids = {r["id"] for r in rows}
    assert ids == {"i1", "s1"}
    for r in rows:
        assert isinstance(r["rank"], int)
        assert r["sort_at"] is not None
    # Rank ordering holds (DESC) — the focused sender+subject hits score highest.
    ranks = [r["rank"] for r in rows]
    assert ranks == sorted(ranks, reverse=True)


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
