"""
Live In-Meeting Assistant — salience-gated context cards + mid-meeting asks.

Pipeline (proactive path):

    final transcript events
        → CandidateGate      (deterministic, free — most turns stop here)
        → Haiku watch call   (decides salience AND writes the card; usually null)
        → acceptance gate    (usefulness_score threshold, dedupe, caps)
        → persist to meeting_assist_items + emit {"type": "assist"} over the WS

Core principle: silence is success. Felix is memory augmentation, not a
meeting coach — the gate, the prompt, and the acceptance thresholds all bias
toward showing nothing.

Ownership: exactly ONE watcher per meeting. A PostgreSQL advisory lock enforces
this across Cloud Run instances; the module-level ``_watchers`` registry keeps
last-writer-wins semantics for reconnects/second tabs within one instance. The
watcher's lifetime is the WS connection's: suggestions are useless with no
client attached, and a dropped socket must stop spend.

Concurrency contract with ``meetings_ws``:
  • the watcher only ever receives ``_SocketWriter.send`` — never the raw
    websocket — so it cannot break the single-writer invariant;
  • ``on_final`` / ``submit_ask`` are synchronous ``put_nowait`` enqueues — the
    audio path never waits on Anthropic;
  • all awaits happen inside the watcher's own task; ``aclose`` cancels rather
    than drains a long AI call.

Fail closed: everything is gated on ``settings.live_assist_mode`` AND
``settings.meeting_capture_mode`` (see ``_assist_enabled``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import Awaitable, Callable

from fastapi import HTTPException

from app import db
from app.config import settings
from app.prompts.live_assist import (
    CARD_KINDS,
    LIVE_ASSIST_ASK_PROMPT,
    LIVE_ASSIST_SYSTEM,
    LIVE_ASSIST_WATCH_PROMPT,
    format_shown_titles,
)
from app.services import ai_service as _ai
from app.services.ai_service import log_ai_call

logger = logging.getLogger(__name__)

SendJson = Callable[[dict], Awaitable[None]]

# ---------------------------------------------------------------------------
# Tunables (Phase 6 eval adjusts these — keep them module-level for tests)
# ---------------------------------------------------------------------------

# Cadence
COALESCE_S = 4.0              # let a speech turn finish before evaluating
MIN_CALL_INTERVAL_S = 10.0    # floor between watch calls, even on high signal
ACCUM_MIN_CHARS = 400         # ordinary conversation: this much new transcript…
ACCUM_MIN_INTERVAL_S = 45.0   # …AND this long since the last call

# Hard caps (belt over the model's judgment; when tripped the watcher idles)
MAX_WATCH_CALLS = 60          # per WS connection
MAX_PROACTIVE_CARDS = 12      # per meeting
MIN_CARD_INTERVAL_S = 20.0    # between shown cards

# Acceptance
SCORE_THRESHOLD = 0.7         # usefulness_score below this is dropped

# Context sizes
TRANSCRIPT_WINDOW_SEGMENTS = 40
TRANSCRIPT_WINDOW_CHARS = 6000
DIGEST_CAP_CHARS = 6000
CONTEXT_RETRY_AFTER_S = 60.0  # lazy re-read of live_context if prefetch was slow

# Ask
ASK_MAX_CHARS = 1000
ASK_MIN_INTERVAL_S = 5.0
MAX_ASKS = 20

# Budget
BUDGET_RECHECK_EVERY = 20     # watch calls between check_monthly_ai_budget runs


# ---------------------------------------------------------------------------
# Gating flag — fail closed on both settings
# ---------------------------------------------------------------------------


async def _assist_enabled(user_id: str) -> bool:
    """Gate: BOTH meeting_capture_mode and live_assist_mode must be on."""
    row = await db.query_one(
        "SELECT meeting_capture_mode, live_assist_mode FROM settings WHERE user_id = $1",
        user_id,
    )
    return bool(
        row and row.get("meeting_capture_mode") and row.get("live_assist_mode")
    )


# ---------------------------------------------------------------------------
# Prefetch — pre-meeting context snapshot onto meetings.live_context
# ---------------------------------------------------------------------------

_DIGEST_FIELDS = (
    ("attendees_summary",    "Attendees"),
    ("per_attendee_context", "Per-attendee history"),
    ("owed_by_user_list",    "Open commitments the user owes"),
    ("owed_to_user_list",    "Open commitments owed to the user"),
    ("recent_threads",       "Recent email threads"),
    ("past_episodes",        "Relevant past episodes"),
)

_STOPWORDS = frozenset(
    "about above after again all also and any are been before being below between "
    "both but cannot could did does doing down during each few from further had has "
    "have having her here hers him his how into its itself just more most not now "
    "off once only other our ours out over own same she should some such than that "
    "the their theirs them then there these they this those through under until "
    "very was were what when where which while who whom why will with would your "
    "yours meeting email emails sent received subject none".split()
)


def _build_digest(ctx: dict) -> dict:
    """Keep only the compact formatted strings, capped at DIGEST_CAP_CHARS total."""
    digest: dict[str, str] = {}
    remaining = DIGEST_CAP_CHARS
    for field, _label in _DIGEST_FIELDS:
        if remaining <= 0:
            break
        value = (ctx.get(field) or "").strip()
        if not value or value in ("—", "(none)"):
            continue
        digest[field] = value[:remaining]
        remaining -= len(digest[field])
    return digest


def format_digest(digest: dict) -> str:
    """Render the stored digest dict into the prompt's context block."""
    parts = []
    for field, label in _DIGEST_FIELDS:
        value = (digest.get(field) or "").strip()
        if value:
            parts.append(f"{label}:\n{value}")
    return "\n\n".join(parts) or "(no background context available)"


def _extract_keywords(ctx: dict) -> list[str]:
    """Match-list for the CandidateGate: attendee name parts + significant terms
    from commitments and episodes. Deterministic and cheap — precision comes
    from the Haiku call behind the gate, not from this list."""
    keywords: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        token = token.strip().lower()
        if len(token) < 3 or token in _STOPWORDS or token in seen:
            return
        seen.add(token)
        keywords.append(token)

    for addr in ctx.get("attendees") or []:
        local = str(addr).split("@")[0]
        for part in re.split(r"[._\-+]", local):
            add(part)

    for field in ("attendees_summary", "owed_by_user_list", "owed_to_user_list", "past_episodes"):
        text = ctx.get(field) or ""
        for token in re.findall(r"[A-Za-z][A-Za-z']{4,}", text):
            add(token)
            if len(keywords) >= 50:
                return keywords
    return keywords[:50]


async def prefetch_context(user_id: str, meeting_id: str) -> None:
    """Snapshot meeting context onto meetings.live_context (spawned at start).

    Best-effort: any failure is logged and the watcher simply runs
    transcript+memory-free. Checks the flag itself so callers can spawn
    unconditionally without an extra query on the request path.
    """
    try:
        if not await _assist_enabled(user_id):
            return
        meeting = await db.query_one(
            "SELECT calendar_event_id, title, attendees, template, started_at, date "
            "FROM meetings WHERE id = $1 AND user_id = $2",
            meeting_id, user_id,
        )
        if not meeting:
            return

        # gather_meeting_context takes a calendar-event dict but tolerates
        # missing fields, so a synthetic dict from the meeting row works for
        # ad-hoc meetings too (empty attendees → transcript/memory-only digest).
        from app.services.meeting_prep_service import meeting_prep_service

        start = meeting.get("started_at") or meeting.get("date")
        event = {
            "id":        meeting.get("calendar_event_id"),
            "title":     meeting.get("title"),
            "attendees": meeting.get("attendees") or [],
            "start":     start.isoformat() if hasattr(start, "isoformat") else None,
        }
        ctx = await meeting_prep_service.gather_meeting_context(user_id, event)

        live_context = {
            "digest":        _build_digest(ctx),
            "keywords":      _extract_keywords(ctx),
            "meeting_title": meeting.get("title") or "(untitled)",
            "template":      meeting.get("template") or "general",
            "prefetched_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.execute(
            "UPDATE meetings SET live_context = $3, updated_at = NOW() "
            "WHERE id = $1 AND user_id = $2",
            meeting_id, user_id, live_context,
        )
    except Exception:
        logger.warning(
            "live assist prefetch failed for meeting %s", meeting_id, exc_info=True
        )


# ---------------------------------------------------------------------------
# CandidateGate — deterministic, pure, no I/O
# ---------------------------------------------------------------------------

_QUESTION_LEAD_RE = re.compile(
    r"^(what|who|whom|whose|when|where|why|how|which|can|could|would|will|won't|"
    r"do|does|did|is|are|was|were|should|shall|have|has|had)\b",
    re.IGNORECASE,
)
_REQUEST_DECISION_RE = re.compile(
    r"\b(can you|could you|will you|would you|we need|we should|let's decide|"
    r"let's agree|agreed|deadline|due date|action item|next steps?|follow(?:ing)? up|"
    r"who(?:'s| is) (?:taking|owning|doing))\b",
    re.IGNORECASE,
)
_DATE_NUMBER_RE = re.compile(
    r"\b\d[\d,.]*\s*(?:%|percent|k\b|thousand|million|billion)?"
    r"|\b(?:january|february|march|april|may|june|july|august|september|october|"
    r"november|december|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"tomorrow|next week|next month|end of (?:the )?(?:week|month|quarter|year))\b",
    re.IGNORECASE,
)
_ENTITY_RE = re.compile(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-z]{2,}\b")
_ENTITY_STOPLIST = frozenset(
    "monday tuesday wednesday thursday friday saturday sunday january february "
    "march april may june july august september october november december okay "
    "yeah yes thanks right sure great cool".split()
)


class CandidateGate:
    """Deterministic pre-gate: decides which finals are worth an LLM look.

    Pure state machine — no I/O, no clocks — so it is unit-testable in
    isolation. Two paths out:
      • ``observe()`` returns a trigger_type string when a final is
        high-signal (evaluate promptly);
      • ``accumulation_ready()`` fires for ordinary conversation once enough
        new transcript has piled up (the caller supplies elapsed time).
    """

    def __init__(self, keywords: list[str] | None = None) -> None:
        self._keywords = {
            k.strip().lower() for k in (keywords or []) if k and k.strip()
        }
        self._keyword_patterns = tuple(
            re.compile(rf"(?<!\w){re.escape(keyword)}(?!\w)")
            for keyword in self._keywords
        )
        self._seen_entities: set[str] = set()
        self.accumulated_chars = 0

    def observe(self, speaker: str, text: str) -> str | None:
        """Feed one final segment; return a trigger_type or None (accumulate)."""
        text = (text or "").strip()
        if not text:
            return None
        self.accumulated_chars += len(text)

        if speaker == "them" and (
            "?" in text or _QUESTION_LEAD_RE.match(text)
        ):
            return "question"
        if _REQUEST_DECISION_RE.search(text):
            return "request_decision"

        lowered = text.lower()
        for pattern in self._keyword_patterns:
            if pattern.search(lowered):
                return "context_keyword"

        for match in _ENTITY_RE.finditer(text):
            entity = match.group(0).lower()
            if entity in _ENTITY_STOPLIST or entity in self._seen_entities:
                continue
            self._seen_entities.add(entity)
            return "new_entity"

        if _DATE_NUMBER_RE.search(text):
            return "date_number"
        return None

    def accumulation_ready(self, seconds_since_last_call: float) -> bool:
        return (
            self.accumulated_chars >= ACCUM_MIN_CHARS
            and seconds_since_last_call >= ACCUM_MIN_INTERVAL_S
        )

    def reset_accumulation(self) -> None:
        self.accumulated_chars = 0


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", (title or "").lower())).strip()


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


class LiveAssistWatcher:
    """Per-meeting assist loop: gate → Haiku → acceptance → persist + emit."""

    def __init__(
        self,
        *,
        user_id: str,
        meeting_id: str,
        send_json: SendJson,
        user_email: str | None = None,
    ) -> None:
        self.user_id = user_id
        self.meeting_id = meeting_id
        self._send_json = send_json
        self._user_email = user_email

        self._events: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._closed = False
        self._lock_key = f"felix_live_assist:{meeting_id}"
        self._owns_advisory_lock = False
        self._gate = CandidateGate()
        self._context: dict | None = None
        self._context_retried = False

        self._window: deque[tuple[str, str, float]] = deque(
            maxlen=TRANSCRIPT_WINDOW_SEGMENTS
        )
        self._shown_titles: list[str] = []
        self._shown_normalized: set[str] = set()

        self._started_at = time.monotonic()
        self._last_call_at = float("-inf")
        self._last_card_at = float("-inf")
        self._last_ask_at = float("-inf")
        self._watch_calls = 0
        self._cards_shown = 0
        self._asks = 0
        self._stopped_for_budget = False

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Seed state from the DB (context, shown titles, transcript window) so
        a reconnect resumes instead of re-suggesting, then start the run task."""
        await self._load_context()

        rows = await db.query(
            "SELECT title, source FROM meeting_assist_items "
            "WHERE user_id = $1 AND meeting_id = $2 ORDER BY created_at",
            self.user_id, self.meeting_id,
        )
        for row in rows:
            self._remember_title(row.get("title") or "")
        # Per-source counters: a meeting full of ask answers must not silence
        # the proactive pipeline, and reconnecting must not reset the ask cap.
        self._cards_shown = sum(1 for r in rows if r.get("source") == "proactive")
        self._asks = sum(1 for r in rows if r.get("source") == "ask")

        segments = await db.query(
            "SELECT speaker, text, ts_start FROM meeting_transcript_segments "
            "WHERE user_id = $1 AND meeting_id = $2 "
            "ORDER BY ts_start DESC LIMIT $3",
            self.user_id, self.meeting_id, TRANSCRIPT_WINDOW_SEGMENTS,
        )
        for seg in reversed(segments):
            self._window.append(
                (seg["speaker"], seg["text"], float(seg.get("ts_start") or 0.0))
            )

        # A newer connection may have taken over (and aclosed us) while the
        # seed queries were in flight — in that case never start the loop.
        if self._closed:
            return
        self._task = asyncio.create_task(
            self._run(), name=f"live_assist:{self.meeting_id}"
        )

    def on_final(self, speaker: str, text: str, ts_start: float) -> None:
        """Sync enqueue from the WS transcript path — must never block or raise."""
        # A superseded connection keeps its watcher reference after takeover;
        # events sent to a closed watcher have no consumer and must be dropped.
        if self._closed:
            return
        try:
            self._events.put_nowait(("final", speaker, text, ts_start))
        except Exception:  # pragma: no cover — unbounded queue shouldn't raise
            logger.debug("live assist enqueue failed", exc_info=True)

    def submit_ask(self, question: str, request_id: str | None) -> None:
        """Sync enqueue of a typed mid-meeting question."""
        if self._closed:
            return
        try:
            self._events.put_nowait(("ask", question, request_id))
        except Exception:  # pragma: no cover
            logger.debug("live assist ask enqueue failed", exc_info=True)

    async def aclose(self) -> None:
        """Cancel the run task, deregister, and release shared ownership."""
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if _watchers.get(self.meeting_id) is self:
            _watchers.pop(self.meeting_id, None)
        if self._owns_advisory_lock:
            self._owns_advisory_lock = False
            try:
                await db.advisory_unlock(self._lock_key)
            except Exception:
                logger.warning(
                    "live assist advisory unlock failed for meeting %s",
                    self.meeting_id, exc_info=True,
                )

    # -- run loop ------------------------------------------------------------

    async def _run(self) -> None:
        pending_trigger: str | None = None
        pending_deadline = 0.0
        while True:
            timeout = None
            if pending_trigger is not None:
                timeout = max(0.0, pending_deadline - time.monotonic())
            try:
                if timeout is None:
                    event = await self._events.get()
                else:
                    event = await asyncio.wait_for(self._events.get(), timeout)
            except asyncio.TimeoutError:
                trigger, pending_trigger = pending_trigger, None
                await self._guarded(self._watch_call(trigger))
                continue

            if event[0] == "ask":
                _, question, request_id = event
                await self._guarded(self._handle_ask(question, request_id))
                continue

            _, speaker, text, ts_start = event
            self._window.append((speaker, text, float(ts_start or 0.0)))
            if not self._may_watch():
                continue

            trigger = self._gate.observe(speaker, text)
            now = time.monotonic()
            if pending_trigger is None:
                if trigger is not None:
                    pending_trigger = trigger
                    pending_deadline = max(
                        now + COALESCE_S,
                        self._last_call_at + MIN_CALL_INTERVAL_S,
                    )
                elif self._gate.accumulation_ready(now - self._last_call_at):
                    pending_trigger = "accumulation"
                    pending_deadline = now + COALESCE_S

    async def _guarded(self, handler: Awaitable[None]) -> None:
        """Run one event handler without letting an unexpected error (e.g. a DB
        failure in the budget check or context reload) kill the sole run task —
        a dead loop would leave the queue accepting events with no consumer."""
        try:
            await handler
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "live assist handler failed for meeting %s",
                self.meeting_id, exc_info=True,
            )

    def _may_watch(self) -> bool:
        return (
            not self._stopped_for_budget
            and self._watch_calls < MAX_WATCH_CALLS
            and self._cards_shown < MAX_PROACTIVE_CARDS
        )

    # -- context -------------------------------------------------------------

    async def _load_context(self) -> None:
        row = await db.query_one(
            "SELECT live_context, title, template FROM meetings "
            "WHERE id = $1 AND user_id = $2",
            self.meeting_id, self.user_id,
        )
        if not row:
            return
        context = row.get("live_context")
        if isinstance(context, str):  # defensive: codec normally decodes JSONB
            try:
                context = json.loads(context)
            except json.JSONDecodeError:
                context = None
        if context:
            self._context = context
            self._gate = CandidateGate(context.get("keywords") or [])
        else:
            self._context = None
        self._meeting_title = row.get("title") or "(untitled)"
        self._template = row.get("template") or "general"

    async def _maybe_retry_context(self) -> None:
        """Prefetch may still be running when the watcher starts — re-read once."""
        if (
            self._context is None
            and not self._context_retried
            and time.monotonic() - self._started_at >= CONTEXT_RETRY_AFTER_S
        ):
            self._context_retried = True
            await self._load_context()

    # -- proactive watch call -------------------------------------------------

    async def _watch_call(self, trigger: str | None) -> None:
        if not self._may_watch():
            return
        await self._maybe_retry_context()
        if not await self._budget_ok():
            return

        self._watch_calls += 1
        self._last_call_at = time.monotonic()
        self._gate.reset_accumulation()

        window_segments = len(self._window)
        prompt = LIVE_ASSIST_WATCH_PROMPT.format(
            meeting_title=getattr(self, "_meeting_title", "(untitled)"),
            template=getattr(self, "_template", "general"),
            context_digest=format_digest((self._context or {}).get("digest") or {}),
            shown_titles=format_shown_titles(self._shown_titles),
            transcript_window=self._format_window(),
        )

        started = time.monotonic()
        result = await self._json_ai_call(
            feature="live_assist_watch",
            model=settings.ANTHROPIC_MODEL_FAST,
            max_tokens=350,
            prompt=prompt,
        )
        latency_ms = int((time.monotonic() - started) * 1000)

        card = (result or {}).get("card")
        if not card:
            return
        accepted, reason = self._accept(card)
        if not accepted:
            logger.debug(
                "live assist card dropped (%s) for meeting %s", reason, self.meeting_id
            )
            return

        transcript_ts = self._window[-1][2] if self._window else None
        item = await self._persist_item(
            kind=card["kind"],
            source="proactive",
            question=None,
            title=str(card["title"]).strip(),
            body=str(card["body"]).strip(),
            transcript_ts=transcript_ts,
            usefulness_score=float(card.get("usefulness_score") or 0.0),
            trigger_type=trigger,
            request_id=None,
            model=settings.ANTHROPIC_MODEL_FAST,
            metadata={
                "window_segments": window_segments,
                "latency_ms": latency_ms,
            },
        )
        if item is None:
            return
        self._remember_title(item["title"])
        self._cards_shown += 1
        self._last_card_at = time.monotonic()
        await self._send_json({"type": "assist", "item": item})

    def _accept(self, card: dict) -> tuple[bool, str]:
        """Server-side acceptance gate — never trust the model's own judgment alone."""
        if not isinstance(card, dict):
            return False, "not_a_dict"
        kind = str(card.get("kind") or "").strip()
        title = str(card.get("title") or "").strip()
        body = str(card.get("body") or "").strip()
        if kind not in CARD_KINDS or not title or not body:
            return False, "invalid_shape"
        try:
            score = float(card.get("usefulness_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        if score < SCORE_THRESHOLD:
            return False, "low_score"
        if _normalize_title(title) in self._shown_normalized:
            return False, "duplicate_title"
        if time.monotonic() - self._last_card_at < MIN_CARD_INTERVAL_S:
            return False, "card_interval"
        return True, "ok"

    # -- ask -----------------------------------------------------------------

    async def _handle_ask(self, question: str, request_id: str | None) -> None:
        async def fail(message: str) -> None:
            await self._send_json(
                {"type": "assist_error", "request_id": request_id, "message": message}
            )

        question = (question or "").strip()
        if not question or len(question) > ASK_MAX_CHARS:
            await fail("Ask a question up to 1000 characters.")
            return
        now = time.monotonic()
        if self._asks >= MAX_ASKS:
            await fail("Ask limit reached for this meeting.")
            return
        if now - self._last_ask_at < ASK_MIN_INTERVAL_S:
            await fail("One question at a time — try again in a few seconds.")
            return
        try:
            from app.middleware.rate_limit import check_monthly_ai_budget
            await check_monthly_ai_budget(self.user_id, self._user_email)
        except HTTPException as exc:
            await fail(str(exc.detail))
            return

        self._asks += 1
        self._last_ask_at = now
        await self._maybe_retry_context()

        prompt = LIVE_ASSIST_ASK_PROMPT.format(
            meeting_title=getattr(self, "_meeting_title", "(untitled)"),
            template=getattr(self, "_template", "general"),
            context_digest=format_digest((self._context or {}).get("digest") or {}),
            transcript_window=self._format_window(),
            question=question,
        )
        started = time.monotonic()
        result = await self._json_ai_call(
            feature="live_assist_ask",
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=400,
            prompt=prompt,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if not result or not str(result.get("body") or "").strip():
            await fail("Couldn't answer that just now — try again.")
            return

        transcript_ts = self._window[-1][2] if self._window else None
        item = await self._persist_item(
            kind="answer",
            source="ask",
            question=question,
            title=str(result.get("title") or question[:80]).strip(),
            body=str(result["body"]).strip(),
            transcript_ts=transcript_ts,
            usefulness_score=None,
            trigger_type=None,
            request_id=request_id,
            model=settings.ANTHROPIC_MODEL_SMART,
            metadata={"latency_ms": latency_ms},
        )
        if item is None:
            await fail("Couldn't answer that just now — try again.")
            return
        self._remember_title(item["title"])
        await self._send_json({"type": "assist", "item": item})

    # -- shared plumbing ------------------------------------------------------

    async def _budget_ok(self) -> bool:
        """Re-check the monthly budget every BUDGET_RECHECK_EVERY watch calls."""
        if self._watch_calls % BUDGET_RECHECK_EVERY != 0:
            return True
        try:
            from app.middleware.rate_limit import check_monthly_ai_budget
            await check_monthly_ai_budget(self.user_id, self._user_email)
            return True
        except HTTPException as exc:
            self._stopped_for_budget = True
            await self._send_json(
                {"type": "assist_error", "request_id": None, "message": str(exc.detail)}
            )
            return False
        except Exception:
            logger.warning("live assist budget check failed", exc_info=True)
            return True

    async def _json_ai_call(
        self, *, feature: str, model: str, max_tokens: int, prompt: str
    ) -> dict | None:
        """One JSON-out Anthropic call with the mandatory ai_calls logging.
        Any failure (API error, unparseable output) returns None — never a
        broken card."""
        started = time.monotonic()
        response = None
        success = True
        parse_error = False
        error_message: str | None = None
        try:
            response = await _ai.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=LIVE_ASSIST_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            try:
                result = json.loads(
                    _ai._strip_markdown_fences(response.content[0].text)
                )
                return result if isinstance(result, dict) else None
            except json.JSONDecodeError as e:
                parse_error = True
                error_message = f"JSONDecodeError: {e}"
                return None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            success = False
            error_message = f"{type(e).__name__}: {e}"
            logger.warning("live assist %s call failed", feature, exc_info=True)
            return None
        finally:
            await log_ai_call(
                feature=feature,
                model=model,
                response=response,
                started_at=started,
                user_id=self.user_id,
                success=success,
                parse_error=parse_error,
                error_message=error_message,
                quota_scope="interactive",
            )

    async def _persist_item(self, **fields) -> dict | None:
        """Insert a meeting_assist_items row; return the wire-format item dict."""
        try:
            row = await db.insert(
                "meeting_assist_items",
                {
                    "user_id":          self.user_id,
                    "meeting_id":       self.meeting_id,
                    "kind":             fields["kind"],
                    "source":           fields["source"],
                    "question":         fields["question"],
                    "title":            fields["title"],
                    "body":             fields["body"],
                    "transcript_ts":    fields["transcript_ts"],
                    "usefulness_score": fields["usefulness_score"],
                    "trigger_type":     fields["trigger_type"],
                    "prompt_version":   _ai.PROMPT_VERSIONS.get(
                        "live_assist_watch" if fields["source"] == "proactive" else "live_assist_ask",
                        "v1",
                    ),
                    "request_id":       fields["request_id"],
                    "metadata":         fields["metadata"] or {},
                    "model":            fields["model"],
                },
            )
        except Exception:
            logger.warning(
                "failed to persist assist item for meeting %s",
                self.meeting_id, exc_info=True,
            )
            return None
        if not row:
            return None
        return item_to_wire(row)

    def _remember_title(self, title: str) -> None:
        title = (title or "").strip()
        if not title:
            return
        self._shown_titles.append(title)
        self._shown_normalized.add(_normalize_title(title))

    def _format_window(self) -> str:
        lines: list[str] = []
        total = 0
        for speaker, text, _ts in reversed(self._window):
            line = f"{speaker}: {text}"
            total += len(line) + 1
            if total > TRANSCRIPT_WINDOW_CHARS:
                break
            lines.append(line)
        lines.reverse()
        return "\n".join(lines) or "(no transcript yet)"


# ---------------------------------------------------------------------------
# Registry — local takeover + cross-instance ownership
# ---------------------------------------------------------------------------

_watchers: dict[str, LiveAssistWatcher] = {}


async def maybe_start_watcher(
    user_id: str,
    meeting_id: str,
    send_json: SendJson,
    *,
    user_email: str | None = None,
) -> LiveAssistWatcher | None:
    """Start a watcher for this connection, closing any previous one for the
    same meeting in this process. A meeting-scoped PostgreSQL advisory lock
    prevents another Cloud Run instance from starting a second inference loop.
    Returns None when the feature is off or another instance owns the watcher."""
    if not await _assist_enabled(user_id):
        return None

    watcher = LiveAssistWatcher(
        user_id=user_id,
        meeting_id=meeting_id,
        send_json=send_json,
        user_email=user_email,
    )
    # Read-previous + reserve happen with NO await in between, so two sockets
    # connecting concurrently serialize through the registry: the second sees
    # the first's reservation and acloses it. A watcher aclosed mid-start (its
    # _closed flag set while the seed queries were in flight) never starts its
    # run loop, so exactly one inference loop survives any interleaving.
    previous = _watchers.get(meeting_id)
    _watchers[meeting_id] = watcher
    if previous is not None:
        await previous.aclose()
    if watcher._closed:
        return watcher
    try:
        if not await db.try_advisory_lock(watcher._lock_key):
            await watcher.aclose()
            return None
        watcher._owns_advisory_lock = True
        # A newer local connection may have superseded this watcher while the
        # advisory-lock query was in flight. Balance the successful acquire and
        # leave ownership to that newer connection.
        if watcher._closed:
            await watcher.aclose()
            return watcher
        await watcher.start()
    except Exception:
        await watcher.aclose()
        raise
    return watcher


def item_to_wire(row: dict) -> dict:
    """meeting_assist_items row → the WS/REST item payload."""
    created = row.get("created_at")
    return {
        "id":            str(row.get("id")),
        "kind":          row.get("kind"),
        "source":        row.get("source"),
        "question":      row.get("question"),
        "title":         row.get("title"),
        "body":          row.get("body"),
        "transcript_ts": row.get("transcript_ts"),
        "dismissed":     bool(row.get("dismissed")),
        # Lets the client correlate an ask answer with ITS pending request —
        # a late answer to an abandoned ask must not settle a newer one.
        "request_id":    row.get("request_id"),
        "created_at":    created.isoformat() if hasattr(created, "isoformat") else created,
    }
