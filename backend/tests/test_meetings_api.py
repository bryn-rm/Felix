"""
Meeting capture REST + WebSocket transport tests — Phase 6.

REST (plan §5a / fail-closed gate):
  • every capture route 404s when meeting_capture_mode is off (feature hidden);
  • when on, the routes delegate to meeting_service and surface its results.

WebSocket (plan §5 a/b + WS smoke):
  • connect rejected on bad Origin / capture off / not owner / not recording;
  • binary frames are demuxed by their channel byte (0x00 me / 0x01 them) and the
    channel byte is stripped before the PCM reaches the STT session;
  • a short two-channel stream persists me/them finals (framing → persistence).
"""

import asyncio
import json
import time

import jwt as pyjwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.config import settings
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded


# ===========================================================================
# REST
# ===========================================================================

def _make_app(user_id: str = "user-cap-1", email: str = "cap@example.com") -> FastAPI:
    from app.api.meetings import router

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(router, prefix="/meetings")
    app.dependency_overrides[get_current_user] = lambda: {"id": user_id, "email": email}
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_app())


def test_routes_404_when_capture_disabled(client, monkeypatch):
    """Fail closed: with the flag off the whole capture surface is hidden (404)."""
    from app.api import meetings as meetings_api

    async def disabled(_user_id):
        return False

    monkeypatch.setattr(meetings_api, "_capture_enabled", disabled)

    assert client.post("/meetings/start", json={"template": "general"}).status_code == 404
    assert client.get("/meetings").status_code == 404
    assert client.get("/meetings/m-1").status_code == 404
    assert client.post("/meetings/m-1/end").status_code == 404
    assert client.post("/meetings/m-1/notes", json={"content": "hi"}).status_code == 404
    assert client.delete("/meetings/m-1").status_code == 404


def test_start_delegates_to_service_when_enabled(client, monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_capture_enabled", AsyncMock(return_value=True))
    start = AsyncMock(return_value={"meeting_id": "m-99"})
    monkeypatch.setattr(meetings_api.meeting_service, "start_meeting", start)

    resp = client.post("/meetings/start", json={"title": "Roadmap", "template": "one_on_one"})

    assert resp.status_code == 200
    assert resp.json() == {"meeting_id": "m-99"}
    assert start.await_args.kwargs["template"] == "one_on_one"
    assert start.await_args.kwargs["title"] == "Roadmap"
    assert start.await_args.kwargs["meeting_type"] == "general"
    assert start.await_args.kwargs["user_role"] is None
    assert start.await_args.kwargs["source"] == "browser_capture"


async def test_start_manual_assistant_uses_no_audio_source(monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_capture_enabled", AsyncMock(return_value=True))
    start = AsyncMock(return_value={"meeting_id": "m-manual"})
    monkeypatch.setattr(meetings_api.meeting_service, "start_meeting", start)

    result = await meetings_api.start_capture.__wrapped__(
        meetings_api.StartMeetingBody(
            meeting_type="interview",
            user_role="candidate",
            assistant_only=True,
        ),
        request=None,
        current_user={"id": "user-cap-1", "email": "cap@example.com"},
    )

    assert result == {"meeting_id": "m-manual"}
    assert start.await_args.kwargs["source"] == "manual_notes"
    assert start.await_args.kwargs["meeting_type"] == "interview"
    assert start.await_args.kwargs["user_role"] == "candidate"


def test_start_rejects_interview_without_role():
    from pydantic import ValidationError

    from app.api.meetings import StartMeetingBody

    with pytest.raises(ValidationError, match="Interview meetings require"):
        StartMeetingBody(meeting_type="interview")


@pytest.mark.parametrize("role", ["candidate", "interviewer"])
async def test_start_passes_explicit_interview_role(monkeypatch, role):
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_capture_enabled", AsyncMock(return_value=True))
    start = AsyncMock(return_value={"meeting_id": "m-1"})
    monkeypatch.setattr(meetings_api.meeting_service, "start_meeting", start)

    body = meetings_api.StartMeetingBody(
        template="interview",
        meeting_type="interview",
        user_role=role,
    )
    result = await meetings_api.start_capture.__wrapped__(
        body,
        request=None,
        current_user={"id": "user-cap-1", "email": "cap@example.com"},
    )

    assert result == {"meeting_id": "m-1"}
    assert start.await_args.kwargs["meeting_type"] == "interview"
    assert start.await_args.kwargs["user_role"] == role


async def test_start_preserves_legacy_interview_when_type_is_omitted(monkeypatch):
    """Cached clients sending only the former template contract keep the old
    interview behavior instead of being treated as an explicit General."""
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_capture_enabled", AsyncMock(return_value=True))
    start = AsyncMock(return_value={"meeting_id": "m-legacy"})
    monkeypatch.setattr(meetings_api.meeting_service, "start_meeting", start)

    body = meetings_api.StartMeetingBody(template="interview")
    assert body.meeting_type == "general"  # Pydantic's public default
    assert "meeting_type" not in body.model_fields_set

    result = await meetings_api.start_capture.__wrapped__(
        body,
        request=None,
        current_user={"id": "user-cap-1", "email": "cap@example.com"},
    )

    assert result == {"meeting_id": "m-legacy"}
    assert start.await_args.kwargs["template"] == "interview"
    assert start.await_args.kwargs["meeting_type"] is None
    assert start.await_args.kwargs["user_role"] is None


async def test_explicit_general_still_overrides_interview_template(monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_capture_enabled", AsyncMock(return_value=True))
    start = AsyncMock(return_value={"meeting_id": "m-general"})
    monkeypatch.setattr(meetings_api.meeting_service, "start_meeting", start)

    body = meetings_api.StartMeetingBody(
        template="interview",
        meeting_type="general",
    )
    assert "meeting_type" in body.model_fields_set

    await meetings_api.start_capture.__wrapped__(
        body,
        request=None,
        current_user={"id": "user-cap-1", "email": "cap@example.com"},
    )

    assert start.await_args.kwargs["template"] == "general"
    assert start.await_args.kwargs["meeting_type"] == "general"


def test_start_coerces_unknown_template_to_general(client, monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_capture_enabled", AsyncMock(return_value=True))
    start = AsyncMock(return_value={"meeting_id": "m-1"})
    monkeypatch.setattr(meetings_api.meeting_service, "start_meeting", start)

    client.post("/meetings/start", json={"template": "nonsense"})

    assert start.await_args.kwargs["template"] == "general"


@pytest.mark.parametrize("body,expected", [
    # An explicit General must never carry the interview template: _fan_out
    # keys off it and would promote the meeting to the job tracker.
    ({"meeting_type": "general", "template": "interview"}, "general"),
    # …and an explicit Interview always gets it, whatever was requested.
    (
        {"meeting_type": "interview", "user_role": "candidate",
         "template": "standup"},
        "interview",
    ),
])
def test_start_derives_template_from_meeting_type(client, monkeypatch, body, expected):
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_capture_enabled", AsyncMock(return_value=True))
    start = AsyncMock(return_value={"meeting_id": "m-1"})
    monkeypatch.setattr(meetings_api.meeting_service, "start_meeting", start)

    client.post("/meetings/start", json=body)

    assert start.await_args.kwargs["template"] == expected


def test_end_404_when_not_recording(client, monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_capture_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meetings_api, "check_monthly_ai_budget", AsyncMock())
    # The pre-SELECT recording-guard passes; end_meeting then returns None when
    # the row isn't owned / not recording (e.g. an auto-end sweep raced us).
    monkeypatch.setattr(meetings_api.db, "query_one", AsyncMock(return_value={"id": "m-1"}))
    monkeypatch.setattr(meetings_api.meeting_service, "end_meeting", AsyncMock(return_value=None))

    assert client.post("/meetings/m-1/end").status_code == 404


async def test_end_checks_budget_before_ending(monkeypatch):
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_capture_enabled", AsyncMock(return_value=True))
    budget = AsyncMock(side_effect=HTTPException(status_code=429, detail="over budget"))
    end = AsyncMock(return_value={"meeting_id": "m-1", "status": "processing"})
    monkeypatch.setattr(meetings_api, "check_monthly_ai_budget", budget)
    monkeypatch.setattr(meetings_api.meeting_service, "end_meeting", end)
    monkeypatch.setattr(meetings_api.db, "query_one", AsyncMock(return_value={"id": "m-1"}))

    with pytest.raises(HTTPException) as exc:
        await meetings_api.end_capture.__wrapped__(
            "m-1",
            request=None,
            current_user={"id": "user-cap-1", "email": "cap@example.com"},
        )

    assert exc.value.status_code == 429
    budget.assert_awaited_once_with("user-cap-1", "cap@example.com")
    end.assert_not_awaited()


async def test_end_missing_recording_returns_404_before_budget(monkeypatch):
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.api import meetings as meetings_api

    budget = AsyncMock()
    monkeypatch.setattr(meetings_api, "_capture_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meetings_api, "check_monthly_ai_budget", budget)
    monkeypatch.setattr(meetings_api.db, "query_one", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as exc:
        await meetings_api.end_capture.__wrapped__(
            "m-1",
            request=None,
            current_user={"id": "user-cap-1", "email": "cap@example.com"},
        )

    assert exc.value.status_code == 404
    budget.assert_not_awaited()


def test_list_and_get_delegate(client, monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_capture_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meetings_api.meeting_service, "list_meetings",
                        AsyncMock(return_value=[{"id": "m-1"}]))
    monkeypatch.setattr(meetings_api.meeting_service, "get_meeting",
                        AsyncMock(return_value={"meeting": {"id": "m-1"}, "segments": [], "summary": None}))

    assert client.get("/meetings").json() == {"meetings": [{"id": "m-1"}]}
    assert client.get("/meetings/m-1").json()["meeting"]["id"] == "m-1"


# ---------------------------------------------------------------------------
# Live assist REST reads (fail closed on the assist double-gate)
# ---------------------------------------------------------------------------

def test_assist_routes_404_when_assist_disabled(client, monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    # Capture may be on — the assist routes gate on the stricter flag pair.
    monkeypatch.setattr(meetings_api, "_capture_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meetings_api, "_assist_enabled", AsyncMock(return_value=False))

    assert client.get("/meetings/m-1/assist").status_code == 404
    assert client.post("/meetings/m-1/assist/i-1/dismiss").status_code == 404


def test_assist_list_returns_wire_items(client, monkeypatch):
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_assist_enabled", AsyncMock(return_value=True))
    row = {
        "id": "i-1", "kind": "fact", "source": "proactive", "question": None,
        "title": "Renewal is Friday", "body": "Agreed by email last week.",
        "transcript_ts": 12.5, "dismissed": False,
        "usefulness_score": 0.9, "trigger_type": "question",
        "prompt_version": "v1", "request_id": None, "metadata": {},
        "model": "haiku", "created_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
    }
    monkeypatch.setattr(meetings_api.db, "query", AsyncMock(return_value=[row]))

    resp = client.get("/meetings/m-1/assist")

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items[0]["id"] == "i-1"
    assert items[0]["kind"] == "fact"
    # Internal eval fields stay server-side — the wire item is display-only.
    assert "usefulness_score" not in items[0]
    assert "trigger_type" not in items[0]


def test_assist_dismiss_scopes_to_owner(client, monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_assist_enabled", AsyncMock(return_value=True))
    update = AsyncMock(return_value={"id": "i-1"})
    monkeypatch.setattr(meetings_api.db, "query_one", update)

    resp = client.post("/meetings/m-1/assist/i-1/dismiss")

    assert resp.status_code == 200
    assert resp.json() == {"dismissed": True}
    # id + meeting_id + user_id all in the WHERE — ownership enforced in SQL.
    assert update.await_args.args[1:] == ("i-1", "m-1", "user-cap-1")

    update.return_value = None
    assert client.post("/meetings/m-1/assist/i-2/dismiss").status_code == 404


async def test_manual_assist_ask_is_scoped_and_returns_item(monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_assist_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meetings_api.db, "query_one", AsyncMock(return_value={
        "id": "m-1", "source": "manual_notes", "status": "recording",
        "live_context": {"digest": {}},
    }))
    answer = AsyncMock(return_value={
        "type": "assist", "item": {"id": "i-1", "body": "Last time..."},
    })
    monkeypatch.setattr(meetings_api, "answer_standalone_question", answer)

    result = await meetings_api.ask_standalone_assist.__wrapped__(
        "m-1",
        meetings_api.AssistAskBody(
            question="What happened last time?", request_id="req-1",
        ),
        request=None,
        current_user={"id": "user-cap-1", "email": "cap@example.com"},
    )

    assert result["item"]["id"] == "i-1"
    assert answer.await_args.kwargs["user_id"] == "user-cap-1"
    assert answer.await_args.kwargs["meeting_id"] == "m-1"


async def test_manual_assist_ask_rejects_capture_session(monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_assist_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meetings_api.db, "query_one", AsyncMock(return_value={
        "id": "m-1", "source": "browser_capture", "status": "recording",
    }))

    with pytest.raises(HTTPException) as exc:
        await meetings_api.ask_standalone_assist.__wrapped__(
            "m-1",
            meetings_api.AssistAskBody(question="Hello?"),
            request=None,
            current_user={"id": "user-cap-1", "email": "cap@example.com"},
        )

    assert exc.value.status_code == 404


def test_ask_body_rejects_a_non_uuid_parent_item_id():
    """parent_item_id is compared against a uuid column. Unvalidated, an
    arbitrary string reached asyncpg and surfaced as a bare 500."""
    from pydantic import ValidationError

    from app.api import meetings as meetings_api

    with pytest.raises(ValidationError):
        meetings_api.AssistAskBody(
            question="Expand that", intent="expand", parent_item_id="not-a-uuid",
        )

    body = meetings_api.AssistAskBody(
        question="Expand that", intent="expand",
        parent_item_id="6f1c0f4e-3b7a-4a3a-9c2f-2f1c9a0d6b11",
    )
    assert str(body.parent_item_id) == "6f1c0f4e-3b7a-4a3a-9c2f-2f1c9a0d6b11"


async def test_ask_reads_only_whether_context_exists(monkeypatch):
    """live_context is the whole prefetched digest; the route only tests it for
    NULL, so it must not pull kilobytes back on every ask."""
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_assist_enabled", AsyncMock(return_value=True))
    query_one = AsyncMock(return_value={
        "id": "m-1", "source": "manual_notes", "status": "recording",
        "has_context": True,
    })
    monkeypatch.setattr(meetings_api.db, "query_one", query_one)
    prefetch = AsyncMock()
    monkeypatch.setattr(meetings_api, "prefetch_context", prefetch)
    monkeypatch.setattr(meetings_api, "answer_standalone_question", AsyncMock(
        return_value={"type": "assist", "item": {"id": "i-1"}},
    ))

    await meetings_api.ask_standalone_assist.__wrapped__(
        "m-1",
        meetings_api.AssistAskBody(question="What did we agree?"),
        request=None,
        current_user={"id": "user-cap-1", "email": "cap@example.com"},
    )

    sql = query_one.await_args.args[0]
    assert "live_context IS NOT NULL AS has_context" in sql
    prefetch.assert_not_awaited()


def test_notes_are_refused_once_the_meeting_is_no_longer_recording(client, monkeypatch):
    """The summary is built from these notes. A late write would leave
    meetings.user_notes silently disagreeing with the summary shown for it."""
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_capture_enabled", AsyncMock(return_value=True))
    save = AsyncMock(return_value=False)   # guarded UPDATE matched no row
    monkeypatch.setattr(meetings_api.meeting_service, "save_user_notes", save)

    resp = client.post("/meetings/m-1/notes", json={"content": "late edit"})

    assert resp.status_code == 409

    save.return_value = True
    assert client.post("/meetings/m-1/notes", json={"content": "ok"}).status_code == 200


# ---------------------------------------------------------------------------
# Live Assist viewer (phone / second screen) — read-only snapshot
# ---------------------------------------------------------------------------

_VIEW_MEETING_ROW = {
    "id": "m-1", "title": "Roadmap", "status": "recording",
    "source": "browser_capture", "template": "general",
    "meeting_type": "general", "user_role": None,
    "started_at": None, "ended_at": None,
}


def test_live_view_404s_when_assist_disabled(client, monkeypatch):
    """Fail closed on the same double-gate as the rest of the assist surface."""
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_capture_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meetings_api, "_assist_enabled", AsyncMock(return_value=False))

    assert client.get("/meetings/m-1/live-view").status_code == 404


def test_live_view_returns_meeting_identity_and_undismissed_items(client, monkeypatch):
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_assist_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        meetings_api.db, "query_one", AsyncMock(return_value=dict(_VIEW_MEETING_ROW)),
    )
    item_query = AsyncMock(return_value=[{
        "id": "i-1", "kind": "fact", "source": "proactive", "question": None,
        "title": "Renewal is Friday", "body": "Agreed by email last week.",
        "transcript_ts": 12.5, "dismissed": False,
        "usefulness_score": 0.9, "trigger_type": "question",
        "prompt_version": "v1", "request_id": None, "metadata": {},
        "model": "haiku", "created_at": datetime(2026, 8, 23, tzinfo=timezone.utc),
    }])
    monkeypatch.setattr(meetings_api.db, "query", item_query)

    resp = client.get("/meetings/m-1/live-view")

    assert resp.status_code == 200
    body = resp.json()
    assert body["meeting"]["title"] == "Roadmap"
    assert body["meeting"]["status"] == "recording"
    assert body["items"][0]["id"] == "i-1"
    # Same display-only wire item the capture page gets — no eval internals.
    assert "usefulness_score" not in body["items"][0]
    # Dismissed cards are excluded in SQL: the viewer cannot dismiss, so a
    # dismissed card would be dead weight on every poll.
    assert "dismissed = FALSE" in item_query.await_args.args[0]


def test_live_view_does_not_expose_transcript_notes_or_context(client, monkeypatch):
    """The viewer is an assist surface, not a second copy of the workspace.

    A 3-second poll must not re-download the transcript, the user's notes, or
    the kilobyte live_context digest.
    """
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_assist_enabled", AsyncMock(return_value=True))
    meeting_query = AsyncMock(return_value=dict(_VIEW_MEETING_ROW))
    monkeypatch.setattr(meetings_api.db, "query_one", meeting_query)
    monkeypatch.setattr(meetings_api.db, "query", AsyncMock(return_value=[]))

    body = client.get("/meetings/m-1/live-view").json()

    assert set(body) == {"meeting", "items"}
    for leaked in ("user_notes", "live_context", "segments", "transcript"):
        assert leaked not in body["meeting"]
        assert leaked not in meeting_query.await_args.args[0]


def test_live_view_is_scoped_to_the_owner(client, monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_assist_enabled", AsyncMock(return_value=True))
    meeting_query = AsyncMock(return_value=None)   # someone else's meeting
    monkeypatch.setattr(meetings_api.db, "query_one", meeting_query)

    assert client.get("/meetings/m-1/live-view").status_code == 404
    # id + user_id both in the WHERE — ownership enforced in SQL.
    assert meeting_query.await_args.args[1:] == ("m-1", "user-cap-1")


def test_live_view_is_a_pure_read_and_never_touches_capture(client, monkeypatch):
    """The core viewer invariant: opening it changes nothing.

    A second device reading the meeting must not take capture ownership, write
    to the row, spawn background work, open an STT session, or start a second
    live-assist watcher. Asserted structurally — every statement the route
    issues is a SELECT, and the capture/assist entry points are never called.
    """
    from unittest.mock import AsyncMock, MagicMock

    from app.api import meetings as meetings_api
    from app.services import live_assist_service, meeting_stt_service

    monkeypatch.setattr(meetings_api, "_assist_enabled", AsyncMock(return_value=True))

    statements: list[str] = []

    async def record_one(sql, *args):
        statements.append(sql)
        return dict(_VIEW_MEETING_ROW)

    async def record_many(sql, *args):
        statements.append(sql)
        return []

    monkeypatch.setattr(meetings_api.db, "query_one", record_one)
    monkeypatch.setattr(meetings_api.db, "query", record_many)
    monkeypatch.setattr(meetings_api.db, "execute", AsyncMock())
    monkeypatch.setattr(meetings_api.db, "insert", AsyncMock())
    monkeypatch.setattr(meetings_api.db, "update", AsyncMock())

    spawn = MagicMock()
    monkeypatch.setattr(meetings_api, "spawn", spawn)
    start_watcher = AsyncMock()
    monkeypatch.setattr(live_assist_service, "maybe_start_watcher", start_watcher)
    stt_session = MagicMock()
    monkeypatch.setattr(meeting_stt_service, "session", stt_session)
    end_meeting = AsyncMock()
    monkeypatch.setattr(meetings_api.meeting_service, "end_meeting", end_meeting)

    assert client.get("/meetings/m-1/live-view").status_code == 200

    assert statements, "the viewer should have read something"
    for sql in statements:
        assert sql.strip().upper().startswith("SELECT"), sql
    meetings_api.db.execute.assert_not_awaited()
    meetings_api.db.insert.assert_not_awaited()
    meetings_api.db.update.assert_not_awaited()
    spawn.assert_not_called()
    start_watcher.assert_not_awaited()
    stt_session.assert_not_called()
    end_meeting.assert_not_awaited()


def test_live_view_still_serves_an_ended_meeting(client, monkeypatch):
    """Read-only after the end: the cards stay readable, the status says done."""
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_assist_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meetings_api.db, "query_one", AsyncMock(
        return_value={**_VIEW_MEETING_ROW, "status": "done"},
    ))
    monkeypatch.setattr(meetings_api.db, "query", AsyncMock(return_value=[]))

    resp = client.get("/meetings/m-1/live-view")

    assert resp.status_code == 200
    assert resp.json()["meeting"]["status"] == "done"


# ===========================================================================
# WebSocket
# ===========================================================================

def _make_jwt(*, sub="user-cap-1", email="cap@example.com", exp_delta=3600):
    payload = {"sub": sub, "email": email, "aud": "authenticated",
               "exp": int(time.time()) + exp_delta}
    return pyjwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


class FakeWebSocket:
    """WebSocket double covering the meeting-capture connect flow.

    ``receive_text`` feeds the auth handshake; ``receive`` replays a queued
    script of Starlette-shaped messages for the audio/control loop.
    """

    def __init__(self, *, origin=None, auth_token=None, recv_script=None):
        self.headers = {}
        if origin is not None:
            self.headers["origin"] = origin
        self._auth_token = auth_token
        self._recv = list(recv_script or [])
        self.accepted = False
        self.sent: list[dict] = []
        self.closed_code: int | None = None

    async def accept(self):
        self.accepted = True

    async def receive_text(self) -> str:
        return json.dumps({"token": self._auth_token})

    async def receive(self) -> dict:
        # A real socket suspends here, letting the spawned STT tasks run between
        # frames; yield so the fake matches that scheduling behaviour.
        import asyncio
        await asyncio.sleep(0)
        if self._recv:
            return self._recv.pop(0)
        return {"type": "websocket.disconnect"}

    async def send_json(self, payload: dict):
        self.sent.append(payload)

    async def close(self, code: int = 1000):
        self.closed_code = code


def _allow_origin():
    return settings.FRONTEND_URL.rstrip("/")


async def test_ws_rejects_bad_origin(monkeypatch):
    from app.api import meetings_ws

    ws = FakeWebSocket(origin="https://evil.example.com", auth_token=_make_jwt())
    await meetings_ws.meeting_capture_stream(ws, "m-1")

    assert ws.closed_code == 4003
    assert ws.accepted is False  # rejected before the upgrade is accepted


async def test_ws_rejects_when_capture_disabled(monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import meetings_ws

    monkeypatch.setattr(meetings_ws, "check_monthly_ai_budget", AsyncMock())
    monkeypatch.setattr(meetings_ws, "_capture_enabled", AsyncMock(return_value=False))

    ws = FakeWebSocket(origin=_allow_origin(), auth_token=_make_jwt())
    await meetings_ws.meeting_capture_stream(ws, "m-1")

    assert ws.accepted is True
    assert ws.closed_code == 4404


async def test_ws_rejects_when_not_recording(monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import meetings_ws

    monkeypatch.setattr(meetings_ws, "check_monthly_ai_budget", AsyncMock())
    monkeypatch.setattr(meetings_ws, "_capture_enabled", AsyncMock(return_value=True))
    # Meeting exists but is 'processing', not 'recording'.
    monkeypatch.setattr("app.db.query_one",
                        AsyncMock(return_value={"id": "m-1", "status": "processing"}))

    ws = FakeWebSocket(origin=_allow_origin(), auth_token=_make_jwt())
    await meetings_ws.meeting_capture_stream(ws, "m-1")

    assert ws.closed_code == 4404


async def test_ws_rejects_when_not_owner(monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import meetings_ws

    monkeypatch.setattr(meetings_ws, "check_monthly_ai_budget", AsyncMock())
    monkeypatch.setattr(meetings_ws, "_capture_enabled", AsyncMock(return_value=True))
    # Ownership is enforced in SQL (user_id = $2); a non-owner gets no row.
    monkeypatch.setattr("app.db.query_one", AsyncMock(return_value=None))

    ws = FakeWebSocket(origin=_allow_origin(), auth_token=_make_jwt())
    await meetings_ws.meeting_capture_stream(ws, "m-1")

    assert ws.closed_code == 4404


async def test_ws_demux_routes_channel_bytes_and_strips_prefix(monkeypatch):
    """Binary frames are demuxed by their leading channel byte; the byte is
    stripped before the PCM reaches the STT session (the new Phase 6 wiring)."""
    from unittest.mock import AsyncMock

    from app.api import meetings_ws

    fed: list[tuple[int, bytes]] = []

    class FakeSession:
        def start(self):
            self.started = True

        async def feed(self, channel_byte, pcm):
            fed.append((channel_byte, pcm))

        async def stop(self):
            self.stopped = True

    fake_session = FakeSession()
    monkeypatch.setattr(meetings_ws, "check_monthly_ai_budget", AsyncMock())
    monkeypatch.setattr(meetings_ws, "_capture_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr("app.db.query_one",
                        AsyncMock(return_value={"id": "m-1", "status": "recording"}))
    monkeypatch.setattr(meetings_ws.meeting_stt_service, "session", lambda *a, **k: fake_session)

    ws = FakeWebSocket(
        origin=_allow_origin(),
        auth_token=_make_jwt(),
        recv_script=[
            {"type": "websocket.receive", "text": json.dumps({"type": "start"})},
            {"type": "websocket.receive", "bytes": b"\x00me-pcm"},
            {"type": "websocket.receive", "bytes": b"\x01them-pcm"},
            {"type": "websocket.receive", "text": json.dumps({"type": "ping"})},
            {"type": "websocket.receive", "bytes": b""},          # empty → ignored
            {"type": "websocket.receive", "text": json.dumps({"type": "stop"})},
        ],
    )

    await meetings_ws.meeting_capture_stream(ws, "m-1")

    assert fed == [(0x00, b"me-pcm"), (0x01, b"them-pcm")]
    assert fake_session.stopped is True                  # torn down on stop
    assert any(m == {"type": "pong"} for m in ws.sent)   # ping answered
    assert {"type": "status", "status": "ready"} in ws.sent


async def test_ws_stream_persists_me_and_them_finals(monkeypatch):
    """End-to-end framing → persistence: a frame on each channel byte yields a
    persisted final tagged with the right speaker (plan §5b via the WS layer)."""
    from unittest.mock import AsyncMock

    from app.api import meetings_ws
    from app.services import meeting_stt_service as mss
    from app.services.meeting_stt_service import MeetingSTTChannel, STTResult

    class FakeDB:
        def __init__(self):
            self.rows = []

        async def query_one(self, sql, *args):
            # Guarded segment insert: persist only while 'recording' (always so
            # in this test — the meeting stays open through the frames).
            assert "INSERT INTO meeting_transcript_segments" in sql
            user_id, meeting_id, speaker, text, ts_start, ts_end = args
            self.rows.append({
                "user_id": user_id, "meeting_id": meeting_id, "speaker": speaker,
                "text": text, "ts_start": ts_start, "ts_end": ts_end,
            })
            return {"id": len(self.rows)}

    fake_db = FakeDB()
    monkeypatch.setattr(mss, "db", fake_db)

    async def fake_recognize(self, audio):
        async for _ in audio:          # drain until the close sentinel
            pass
        yield STTResult(f"{self.speaker} spoke", True, 0.0, 1.0)

    monkeypatch.setattr(MeetingSTTChannel, "_recognize_stream", fake_recognize)

    monkeypatch.setattr(meetings_ws, "check_monthly_ai_budget", AsyncMock())
    monkeypatch.setattr(meetings_ws, "_capture_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr("app.db.query_one",
                        AsyncMock(return_value={"id": "m-1", "status": "recording"}))

    ws = FakeWebSocket(
        origin=_allow_origin(),
        auth_token=_make_jwt(),
        recv_script=[
            {"type": "websocket.receive", "bytes": b"\x00" + b"\x00" * 320},   # me
            {"type": "websocket.receive", "bytes": b"\x01" + b"\x00" * 320},   # them
            {"type": "websocket.receive", "text": json.dumps({"type": "stop"})},
        ],
    )

    await meetings_ws.meeting_capture_stream(ws, "m-1")

    speakers = sorted(r["speaker"] for r in fake_db.rows)
    assert speakers == ["me", "them"]
    assert all(r["meeting_id"] == "m-1" for r in fake_db.rows)


# ---------------------------------------------------------------------------
# Single-writer serialization (review finding #1)
# The socket has concurrent producers (me + them STT tasks, plus pong/status);
# Starlette send_json isn't concurrency-safe. _SocketWriter must guarantee that
# exactly one coroutine ever awaits send_json, with no message lost.
# ---------------------------------------------------------------------------

class ConcurrencyProbeWebSocket:
    """A ``send_json`` that detects concurrent entry and records delivery order.

    On entry it bumps an in-flight counter, then yields (``await sleep(0)``) so
    any other coroutine sitting in ``send_json`` gets a chance to interleave. If
    two producers ever sent directly (no single writer), ``max_inflight`` would
    exceed 1 — exactly the ASGI-interleave race the writer must prevent.
    """

    def __init__(self):
        self.sent: list[dict] = []
        self._inflight = 0
        self.max_inflight = 0

    async def send_json(self, payload: dict) -> None:
        self._inflight += 1
        self.max_inflight = max(self.max_inflight, self._inflight)
        try:
            # Two yield points widen the window in which an interleaving send
            # would be observed.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.sent.append(payload)
        finally:
            self._inflight -= 1


async def test_socket_writer_serializes_concurrent_producers():
    """Fire interims/finals from BOTH channels plus a ping on the same tick:
    the single writer must serialize every send (no interleave) and lose none."""
    from app.api.meetings_ws import _SocketWriter

    ws = ConcurrencyProbeWebSocket()
    writer = _SocketWriter(ws)
    writer.start()

    N = 50

    async def channel(speaker: str):
        for i in range(N):
            # Interleave a final every few interims, like a real STT channel.
            await writer.send({"type": "transcript", "speaker": speaker,
                               "is_final": i % 4 == 0, "i": i})
            await asyncio.sleep(0)

    async def pinger():
        for _ in range(N):
            await writer.send({"type": "pong"})
            await asyncio.sleep(0)

    # Launch all three producers together — the "both parties talking + a
    # heartbeat on the same tick" case that provoked the original race.
    await asyncio.gather(channel("me"), channel("them"), pinger())
    await writer.aclose()

    # (1) No interleave: no two coroutines were inside send_json at once.
    assert ws.max_inflight == 1
    # (2) No message lost: every enqueued payload was delivered.
    assert len(ws.sent) == 3 * N
    me = [m for m in ws.sent if m.get("speaker") == "me"]
    them = [m for m in ws.sent if m.get("speaker") == "them"]
    pongs = [m for m in ws.sent if m.get("type") == "pong"]
    assert len(me) == N and len(them) == N and len(pongs) == N
    # (3) Per-producer FIFO order preserved through the queue.
    assert [m["i"] for m in me] == list(range(N))
    assert [m["i"] for m in them] == list(range(N))


async def test_socket_writer_flushes_tail_then_drops_after_close():
    """aclose() drains the queued tail before stopping; post-close sends are a
    no-op (a producer can't deadlock or raise on a gone socket)."""
    from app.api.meetings_ws import _SocketWriter

    ws = ConcurrencyProbeWebSocket()
    writer = _SocketWriter(ws)
    writer.start()

    await writer.send({"type": "transcript", "text": "tail"})
    await writer.aclose()                       # must flush the queued tail
    assert ws.sent == [{"type": "transcript", "text": "tail"}]

    # Enqueue after close is dropped silently — no exception, no hang.
    await writer.send({"type": "transcript", "text": "late"})
    await writer.aclose()                       # idempotent
    assert ws.sent == [{"type": "transcript", "text": "tail"}]
