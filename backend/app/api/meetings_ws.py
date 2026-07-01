"""
Meeting capture WebSocket transport — Phase 6.

Route: ``/ws/meetings/{meeting_id}`` (registered as its own router, no REST
prefix — see ``main.py``).

Connect flow (fail closed at every gate):
  1. Origin check (CSWSH guard — CORS does not apply to WS upgrades in Starlette).
  2. ``accept()`` then JWT auth via the first JSON message ``{"token": "..."}``.
  3. Monthly AI-budget gate.
  4. ``meeting_capture_mode`` gate — off/unset rejects.
  5. Ownership + ``status='recording'`` check on the meeting.

Wire framing (client → server):
  • JSON control: ``{"type": "start"|"stop"|"ping"}``.
  • Binary audio: first byte = channel (``0x00`` me, ``0x01`` them), the rest is
    LINEAR16 16 kHz mono PCM — demuxed to the matching STT queue.

Server → client: ``{"type": "transcript"|"status"|"pong"|"error", ...}`` JSON.

A WS drop does **not** end the meeting — only REST ``/meetings/{id}/end`` or the
Phase 7 auto-end sweep finalizes it. On disconnect we just tear down the STT
session; the meeting stays ``recording`` so the client can reconnect.

Per the Phase 0 decision the ``_ws_auth.py`` extract was skipped, so only
``_authenticate_ws`` is importable as a shared function. The Origin check and
budget gate live inline in ``voice.voice_stream`` and were never extracted, so
they are re-implemented here (mirroring ``voice.py:221-227`` / ``voice.py:238-244``).
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app import db
from app.api.voice import _authenticate_ws
from app.config import settings
from app.middleware.rate_limit import check_monthly_ai_budget
from app.services import meeting_stt_service
from app.services.meeting_service import _capture_enabled

logger = logging.getLogger(__name__)
router = APIRouter()

# Close codes (mirror voice.py's scheme; 4404 is meeting-capture-specific).
_CLOSE_BAD_ORIGIN = 4003   # Origin mismatch (CSWSH)
_CLOSE_BUDGET = 4029       # monthly AI budget exhausted
_CLOSE_FORBIDDEN = 4404    # capture off / not owner / not recording


def _origin_allowed(websocket: WebSocket) -> bool:
    """Validate Origin against FRONTEND_URL (mirrors voice.py:221-227)."""
    origin = websocket.headers.get("origin", "")
    allowed_origin = settings.FRONTEND_URL.rstrip("/")
    return origin.rstrip("/").lower() == allowed_origin.lower()


class _SocketWriter:
    """Single-writer serializer for a capture WebSocket.

    Starlette/uvicorn ``send_json`` is NOT safe to call from two coroutines at
    once. The meeting socket has several concurrent producers — the ``me`` and
    ``them`` STT tasks (both emitting interims/finals), plus the control loop's
    pong/status replies — and simultaneous sends (both parties talking, the
    common case) can interleave at the ASGI layer, raise, and tear the socket
    down mid-meeting.

    Rather than sprinkle a lock around each call site (which the next-added send
    site could forget), we funnel *every* emit through one background task:
    producers only :meth:`send` (enqueue); exactly one coroutine — :meth:`_run`
    — ever awaits ``websocket.send_json``. The invariant is therefore
    structural: it is impossible for two coroutines to await a send on this
    socket concurrently, and a new send site cannot reintroduce the race unless
    it deliberately bypasses this writer.
    """

    _CLOSE = object()  # sentinel: flush everything ahead of me, then stop

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket
        # Unbounded + non-blocking enqueue: a producer must never block (or
        # deadlock) waiting on a writer whose socket has already gone away.
        # Transcript messages are small and low-rate, so growth isn't a concern.
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._closed = False

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def send(self, payload: dict) -> None:
        """Enqueue a message for the single writer. Never blocks, never raises.

        After close, late enqueues are dropped rather than queued forever (no
        producer can hang on a dead socket).
        """
        if self._closed:
            return
        self._queue.put_nowait(payload)

    async def _run(self) -> None:
        """The one and only coroutine that touches ``websocket.send_json``."""
        while True:
            payload = await self._queue.get()
            if payload is self._CLOSE:
                return
            try:
                await self._ws.send_json(payload)
            except Exception:
                # Socket dropped mid-send; a dead socket won't recover. Stop
                # writing, but never propagate to producers. Marking closed lets
                # aclose() complete without waiting on further sends.
                self._closed = True
                logger.debug(
                    "meeting WS writer send failed; stopping writer", exc_info=True
                )
                return

    async def aclose(self) -> None:
        """Drain queued messages, then stop the writer task. Idempotent.

        FIFO ordering means the sentinel sits behind the last real message, so
        everything already enqueued flushes to the socket before the writer
        exits — the meeting's transcript tail isn't dropped on a clean stop.
        """
        if self._task is None:
            return
        if not self._closed:
            self._queue.put_nowait(self._CLOSE)
        self._closed = True
        try:
            await self._task
        finally:
            self._task = None


@router.websocket("/ws/meetings/{meeting_id}")
async def meeting_capture_stream(websocket: WebSocket, meeting_id: str) -> None:
    # 1. Origin check — reject before accepting the upgrade.
    if not _origin_allowed(websocket):
        await websocket.close(code=_CLOSE_BAD_ORIGIN)
        return

    await websocket.accept()

    # 2. Authenticate via the first JSON message (shared helper).
    user = await _authenticate_ws(websocket)
    if user is None:
        return  # _authenticate_ws already sent error + closed
    user_id: str = user["id"]

    # 3. Budget gate — reject before spinning up the STT pipeline (voice.py:238-244).
    try:
        await check_monthly_ai_budget(user_id, user.get("email"))
    except HTTPException as exc:
        await websocket.send_json({"type": "error", "message": exc.detail})
        await websocket.close(code=_CLOSE_BUDGET)
        return

    # 4. Capture-mode gate — fail closed.
    if not await _capture_enabled(user_id):
        await websocket.send_json({"type": "error", "message": "meeting capture is disabled"})
        await websocket.close(code=_CLOSE_FORBIDDEN)
        return

    # 5. Ownership + recording check.
    meeting = await db.query_one(
        "SELECT id, status FROM meetings WHERE id = $1 AND user_id = $2",
        meeting_id, user_id,
    )
    if not meeting or meeting.get("status") != "recording":
        await websocket.send_json({"type": "error", "message": "meeting not open for capture"})
        await websocket.close(code=_CLOSE_FORBIDDEN)
        return

    # The connect/reject sends above and this `ready` run in a single coroutine
    # before any concurrent producer exists (the STT tasks + writer are started
    # inside _run_capture), so they can't race and go direct to the socket.
    await websocket.send_json({"type": "status", "status": "ready"})
    await _run_capture(websocket, user_id, meeting_id)


async def _run_capture(websocket: WebSocket, user_id: str, meeting_id: str) -> None:
    """Drive the two-channel STT session from the live socket until stop/disconnect."""

    # Once the STT tasks are running, this socket has concurrent producers, so
    # every emit must go through the single writer — never call
    # websocket.send_json directly below this point.
    writer = _SocketWriter(websocket)
    writer.start()

    async def send_json(payload: dict) -> None:
        await writer.send(payload)

    # Seed the meeting clock from segments already persisted so a reconnect
    # continues meeting-relative time instead of restarting at 0 (which would
    # scramble the transcript, read back ORDER BY ts_start).
    base_row = await db.query_one(
        "SELECT COALESCE(MAX(ts_end), MAX(ts_start), 0) AS base "
        "FROM meeting_transcript_segments WHERE user_id = $1 AND meeting_id = $2",
        user_id, meeting_id,
    )
    base_offset_s = float(base_row["base"]) if base_row and base_row.get("base") is not None else 0.0

    stt = meeting_stt_service.session(meeting_id, user_id, send_json, start_offset_s=base_offset_s)
    stt.start()
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            data = message.get("bytes")
            if data is not None:
                if not data:
                    continue  # empty frame — no channel byte
                channel_byte = data[0]
                pcm = data[1:]
                if pcm:
                    await stt.feed(channel_byte, pcm)
                continue

            text = message.get("text")
            if not text:
                continue
            try:
                ctrl = json.loads(text)
            except json.JSONDecodeError:
                continue
            ctype = ctrl.get("type")
            if ctype == "stop":
                break  # client signalled end-of-audio; REST /end finalizes
            if ctype == "ping":
                await send_json({"type": "pong"})
            # "start" is an ack — the session is already running.
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("meeting capture WS error (meeting=%s)", meeting_id)
    finally:
        # Tear down STT FIRST so its final flush enqueues onto the writer, THEN
        # drain + stop the writer so that tail actually reaches the socket before
        # this handler returns (which closes it). A WS drop must NOT end the
        # meeting — REST /end or the Phase 7 auto-end sweep does that, so a
        # reconnect can resume.
        await stt.stop()
        await writer.aclose()
