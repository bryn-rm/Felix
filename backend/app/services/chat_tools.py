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
from typing import Any, Awaitable, Callable

from app import db
from app.middleware.auth import get_google_credentials
from app.services.calendar_service import CalendarService

logger = logging.getLogger(__name__)


# A user turn must contain one of these substrings (after lowercasing) for
# create_calendar_event to be allowed to actually write to Google Calendar.
# Kept narrow on purpose — the agent must propose first, and the user must
# clearly confirm in plain language.
_CONFIRMATION_PATTERNS = re.compile(
    r"\b(yes|yep|yeah|yup|sure|ok(ay)?|please do|do it|go ahead|book it|confirm|"
    r"add it|create it|schedule it|sounds good|that works|perfect)\b",
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
            "Stage a calendar event for the user to confirm. This does NOT write to the "
            "calendar — it just records the proposal server-side. After calling this, "
            "describe the proposed event to the user in plain text and ask them to confirm. "
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
            "Create the most recently proposed event on the user's primary Google Calendar. "
            "Takes no arguments — the server pulls the pending proposal staged by your last "
            "propose_calendar_event call. The server will reject this if there is no pending "
            "proposal or if the user has not just confirmed (e.g. 'yes', 'go ahead', 'book it'). "
            "Never call this on the same turn as propose_calendar_event."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
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
# token returned to it in turn N is gone by turn N+1 — instead we persist the
# pending proposal in `pending_calendar_proposals`, keyed by user_id.
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
    # UPSERT — a new proposal overwrites the prior pending one for this user.
    # Reset created_at so the TTL clock starts fresh.
    await db.execute(
        """
        INSERT INTO pending_calendar_proposals (user_id, payload, created_at)
        VALUES ($1, $2::jsonb, NOW())
        ON CONFLICT (user_id) DO UPDATE
          SET payload    = EXCLUDED.payload,
              created_at = EXCLUDED.created_at
        """,
        user_id, json.dumps(payload),
    )
    return {
        "proposed": True,
        "event": payload,
        "note": (
            "Now describe this event to the user in plain text and ask them to confirm. "
            "Once they confirm in their next turn, call create_calendar_event (no arguments)."
        ),
    }


async def _create_calendar_event(
    user_id: str,
    latest_user_turn: str,
) -> dict:
    row = await db.query_one(
        f"""
        SELECT payload
        FROM pending_calendar_proposals
        WHERE user_id = $1
          AND created_at > NOW() - INTERVAL '{_PROPOSAL_TTL_SECONDS} seconds'
        """,
        user_id,
    )
    if not row:
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
                "(e.g. 'yes' or 'go ahead') before calling create_calendar_event again."
            )
        }

    payload = row["payload"]
    if isinstance(payload, str):
        # asyncpg may return JSONB as text depending on codec setup.
        payload = json.loads(payload)

    try:
        creds = await get_google_credentials(user_id)
    except Exception:
        return {"error": "No Google calendar access. Ask the user to reconnect Google in Settings."}

    event_body: dict[str, Any] = {
        "summary": payload["title"],
        "start": {"dateTime": payload["start_iso"], "timeZone": payload["timezone"]},
        "end":   {"dateTime": payload["end_iso"],   "timeZone": payload["timezone"]},
    }
    if payload.get("description"):
        event_body["description"] = payload["description"]
    if payload.get("attendees"):
        event_body["attendees"] = [{"email": a} for a in payload["attendees"]]

    cal = CalendarService(creds)
    try:
        created = await cal.create_event(event_body, user_timezone=payload["timezone"])
    except Exception as e:
        # Leave the row in place so the user can retry by re-confirming.
        logger.exception("create_calendar_event tool failed for user %s", user_id)
        return {"error": f"Calendar create failed: {type(e).__name__}"}

    # Single-use — drop the pending row now that the event is on the calendar.
    await db.execute(
        "DELETE FROM pending_calendar_proposals WHERE user_id = $1",
        user_id,
    )

    return {
        "ok": True,
        "id": created.get("id"),
        "title": created.get("title") or payload["title"],
        "start": created.get("start") or payload["start_iso"],
        "end": created.get("end") or payload["end_iso"],
        "html_link": created.get("html_link"),
    }


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
                    )
            else:
                result = {"error": f"Unknown tool: {name}"}
        except Exception as e:
            logger.exception("Tool '%s' raised for user %s", name, user_id)
            result = {"error": f"Tool '{name}' failed: {type(e).__name__}"}

        return json.dumps(result, default=str)

    return dispatch
