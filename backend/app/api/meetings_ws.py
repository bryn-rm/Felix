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
import time
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app import db
from app.api.voice import _authenticate_ws
from app.config import settings
from app.middleware.rate_limit import check_monthly_ai_budget
from app.models.meeting import (
    CAPTURE_HEARTBEAT_INTERVAL_S,
    MEETING_SOURCE_CAPTURE,
)
from app.services import live_assist_service, meeting_stt_service
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


class _CaptureHeartbeat:
    """Persist capture-socket liveness so any Cloud Run instance can read it.

    A connection token makes takeover safe: an older socket's late heartbeat or
    cleanup cannot overwrite/clear the newer connection's state.
    """

    def __init__(self, user_id: str, meeting_id: str) -> None:
        self._user_id = user_id
        self._meeting_id = meeting_id
        self._token: UUID = uuid4()
        self._last_write = float("-inf")
        # Whether this connection's token is actually on the row. Only a write
        # that matched a row proves it; until then a plain heartbeat would
        # update nothing, because it filters on that same token.
        self._attached = False

    async def attach(self) -> None:
        self._last_write = time.monotonic()
        await self._write(claim=True)

    async def beat(self) -> None:
        now = time.monotonic()
        if now - self._last_write < CAPTURE_HEARTBEAT_INTERVAL_S:
            return
        # Advance the throttle BEFORE the write, not after a successful one.
        # beat() runs on the receive path — ~100 audio frames a second — so
        # retrying a failed write on the next frame would put a pooled database
        # round-trip in front of every frame for as long as the database is
        # unhappy, starving STT and draining the pool for the whole process.
        # Liveness that is one interval stale is the cheap failure; a write
        # storm on the capture hot path is not.
        self._last_write = now
        # Re-claim when the row does not carry this token: attach may have hit
        # a transient error, or lost the status race with /end + reconnect.
        # Beating with an unclaimed token matches no row forever, which would
        # leave the viewer calling a live capture disconnected for the life of
        # the socket.
        await self._write(claim=not self._attached)

    async def _write(self, *, claim: bool) -> None:
        """Run one heartbeat UPDATE and record whether it matched the row."""
        if claim:
            sql = (
                "UPDATE meetings SET capture_connection_id = $1, "
                "capture_heartbeat_at = NOW() "
                "WHERE id = $2 AND user_id = $3 AND status = 'recording'"
            )
            args: tuple = (self._token, self._meeting_id, self._user_id)
        else:
            sql = (
                "UPDATE meetings SET capture_heartbeat_at = NOW() "
                "WHERE id = $1 AND user_id = $2 "
                "AND capture_connection_id = $3 AND status = 'recording'"
            )
            args = (self._meeting_id, self._user_id, self._token)
        try:
            status = await db.execute(sql, *args)
        except Exception:
            self._attached = False
            logger.warning(
                "capture heartbeat write failed (meeting=%s, claim=%s)",
                self._meeting_id, claim,
                exc_info=True,
            )
            return
        # asyncpg returns the command tag ("UPDATE 1"); zero rows means the
        # predicate missed — another connection owns the row, or the meeting is
        # no longer recording. Either way this token is not attached.
        self._attached = str(status).rsplit(" ", 1)[-1] not in {"0", ""}

    async def detach(self) -> None:
        try:
            await db.execute(
                "UPDATE meetings SET capture_connection_id = NULL, "
                "capture_heartbeat_at = NULL "
                "WHERE id = $1 AND user_id = $2 AND capture_connection_id = $3",
                self._meeting_id,
                self._user_id,
                self._token,
            )
        except Exception:
            logger.warning(
                "capture heartbeat detach failed (meeting=%s)",
                self._meeting_id,
                exc_info=True,
            )


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
        "SELECT id, status FROM meetings "
        "WHERE id = $1 AND user_id = $2 AND source = $3",
        meeting_id, user_id, MEETING_SOURCE_CAPTURE,
    )
    if not meeting or meeting.get("status") != "recording":
        await websocket.send_json({"type": "error", "message": "meeting not open for capture"})
        await websocket.close(code=_CLOSE_FORBIDDEN)
        return

    # The connect/reject sends above and this `ready` run in a single coroutine
    # before any concurrent producer exists (the STT tasks + writer are started
    # inside _run_capture), so they can't race and go direct to the socket.
    await websocket.send_json({"type": "status", "status": "ready"})
    await _run_capture(websocket, user_id, meeting_id, user_email=user.get("email"))


async def _run_capture(
    websocket: WebSocket,
    user_id: str,
    meeting_id: str,
    *,
    user_email: str | None = None,
) -> None:
    """Drive the two-channel STT session from the live socket until stop/disconnect."""

    # Once the STT tasks are running, this socket has concurrent producers, so
    # every emit must go through the single writer — never call
    # websocket.send_json directly below this point.
    writer = _SocketWriter(websocket)
    writer.start()

    # This belongs to the capture socket rather than Live Assist: liveness must
    # remain accurate when the assist flag is off, watcher startup fails, or a
    # viewer poll lands on another Cloud Run instance.
    heartbeat = _CaptureHeartbeat(user_id, meeting_id)
    await heartbeat.attach()

    # Live assist (fails closed — None when the flag is off). The watcher only
    # ever receives writer.send, preserving the single-writer invariant, and a
    # new connection takes over any previous watcher for this meeting.
    # Best-effort: assist is an overlay on capture — a failure loading its
    # context/state must never abort the recording itself.
    assist = None
    try:
        assist = await live_assist_service.maybe_start_watcher(
            user_id, meeting_id, writer.send, user_email=user_email,
        )
    except Exception:
        logger.warning(
            "live assist failed to start; capture continues without it (meeting=%s)",
            meeting_id, exc_info=True,
        )

    async def send_json(payload: dict) -> None:
        # Tap finalized transcript segments for the assist watcher — a sync
        # enqueue, so the STT path never waits on the assist pipeline.
        if (
            assist is not None
            and payload.get("type") == "transcript"
            and payload.get("is_final")
        ):
            assist.on_final(
                payload.get("speaker", ""),
                payload.get("text", ""),
                payload.get("ts_start", 0.0),
            )
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
            await heartbeat.beat()

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
            elif ctype == "ask":
                if assist is not None:
                    # Sync enqueue; the watcher answers (or errors) on its own
                    # task and pushes back through the writer.
                    assist.submit_ask(
                        str(ctrl.get("question") or ""),
                        str(ctrl.get("request_id") or "") or None,
                        intent=str(ctrl.get("intent") or "answer"),
                        parent_item_id=(
                            str(ctrl.get("parent_item_id"))
                            if ctrl.get("parent_item_id")
                            else None
                        ),
                        focus=str(ctrl.get("focus")) if ctrl.get("focus") else None,
                    )
                else:
                    await send_json({
                        "type": "assist_error",
                        "request_id": str(ctrl.get("request_id") or "") or None,
                        "message": "Live assist is not enabled.",
                    })
            # "start" is an ack — the session is already running.
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("meeting capture WS error (meeting=%s)", meeting_id)
    finally:
        # Tear down STT FIRST so its final flush enqueues onto the writer, THEN
        # the assist watcher (cancelled, not drained — a long AI call must not
        # hold the close), THEN drain + stop the writer so that tail actually
        # reaches the socket before this handler returns (which closes it). A WS
        # drop must NOT end the meeting — REST /end or the Phase 7 auto-end
        # sweep does that, so a reconnect can resume.
        await stt.stop()
        if assist is not None:
            await assist.aclose()
        await heartbeat.detach()
        await writer.aclose()
