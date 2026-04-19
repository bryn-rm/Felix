"""Regression tests for the three-layer memory system."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.services import memory_service, session_manager


def _msg(role: str, text: str) -> dict:
    return {
        "role": role,
        "text": text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def setup_function() -> None:
    session_manager._sessions.clear()
    session_manager._locks.clear()
    memory_service._pgvector_available = None


async def test_touch_session_preserves_first_new_message_after_idle_rollover(monkeypatch):
    old_now = datetime.now(timezone.utc) - timedelta(minutes=31)
    new_now = datetime.now(timezone.utc)

    session_manager._sessions["user-1"] = {
        "session_id": "stale-session",
        "started_at": old_now,
        "last_activity": old_now,
        "messages": [
            _msg("user", "Old question"),
            _msg("felix", "Old answer"),
        ],
    }

    stored = AsyncMock(return_value={"id": "summary-1"})
    summarise = AsyncMock(return_value={"summary": "Old summary", "open_items": []})

    monkeypatch.setattr(session_manager, "_now", lambda: new_now)
    monkeypatch.setattr(session_manager, "_summarise_conversation", summarise)
    monkeypatch.setattr("app.services.memory_service.store_session_summary", stored)

    new_session_id = await session_manager.touch_session(
        "user-1",
        role="user",
        text="Fresh question",
    )
    await asyncio.sleep(0)

    active = session_manager.get_active_session("user-1")
    assert active is not None
    assert new_session_id == active["session_id"]
    assert new_session_id != "stale-session"
    assert [m["text"] for m in active["messages"]] == ["Fresh question"]

    stored.assert_awaited_once()
    metadata = stored.await_args.kwargs["session_metadata"]
    assert metadata["session_id"] == "stale-session"


async def test_create_episode_stores_text_embedding_when_pgvector_unavailable(monkeypatch):
    monkeypatch.setattr(memory_service, "_pgvector_available", False)

    insert = AsyncMock(return_value={"id": "episode-1"})
    execute = AsyncMock(return_value="UPDATE 1")

    monkeypatch.setattr(memory_service.db, "insert", insert)
    monkeypatch.setattr(memory_service.db, "execute", execute)

    await memory_service.create_episode(
        user_id="user-1",
        episode_type="email",
        summary="Important update",
        embedding=[0.1, 0.2, 0.3],
    )

    execute.assert_awaited_once()
    sql, payload, episode_id = execute.await_args.args
    assert "SET embedding = $1 WHERE id = $2" in sql
    assert "::vector" not in sql
    assert payload.startswith("[")
    assert episode_id == "episode-1"


async def test_retrieve_episodes_skips_vector_sql_when_pgvector_unavailable(monkeypatch):
    monkeypatch.setattr(memory_service, "_pgvector_available", False)

    query = AsyncMock(return_value=[{
        "id": "episode-1",
        "episode_type": "chat",
        "summary": "Discussed the Q3 budget with Sarah Chen.",
        "entities": ["Sarah Chen", "Q3 budget"],
        "importance": 0.8,
        "source_type": "session",
        "source_id": "session-1",
        "occurred_at": datetime.now(timezone.utc),
        "semantic": None,
    }])
    embed = AsyncMock(return_value=[0.1, 0.2])

    monkeypatch.setattr(memory_service.db, "query", query)
    monkeypatch.setattr(memory_service, "_generate_embedding", embed)

    rows = await memory_service.retrieve_episodes(
        user_id="user-1",
        query="What happened with Sarah Chen?",
    )

    assert len(rows) == 1
    embed.assert_not_awaited()
    sql = query.await_args.args[0]
    assert "<=>" not in sql
