"""
Lightweight chat-session lifecycle for Felix.

A "session" is the conversational context behind a /voice/chat or /voice/stream
interaction. It starts implicitly when the user sends their first message in a
new conversation and ends when either:

  • the user explicitly ends it (end_session), or
  • there has been no activity for SESSION_INACTIVITY_MINUTES.

On session end, the transcript is distilled into a `session_summaries` row
via Claude. A background job sweeps stale in-memory sessions every few
minutes so idle sessions are summarised even without a closing signal.

State is process-local. That is acceptable here because:
  • the Felix backend runs as a single FastAPI process (uvicorn + APScheduler);
  • if the process restarts before summary, a partial summary is better than
    no summary. We save whatever we have on shutdown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import settings as _settings

logger = logging.getLogger(__name__)


SESSION_INACTIVITY_MINUTES = 30
MAX_TRANSCRIPT_MESSAGES    = 40   # hard cap to keep summariser inputs small
MIN_MESSAGES_TO_SUMMARISE  = 2    # skip truly empty sessions


# user_id → ActiveSession
_sessions: dict[str, dict[str, Any]] = {}

# Serialise end-session operations per user to avoid double summarisation
_locks: dict[str, asyncio.Lock] = {}


def _user_lock(user_id: str) -> asyncio.Lock:
    lock = _locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[user_id] = lock
    return lock


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_active_session(user_id: str) -> dict | None:
    return _sessions.get(user_id)


def _is_stale(session: dict[str, Any], *, now: datetime) -> bool:
    last = session.get("last_activity") or now
    return (now - last).total_seconds() / 60.0 > SESSION_INACTIVITY_MINUTES


async def touch_session(
    user_id: str,
    *,
    role: str,
    text: str,
    session_id: str | None = None,
) -> str:
    """
    Append a message to the user's active session, creating one if needed.
    If the previous session is inactive longer than the threshold it's
    summarised and closed before a new session is started.

    Returns the current session_id.
    """
    now = _now()
    stale_session: dict[str, Any] | None = None

    lock = _user_lock(user_id)
    async with lock:
        existing = _sessions.get(user_id)
        if existing is not None and _is_stale(existing, now=now):
            stale_session = _sessions.pop(user_id, None)
            existing = None

        if existing is None:
            existing = {
                "session_id":    session_id or str(uuid.uuid4()),
                "started_at":    now,
                "last_activity": now,
                "messages":      [],
            }
            _sessions[user_id] = existing

        existing["last_activity"] = now
        existing["messages"].append({
            "role":      role,
            "text":      text,
            "timestamp": now.isoformat(),
        })

        # Cap transcript length
        if len(existing["messages"]) > MAX_TRANSCRIPT_MESSAGES:
            # Keep the most recent slice — the summary will absorb older ones next time.
            existing["messages"] = existing["messages"][-MAX_TRANSCRIPT_MESSAGES:]

        current_session_id = existing["session_id"]

    if stale_session is not None:
        asyncio.create_task(_finalise_session(user_id, stale_session, reason="idle-timeout"))

    return current_session_id


async def end_session(user_id: str, *, reason: str = "explicit") -> dict | None:
    """
    Summarise + persist the user's active session.

    Returns the stored session_summaries row, or None if there was nothing
    worth summarising.
    """
    lock = _user_lock(user_id)
    async with lock:
        session = _sessions.pop(user_id, None)
    if not session:
        return None
    return await _finalise_session(user_id, session, reason=reason)

async def flush_all_sessions(*, reason: str = "shutdown") -> int:
    """Persist every in-memory session before process shutdown."""
    user_ids = list(_sessions.keys())
    flushed = 0
    for user_id in user_ids:
        try:
            row = await end_session(user_id, reason=reason)
            if row:
                flushed += 1
        except Exception:
            logger.warning("flush_all_sessions: end_session failed for %s", user_id, exc_info=True)
    return flushed


async def _finalise_session(user_id: str, session: dict[str, Any], *, reason: str) -> dict | None:
    messages = session.get("messages") or []
    if len(messages) < MIN_MESSAGES_TO_SUMMARISE:
        return None

    conversation = _render_conversation(messages)
    try:
        summary_data = await _summarise_conversation(user_id, conversation)
    except Exception:
        logger.exception("Session summariser failed for user %s", user_id)
        summary_data = {
            "summary": _fallback_summary(messages),
            "open_items": [],
        }

    try:
        from app.services import memory_service
        row = await memory_service.store_session_summary(
            user_id=user_id,
            summary=summary_data.get("summary") or _fallback_summary(messages),
            open_items=summary_data.get("open_items") or [],
            session_metadata={
                "session_id":   session["session_id"],
                "started_at":   session["started_at"].isoformat(),
                "ended_at":     _now().isoformat(),
                "message_count": len(messages),
                "reason":       reason,
            },
        )
    except Exception:
        logger.exception("Failed to persist session summary for user %s", user_id)
        return None

    logger.info(
        "Session ended for user=%s reason=%s messages=%d",
        user_id, reason, len(messages),
    )

    # If the session yielded concrete open items, promote a distilled
    # copy to Layer 3 so future retrieval can surface it semantically.
    open_items = summary_data.get("open_items") or []
    summary_text = (summary_data.get("summary") or "").strip()
    if summary_text and open_items:
        try:
            from app.services import memory_service
            asyncio.create_task(
                memory_service.distil_and_store_episode(
                    user_id=user_id,
                    episode_type="chat",
                    content=summary_text
                    + "\nOpen items: "
                    + json.dumps(open_items, default=str),
                    source_type="session",
                    source_id=session.get("session_id"),
                    occurred_at=_now(),
                    min_importance=0.4,
                )
            )
        except Exception:
            logger.debug("session → episode promotion failed", exc_info=True)

    return row


async def sweep_stale_sessions() -> int:
    """Called by APScheduler — summarise any sessions that have gone silent."""
    now = _now()
    stale = [
        uid for uid, s in list(_sessions.items())
        if (now - (s.get("last_activity") or now)).total_seconds() / 60.0
        > SESSION_INACTIVITY_MINUTES
    ]
    for uid in stale:
        try:
            await end_session(uid, reason="sweep")
        except Exception:
            logger.warning("sweep_stale_sessions: end_session failed for %s", uid)
    return len(stale)


# ---------------------------------------------------------------------------
# Summariser
# ---------------------------------------------------------------------------

def _render_conversation(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role", "").capitalize() or "User"
        text = (m.get("text") or "").strip().replace("\n", " ")
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines)


def _fallback_summary(messages: list[dict]) -> str:
    head = next(
        (m["text"] for m in messages if m.get("role") == "user" and m.get("text")),
        "",
    )
    return f"Chat session ({len(messages)} messages). First user turn: {head[:200]}"


async def _summarise_conversation(user_id: str, conversation: str) -> dict:
    """Use Claude Haiku via ai_service to distil a finished session."""
    # Lazy imports to avoid circulars
    from anthropic import AsyncAnthropic
    import re
    import time as _time

    from app.prompts.memory import SESSION_SUMMARY_PROMPT
    from app.services.ai_service import log_ai_call

    _client = AsyncAnthropic(api_key=_settings.ANTHROPIC_API_KEY)

    started = _time.monotonic()
    response = None
    success = True
    parse_error = False
    error_message: str | None = None
    try:
        response = await _client.messages.create(
            model=_settings.ANTHROPIC_MODEL_FAST,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": SESSION_SUMMARY_PROMPT.format(conversation=conversation[:12000]),
            }],
        )
        text = response.content[0].text
        text = re.sub(r"^```(?:json)?\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            parse_error = True
            error_message = f"JSONDecodeError: {e}"
            return {"summary": text.strip()[:500], "open_items": []}
        return {
            "summary": (data.get("summary") or "").strip(),
            "open_items": data.get("open_items") or [],
        }
    except Exception as e:
        success = False
        error_message = f"{type(e).__name__}: {e}"
        raise
    finally:
        await log_ai_call(
            feature="session_summary",
            model=_settings.ANTHROPIC_MODEL_FAST,
            response=response,
            started_at=started,
            user_id=user_id,
            success=success,
            parse_error=parse_error,
            error_message=error_message,
        )
