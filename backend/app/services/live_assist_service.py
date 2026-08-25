"""
Live In-Meeting Assistant — salience-gated context cards + mid-meeting asks.

Pipeline (proactive path):

    final transcript events
        → CandidateGate      (deterministic, free — most turns stop here)
        → Haiku watch call   (decides salience AND writes the card; usually null)
        → acceptance gate    (usefulness_score threshold, dedupe, caps)
        → persist to meeting_assist_items + emit {"type": "assist"} over the WS

Candidate interview meetings run the same pipeline with a second exit: the watch
call may triage the transcript into a technical question instead of a card,
which a Sonnet solve then answers. That branch is deliberately narrow — only a
question the OTHER participant put to the user, only once it is completely
stated (an incomplete one arms a short recheck rather than waiting out the
accumulation gate), and only with the monthly budget freshly checked, because a
solve costs far more than the watch call that proposed it. Explicit interviewer
meetings retain the general card flow and never enter this candidate solve path.

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

Asks are transport-neutral. ``_handle_ask`` is the single implementation of
what an ask *is* — limits, cooldown, budget, expansion rules, model choice,
context, persistence — and every transport reaches it:

  • the capture WebSocket enqueues via ``submit_ask`` onto the live watcher;
  • REST callers (a manual session's ask box, and the phone viewer on a
    captured meeting) go through ``answer_typed_question``, which drives one
    ask on a short-lived watcher (``start(run_loop=False)``) and awaits it on
    the request.

The REST path is the one exception to the enqueue contract above. The limits it
would otherwise lose to a per-request instance are restored explicitly: while
the meeting's ask slot is held, every persisted item is folded back in, so each
transport spends the same MAX_ASKS budget and answers against the same
conversation regardless of which device or instance wrote it.

Exactly one ask runs per meeting at a time, across transports and Cloud Run
instances: ``_handle_ask`` takes a process-local fast-fail slot plus a shared
PostgreSQL advisory lock, and refuses rather than queues when another ask holds
either one.

A ``request_id`` correlates an answer with the ask that asked for it, and is
kept on the row. If a client ever does retry under the same id after the first
attempt persisted, that answer is replayed rather than paid for twice — but
neither client retries today (both mint a fresh id per attempt), so treat this
as the correlation key it is, not as an idempotency guarantee to rely on.

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
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Awaitable, Callable

from fastapi import HTTPException

from app import db
from app.config import settings
from app.models.meeting import (
    CANDIDATE_ASSIST_MODES,
    MEETING_SOURCE_MANUAL,
    AssistMeetingMode,
    resolve_assist_meeting_mode,
)
from app.prompts.live_assist import (
    CARD_KINDS,
    LIVE_ASSIST_ASK_PROMPT,
    LIVE_ASSIST_INTERVIEW_ANSWER_PROMPT,
    LIVE_ASSIST_INTERVIEW_EXPAND_PROMPT,
    LIVE_ASSIST_INTERVIEW_WATCH_PROMPT,
    LIVE_ASSIST_STANDALONE_ASK_PROMPT,
    LIVE_ASSIST_SYSTEM,
    LIVE_ASSIST_WATCH_PROMPT,
    format_shown_titles,
)
from app.services import ai_service as _ai
from app.services.ai_service import log_ai_call
from app.services.timezone_utils import local_date_of

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
# A half-stated interview question is re-evaluated on its own short clock: the
# continuation is ordinary transcript that would otherwise wait out the 400-char
# accumulation gate, long after the candidate had to start answering.
INTERVIEW_RECHECK_S = 6.0
MAX_INTERVIEW_RECHECKS = 3    # consecutive; a question never completing is a no

# Hard caps (belt over the model's judgment; when tripped the watcher idles)
MAX_WATCH_CALLS = 60          # per meeting (survives reconnects — see _watch_calls)
MAX_PROACTIVE_CARDS = 12      # per meeting
MIN_CARD_INTERVAL_S = 20.0    # between shown cards

# Hard deadline on any single Anthropic call, retries included. The shared
# client allows 120s and two retries — five times the browser's ask deadline
# (useMeetingCapture.ASK_TIMEOUT_MS), so a slow expansion would have the client
# report "no answer arrived" while the server kept generating, then drop the
# late answer into the sidebar beside that error, or beside the duplicate a
# retry produced. Every live-assist call therefore fails HERE first, with an
# assist_error the client can correlate. Keep it strictly below ASK_TIMEOUT_MS,
# with room for the persist + WS hop.
CALL_TIMEOUT_S = 60.0

# Acceptance
SCORE_THRESHOLD = 0.7         # usefulness_score below this is dropped

# Context sizes
TRANSCRIPT_WINDOW_SEGMENTS = 40
TRANSCRIPT_WINDOW_CHARS = 6000
INTERVIEW_WINDOW_CHARS = 12000
DIGEST_CAP_CHARS = 6000
# Floor on one past meeting's share of the digest, so the even split can't
# degenerate into ten unreadable slivers, and the shortest line worth emitting.
MIN_EPISODE_CHARS = 400
MIN_LINE_CHARS = 40
CONTEXT_RETRY_AFTER_S = 60.0  # lazy re-read of live_context if prefetch was slow

# Ask
ASK_MAX_CHARS = 6000
# A burst backstop, not a conversational delay. Both transports already
# serialize asks and cap them per meeting; five seconds rejected legitimate
# follow-ups when the previous answer returned quickly.
ASK_MIN_INTERVAL_S = 1.0
MAX_ASKS = 20
ASK_UNAVAILABLE_MESSAGE = "Couldn't answer that just now — try again."

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
            "SELECT calendar_event_id, title, attendees, template, started_at, date, source "
            "FROM meetings WHERE id = $1 AND user_id = $2",
            meeting_id, user_id,
        )
        if not meeting:
            return

        # A standalone session has no attendees or transcript to scope context
        # around. Give it a compact recent-meetings snapshot instead so the user
        # can ask questions such as "what did we decide last time?" immediately.
        if meeting.get("source") == MEETING_SOURCE_MANUAL:
            rows = await db.query(
                """
                SELECT m.title, COALESCE(m.started_at, m.date, m.created_at) AS occurred_at,
                       m.user_notes, s.tldr, s.decisions, s.action_items,
                       s.enhanced_notes
                FROM meetings m
                LEFT JOIN LATERAL (
                    SELECT tldr, decisions, action_items, enhanced_notes
                    FROM meeting_summaries
                    WHERE user_id = $1 AND meeting_id = m.id
                    ORDER BY created_at DESC LIMIT 1
                ) s ON TRUE
                WHERE m.user_id = $1 AND m.id <> $2
                  AND (m.status = 'done' OR COALESCE(m.user_notes, '') <> '')
                ORDER BY COALESCE(m.started_at, m.date, m.created_at) DESC
                LIMIT 10
                """,
                user_id, meeting_id,
            )
            tz_row = await db.query_one(
                "SELECT timezone FROM settings WHERE user_id = $1", user_id,
            )
            history = _format_previous_meetings(
                rows, tz_name=(tz_row or {}).get("timezone") or "UTC",
            )
            live_context = {
                "digest": {"past_episodes": history} if history else {},
                # No keywords on purpose. They feed exactly one consumer —
                # CandidateGate, which only ever observes transcript segments —
                # and a standalone session has no transcript by construction, so
                # anything computed here could never be read.
                "keywords": [],
                "meeting_title": meeting.get("title") or "(untitled)",
                "template": meeting.get("template") or "general",
                "prefetched_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.execute(
                "UPDATE meetings SET live_context = $3, updated_at = NOW() "
                "WHERE id = $1 AND user_id = $2",
                meeting_id, user_id, live_context,
            )
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


def _format_previous_meetings(rows: list[dict], *, tz_name: str = "UTC") -> str:
    """Render recent saved meetings into a bounded, prompt-friendly digest.

    The budget is shared out across meetings rather than spent strictly in
    recency order — one meeting with large enhanced_notes would otherwise
    consume all of DIGEST_CAP_CHARS and starve the other nine out entirely.
    Each meeting gets an even share of what's left, so an early small meeting
    hands its slack to the ones after it.
    """
    blocks: list[str] = []
    remaining = DIGEST_CAP_CHARS
    for index, row in enumerate(rows):
        rows_left = len(rows) - index
        budget = min(remaining, max(MIN_EPISODE_CHARS, remaining // rows_left))
        if budget <= 0:
            break
        block = _format_previous_meeting(row, tz_name, budget)
        if block:
            blocks.append(block)
            remaining -= len(block)
    return "\n\n".join(blocks)


def _format_previous_meeting(row: dict, tz_name: str, budget: int) -> str:
    """One meeting's digest block, rendered within ``budget`` characters."""
    occurred = row.get("occurred_at")
    if isinstance(occurred, datetime):
        # The user's local date, not UTC — these labels are what they reason
        # about, and a late-evening meeting carries the next day's UTC date.
        date = local_date_of(occurred, tz_name).isoformat()
    else:
        date = str(occurred or "")[:10]

    header = f"Meeting: {row.get('title') or 'Untitled'}" + (f" ({date})" if date else "")
    lines = [_truncate(header, budget)]
    used = len(lines[0])

    # (line, may_truncate). JSON payloads are all-or-nothing: half of a
    # json.dumps() output is a syntactically broken fragment, not a shorter
    # answer, so they're dropped whole when they don't fit. Prose can be cut.
    candidates: list[tuple[str, bool]] = []
    if row.get("tldr"):
        candidates.append((f"Summary: {row['tldr']}", True))
    if row.get("decisions"):
        candidates.append((f"Decisions: {json.dumps(row['decisions'], default=str)}", False))
    if row.get("action_items"):
        candidates.append(
            (f"Action items: {json.dumps(row['action_items'], default=str)}", False)
        )
    if row.get("enhanced_notes"):
        candidates.append(
            (f"Enhanced notes: {json.dumps(row['enhanced_notes'], default=str)}", False)
        )
    elif row.get("user_notes"):
        candidates.append((f"Manual notes: {row['user_notes']}", True))

    for line, may_truncate in candidates:
        room = budget - used - 1  # the joining newline
        if room < MIN_LINE_CHARS:
            break
        if len(line) > room:
            if not may_truncate:
                continue  # a shorter later line may still fit
            line = _truncate(line, room)
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    """Cut prose to ``limit`` characters, marking that it was cut."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


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

# Interview meetings only. An interviewer usually STATES a problem rather than
# asking a question — "Implement an LRU cache", "Given a sorted array, return
# the two indices…". Those turns carry no "?", no question-leading word, and
# often no novel entity or number, so every gate above sits on them until the
# 400-char accumulation window; by then the candidate has been talking for a
# minute. These two patterns give interviews their own prompt trigger.
#
# Recall over precision by design: a false trigger costs one Haiku triage call
# (floored at MIN_CALL_INTERVAL_S and capped by MAX_WATCH_CALLS), while a miss
# costs the whole feature. Triage is the precision layer, not this.
_INTERVIEW_TASK_LEAD_RE = re.compile(
    # sentence start, so "Two sum: find all pairs" counts at the colon…
    r"(?:^|[.!?:;\n]\s*)"
    # …past the throat-clearing an interviewer opens with
    r"(?:(?:ok(?:ay)?|alright|all right|so|now|right|great|cool|perfect|awesome|"
    r"sure|and|then|first|next)[,\s]+){0,3}"
    r"(?:implement|write|design|build|code|solve|create|reverse|sort|merge|parse|"
    r"traverse|compute|calculate|count|find|return|print|given|suppose|assume|"
    r"consider|imagine|say|let[’']?s|we[’']?ll|here[’']?s|start with|take a look)"
    r"\b",
    re.IGNORECASE,
)
_INTERVIEW_TASK_PHRASE_RE = re.compile(
    r"\b(?:your task|you(?:[’']re| are) given|we(?:[’']re| are) given|"
    r"the problem is|here[’']?s (?:the|a|another) (?:problem|question|exercise)|"
    r"first (?:question|problem)|next (?:question|problem)|"
    r"coding (?:question|problem|exercise|challenge)|system design|"
    r"time complexity|space complexity|big-?o|data structure|"
    r"walk me through|whiteboard|edge cases?)\b",
    re.IGNORECASE,
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

    def __init__(
        self, keywords: list[str] | None = None, *, interview: bool = False
    ) -> None:
        self._keywords = {
            k.strip().lower() for k in (keywords or []) if k and k.strip()
        }
        self._keyword_patterns = tuple(
            re.compile(rf"(?<!\w){re.escape(keyword)}(?!\w)")
            for keyword in self._keywords
        )
        self._seen_entities: set[str] = set()
        self.accumulated_chars = 0
        # Set from the meeting template once it is known (see _load_context) —
        # only interview meetings get the imperative-prompt trigger below.
        self.interview = interview

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
        # Only the other participant's turns: a problem the user states
        # themselves means they are running the interview — never ours to solve.
        if self.interview and speaker == "them" and (
            _INTERVIEW_TASK_LEAD_RE.search(text)
            or _INTERVIEW_TASK_PHRASE_RE.search(text)
        ):
            return "interview_prompt"
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
# Question fingerprints — near-duplicate matching for proactive solves
#
# The interviewer keeps discussing a problem after stating it, so the sliding
# transcript window makes triage re-state the same question in different words:
# "design a URL shortener" becomes "design a highly available URL-shortening
# service". Normalizing punctuation and case (above) treats those as different
# questions and pays for a second Sonnet solve.
#
# So compare stemmed content words instead, and compare them against the
# SHORTER question (overlap coefficient, not Jaccard): a re-statement adds
# qualifiers rather than changing the problem, which would sink a Jaccard score
# well below any usable threshold.
# ---------------------------------------------------------------------------

_QUESTION_STOPWORDS = frozenset("""
about again also and any are can could did does doing done down for from
give going has have here how into its just like make many may might much
must need not now off out over please should some such tell than that the
their them then there these they this those through under use using very
walk was way were what when where which who why will with would you your
""".split())

_STEM_SUFFIXES = ("ization", "isation", "ations", "ation", "ings", "ing",
                  "ers", "er", "ies", "es", "s")

# Overlap at or above this is the same problem restated. Deliberately biased
# toward silence (the feature's core principle): a variation the gate swallows
# is one the user can still type into the ask box, which is never deduped.
QUESTION_OVERLAP_THRESHOLD = 0.8
QUESTION_MIN_TOKENS = 2       # below this, exact normalization is all we trust


def _stem(word: str) -> str:
    """Crude suffix stripper — enough to fold shortener/shortening onto shorten."""
    for suffix in _STEM_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _question_tokens(question: str) -> frozenset[str]:
    """Stemmed content words — the fingerprint near-duplicate matching uses."""
    return frozenset(
        _stem(word)
        for word in re.findall(r"[a-z0-9]+", (question or "").lower())
        if len(word) > 2 and word not in _QUESTION_STOPWORDS
    )


def _token_overlap(a: frozenset[str], b: frozenset[str]) -> float:
    """Overlap coefficient: shared tokens as a fraction of the smaller set."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


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
        # Persisted meeting-scoped assistant conversation. Unlike the live
        # transcript window, this is intentionally retained for the whole
        # meeting so referential follow-ups such as "tell me more about that"
        # survive both a WebSocket reconnect and the manual REST transport's
        # fresh watcher per request. MAX_ASKS bounds user turns for the meeting.
        self._conversation: list[tuple[str, str, str, str]] = []
        # Ids of persisted items already folded into the state above, so the
        # pre-ask refresh can absorb another device's rows without re-adding
        # this watcher's own (see _absorb_persisted_items).
        self._seen_item_ids: set[str] = set()
        # Proactive interview solves dedupe on the question, not on the answer
        # title — the title isn't known until after the expensive call.
        self._solved_normalized: set[str] = set()
        self._solved_tokens: list[frozenset[str]] = []

        # Meeting configuration, filled in by _load_context. Initialized here so
        # every read site can be a plain attribute access: _load_context returns
        # early when the row is missing, and these defaults are the fail-closed
        # answer for that case.
        self._meeting_title = "(untitled)"
        self._template = "general"
        self._assist_meeting_mode: AssistMeetingMode = "general"
        self._standalone = False
        self._manual_notes = ""

        self._started_at = time.monotonic()
        self._last_call_at = float("-inf")
        self._last_card_at = float("-inf")
        self._last_ask_at = float("-inf")
        self._cards_shown = 0
        self._asks = 0
        self._stopped_for_budget = False
        # Set when a persist was refused because the meeting had ended under a
        # long model call, so the ask can say that rather than "try again".
        self._meeting_closed = False
        self._last_call_truncated = False
        self._last_call_timed_out = False
        self._last_call_parse_error = False
        # Set by a watch call that wants to be run again soon (an interview
        # question still being stated); consumed by the run loop.
        self._rearm: tuple[str, float] | None = None
        self._interview_rechecks = 0

    @property
    def _candidate_assist(self) -> bool:
        """Is the user the one being interviewed? Derived, never stored — a
        second copy of this could go stale against _assist_meeting_mode."""
        return self._assist_meeting_mode in CANDIDATE_ASSIST_MODES

    @property
    def _watch_calls(self) -> int:
        """Watch calls spent on this MEETING, across reconnects — see
        ``_meeting_watch_calls``. Card and ask counters are restored from their
        persisted rows in start(); watch calls leave no row, so they live in the
        process-local registry instead."""
        return _meeting_watch_calls.get(self.meeting_id, 0)

    # -- lifecycle -----------------------------------------------------------

    async def start(self, *, run_loop: bool = True) -> None:
        """Seed state from the DB (context, shown titles, transcript window) so
        a reconnect resumes instead of re-suggesting, then start the run task.

        ``run_loop=False`` seeds without creating that task: the standalone REST
        ask path drives ``_handle_ask`` directly and never enqueues an event, so
        a run task there would only block on an empty queue until ``aclose``
        cancelled it.
        """
        await self._load_context()

        await self._absorb_persisted_items()

        # A standalone session has no transcript segments by construction, so
        # this query is guaranteed to come back empty — and the REST ask path
        # pays for start() on every single request.
        if not self._standalone:
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
        if self._closed or not run_loop:
            return
        self._task = asyncio.create_task(
            self._run(), name=f"live_assist:{self.meeting_id}"
        )

    def _seed_ask_cooldown(self, rows: list[dict]) -> None:
        """Carry ASK_MIN_INTERVAL_S across watcher instances for this meeting.

        ``_last_ask_at`` is monotonic and starts at -inf, so a watcher rebuilt
        per request (the standalone REST path) or on reconnect would let the
        next ask through immediately. The newest persisted ask row dates the
        last one; translate its age into this process's monotonic clock.
        """
        stamps = [
            r["created_at"] for r in rows
            if r.get("source") == "ask" and r.get("created_at")
        ]
        if not stamps:
            return
        newest = max(stamps)
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - newest).total_seconds()
        if 0 <= age < ASK_MIN_INTERVAL_S:
            # max, not assignment: re-seeding mid-meeting must not rewind a
            # cooldown this watcher started for an ask that never persisted.
            self._last_ask_at = max(self._last_ask_at, time.monotonic() - age)

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

    def submit_ask(
        self,
        question: str,
        request_id: str | None,
        *,
        intent: str = "answer",
        parent_item_id: str | None = None,
        focus: str | None = None,
    ) -> None:
        """Sync enqueue of a typed mid-meeting question."""
        if self._closed:
            return
        try:
            self._events.put_nowait(
                ("ask", question, request_id, intent, parent_item_id, focus)
            )
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
                # The call may have asked to be re-run shortly (a question that
                # was still being stated). Re-arming here rather than waiting on
                # the generic accumulation gate is the whole point.
                if self._rearm is not None:
                    pending_trigger, pending_deadline = self._rearm
                    self._rearm = None
                continue

            if event[0] == "ask":
                _, question, request_id, intent, parent_item_id, focus = event
                await self._guarded(
                    self._handle_ask(
                        question,
                        request_id,
                        intent=intent,
                        parent_item_id=parent_item_id,
                        focus=focus,
                    )
                )
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
            "SELECT live_context, title, template, meeting_type, user_role, source, user_notes "
            "FROM meetings "
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
        self._standalone = row.get("source") == MEETING_SOURCE_MANUAL
        # Keep the TAIL, like _format_window does for transcripts. In a long
        # in-person meeting the notes the user is asking about are the newest
        # ones, which is exactly what head-truncation would throw away.
        self._manual_notes = (row.get("user_notes") or "")[-TRANSCRIPT_WINDOW_CHARS:]
        self._assist_meeting_mode = resolve_assist_meeting_mode(
            row.get("meeting_type"),
            row.get("user_role"),
            self._template,
        )
        # Assigned rather than passed to the constructor: a late-context retry
        # rebuilds the gate above only when keywords actually arrived, and must
        # not silently reset accumulation on the no-context path.
        self._gate.interview = self._candidate_assist

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
        self._rearm = None
        if not self._may_watch():
            return
        await self._maybe_retry_context()
        if not await self._budget_ok():
            return

        _meeting_watch_calls[self.meeting_id] = self._watch_calls + 1
        _evict_stale_watch_calls(self.meeting_id)
        self._last_call_at = time.monotonic()
        self._gate.reset_accumulation()

        window_segments = len(self._window)
        is_interview = self._candidate_assist
        # Two different prompts share this call site, so they get two feature
        # keys: bumping the interview prompt must not restamp general-meeting
        # cards with a version their prompt never had — the offline usefulness
        # eval segments on exactly that field.
        watch_feature = (
            "live_assist_interview_watch" if is_interview else "live_assist_watch"
        )
        prompt_template = (
            LIVE_ASSIST_INTERVIEW_WATCH_PROMPT
            if is_interview
            else LIVE_ASSIST_WATCH_PROMPT
        )
        prompt = prompt_template.format(
            meeting_title=self._meeting_title,
            template=self._template,
            role_guidance=(
                "The user explicitly selected Candidate. Do not infer or change "
                "their role; only technical questions spoken by 'them' are theirs "
                "to answer."
                if self._assist_meeting_mode == "interview_candidate"
                else "No explicit role was stored for this legacy interview. "
                "Infer who posed the question from the speaker tags and solve "
                "only questions spoken by 'them'."
            ),
            context_digest=self._format_context(),
            shown_titles=format_shown_titles(self._shown_titles),
            transcript_window=self._format_window(interview=is_interview),
        )

        started = time.monotonic()
        result = await self._json_ai_call(
            feature=watch_feature,
            model=settings.ANTHROPIC_MODEL_FAST,
            max_tokens=350,
            prompt=prompt,
        )
        latency_ms = int((time.monotonic() - started) * 1000)

        interview_question = (result or {}).get("interview_question")
        if is_interview and isinstance(interview_question, dict):
            if await self._dispatch_interview_triage(
                interview_question,
                trigger=trigger,
                triage_latency_ms=latency_ms,
            ):
                return
            # Not dispatched (not ours to solve, or not a technical question) —
            # fall through so a card returned alongside it is still considered.

        self._interview_rechecks = 0
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
            prompt_feature=watch_feature,
        )
        if item is None:
            return
        self._remember_title(item["title"])
        self._cards_shown += 1
        self._last_card_at = time.monotonic()
        await self._send_json({"type": "assist", "item": item})

    async def _dispatch_interview_triage(
        self,
        triaged: dict,
        *,
        trigger: str | None,
        triage_latency_ms: int,
    ) -> bool:
        """Act on an ``interview_question`` object from the triage call.

        Returns True when the object was handled (solved, or a recheck armed)
        and the caller should stop; False when it is not ours to act on and the
        card path should still run.
        """
        question = str(triaged.get("question") or "").strip()
        answer_type = str(triaged.get("answer_type") or "")
        # Speaker semantics are stable end-to-end: "me" is always the Felix
        # user and "them" is the other participant. Anything but an explicit
        # "them" attribution is not the candidate's prompt and fails closed.
        asked_by = str(triaged.get("asked_by") or "").strip().lower()
        if asked_by not in {"them", "they"}:
            logger.debug(
                "live assist interview question not addressed to the user "
                "(asked_by=%r) for meeting %s", asked_by, self.meeting_id,
            )
            return False
        # Deterministic backstop: the other participant has not spoken in the
        # window at all (system audio not shared, say), so nothing in it was put
        # to the user whatever the model reports.
        if not any(speaker == "them" for speaker, _text, _ts in self._window):
            return False
        if not question or answer_type not in {"coding", "system_design"}:
            return False
        # Second backstop, on the question's CONTENT rather than on any one
        # turn: whose words does it actually match? Speaker-based checks can't
        # get both failure modes right — the user asking a question triage then
        # misattributes, and the interviewer stating the problem inside a
        # coalesce window some earlier turn opened. The transcript settles it.
        if not self._question_came_from_them(question):
            logger.debug(
                "live assist interview question matches the user's own speech, "
                "not the other participant's, for meeting %s", self.meeting_id,
            )
            return False

        # "complete": false means the interviewer is still stating the problem.
        # A missing field keeps the pre-schema behaviour (treat as complete).
        complete = triaged.get("complete")
        if isinstance(complete, str):
            complete = complete.strip().lower() not in {"false", "no", "0", ""}
        if complete is False:
            self._arm_interview_recheck()
            return True

        self._interview_rechecks = 0
        await self._solve_interview(
            question=question,
            answer_type_hint=answer_type,
            source="proactive",
            request_id=None,
            trigger_type=trigger,
            triage_latency_ms=triage_latency_ms,
        )
        return True

    def _question_came_from_them(self, question: str) -> bool:
        """Is the triaged question better covered by the other participant's
        words than by the user's own?

        Triage paraphrases, so this compares content-word coverage rather than
        looking for the sentence verbatim. Ties go to "them": ``asked_by`` has
        already said so, and this is a backstop against misattribution, not a
        second opinion. Too thin a question to judge defers to ``asked_by``.
        """
        tokens = _question_tokens(question)
        if len(tokens) < QUESTION_MIN_TOKENS:
            return True

        def coverage(speaker: str) -> float:
            said = _question_tokens(
                " ".join(t for s, t, _ts in self._window if s == speaker)
            )
            return len(tokens & said) / len(tokens)

        return coverage("them") >= coverage("me")

    def _arm_interview_recheck(self) -> None:
        """Ask the run loop to evaluate again shortly.

        Without this, the continuation of a half-stated question is ordinary
        transcript: the chars that piled up during coalescing were just cleared
        by reset_accumulation(), so the promised reconsideration would wait on
        the 400-char / 45-second gate — minutes after the candidate had to
        speak. Bounded by MAX_INTERVIEW_RECHECKS so a question that never
        completes stops costing triage calls.
        """
        if self._interview_rechecks >= MAX_INTERVIEW_RECHECKS:
            self._interview_rechecks = 0
            return
        self._interview_rechecks += 1
        self._rearm = (
            "interview_recheck",
            max(
                time.monotonic() + INTERVIEW_RECHECK_S,
                self._last_call_at + MIN_CALL_INTERVAL_S,
            ),
        )

    def _ask_failure_message(self) -> str:
        """Retrying a truncated or timed-out answer just repeats it — say so."""
        if self._meeting_closed:
            # Retrying is hopeless: the meeting is summarized and closed to writes.
            return "This meeting has ended."
        if self._last_call_truncated:
            return "That answer ran too long to show — ask for one part of it."
        if self._last_call_timed_out:
            return "That took too long to answer — ask for a smaller piece of it."
        return ASK_UNAVAILABLE_MESSAGE

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

    async def _handle_ask(
        self,
        question: str,
        request_id: str | None,
        *,
        intent: str = "answer",
        parent_item_id: str | None = None,
        focus: str | None = None,
    ) -> None:
        """Transport-neutral entry point for one ask, serialized per meeting.

        Every transport lands here: the capture WebSocket via the run loop, and
        REST (a manual session's ask box, the phone viewer on a captured
        meeting) via ``answer_typed_question``. The meeting's local + database
        ask slot stops a phone ask and a laptop ask — even on different Cloud
        Run instances — from both reaching the model. Whoever gets there second
        is refused rather than queued.
        """
        async with _ask_slot(self.meeting_id) as slot_status:
            if slot_status != "acquired":
                if slot_status == "busy":
                    # Name what is actually happening. With two devices on one
                    # meeting this is nearly always the other one's question
                    # still running, and waiting is the fix.
                    message = (
                        "Felix is already answering another question for this "
                        "meeting — try again once it finishes."
                    )
                else:
                    # Includes "unavailable" and any future/invalid status: an
                    # ask slot must be explicitly acquired before model work.
                    message = ASK_UNAVAILABLE_MESSAGE
                await self._send_json({
                    "type": "assist_error",
                    "request_id": request_id,
                    "message": message,
                })
                return
            # A capture watcher may have lived for hours while another device
            # or instance persisted asks. The shared lock prevents a concurrent
            # writer; absorb their rows now, before the cap, the cooldown or
            # the conversation this ask is answered against is consulted.
            await self._absorb_persisted_items()
            await self._run_ask(
                question,
                request_id,
                intent=intent,
                parent_item_id=parent_item_id,
                focus=focus,
            )

    async def _absorb_persisted_items(self) -> None:
        """Fold every persisted assist item for this meeting into local state.

        Run at seed and again before each ask, because a meeting's assist items
        no longer have one writer: the phone asks over REST — building its own
        watcher, on any instance — while the laptop's capture watcher can live
        for hours. Refreshing only the ask counters would leave that watcher
        resolving "tell me more about that" against a conversation missing the
        phone's turn, and re-surfacing proactively what the phone just answered.

        Strictly additive, never a rebuild. The proactive pipeline runs on this
        watcher's own task and claims a solve before its card persists;
        recomputing from persisted rows would drop that in-flight claim and let
        a duplicate solve through. Counters take the max for the same reason: an
        ask that spent a model call but persisted nothing (timeout, unparseable
        answer, a persist refused because the meeting closed) already counted
        against MAX_ASKS and has to keep counting — otherwise a systematically
        failing ask is unbounded on the WebSocket transport, which has no HTTP
        rate limit of its own.

        Rows absorbed here append to the conversation in ``created_at`` order,
        after anything this watcher persisted itself. That is a coarser ordering
        than a merge would give, and deliberately so: the conversation is
        context for the model, not a transcript the user reads.
        """
        rows = await db.query(
            "SELECT id, title, body, source, question, created_at "
            "FROM meeting_assist_items "
            "WHERE user_id = $1 AND meeting_id = $2 ORDER BY created_at",
            self.user_id, self.meeting_id,
        )
        for row in rows:
            if str(row.get("id") or "") in self._seen_item_ids:
                continue
            self._remember_title(row.get("title") or "")
            self._remember_conversation_item(row)
            if row.get("source") == "proactive":
                self._remember_solved(row.get("question") or "")
        # Per-source counters: a meeting full of ask answers must not silence
        # the proactive pipeline, and reconnecting must not reset the ask cap.
        self._cards_shown = max(
            self._cards_shown,
            sum(1 for r in rows if r.get("source") == "proactive"),
        )
        self._asks = max(
            self._asks,
            sum(1 for r in rows if r.get("source") == "ask"),
        )
        self._seed_ask_cooldown(rows)

    async def _run_ask(
        self,
        question: str,
        request_id: str | None,
        *,
        intent: str = "answer",
        parent_item_id: str | None = None,
        focus: str | None = None,
    ) -> None:
        async def fail(message: str) -> None:
            await self._send_json(
                {"type": "assist_error", "request_id": request_id, "message": message}
            )

        # Replay a request_id that already has an answer, BEFORE any limit is
        # consulted: the first attempt may have persisted and started the
        # cooldown a retry would now be rejected by. No client reuses an id
        # today — both mint a fresh one per attempt — so this is insurance for
        # a client that starts retrying, not a live path. Note the scope: this
        # runs inside the ask slot, so it cannot answer a retry that arrives
        # while the first attempt is still in the model; that one is refused as
        # a concurrent ask, which is the correct answer for it anyway.
        if request_id:
            prior = await db.query_one(
                "SELECT * FROM meeting_assist_items "
                "WHERE user_id = $1 AND meeting_id = $2 AND request_id = $3 "
                "ORDER BY created_at DESC LIMIT 1",
                self.user_id, self.meeting_id, request_id,
            )
            if prior:
                await self._send_json({"type": "assist", "item": item_to_wire(prior)})
                return

        question = (question or "").strip()
        if not question or len(question) > ASK_MAX_CHARS:
            await fail("Ask a question up to 6000 characters.")
            return
        if intent not in {"answer", "expand"}:
            await fail("Unsupported live-assist request.")
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

        # Validate an expansion BEFORE charging the ask budget below: a request
        # that never reaches the model must not consume one of MAX_ASKS or put
        # the user into the cooldown.
        parent: dict | None = None
        metadata: dict = {}
        if intent == "expand":
            # An Interviewer-mode meeting IS an interview, so the old wording
            # read as a contradiction to the only user who can hit this. What
            # gates expansion is being the one answering, not the template.
            if not self._candidate_assist:
                await fail(
                    "Answer expansion is available when you're the candidate "
                    "in an interview."
                )
                return
            parent = await db.query_one(
                "SELECT * FROM meeting_assist_items "
                "WHERE id = $1 AND meeting_id = $2 AND user_id = $3",
                parent_item_id,
                self.meeting_id,
                self.user_id,
            )
            metadata = (parent or {}).get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            allowed = metadata.get("expansion_options") or []
            if not parent or focus not in allowed:
                await fail("That answer cannot be expanded in this way.")
                return

        self._asks += 1
        self._last_ask_at = now
        await self._maybe_retry_context()

        if parent is not None:
            prompt = LIVE_ASSIST_INTERVIEW_EXPAND_PROMPT.format(
                transcript_window=self._format_window(interview=True),
                question=parent.get("question") or question,
                parent_body=parent.get("body") or "",
                focus=focus,
                answer_type=metadata.get("answer_type") or "general",
            )
            await self._finish_interview_answer(
                prompt=prompt,
                question=parent.get("question") or question,
                source="ask",
                request_id=request_id,
                trigger_type=None,
                parent_item_id=str(parent.get("id")),
                depth="expanded",
                prompt_feature="live_assist_expand",
                max_tokens=2200,
                fallback_answer_type=metadata.get("answer_type") or "general",
                extra_metadata={"focus": focus},
                fail=fail,
            )
            return

        if self._candidate_assist:
            await self._solve_interview(
                question=question,
                answer_type_hint=None,
                source="ask",
                request_id=request_id,
                trigger_type=None,
                fail=fail,
            )
            return

        prompt_template = (
            LIVE_ASSIST_STANDALONE_ASK_PROMPT
            if self._standalone
            else LIVE_ASSIST_ASK_PROMPT
        )
        prompt = prompt_template.format(
            meeting_title=self._meeting_title,
            template=self._template,
            context_digest=self._format_context(),
            transcript_window=self._format_window(),
            conversation_history=self._format_conversation(),
            question=question,
        )
        started = time.monotonic()
        result = await self._json_ai_answer_call(
            feature=(
                "live_assist_standalone_ask"
                if self._standalone
                else "live_assist_ask"
            ),
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=400,
            prompt=prompt,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if not result or not str(result.get("body") or "").strip():
            await fail(self._ask_failure_message())
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
            prompt_feature=(
                "live_assist_standalone_ask"
                if self._standalone
                else "live_assist_ask"
            ),
        )
        if item is None:
            await fail(self._ask_failure_message())
            return
        self._remember_title(item["title"])
        await self._send_json({"type": "assist", "item": item})

    async def _solve_interview(
        self,
        *,
        question: str,
        answer_type_hint: str | None,
        source: str,
        request_id: str | None,
        trigger_type: str | None,
        triage_latency_ms: int | None = None,
        fail=None,
    ) -> None:
        # The interviewer keeps discussing a problem long after stating it, so
        # the gate keeps firing and triage keeps re-emitting the same question,
        # reworded each time. Dedupe here, before the solve — the post-call
        # title dedupe would drop the answer only after paying for it, and only
        # if the two answers happened to be titled alike.
        claim: tuple[str, frozenset[str]] | None = None
        if source == "proactive":
            if self._already_solved(question):
                return
            if time.monotonic() - self._last_card_at < MIN_CARD_INTERVAL_S:
                return
            # Uninvited, and far more expensive than the watch call that
            # proposed it: ~1400 smart-model tokens, logged as interactive
            # usage. BUDGET_RECHECK_EVERY was calibrated for cheap Haiku watch
            # calls and would let up to 19 of these through after the user goes
            # over quota, so this one is always checked for real. Before the
            # dedupe claim below: a question refused for budget is unanswered,
            # not answered.
            if not await self._budget_ok(force=True):
                return
            claim = self._remember_solved(question)
        prompt = LIVE_ASSIST_INTERVIEW_ANSWER_PROMPT.format(
            meeting_title=self._meeting_title,
            context_digest=self._format_context(),
            transcript_window=self._format_window(interview=True),
            conversation_history=self._format_conversation(),
            question=question,
        )
        outcome = await self._finish_interview_answer(
            prompt=prompt,
            question=question,
            source=source,
            request_id=request_id,
            trigger_type=trigger_type,
            parent_item_id=None,
            depth="concise",
            prompt_feature="live_assist_interview",
            # The concise answer now carries a real code block, not pseudocode.
            max_tokens=1400,
            fallback_answer_type=answer_type_hint or "general",
            extra_metadata={"triage_latency_ms": triage_latency_ms}
            if triage_latency_ms is not None
            else {},
            fail=fail,
        )
        # A transient failure is not a decision — let a later gate hit retry it.
        if claim is not None and outcome == "error":
            self._forget_solved(claim)

    async def _finish_interview_answer(
        self,
        *,
        prompt: str,
        question: str,
        source: str,
        request_id: str | None,
        trigger_type: str | None,
        parent_item_id: str | None,
        depth: str,
        prompt_feature: str,
        max_tokens: int,
        fallback_answer_type: str,
        extra_metadata: dict,
        fail=None,
    ) -> str:
        """Solve → accept → persist → emit.

        Returns "sent", "dropped" (the acceptance gate decided against it) or
        "error" (transient — the same question may be worth retrying).
        """
        started = time.monotonic()
        if source == "ask":
            result = await self._json_ai_answer_call(
                feature=prompt_feature,
                model=settings.ANTHROPIC_MODEL_SMART,
                max_tokens=max_tokens,
                prompt=prompt,
            )
        else:
            result = await self._json_ai_call(
                feature=prompt_feature,
                model=settings.ANTHROPIC_MODEL_SMART,
                max_tokens=max_tokens,
                prompt=prompt,
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        if not result or not str(result.get("body") or "").strip():
            if fail is not None:
                await fail(self._ask_failure_message())
            return "error"

        title = str(result.get("title") or question[:80]).strip()
        # A proactive interview answer is an uninvited card like any other, so
        # it goes through the same acceptance gate: the model's self-assessed
        # score is enforced here, not taken on trust, and it is the score the
        # offline usefulness eval reads back. An ask was invited — it is never
        # score-gated, and carries no score.
        usefulness_score = None
        if source == "proactive":
            accepted, reason = self._accept({
                "kind": "answer",
                "title": title,
                "body": str(result["body"]).strip(),
                "usefulness_score": result.get("usefulness_score"),
            })
            if not accepted:
                logger.debug(
                    "live assist interview answer dropped (%s) for meeting %s",
                    reason, self.meeting_id,
                )
                return "dropped"
            usefulness_score = float(result.get("usefulness_score") or 0.0)

        answer_type = str(result.get("answer_type") or fallback_answer_type)
        if answer_type not in {"coding", "system_design", "behavioral", "general"}:
            answer_type = fallback_answer_type
        allowed_by_type = {
            "coding": {"code", "walkthrough", "edge_cases"},
            "system_design": {"architecture", "scale", "tradeoffs"},
            "behavioral": set(),
            "general": set(),
        }
        expansion_options = [
            str(option)
            for option in (result.get("expansion_options") or [])
            if str(option) in allowed_by_type.get(answer_type, set())
        ]
        metadata = {
            "answer_type": answer_type,
            "depth": depth,
            "parent_item_id": parent_item_id,
            "expansion_options": expansion_options if depth == "concise" else [],
            "latency_ms": latency_ms,
            **extra_metadata,
        }
        item = await self._persist_item(
            kind="answer",
            source=source,
            question=question,
            title=title,
            body=str(result["body"]).strip(),
            transcript_ts=self._window[-1][2] if self._window else None,
            usefulness_score=usefulness_score,
            trigger_type=trigger_type,
            request_id=request_id,
            model=settings.ANTHROPIC_MODEL_SMART,
            metadata=metadata,
            prompt_feature=prompt_feature,
        )
        if item is None:
            if fail is not None:
                await fail(self._ask_failure_message())
            return "error"
        self._remember_title(item["title"])
        if source == "proactive":
            self._cards_shown += 1
            self._last_card_at = time.monotonic()
        await self._send_json({"type": "assist", "item": item})
        return "sent"

    # -- shared plumbing ------------------------------------------------------

    async def _budget_ok(self, *, force: bool = False) -> bool:
        """Re-check the monthly budget every BUDGET_RECHECK_EVERY watch calls.

        ``force`` bypasses the cadence for spend the cadence was never sized
        for (a proactive smart-model solve).
        """
        if not force and self._watch_calls % BUDGET_RECHECK_EVERY != 0:
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
        Any failure (API error, unparseable output, CALL_TIMEOUT_S elapsed)
        returns None — never a broken card. Sets _last_call_truncated /
        _last_call_timed_out so a caller can tell "ran out of room mid-answer"
        and "took too long" apart from "returned junk"; the three need different
        messages, because retrying a truncation just truncates again and
        retrying a timeout on the same prompt just times out again.

        wait_for is the authority on the deadline, not the per-request timeout:
        the client's max_retries would otherwise multiply that timeout by three.
        """
        started = time.monotonic()
        response = None
        success = True
        parse_error = False
        error_message: str | None = None
        self._last_call_truncated = False
        self._last_call_timed_out = False
        self._last_call_parse_error = False
        try:
            response = await asyncio.wait_for(
                _ai.client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    **_ai.thinking_kwarg(model),
                    system=LIVE_ASSIST_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                    timeout=CALL_TIMEOUT_S,
                ),
                CALL_TIMEOUT_S,
            )
            self._last_call_truncated = (
                getattr(response, "stop_reason", None) == "max_tokens"
            )
            try:
                result = json.loads(
                    _ai._strip_markdown_fences(response.content[0].text)
                )
                if isinstance(result, dict):
                    return result
                parse_error = True
                self._last_call_parse_error = True
                error_message = f"expected JSON object, got {type(result).__name__}"
                return None
            except json.JSONDecodeError as e:
                parse_error = True
                self._last_call_parse_error = True
                error_message = (
                    f"truncated at max_tokens={max_tokens}"
                    if self._last_call_truncated
                    else f"JSONDecodeError: {e}"
                )
                return None
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            success = False
            self._last_call_timed_out = True
            error_message = f"timed out after {CALL_TIMEOUT_S:.0f}s"
            logger.warning(
                "live assist %s call timed out after %.0fs for meeting %s",
                feature, CALL_TIMEOUT_S, self.meeting_id,
            )
            return None
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

    async def _json_ai_answer_call(
        self, *, feature: str, model: str, max_tokens: int, prompt: str
    ) -> dict | None:
        """Run an interactive answer, retrying one malformed model response.

        The Anthropic client already retries transport/provider failures. This
        only covers a model response that arrived but was not valid answer JSON
        (or omitted its body), which is the case where asking the exact same
        question again often works. Timeouts and truncations are deliberately
        not retried: they would repeat a long wait or an answer that cannot fit.
        """
        result = await self._json_ai_call(
            feature=feature,
            model=model,
            max_tokens=max_tokens,
            prompt=prompt,
        )
        if result and str(result.get("body") or "").strip():
            return result
        if self._last_call_timed_out or self._last_call_truncated:
            return result
        if result is None and not self._last_call_parse_error:
            return None
        return await self._json_ai_call(
            feature=feature,
            model=model,
            max_tokens=max_tokens,
            prompt=prompt,
        )

    async def _persist_item(self, **fields) -> dict | None:
        """Insert a meeting_assist_items row; return the wire-format item dict.

        Refuses to write once the meeting has left ``recording``. The model call
        that produced this item can run for up to CALL_TIMEOUT_S, which is ample
        time for the capturing device to press Stop: the summary is built at that
        moment, so a card landing afterwards would appear in a finished meeting
        that was never summarized with it — and, for an ask, alongside a client
        that has already navigated to the summary.
        """
        # Built inside the guard below, not before it: every caller passes
        # these by keyword, so a missing one is a KeyError — and outside the
        # try it would escape _persist_item entirely, past the callers' "could
        # not answer" handling and (on the proactive path, which has none) out
        # through _guarded as a watcher error. Degraded card, not dead watcher.
        try:
            data = {
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
                    fields.get("prompt_feature")
                    or ("live_assist_watch" if fields["source"] == "proactive" else "live_assist_ask"),
                    "v1",
                ),
                "request_id":       fields["request_id"],
                "metadata":         fields["metadata"] or {},
                "model":            fields["model"],
            }
            # The row lock makes this serialize with /end's UPDATE. If /end
            # wins, PostgreSQL re-checks the status predicate after waiting and
            # inserts nothing; if this wins, the answer commits before /end.
            row = await db.query_one(
                "WITH open_meeting AS ("
                "SELECT id FROM meetings "
                "WHERE id = $1 AND user_id = $2 AND status = 'recording' "
                "FOR UPDATE"
                ") "
                "INSERT INTO meeting_assist_items ("
                "user_id, meeting_id, kind, source, question, title, body, "
                "transcript_ts, usefulness_score, trigger_type, prompt_version, "
                "request_id, metadata, model"
                ") "
                "SELECT $2, open_meeting.id, $3, $4, $5, $6, $7, $8, $9, "
                "$10, $11, $12, $13, $14 FROM open_meeting RETURNING *",
                self.meeting_id,
                data["user_id"],
                data["kind"],
                data["source"],
                data["question"],
                data["title"],
                data["body"],
                data["transcript_ts"],
                data["usefulness_score"],
                data["trigger_type"],
                data["prompt_version"],
                data["request_id"],
                data["metadata"],
                data["model"],
            )
        except Exception:
            logger.warning(
                "failed to persist assist item for meeting %s",
                self.meeting_id, exc_info=True,
            )
            return None
        if not row:
            self._meeting_closed = True
            logger.info(
                "live assist item dropped: meeting %s is no longer recording",
                self.meeting_id,
            )
            return None
        self._remember_conversation_item(row)
        return item_to_wire(row)

    def _remember_title(self, title: str) -> None:
        title = (title or "").strip()
        if not title:
            return
        self._shown_titles.append(title)
        self._shown_normalized.add(_normalize_title(title))

    def _remember_conversation_item(self, row: dict) -> None:
        """Keep one persisted assist item as meeting-scoped conversation.

        Ask items preserve both sides of the exchange. Proactive cards preserve
        what Felix surfaced so a later "expand on that" can resolve the card in
        front of the user too. Rows without a body are legacy/incomplete and
        cannot contribute useful context.
        """
        # Recorded before the body guard: an id that is skipped here must
        # still be skipped by the next _absorb_persisted_items, or a bodyless
        # row would re-run _remember_title/_remember_solved on every ask.
        item_id = str(row.get("id") or "")
        if item_id:
            self._seen_item_ids.add(item_id)
        body = str(row.get("body") or "").strip()
        if not body:
            return
        source = str(row.get("source") or "")
        question = str(row.get("question") or "").strip()
        title = str(row.get("title") or "").strip()
        self._conversation.append((source, question, title, body))

    # -- proactive question dedupe -------------------------------------------

    def _already_solved(self, question: str) -> bool:
        """Has this meeting already paid for an answer to this problem?

        Exact match first (cheap), then content-word overlap for the same
        problem restated — see the fingerprint helpers above.
        """
        normalized = _normalize_title(question)
        if normalized and normalized in self._solved_normalized:
            return True
        tokens = _question_tokens(question)
        # One content word is too thin a fingerprint to refuse a solve over.
        if len(tokens) < QUESTION_MIN_TOKENS:
            return False
        return any(
            _token_overlap(tokens, seen) >= QUESTION_OVERLAP_THRESHOLD
            for seen in self._solved_tokens
        )

    def _remember_solved(self, question: str) -> tuple[str, frozenset[str]] | None:
        """Claim a question so a re-statement never pays for a second solve.
        Returns the claim (or None for an empty question) so a transient failure
        can hand it back."""
        normalized = _normalize_title(question)
        if not normalized:
            return None
        tokens = _question_tokens(question)
        self._solved_normalized.add(normalized)
        self._solved_tokens.append(tokens)
        return normalized, tokens

    def _forget_solved(self, claim: tuple[str, frozenset[str]]) -> None:
        normalized, tokens = claim
        self._solved_normalized.discard(normalized)
        try:
            self._solved_tokens.remove(tokens)
        except ValueError:  # already released
            pass

    def _format_context(self) -> str:
        context = format_digest((self._context or {}).get("digest") or {})
        if self._standalone and self._manual_notes:
            context += f"\n\nCurrent manual notes:\n{self._manual_notes}"
        return context

    def _format_conversation(self) -> str:
        """Render every assistant exchange retained for this meeting.

        The meeting-level MAX_ASKS cap bounds direct exchanges; proactive cards
        have their own cap. Keeping all of them avoids turning references to an
        early topic into guesswork later in the same meeting.
        """
        if not self._conversation:
            return "(none yet)"
        turns: list[str] = []
        for source, question, title, body in self._conversation:
            if source == "ask":
                user_line = (
                    f"User: {question}"
                    if question
                    else "User: (follow-up request)"
                )
                turns.append(f"{user_line}\nFelix: {body}")
            else:
                label = f"Felix surfaced [{title}]" if title else "Felix surfaced"
                turns.append(f"{label}: {body}")
        return "\n\n".join(turns)

    def _format_window(self, *, interview: bool = False) -> str:
        lines: list[str] = []
        total = 0
        for speaker, text, _ts in reversed(self._window):
            line = f"{speaker}: {text}"
            total += len(line) + 1
            if total > (INTERVIEW_WINDOW_CHARS if interview else TRANSCRIPT_WINDOW_CHARS):
                break
            lines.append(line)
        lines.reverse()
        return "\n".join(lines) or "(no transcript yet)"


# ---------------------------------------------------------------------------
# Registry — local takeover + cross-instance ownership
# ---------------------------------------------------------------------------

_watchers: dict[str, LiveAssistWatcher] = {}

# MAX_WATCH_CALLS is a cost cap on the MEETING, not on one socket. Cards and
# asks are restored from their rows on reconnect; watch calls persist nothing,
# so without this a flaky connection resets the counter on every reconnect and
# the cap stops bounding a long or unreliable call at all. Process-local, like
# _watchers — the advisory lock keeps one meeting on one instance, and the
# monthly budget is the cross-instance bound.
_meeting_watch_calls: dict[str, int] = {}

# forget_meeting() clears an entry when a meeting ends; this bounds the leak
# from meetings that never get there (browser closed, process outlives them).
# Eviction only loses the cap for that meeting — it can never block a watcher.
MAX_TRACKED_MEETINGS = 500


def _evict_stale_watch_calls(keep: str) -> None:
    if len(_meeting_watch_calls) <= MAX_TRACKED_MEETINGS:
        return
    # dicts iterate in insertion order — drop the meetings seen longest ago.
    excess = len(_meeting_watch_calls) - MAX_TRACKED_MEETINGS
    for stale in [m for m in _meeting_watch_calls if m != keep][:excess]:
        _meeting_watch_calls.pop(stale, None)


def forget_meeting(meeting_id: str) -> None:
    """Drop a finished meeting's watch-call count. Safe to call for meetings
    that never had a watcher (the feature may be off for this user)."""
    _meeting_watch_calls.pop(meeting_id, None)


class _AskSlot:
    """One meeting's ask mutex, plus the count of holders/waiters on it."""

    __slots__ = ("lock", "users")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.users = 0


# One in-flight ask per meeting within this process. Without this a REST ask
# (phone) and a WebSocket ask (laptop) run on different watcher instances that
# each read `_asks` from the persisted rows before either writes, so both pass
# the MAX_ASKS check and both spend a Sonnet call — and the route's 12/minute
# allowance is not itself a concurrency bound.
#
# The PostgreSQL advisory lock below supplies the corresponding cross-instance
# exclusion. Both are required because the shared lock connection treats a
# repeated acquire in one process as successful.
#
# Entries are dropped once nobody holds or waits on them, so this cannot grow
# unbounded.
_ask_slots: dict[str, _AskSlot] = {}


@asynccontextmanager
async def _ask_slot(meeting_id: str):
    """Yield ``acquired``, ``busy``, or ``unavailable`` for an ask attempt.

    Refusing beats queueing: the caller's transport has a deadline
    (ASK_TIMEOUT_MS in the browser), the answer would be generated against a
    conversation that has since moved on, and the user has already been told
    "one question at a time" by the cooldown that guards the same contract.
    """
    slot = _ask_slots.get(meeting_id)
    if slot is None:
        slot = _AskSlot()
        _ask_slots[meeting_id] = slot
    # Incremented before the first await, so a concurrent caller always sees a
    # nonzero count and never removes the slot out from under this one.
    slot.users += 1
    try:
        # No await between the test and the acquire, so this cannot interleave.
        if slot.lock.locked():
            yield "busy"
        else:
            async with slot.lock:
                lock_key = f"felix_live_assist_ask:{meeting_id}"
                try:
                    owns_shared_lock = await db.try_advisory_lock(lock_key)
                except Exception:
                    logger.warning(
                        "live assist ask lock failed for meeting %s",
                        meeting_id,
                        exc_info=True,
                    )
                    yield "unavailable"
                    return
                if not owns_shared_lock:
                    yield "busy"
                    return
                try:
                    yield "acquired"
                finally:
                    # Shielded: the caller is a request handler FastAPI
                    # cancels when the browser aborts (its ASK_TIMEOUT_MS, or a
                    # phone navigating away) while the model call is still
                    # running. A bare `await` here would take the
                    # CancelledError instead of issuing pg_advisory_unlock —
                    # and db.py holds every advisory lock on one process-wide
                    # session, so the lock would outlive the request and every
                    # OTHER instance would be refused asks for this meeting
                    # until the process restarted. Invisible on this instance,
                    # where a same-session re-acquire succeeds.
                    try:
                        await asyncio.shield(db.advisory_unlock(lock_key))
                    except asyncio.CancelledError:
                        # The shielded unlock still runs to completion; let the
                        # cancellation the caller asked for propagate.
                        raise
                    except Exception:
                        logger.warning(
                            "live assist ask unlock failed for meeting %s",
                            meeting_id,
                            exc_info=True,
                        )
    finally:
        slot.users -= 1
        if slot.users == 0 and _ask_slots.get(meeting_id) is slot:
            del _ask_slots[meeting_id]


async def answer_typed_question(
    *,
    user_id: str,
    meeting_id: str,
    question: str,
    request_id: str | None,
    intent: str = "answer",
    parent_item_id: str | None = None,
    focus: str | None = None,
    user_email: str | None = None,
) -> dict:
    """Run one typed ask over REST, without opening an audio/WebSocket session.

    Serves every socket-less client: a manual assistant session's ask box, and
    the phone viewer asking about a meeting the laptop is capturing. A watcher
    is built and driven directly (``start(run_loop=False)``) so the ask keeps
    the same limits, model, context, persistence, interview-answer format and
    expansion behaviour as one typed on the capture client — transport decides
    nothing. ``_handle_ask`` serializes it against any concurrent ask for the
    same meeting, whichever transport that one arrived on.

    Always resolves to an ``assist`` or ``assist_error`` payload. The WS path
    runs ``_handle_ask`` inside ``_guarded``; without an equivalent here an
    unexpected failure (a transient DB error in the persist, say) would escape
    as a bare 500 with nothing the client could correlate to its request.
    """
    emitted: list[dict] = []

    async def collect(payload: dict) -> None:
        emitted.append(payload)

    watcher = LiveAssistWatcher(
        user_id=user_id,
        meeting_id=meeting_id,
        send_json=collect,
        user_email=user_email,
    )
    try:
        await watcher.start(run_loop=False)
        await watcher._handle_ask(
            question,
            request_id,
            intent=intent,
            parent_item_id=parent_item_id,
            focus=focus,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("typed ask failed for meeting %s", meeting_id, exc_info=True)
    finally:
        await watcher.aclose()

    for payload in reversed(emitted):
        if payload.get("type") in {"assist", "assist_error"}:
            return payload
    return {
        "type": "assist_error",
        "request_id": request_id,
        "message": ASK_UNAVAILABLE_MESSAGE,
    }


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
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
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
        "answer_type":   metadata.get("answer_type"),
        "depth":         metadata.get("depth"),
        "parent_item_id": metadata.get("parent_item_id"),
        "expansion_options": metadata.get("expansion_options") or [],
        "created_at":    created.isoformat() if hasattr(created, "isoformat") else created,
    }
