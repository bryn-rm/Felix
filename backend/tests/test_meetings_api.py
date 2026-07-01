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
from fastapi import FastAPI
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


def test_start_coerces_unknown_template_to_general(client, monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import meetings as meetings_api

    monkeypatch.setattr(meetings_api, "_capture_enabled", AsyncMock(return_value=True))
    start = AsyncMock(return_value={"meeting_id": "m-1"})
    monkeypatch.setattr(meetings_api.meeting_service, "start_meeting", start)

    client.post("/meetings/start", json={"template": "nonsense"})

    assert start.await_args.kwargs["template"] == "general"


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
