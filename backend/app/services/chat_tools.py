"""
Tool definitions for the chat agent loop in _general_question.

Three tools, all scoped to a single user_id via the dispatcher closure:

  - search_emails(query, limit)        : ILIKE search across the local emails table
  - get_email(email_id)                : full row including body
  - create_calendar_event(...)         : insert event into the user's primary calendar

The local emails table is the right source for search — it's already populated by
the sync job, has body up to 50k, and is indexed on (user_id, received_at DESC).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Awaitable, Callable

from app import db
from app.middleware.auth import get_google_credentials
from app.services.calendar_service import CalendarService

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


# Anthropic tool schemas — exposed to the model.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_emails",
        "description": (
            "Search the user's emails by free-text query. Matches against sender name, "
            "sender address, subject, snippet, and body (case-insensitive substring). "
            "Use this whenever the user asks about a specific email, sender, or topic. "
            "Returns up to `limit` results ordered by most recent first."
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
    limit = max(1, min(int(limit or 5), 10))
    pattern = f"%{(query or '').strip().lower()}%"
    if pattern == "%%":
        return {"results": [], "note": "Empty query."}

    rows = await db.query(
        """
        SELECT id, from_name, from_email, subject, snippet, received_at, category
        FROM emails
        WHERE user_id = $1
          AND (
                LOWER(from_name)   LIKE $2
             OR LOWER(from_email)  LIKE $2
             OR LOWER(subject)     LIKE $2
             OR LOWER(snippet)     LIKE $2
             OR LOWER(body)        LIKE $2
          )
        ORDER BY received_at DESC
        LIMIT $3
        """,
        user_id, pattern, limit,
    )
    results = [
        {
            "id": r.get("id"),
            "from_name": r.get("from_name"),
            "from_email": r.get("from_email"),
            "subject": r.get("subject"),
            "snippet": r.get("snippet"),
            "received_at": r.get("received_at").isoformat() if r.get("received_at") else None,
            "category": r.get("category"),
        }
        for r in rows
    ]
    return {"results": results, "count": len(results)}


async def _get_email(user_id: str, email_id: str) -> dict:
    if not email_id:
        return {"error": "Missing email_id."}
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
