"""
Tool definitions for the chat agent loop in _general_question.

Three tools, all scoped to a single user_id via the dispatcher closure:

  - search_emails(query, limit)        : ranked local search + live Gmail fallback
  - get_email(email_id)                : full row/body from local cache or Gmail
  - create_calendar_event(...)         : insert event into the user's primary calendar

The local email tables are the fast path. Gmail live search is the fallback when
the cache is incomplete, stale, or does not contain archived/older messages.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Awaitable, Callable

from app import db
from app.middleware.auth import get_google_credentials
from app.services.calendar_service import CalendarService
from app.services.gmail_service import GmailService

logger = logging.getLogger(__name__)


# A user turn must contain one of these substrings (after lowercasing) for
# create_calendar_event to be allowed to actually write to Google Calendar.
# Covers the natural ways people confirm a calendar proposal in chat — short
# affirmations ("yes"), explicit instructions ("book it", "add both"), and the
# common "add (it|both|all|them|that|to|the event) (to (my )?calendar)?" shape.
_CONFIRMATION_PATTERNS = re.compile(
    r"\b(yes|yep|yeah|yup|sure|ok(ay)?|please do|do it|go ahead|book (it|both|them|all)|"
    r"confirm|create (it|both|them|all|the event|the events)|"
    r"schedule (it|both|them|all|the event|the events)|"
    r"add (it|both|them|all|that|the event|the events|to (my )?calendar)|"
    r"sounds good|that works|perfect)\b",
    re.IGNORECASE,
)
# Negation guard so "no, don't book it" or "not sure" doesn't pass the
# confirmation test even when a positive token (e.g. "sure") appears in the
# same sentence. Conservative — when in doubt, refuse to write.
_NEGATION_PATTERNS = re.compile(
    r"\b(no|nope|nah|not|don'?t|do not|cancel|never mind|nevermind|stop|wait|"
    r"hold on|maybe|unsure|change|different)\b",
    re.IGNORECASE,
)

# Pending proposals expire after this so a stale row from yesterday can't be
# accidentally booked.
_PROPOSAL_TTL_SECONDS = 60 * 60  # 1 hour
_SEARCH_TERM_RE = re.compile(r"[a-z0-9@._+-]{2,}", re.IGNORECASE)

# Per-table free-text "haystacks". These MUST stay byte-for-byte identical to the
# expressions indexed in infra/migrations/014_email_search_indexes.sql — the GIN
# pg_trgm index is only used when the query's `LIKE` operand matches the indexed
# expression exactly. Searching with positive `haystack LIKE $pat` predicates (one
# per term, AND-ed) is what lets the planner pick a bitmap index scan.
#
# Built from IMMUTABLE pieces (COALESCE/|| for scalars, the felix_array_text
# wrapper for text[] recipients) because index expressions must be IMMUTABLE and
# CONCAT_WS / ARRAY_TO_STRING are only STABLE.
_INBOUND_HAYSTACK = (
    "LOWER(COALESCE(from_name, '') || ' ' || COALESCE(from_email, '') || ' ' || "
    "COALESCE(to_email, '') || ' ' || COALESCE(subject, '') || ' ' || "
    "COALESCE(snippet, '') || ' ' || COALESCE(body, ''))"
)
_SENT_HAYSTACK = (
    "LOWER(COALESCE(from_email, '') || ' ' || felix_array_text(to_emails) || ' ' || "
    "felix_array_text(to_names) || ' ' || COALESCE(subject, '') || ' ' || "
    "COALESCE(snippet, '') || ' ' || COALESCE(body, ''))"
)


def _like_escape(term: str) -> str:
    """Escape LIKE metacharacters so a search term matches literally.

    Postgres' default LIKE escape character is backslash, so escaped patterns
    work without an explicit ESCAPE clause.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Anthropic tool schemas — exposed to the model.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_emails",
        "description": (
            "Search the user's emails by free-text query. Searches Felix's local inbound "
            "and sent-mail cache using tokenized matching, then falls back to live Gmail "
            "search when local results are weak or empty. Use this whenever the user asks "
            "about a specific email, sender, or topic. Results include source and mailbox; "
            "pass the returned id to get_email for full text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms — sender name, subject keywords, topic words.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (1-10). Default 5.",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_email",
        "description": (
            "Fetch the full body and metadata of a single email by its id. "
            "Use this after search_emails when you need the full text to answer a question "
            "or extract details for a calendar event."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {
                    "type": "string",
                    "description": "The email id returned by search_emails.",
                },
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "propose_calendar_event",
        "description": (
            "Prepare a calendar event as an internal pending offer. This does NOT write to "
            "the calendar and should not be described as staged, queued, or pending. After "
            "calling this, describe the event details and ask if the user wants it added. "
            "If they confirm in a follow-up turn, call create_calendar_event (no arguments)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title / summary."},
                "start_iso": {
                    "type": "string",
                    "description": "Start time as ISO 8601 local datetime, e.g. '2026-05-12T18:00:00'. No timezone suffix.",
                },
                "end_iso": {
                    "type": "string",
                    "description": "End time as ISO 8601 local datetime, e.g. '2026-05-12T19:00:00'. No timezone suffix.",
                },
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone name, e.g. 'Europe/London'. Use the user's timezone given in the system context.",
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Attendee email addresses (optional).",
                    "default": [],
                },
                "description": {
                    "type": "string",
                    "description": "Optional event description / notes.",
                    "default": "",
                },
            },
            "required": ["title", "start_iso", "end_iso", "timezone"],
        },
    },
    {
        "name": "create_calendar_event",
        "description": (
            "Create proposed event(s) on the user's primary Google Calendar. With no arguments, "
            "creates ALL pending proposals for the user — use this when the user confirms "
            "everything you proposed (e.g. 'yes', 'add both', 'book them'). Pass `proposal_id` "
            "to create just one specific proposal (use the id returned by propose_calendar_event). "
            "The server rejects this call if there are no pending proposals or if the user has "
            "not just confirmed in plain language. Never call this on the same turn as "
            "propose_calendar_event."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "proposal_id": {
                    "type": "string",
                    "description": (
                        "Optional id of a specific pending proposal to create. Omit to create "
                        "all pending proposals."
                    ),
                },
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

async def _search_emails(user_id: str, query: str, limit: int = 5) -> dict:
    started = time.perf_counter()
    limit = max(1, min(int(limit or 5), 10))
    raw_query = (query or "").strip()
    terms = _search_terms(raw_query)
    if not terms:
        logger.info(
            "chat_email_search user_id=%s query_empty=true limit=%d elapsed_ms=%d",
            user_id,
            limit,
            int((time.perf_counter() - started) * 1000),
        )
        return {"results": [], "note": "Empty query."}

    logger.info(
        "chat_email_search_start user_id=%s terms=%s limit=%d",
        user_id,
        terms,
        limit,
    )
    local_rows = await _search_local_email_cache(user_id, raw_query, terms, limit)
    live_results: list[dict] = []
    live_note: str | None = None

    # Only reach for live Gmail when the local cache turns up nothing. That
    # covers older/archived messages and rows missed by failed background sync,
    # without spending a Gmail round-trip (plus per-hit fetches) on every search
    # that already found matches locally.
    do_live = len(local_rows) == 0
    if do_live:
        try:
            live_results = await _search_gmail_live(
                user_id,
                raw_query=raw_query,
                terms=terms,
                limit=limit,
            )
        except Exception:
            logger.exception("Live Gmail search failed for user %s", user_id)
            live_note = "Live Gmail search failed; results only include Felix's local cache."

    results = _dedupe_search_results([*_format_local_results(local_rows), *live_results])[:limit]
    logger.info(
        "chat_email_search_done user_id=%s terms=%s local_count=%d gmail_attempted=%s gmail_count=%d result_count=%d elapsed_ms=%d",
        user_id,
        terms,
        len(local_rows),
        do_live,
        len(live_results),
        len(results),
        int((time.perf_counter() - started) * 1000),
    )
    notes = []
    if live_note:
        notes.append(live_note)
    elif live_results:
        notes.append("Included live Gmail results because Felix's local cache had no matches.")
    elif len(results) == 0:
        notes.append("No matches found in Felix's local cache or live Gmail search.")
    return {
        "results": results,
        "count": len(results),
        "searched": {
            "local_inbound": True,
            "local_sent": True,
            "gmail_live": do_live,
        },
        "note": " ".join(notes) if notes else None,
    }


def _search_terms(query: str) -> list[str]:
    # Keep sender-ish tokens intact, drop tiny filler words, cap for query cost.
    stop = {
        "a", "an", "are", "about", "and", "any", "did", "do", "does", "email",
        "emails", "find", "for", "from", "get", "got", "have", "in", "is",
        "me", "message", "messages", "my", "of", "on", "show", "that", "the",
        "there", "to", "was", "were", "with",
    }
    seen: set[str] = set()
    terms: list[str] = []
    for match in _SEARCH_TERM_RE.finditer(query.lower()):
        term = match.group(0).strip("._+-")
        if len(term) < 2 or term in stop or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= 8:
            break
    return terms


async def _search_local_email_cache(
    user_id: str,
    raw_query: str,
    terms: list[str],
    limit: int,
) -> list[dict]:
    """Rank-search the inbound + sent caches with one positive LIKE per term.

    The query is assembled in Python so each term contributes its own
    `haystack LIKE $pat` predicate (AND-ed). Positive LIKE against the exact
    indexed expression is what lets Postgres pick the pg_trgm GIN bitmap index;
    the previous `NOT EXISTS … NOT LIKE` anti-join could not use the index and
    forced a per-user sequential scan.

    Only the *structure* (column expressions and `$n` placeholders derived from
    integer ranges) is interpolated — every search term flows through a bound
    parameter, so there is no SQL-injection surface.
    """
    if not terms:
        return []

    patterns = ["%" + _like_escape(t) + "%" for t in terms]
    # $1 = user_id; $2..$(n+1) = LIKE patterns; $(n+2) = limit.
    term_params = [f"${i}" for i in range(2, 2 + len(patterns))]
    limit_param = f"${2 + len(patterns)}"

    def _rank_expr(subject_l: str, sender_l: str, body_l: str) -> str:
        parts: list[str] = []
        for p in term_params:
            parts.append(f"(CASE WHEN {sender_l} LIKE {p} THEN 20 ELSE 0 END)")
            parts.append(f"(CASE WHEN {subject_l} LIKE {p} THEN 16 ELSE 0 END)")
            parts.append(f"(CASE WHEN {body_l} LIKE {p} THEN 4 ELSE 0 END)")
        parts.append("(CASE WHEN sort_at > NOW() - INTERVAL '30 days' THEN 5 ELSE 0 END)")
        # Bonus when every term lands in the subject or sender — rewards a
        # focused sender/subject hit over scattered body mentions.
        all_focus = " AND ".join(
            f"({subject_l} LIKE {p} OR {sender_l} LIKE {p})" for p in term_params
        )
        parts.append(f"(CASE WHEN {all_focus} THEN 25 ELSE 0 END)")
        return " + ".join(parts)

    where_inbound = " AND ".join(f"{_INBOUND_HAYSTACK} LIKE {p}" for p in term_params)
    where_sent = " AND ".join(f"{_SENT_HAYSTACK} LIKE {p}" for p in term_params)

    inbound_subject_l = "LOWER(COALESCE(subject, ''))"
    inbound_sender_l = "LOWER(CONCAT_WS(' ', from_name, from_email))"
    inbound_body_l = "LOWER(COALESCE(body, ''))"
    sent_subject_l = "LOWER(COALESCE(subject, ''))"
    sent_sender_l = (
        "LOWER(CONCAT_WS(' ', from_email, ARRAY_TO_STRING(to_emails, ' '), "
        "ARRAY_TO_STRING(to_names, ' ')))"
    )
    sent_body_l = "LOWER(COALESCE(body, ''))"

    sql = f"""
        SELECT mailbox, id, from_name, from_email, to_email, to_emails,
               subject, snippet, sort_at, category, thread_id, rank
        FROM (
            SELECT
                'inbound'::text AS mailbox,
                id, from_name, from_email, to_email,
                NULL::text[] AS to_emails,
                subject, snippet,
                received_at AS sort_at,
                category, thread_id,
                ({_rank_expr(inbound_subject_l, inbound_sender_l, inbound_body_l)}) AS rank
            FROM emails
            WHERE user_id = $1 AND {where_inbound}

            UNION ALL

            SELECT
                'sent'::text AS mailbox,
                id,
                NULL::text AS from_name,
                from_email,
                NULL::text AS to_email,
                to_emails,
                subject, snippet,
                sent_at AS sort_at,
                NULL::text AS category, thread_id,
                ({_rank_expr(sent_subject_l, sent_sender_l, sent_body_l)}) AS rank
            FROM sent_emails
            WHERE user_id = $1 AND {where_sent}
        ) combined
        ORDER BY rank DESC, sort_at DESC NULLS LAST
        LIMIT {limit_param}
    """
    rows = await db.query(sql, user_id, *patterns, limit)
    return rows


def _format_local_results(rows: list[dict]) -> list[dict]:
    return [
        {
            "id": f"sent:{r.get('id')}" if r.get("mailbox") == "sent" else r.get("id"),
            "source": "local_cache",
            "mailbox": r.get("mailbox"),
            "from_name": r.get("from_name"),
            "from_email": r.get("from_email"),
            "to_email": r.get("to_email"),
            "to_emails": r.get("to_emails"),
            "subject": r.get("subject"),
            "snippet": r.get("snippet"),
            "received_at": r.get("sort_at").isoformat() if r.get("sort_at") else None,
            "category": r.get("category"),
            "thread_id": r.get("thread_id"),
            "rank": r.get("rank"),
        }
        for r in rows
    ]


async def _search_gmail_live(
    user_id: str,
    *,
    raw_query: str,
    terms: list[str],
    limit: int,
) -> list[dict]:
    if limit <= 0:
        return []
    creds = await get_google_credentials(user_id)
    gmail = GmailService(creds)
    gmail_query = " ".join(terms) or raw_query
    messages = await gmail.search_messages(gmail_query, max_results=limit)
    results = []
    for msg in messages:
        labels = set(msg.get("labels") or [])
        mailbox = "sent" if "SENT" in labels else "inbound"
        results.append({
            "id": f"gmail:{msg.get('id')}",
            "source": "gmail_live",
            "mailbox": mailbox,
            "from_name": msg.get("from_name"),
            "from_email": msg.get("from_email"),
            "to_email": msg.get("to"),
            "subject": msg.get("subject"),
            "snippet": msg.get("snippet"),
            "received_at": msg.get("received_at").isoformat() if msg.get("received_at") else None,
            "category": None,
            "thread_id": msg.get("thread_id"),
        })
    return results


def _dedupe_search_results(results: list[dict]) -> list[dict]:
    seen: set[str] = set()
    deduped: list[dict] = []
    for result in results:
        raw_id = str(result.get("id") or "")
        key = raw_id.split(":", 1)[-1]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped


async def _get_email(user_id: str, email_id: str) -> dict:
    if not email_id:
        return {"error": "Missing email_id."}
    if email_id.startswith("gmail:"):
        return await _get_gmail_email(user_id, email_id.removeprefix("gmail:"))
    if email_id.startswith("sent:"):
        return await _get_sent_email(user_id, email_id.removeprefix("sent:"))

    row = await db.query_one(
        """
        SELECT id, from_name, from_email, to_email, subject, body, snippet,
               received_at, category, topic, thread_id
        FROM emails
        WHERE user_id = $1 AND id = $2
        """,
        user_id, email_id,
    )
    if not row:
        return {"error": f"No email found with id {email_id}."}

    body = (row.get("body") or "").strip()
    # Cap body for the LLM so a single long message doesn't blow the context window.
    if len(body) > 8000:
        body = body[:8000] + "\n…[truncated]"

    return {
        "id": row.get("id"),
        "from_name": row.get("from_name"),
        "from_email": row.get("from_email"),
        "to_email": row.get("to_email"),
        "subject": row.get("subject"),
        "body": body,
        "received_at": row.get("received_at").isoformat() if row.get("received_at") else None,
        "category": row.get("category"),
        "topic": row.get("topic"),
        "thread_id": row.get("thread_id"),
    }


async def _get_sent_email(user_id: str, email_id: str) -> dict:
    row = await db.query_one(
        """
        SELECT id, from_email, to_emails, to_names, subject, body, snippet,
               sent_at, thread_id
        FROM sent_emails
        WHERE user_id = $1 AND id = $2
        """,
        user_id, email_id,
    )
    if not row:
        return {"error": f"No sent email found with id {email_id}."}

    body = (row.get("body") or "").strip()
    if len(body) > 8000:
        body = body[:8000] + "\n…[truncated]"

    return {
        "id": f"sent:{row.get('id')}",
        "source": "local_cache",
        "mailbox": "sent",
        "from_email": row.get("from_email"),
        "to_emails": row.get("to_emails"),
        "to_names": row.get("to_names"),
        "subject": row.get("subject"),
        "body": body,
        "sent_at": row.get("sent_at").isoformat() if row.get("sent_at") else None,
        "thread_id": row.get("thread_id"),
    }


async def _get_gmail_email(user_id: str, email_id: str) -> dict:
    try:
        creds = await get_google_credentials(user_id)
        gmail = GmailService(creds)
        msg = await gmail.get_message(email_id)
    except Exception:
        logger.exception("Live Gmail get failed for user %s message %s", user_id, email_id)
        return {"error": "Could not fetch that message from Gmail."}

    body = (msg.get("body") or "").strip()
    if len(body) > 8000:
        body = body[:8000] + "\n…[truncated]"
    labels = set(msg.get("labels") or [])
    return {
        "id": f"gmail:{msg.get('id')}",
        "source": "gmail_live",
        "mailbox": "sent" if "SENT" in labels else "inbound",
        "from_name": msg.get("from_name"),
        "from_email": msg.get("from_email"),
        "to_email": msg.get("to"),
        "subject": msg.get("subject"),
        "body": body,
        "received_at": msg.get("received_at").isoformat() if msg.get("received_at") else None,
        "thread_id": msg.get("thread_id"),
    }


# ---------------------------------------------------------------------------
# Calendar proposal gate
#
# The agent's calendar flow is two-step: propose first, create on user
# confirmation. The model rebuilds tool history from plain text only, so any
# token returned to it in turn N is gone by turn N+1 — instead we persist
# pending proposals in `pending_calendar_proposals`. Multiple rows per user
# are allowed so "add both" can stage and create two events together.
# ---------------------------------------------------------------------------

def _looks_like_confirmation(text: str) -> bool:
    if not text:
        return False
    if _NEGATION_PATTERNS.search(text):
        return False
    return bool(_CONFIRMATION_PATTERNS.search(text))


async def _propose_calendar_event(
    user_id: str,
    title: str,
    start_iso: str,
    end_iso: str,
    timezone: str,
    attendees: list[str] | None = None,
    description: str = "",
) -> dict:
    if not (title and start_iso and end_iso and timezone):
        return {"error": "title, start_iso, end_iso, and timezone are all required."}
    cleaned_attendees = [a.strip() for a in (attendees or []) if a and "@" in a]
    payload = {
        "title": title,
        "start_iso": start_iso,
        "end_iso": end_iso,
        "timezone": timezone,
        "attendees": cleaned_attendees,
        "description": description or "",
    }
    # Append a new pending offer. Multiple rows are allowed so the agent can
    # prepare several events before the user confirms with a single "add both".
    row = await db.query_one(
        """
        INSERT INTO pending_calendar_proposals (user_id, payload, created_at)
        VALUES ($1, $2::jsonb, NOW())
        RETURNING id
        """,
        user_id, json.dumps(payload),
    )
    return {
        "proposed": True,
        "proposal_id": str(row["id"]) if row else None,
        "event": payload,
        "note": (
            "Now describe the event details to the user in plain text and ask whether they "
            "want it added to their calendar. Do not mention staging, queuing, pending "
            "offers, or this internal tool. If you have multiple events to offer, call "
            "propose_calendar_event again before you reply. Once the user confirms in their "
            "next turn, call create_calendar_event (no arguments creates all pending "
            "proposals; pass proposal_id for a specific one)."
        ),
    }


def _payload_key(payload: dict[str, Any]) -> tuple:
    attendees = tuple(sorted(a.strip().lower() for a in payload.get("attendees") or [] if a))
    return (
        (payload.get("title") or "").strip().lower(),
        (payload.get("start_iso") or "").strip(),
        (payload.get("end_iso") or "").strip(),
        (payload.get("timezone") or "").strip(),
        attendees,
        (payload.get("description") or "").strip(),
    )


def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row["payload"]
    if isinstance(payload, str):
        return json.loads(payload)
    return payload


def _format_event_when(start: str | None) -> str:
    if not start:
        return ""
    try:
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    minute = "" if dt.minute == 0 else f":{dt.minute:02d}"
    hour = dt.hour % 12 or 12
    suffix = "am" if dt.hour < 12 else "pm"
    return f" on {dt.strftime('%A %d %b')} at {hour}{minute}{suffix}"


def _format_created_event_message(created_events: list[dict[str, Any]]) -> str:
    if not created_events:
        return "I had trouble creating that calendar event. Please try again."

    if len(created_events) == 1:
        event = created_events[0]
        title = event.get("title") or "that event"
        return f"Done, I added {title} to your calendar{_format_event_when(event.get('start'))}."

    parts = []
    for event in created_events[:3]:
        title = event.get("title") or "that event"
        parts.append(f"{title}{_format_event_when(event.get('start'))}")
    suffix = "" if len(created_events) <= 3 else f", and {len(created_events) - 3} more"
    return f"Done, I added {len(created_events)} events to your calendar: {'; '.join(parts)}{suffix}."


async def _pending_calendar_rows(user_id: str, proposal_id: str | None = None) -> list[dict[str, Any]]:
    if proposal_id:
        return await db.query(
            f"""
            SELECT id, payload
            FROM pending_calendar_proposals
            WHERE user_id = $1
              AND id = $2::uuid
              AND created_at > NOW() - INTERVAL '{_PROPOSAL_TTL_SECONDS} seconds'
            """,
            user_id, proposal_id,
        )

    return await db.query(
        f"""
        SELECT id, payload
        FROM pending_calendar_proposals
        WHERE user_id = $1
          AND created_at > NOW() - INTERVAL '{_PROPOSAL_TTL_SECONDS} seconds'
        ORDER BY created_at ASC
        """,
        user_id,
    )


async def _delete_pending_rows(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        await db.execute(
            "DELETE FROM pending_calendar_proposals WHERE id = $1",
            row["id"],
        )


async def _create_calendar_event(
    user_id: str,
    latest_user_turn: str,
    proposal_id: str | None = None,
) -> dict:
    rows = await _pending_calendar_rows(user_id, proposal_id)
    return await _create_calendar_events_from_rows(
        user_id,
        latest_user_turn=latest_user_turn,
        rows=rows,
        dedupe=proposal_id is None,
    )


async def _create_calendar_events_from_rows(
    user_id: str,
    *,
    latest_user_turn: str,
    rows: list[dict[str, Any]],
    dedupe: bool = True,
) -> dict:
    if not rows:
        return {
            "error": (
                "No pending proposal. Call propose_calendar_event first, then ask the user "
                "to confirm before calling create_calendar_event."
            )
        }
    if not _looks_like_confirmation(latest_user_turn):
        return {
            "error": (
                "User has not confirmed in the latest turn. Ask them to confirm "
                "(e.g. 'yes', 'go ahead', 'add to calendar') before calling "
                "create_calendar_event again."
            )
        }

    try:
        creds = await get_google_credentials(user_id)
    except Exception:
        return {"error": "No Google calendar access. Ask the user to reconnect Google in Settings."}

    cal = CalendarService(creds)
    created_events: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    rows_to_create: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = []
    seen: dict[tuple, tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = {}

    for row in rows:
        payload = _row_payload(row)
        key = _payload_key(payload)
        if not dedupe:
            rows_to_create.append((row, payload, [row]))
            continue
        if key in seen:
            seen[key][2].append(row)
            continue
        group = (row, payload, [row])
        seen[key] = group
        rows_to_create.append(group)

    for row, payload, duplicate_rows in rows_to_create:
        event_body: dict[str, Any] = {
            "summary": payload["title"],
            "start": {"dateTime": payload["start_iso"], "timeZone": payload["timezone"]},
            "end":   {"dateTime": payload["end_iso"],   "timeZone": payload["timezone"]},
        }
        if payload.get("description"):
            event_body["description"] = payload["description"]
        if payload.get("attendees"):
            event_body["attendees"] = [{"email": a} for a in payload["attendees"]]

        try:
            created = await cal.create_event(event_body, user_timezone=payload["timezone"])
        except Exception as e:
            # Leave this row in place so the user can retry by re-confirming.
            logger.exception("create_calendar_event tool failed for user %s", user_id)
            failed.append({"proposal_id": str(row["id"]), "error": type(e).__name__})
            continue

        # Single-use — drop the consumed row and any identical duplicate offers.
        await _delete_pending_rows(duplicate_rows)
        created_events.append({
            "id": created.get("id"),
            "title": created.get("title") or payload["title"],
            "start": created.get("start") or payload["start_iso"],
            "end": created.get("end") or payload["end_iso"],
            "html_link": created.get("html_link"),
        })

    if not created_events and failed:
        return {"error": f"Calendar create failed: {failed[0]['error']}"}

    result: dict[str, Any] = {
        "ok": True,
        "created": created_events,
        "count": len(created_events),
    }
    if failed:
        result["failed"] = failed
    return result


async def try_confirm_pending_calendar_proposals(user_id: str, latest_user_turn: str) -> str | None:
    """
    Deterministic fast path for "yes, add it" after Felix has already offered a
    calendar add. Returns None when this turn should continue through the LLM.
    """
    if not _looks_like_confirmation(latest_user_turn):
        return None

    rows = await _pending_calendar_rows(user_id)
    if not rows:
        return None

    result = await _create_calendar_events_from_rows(
        user_id,
        latest_user_turn=latest_user_turn,
        rows=rows,
    )
    if result.get("ok"):
        return _format_created_event_message(result.get("created") or [])

    error = result.get("error") or "Calendar create failed"
    if "No Google calendar access" in error:
        return "I can't access your calendar right now. Reconnect Google in Settings and try again."
    return "I had trouble creating that calendar event. Please try again."


# ---------------------------------------------------------------------------
# Dispatcher factory
# ---------------------------------------------------------------------------

def make_dispatcher(
    user_id: str,
    *,
    latest_user_turn: str = "",
) -> Callable[[str, dict], Awaitable[str]]:
    """
    Return an async dispatch(name, args) function bound to a specific user_id.

    `latest_user_turn` is the user's most recent message (the one that triggered
    this agent run). It is consulted by create_calendar_event to verify the user
    has actually confirmed in plain language before any calendar write happens.

    Within a single agent run we also block create_calendar_event from firing on
    the same turn as propose_calendar_event — otherwise a request that already
    contains a confirmation phrase ("find the event in that email and book it")
    would let the model propose-and-create back-to-back, before the user has
    even seen the extracted details.

    The returned callable always yields a JSON-serializable string suitable for a
    tool_result content block.
    """
    state = {"proposed_this_run": False}

    async def dispatch(name: str, args: dict) -> str:
        args = args or {}
        try:
            if name == "search_emails":
                result = await _search_emails(
                    user_id,
                    query=args.get("query", ""),
                    limit=args.get("limit", 5),
                )
            elif name == "get_email":
                result = await _get_email(user_id, email_id=args.get("email_id", ""))
            elif name == "propose_calendar_event":
                result = await _propose_calendar_event(
                    user_id,
                    title=args.get("title", ""),
                    start_iso=args.get("start_iso", ""),
                    end_iso=args.get("end_iso", ""),
                    timezone=args.get("timezone", ""),
                    attendees=args.get("attendees") or [],
                    description=args.get("description", ""),
                )
                if isinstance(result, dict) and result.get("proposed"):
                    state["proposed_this_run"] = True
            elif name == "create_calendar_event":
                if state["proposed_this_run"]:
                    result = {
                        "error": (
                            "You just proposed this event — you cannot create it on the same "
                            "turn. Describe the proposal to the user, end your reply, and wait "
                            "for them to confirm in their next message before calling "
                            "create_calendar_event."
                        )
                    }
                else:
                    result = await _create_calendar_event(
                        user_id,
                        latest_user_turn=latest_user_turn,
                        proposal_id=(args.get("proposal_id") or None),
                    )
            else:
                result = {"error": f"Unknown tool: {name}"}
        except Exception as e:
            logger.exception("Tool '%s' raised for user %s", name, user_id)
            result = {"error": f"Tool '{name}' failed: {type(e).__name__}"}

        return json.dumps(result, default=str)

    return dispatch
