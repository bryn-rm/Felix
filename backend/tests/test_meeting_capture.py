"""
Meeting capture tests.

Phase 2 scope: the STT relay in isolation (no network, no Google client) — the
highest-risk piece. Covers:
  • two channels persist as me/them segments with monotonic ts_start  (plan §5b)
  • stream rollover keeps ts_start meeting-relative and loses no finals (plan §5c)
  • only finals persist; interims are emitted for display but discarded

Phase 3 scope: the shared WS auth path. Per the Phase 0 decision we do NOT
extract a ``_ws_auth.py`` / refactor voice — the meetings WS (Phase 6) imports
``voice._authenticate_ws`` directly. These tests lock that cross-module contract
so a future voice change can't silently break the meetings dependency, and they
back-fill the auth coverage the refactor option would otherwise have provided.
"""

import asyncio
import time

import jwt as pyjwt
import pytest

from app.config import settings
from app.services import meeting_stt_service as mss
from app.services.meeting_stt_service import (
    BYTES_PER_SECOND,
    MeetingSTTChannel,
    STTResult,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeDB:
    """Models the guarded segment insert (`_persist_final`): a row is persisted
    only while the meeting is still ``recording``. Flip ``recording`` to False to
    simulate any path that leaves 'recording' mid-capture (auto-end, budget-429,
    manual/admin stop)."""

    def __init__(self):
        self.rows: list[dict] = []
        self.recording = True

    async def query_one(self, sql: str, *args):
        assert "INSERT INTO meeting_transcript_segments" in sql
        if not self.recording:
            return None  # WHERE EXISTS (... status='recording') matched nothing
        user_id, meeting_id, speaker, text, ts_start, ts_end = args
        self.rows.append({
            "user_id": user_id, "meeting_id": meeting_id, "speaker": speaker,
            "text": text, "ts_start": ts_start, "ts_end": ts_end,
        })
        return {"id": len(self.rows)}


class ScriptedChannel(MeetingSTTChannel):
    """
    A channel whose Google call is replaced by a script. Each script entry is
    ``(chunks_to_consume, [STTResult, ...])``: the fake consumes that many audio
    chunks from the queue (advancing the byte clock exactly like the real drain),
    yields the canned results, then returns — which the run loop treats as a
    stream end / rollover. Once the script is exhausted it drains to the close
    sentinel so the meeting can end gracefully.
    """

    def __init__(self, *, script, **kw):
        super().__init__(**kw)
        self._script = list(script)

    async def _recognize_stream(self, audio):
        if not self._script:
            async for _ in audio:      # drain until the close sentinel
                pass
            return
        n_chunks, results = self._script.pop(0)
        consumed = 0
        async for _chunk in audio:
            consumed += 1
            if consumed >= n_chunks:
                break
        for r in results:
            yield r


async def _wait_until(predicate, timeout=2.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while not predicate():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("condition not met within timeout")
        await asyncio.sleep(0.01)


def _collect_emits():
    emitted: list[dict] = []

    async def send_json(payload: dict):
        emitted.append(payload)

    return emitted, send_json


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_rollover_keeps_ts_meeting_relative_and_loses_no_finals(monkeypatch):
    """A simulated ~5-min stream boundary: offsets rebase, both finals survive (§5c)."""
    fake_db = FakeDB()
    monkeypatch.setattr(mss, "db", fake_db)
    emitted, send_json = _collect_emits()

    # ~300s of audio in stream 1, then 1s in stream 2.
    five_min_chunk = b"\x00" * (300 * BYTES_PER_SECOND)
    one_sec_chunk = b"\x00" * BYTES_PER_SECOND

    channel = ScriptedChannel(
        meeting_id="m1",
        user_id="u1",
        speaker="me",
        send_json=send_json,
        script=[
            (1, [STTResult("hello there", True, 2.0, 4.0)]),       # stream 1
            (1, [STTResult("general kenobi", True, 1.0, 3.0)]),    # stream 2 (post-rollover)
        ],
    )

    task = asyncio.create_task(channel.run())
    try:
        await channel.push_audio(five_min_chunk)
        await _wait_until(lambda: len(fake_db.rows) == 1)

        await channel.push_audio(one_sec_chunk)
        await _wait_until(lambda: len(fake_db.rows) == 2)

        await channel.close()
        await asyncio.wait_for(task, timeout=2.0)
    finally:
        if not task.done():
            task.cancel()

    assert [r["text"] for r in fake_db.rows] == ["hello there", "general kenobi"]
    # Stream 1 had zero offset; stream 2 is rebased by the 300s consumed before it.
    assert fake_db.rows[0]["ts_start"] == pytest.approx(2.0)
    assert fake_db.rows[1]["ts_start"] == pytest.approx(301.0)
    assert fake_db.rows[1]["ts_start"] > fake_db.rows[0]["ts_start"]


async def test_only_finals_persist_interims_are_emitted(monkeypatch):
    """Interims reach the WS for display but never hit the DB."""
    fake_db = FakeDB()
    monkeypatch.setattr(mss, "db", fake_db)
    emitted, send_json = _collect_emits()

    channel = ScriptedChannel(
        meeting_id="m1",
        user_id="u1",
        speaker="me",
        send_json=send_json,
        script=[
            (1, [
                STTResult("partial", False, 0.0, 1.0),
                STTResult("partial done", True, 0.0, 1.5),
            ]),
        ],
    )

    task = asyncio.create_task(channel.run())
    try:
        await channel.push_audio(b"\x00" * BYTES_PER_SECOND)
        await _wait_until(lambda: len(fake_db.rows) == 1)
        await channel.close()
        await asyncio.wait_for(task, timeout=2.0)
    finally:
        if not task.done():
            task.cancel()

    # One persisted final; the interim was emitted but not stored.
    assert [r["text"] for r in fake_db.rows] == ["partial done"]
    assert any(e["text"] == "partial" and e["is_final"] is False for e in emitted)
    assert any(e["text"] == "partial done" and e["is_final"] is True for e in emitted)


async def test_two_channels_tag_me_and_them_with_monotonic_ts(monkeypatch):
    """me/them channels persist under their own speaker tag with ordered ts_start (§5b)."""
    fake_db = FakeDB()
    monkeypatch.setattr(mss, "db", fake_db)
    _, send_json = _collect_emits()

    def make(speaker):
        return ScriptedChannel(
            meeting_id="m1",
            user_id="u1",
            speaker=speaker,
            send_json=send_json,
            script=[
                (1, [STTResult("first", True, 1.0, 2.0)]),
                (1, [STTResult("second", True, 0.5, 1.0)]),
            ],
        )

    for ch in (make("me"), make("them")):
        task = asyncio.create_task(ch.run())
        try:
            await ch.push_audio(b"\x00" * (10 * BYTES_PER_SECOND))
            await _wait_until(lambda c=ch: sum(r["speaker"] == c.speaker for r in fake_db.rows) == 1)
            await ch.push_audio(b"\x00" * BYTES_PER_SECOND)
            await _wait_until(lambda c=ch: sum(r["speaker"] == c.speaker for r in fake_db.rows) == 2)
            await ch.close()
            await asyncio.wait_for(task, timeout=2.0)
        finally:
            if not task.done():
                task.cancel()

    me_rows = [r for r in fake_db.rows if r["speaker"] == "me"]
    them_rows = [r for r in fake_db.rows if r["speaker"] == "them"]
    assert len(me_rows) == 2 and len(them_rows) == 2
    for rows in (me_rows, them_rows):
        assert rows[0]["ts_start"] < rows[1]["ts_start"]   # rebased by 10s consumed
        assert rows[1]["ts_start"] == pytest.approx(10.5)


# ---------------------------------------------------------------------------
# Persist guard — no segment lands once a meeting leaves 'recording' (finding #2)
# Invariant: whichever path flips status (auto-end sweep, budget-429, manual/
# admin stop, …), zero further transcript segments are persisted for it.
# ---------------------------------------------------------------------------

async def test_persist_stops_the_moment_meeting_leaves_recording(monkeypatch):
    """A status flip mid-capture stops persistence immediately: the guarded insert
    writes nothing, emits nothing, and later finals short-circuit."""
    fake_db = FakeDB()
    monkeypatch.setattr(mss, "db", fake_db)
    emitted, send_json = _collect_emits()

    channel = MeetingSTTChannel(
        meeting_id="m1", user_id="u1", speaker="me", send_json=send_json,
    )

    # While recording: the final persists and is shown live.
    await channel._persist_final("before", 0.0, 1.0)
    assert [r["text"] for r in fake_db.rows] == ["before"]
    assert any(e["text"] == "before" and e["is_final"] for e in emitted)

    # Some other path flips the meeting out of 'recording' mid-capture.
    fake_db.recording = False

    # The guarded insert matches no 'recording' row → nothing stored, nothing
    # emitted, and the channel latches finalized.
    await channel._persist_final("after", 1.0, 2.0)
    assert [r["text"] for r in fake_db.rows] == ["before"]      # no orphan segment
    assert not any(e["text"] == "after" for e in emitted)       # not shown live
    assert channel._finalized is True

    # Further finals short-circuit above the DB entirely.
    await channel._persist_final("also after", 2.0, 3.0)
    assert [r["text"] for r in fake_db.rows] == ["before"]


async def test_run_loop_exits_after_status_flip_instead_of_rolling_over(monkeypatch):
    """Once the guarded insert reports the meeting left 'recording', the run loop
    latches and stops rather than rolling into another STT stream to persist nothing."""
    fake_db = FakeDB()
    monkeypatch.setattr(mss, "db", fake_db)
    _, send_json = _collect_emits()

    channel = ScriptedChannel(
        meeting_id="m1",
        user_id="u1",
        speaker="me",
        send_json=send_json,
        script=[
            (1, [STTResult("kept", True, 0.0, 1.0)]),      # stream 1 → persists
            (1, [STTResult("dropped", True, 0.0, 1.0)]),   # stream 2 → must persist nothing
        ],
    )

    task = asyncio.create_task(channel.run())
    try:
        await channel.push_audio(b"\x00" * BYTES_PER_SECOND)
        await _wait_until(lambda: len(fake_db.rows) == 1)
        # Meeting leaves 'recording' before stream 2 can consume its chunk (the
        # data dependency guarantees the flip lands first).
        fake_db.recording = False
        await channel.push_audio(b"\x00" * BYTES_PER_SECOND)
        await asyncio.wait_for(task, timeout=2.0)   # loop exits on its own
    finally:
        if not task.done():
            task.cancel()

    assert channel._finalized is True
    assert [r["text"] for r in fake_db.rows] == ["kept"]   # stream 2 never persisted


# ---------------------------------------------------------------------------
# Phase 3 — shared WS auth (import-directly contract)
# ---------------------------------------------------------------------------

class FakeWebSocket:
    """Minimal WebSocket double for ``_authenticate_ws``.

    Supports only what the auth handshake touches: a single ``receive_text``
    (optionally raising to simulate timeout/transport errors), ``send_json``,
    and ``close``.
    """

    def __init__(self, *, text=None, raise_on_receive=None):
        self._text = text
        self._raise = raise_on_receive
        self.sent: list[dict] = []
        self.closed_code: int | None = None

    async def receive_text(self) -> str:
        if self._raise is not None:
            raise self._raise
        return self._text

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code


def _make_jwt(*, sub="meeting-user-1", email="meet@example.com", exp_delta=3600):
    """Sign a Supabase-shaped JWT with the test secret so the local fast path
    in ``_authenticate_ws`` accepts (or, with a negative delta, expires) it."""
    payload = {
        "sub": sub,
        "email": email,
        "aud": "authenticated",
        "exp": int(time.time()) + exp_delta,
    }
    return pyjwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


def test_authenticate_ws_is_importable_for_meetings():
    """Phase 6's meetings WS imports this private helper directly (no _ws_auth
    extract per the Phase 0 decision). Lock the import path + callable shape."""
    from app.api.voice import _authenticate_ws

    assert callable(_authenticate_ws)


async def test_authenticate_ws_accepts_valid_jwt():
    """Valid token → user dict from the local verify fast path; socket stays open."""
    import json

    from app.api.voice import _authenticate_ws

    ws = FakeWebSocket(text=json.dumps({"token": _make_jwt()}))
    user = await _authenticate_ws(ws)

    assert user == {"id": "meeting-user-1", "email": "meet@example.com"}
    assert ws.closed_code is None
    assert ws.sent == []


async def test_authenticate_ws_rejects_missing_token():
    import json

    from app.api.voice import _authenticate_ws

    ws = FakeWebSocket(text=json.dumps({"token": "   "}))
    user = await _authenticate_ws(ws)

    assert user is None
    assert ws.closed_code == 4001
    assert ws.sent and ws.sent[-1]["type"] == "error"


async def test_authenticate_ws_rejects_invalid_jwt():
    import json

    from app.api.voice import _authenticate_ws

    ws = FakeWebSocket(text=json.dumps({"token": "not-a-real-jwt"}))
    user = await _authenticate_ws(ws)

    assert user is None
    assert ws.closed_code == 4001


async def test_authenticate_ws_rejects_expired_jwt():
    """Expired token → _verify_jwt_locally raises HTTPException; auth must reject,
    not fall through to the network path (the signature-bypass guard in voice.py)."""
    import json

    from app.api.voice import _authenticate_ws

    ws = FakeWebSocket(text=json.dumps({"token": _make_jwt(exp_delta=-10)}))
    user = await _authenticate_ws(ws)

    assert user is None
    assert ws.closed_code == 4001


async def test_authenticate_ws_rejects_malformed_message():
    from app.api.voice import _authenticate_ws

    ws = FakeWebSocket(text="this is not json {")
    user = await _authenticate_ws(ws)

    assert user is None
    assert ws.closed_code == 4001
    assert ws.sent and ws.sent[-1]["message"] == "Invalid auth message"


async def test_authenticate_ws_rejects_on_receive_timeout():
    from app.api.voice import _authenticate_ws

    ws = FakeWebSocket(raise_on_receive=asyncio.TimeoutError())
    user = await _authenticate_ws(ws)

    assert user is None
    assert ws.closed_code == 4001
    assert ws.sent and ws.sent[-1]["message"] == "Auth timeout"


# ---------------------------------------------------------------------------
# Phase 4 — AI summarization (ai_service.summarize_meeting)
# ---------------------------------------------------------------------------

def _claude_response(text: str):
    """Mock Anthropic Messages response carrying a single text block."""
    from unittest.mock import MagicMock

    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    resp.usage = MagicMock(input_tokens=100, output_tokens=50)
    return resp


async def test_summarize_meeting_returns_capture_schema(monkeypatch):
    """summarize_meeting returns the exact capture output schema (plan §5d)."""
    import json
    from unittest.mock import AsyncMock, patch

    from app.services.ai_service import AIService

    payload = {
        "tldr": "Discussed Q3 roadmap and agreed to ship the billing fix.",
        "decisions": [{"text": "Ship the billing fix before the next release."}],
        "action_items": [
            {"text": "Send the revised quote", "owner": "me", "due_hint": "by Friday"},
            {"text": "Review the contract", "owner": "them", "due_hint": None},
        ],
        "enhanced_notes": [
            {"origin": "user", "text": "billing fix !!"},
            {"origin": "ai", "text": "Refers to the duplicate-charge bug raised mid-call."},
        ],
        "confidence": 0.82,
    }

    with patch("app.services.ai_service.client") as mock_client, \
         patch("app.db.insert", new=AsyncMock(return_value={"id": "ai-call-1"})):
        mock_client.messages.create = AsyncMock(return_value=_claude_response(json.dumps(payload)))
        result = await AIService().summarize_meeting(
            transcript="me: let's ship the fix\nthem: agreed",
            user_notes="billing fix !!",
            template="general",
            memory_context="",  # skip the auto-memory network path
        )

    assert set(result) == {"tldr", "decisions", "action_items", "enhanced_notes", "confidence"}
    assert result["action_items"][0]["owner"] == "me"
    assert result["action_items"][1]["owner"] == "them"
    # enhanced_notes carries the user's note verbatim under origin:user
    user_blocks = [n for n in result["enhanced_notes"] if n["origin"] == "user"]
    assert user_blocks == [{"origin": "user", "text": "billing fix !!"}]


async def test_summarize_meeting_prompt_carries_verbatim_notes_and_template(monkeypatch):
    """The prompt must hand the model the user's notes verbatim + the right
    template guidance + the verbatim-preservation instruction (plan §5d)."""
    import json
    from unittest.mock import AsyncMock, patch

    from app.prompts.meeting_summary import guidance_for
    from app.services.ai_service import AIService

    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _claude_response(json.dumps({
            "tldr": "", "decisions": [], "action_items": [],
            "enhanced_notes": [], "confidence": 0.5,
        }))

    note = "ask about visa status — {important}"  # braces must survive .format()
    with patch("app.services.ai_service.client") as mock_client, \
         patch("app.db.insert", new=AsyncMock(return_value={"id": "ai-call-1"})):
        mock_client.messages.create = AsyncMock(side_effect=fake_create)
        await AIService().summarize_meeting(
            transcript="me: hi\nthem: hello",
            user_notes=note,
            template="interview",
            memory_context="",
        )

    sent = captured["messages"][0]["content"]
    assert note in sent                                   # user note passed verbatim
    assert guidance_for("interview") in sent              # interview template guidance
    assert "CHARACTER-FOR-CHARACTER" in sent              # verbatim-preservation rule
    assert captured["model"].startswith("claude-sonnet")  # ANTHROPIC_MODEL_SMART


async def test_summarize_meeting_falls_back_on_bad_json(monkeypatch):
    """A non-JSON model reply yields the schema shape, not an exception."""
    from unittest.mock import AsyncMock, patch

    from app.services.ai_service import AIService

    with patch("app.services.ai_service.client") as mock_client, \
         patch("app.db.insert", new=AsyncMock(return_value={"id": "ai-call-1"})):
        mock_client.messages.create = AsyncMock(return_value=_claude_response("not json at all"))
        result = await AIService().summarize_meeting(
            transcript="me: hi", user_notes="", template="general", memory_context="",
        )

    assert set(result) == {
        "tldr", "decisions", "action_items", "enhanced_notes", "confidence", "parse_error",
    }
    assert result["action_items"] == [] and result["confidence"] == 0.0
    # parse_error flags the unusable summary so meeting_service fails into 'error'
    # (recoverable via /summarize) instead of persisting it as 'done'.
    assert result["parse_error"] is True
