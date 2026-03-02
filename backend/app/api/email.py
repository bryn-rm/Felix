"""
Email routes — Phase 2.

All reads come from our Supabase emails/drafts tables (pre-populated by the
background sync job). Sends go via the Gmail API. Every route is scoped to
the requesting user via Depends(get_current_user).

Endpoints:
  GET  /emails                        — list emails (filterable)
  GET  /emails/stats                  — counts per category for dashboard
  GET  /emails/{id}                   — single email + attached draft
  GET  /emails/{id}/thread            — full Gmail thread
  POST /emails/{id}/draft             — generate / regenerate draft (SSE stream)
  PATCH /emails/{id}/draft            — save user's edited draft text
  POST /emails/{id}/send              — send approved draft via Gmail
  DELETE /emails/{id}/draft           — discard draft
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import db
from app.middleware.auth import get_current_user, get_google_credentials
from app.services.ai_service import ai_service
from app.services.gmail_service import GmailService

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------

class DraftEditRequest(BaseModel):
    edited_text: str


class SendRequest(BaseModel):
    """Optionally override the draft text at send time."""
    edited_text: str | None = None


class RegenerateRequest(BaseModel):
    user_intent: str = ""   # e.g. "decline politely", "ask for more time"


# ---------------------------------------------------------------------------
# GET /emails
# ---------------------------------------------------------------------------

@router.get("")
async def list_emails(
    category: str | None = Query(None, description="Filter by triage category"),
    urgency: str | None = Query(None),
    draft_pending: bool | None = Query(None, description="Only emails with a pending draft"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """
    List processed emails from the local DB, newest first.
    The background sync job keeps this table up to date.
    """
    conditions = ["e.user_id = $1"]
    args: list = [current_user["id"]]
    idx = 2

    if category:
        conditions.append(f"e.category = ${idx}")
        args.append(category)
        idx += 1

    if urgency:
        conditions.append(f"e.urgency = ${idx}")
        args.append(urgency)
        idx += 1

    if draft_pending is True:
        conditions.append("d.status = 'pending'")
    elif draft_pending is False:
        conditions.append("(d.id IS NULL OR d.status != 'pending')")

    where = " AND ".join(conditions)

    # LEFT JOIN drafts so we get the draft status in one query
    sql = f"""
        SELECT
            e.id, e.thread_id, e.from_email, e.from_name, e.to_email,
            e.subject, e.snippet, e.received_at, e.category, e.urgency,
            e.sentiment, e.topic, e.draft_generated,
            d.id        AS draft_id,
            d.status    AS draft_status
        FROM emails e
        LEFT JOIN drafts d ON d.email_id = e.id AND d.user_id = e.user_id
        WHERE {where}
        ORDER BY e.received_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
    """
    args.extend([limit, offset])

    rows = await db.query(sql, *args)
    return {"emails": rows, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# GET /emails/stats
# ---------------------------------------------------------------------------

@router.get("/stats")
async def email_stats(current_user: dict = Depends(get_current_user)):
    """
    Return email counts per category. Used by the dashboard widgets.
    """
    rows = await db.query(
        """
        SELECT category, COUNT(*) AS count
        FROM emails
        WHERE user_id = $1
        GROUP BY category
        """,
        current_user["id"],
    )
    counts = {r["category"]: r["count"] for r in rows if r["category"]}

    pending_drafts = await db.query_one(
        "SELECT COUNT(*) AS count FROM drafts WHERE user_id = $1 AND status = 'pending'",
        current_user["id"],
    )

    return {
        "by_category": counts,
        "pending_drafts": pending_drafts["count"] if pending_drafts else 0,
    }


# ---------------------------------------------------------------------------
# GET /emails/{email_id}
# ---------------------------------------------------------------------------

@router.get("/{email_id}")
async def get_email(
    email_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Return a single email (including body) with its attached draft if any.
    """
    email = await db.query_one(
        "SELECT * FROM emails WHERE id = $1 AND user_id = $2",
        email_id, current_user["id"],
    )
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    draft = await db.query_one(
        "SELECT * FROM drafts WHERE email_id = $1 AND user_id = $2",
        email_id, current_user["id"],
    )

    return {**email, "draft": draft}


# ---------------------------------------------------------------------------
# GET /emails/{email_id}/thread
# ---------------------------------------------------------------------------

@router.get("/{email_id}/thread")
async def get_thread(
    email_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Fetch the full Gmail thread for an email.
    Makes a live call to the Gmail API — not cached in our DB.
    """
    email = await db.query_one(
        "SELECT thread_id FROM emails WHERE id = $1 AND user_id = $2",
        email_id, current_user["id"],
    )
    if not email or not email.get("thread_id"):
        raise HTTPException(status_code=404, detail="Thread not found")

    creds = await get_google_credentials(current_user["id"])
    gmail = GmailService(creds)
    messages = await gmail.get_thread(email["thread_id"])
    return {"thread_id": email["thread_id"], "messages": messages}


# ---------------------------------------------------------------------------
# POST /emails/{email_id}/draft  — generate or regenerate (streaming SSE)
# ---------------------------------------------------------------------------

@router.post("/{email_id}/draft")
async def generate_draft(
    email_id: str,
    body: RegenerateRequest = RegenerateRequest(),
    current_user: dict = Depends(get_current_user),
):
    """
    Generate (or regenerate) an AI draft reply for an email.

    Returns a Server-Sent Events stream. Each event is JSON:
      {"chunk": "...text..."}          — token streamed from Claude
      {"done": true, "draft_id": ...}  — generation complete

    The client should accumulate chunk values and display live. On "done",
    the draft has been saved to the DB and the draft_id is returned.
    """
    email = await db.query_one(
        "SELECT * FROM emails WHERE id = $1 AND user_id = $2",
        email_id, current_user["id"],
    )
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    user_settings = await db.query_one(
        "SELECT display_name, style_profile FROM settings WHERE user_id = $1",
        current_user["id"],
    )
    user_name: str = (user_settings or {}).get("display_name") or "User"
    style_profile: dict = (user_settings or {}).get("style_profile") or {}

    # Fetch context in parallel
    creds = await get_google_credentials(current_user["id"])
    gmail = GmailService(creds)

    thread_history: list[dict] = []
    if email.get("thread_id"):
        try:
            thread_history = await gmail.get_thread(email["thread_id"])
        except Exception:
            logger.warning("Could not fetch thread for email %s", email_id)

    contact: dict = await db.query_one(
        "SELECT * FROM contacts WHERE email = $1 AND user_id = $2",
        email.get("from_email", ""), current_user["id"],
    ) or {}

    async def sse_stream():
        full_text = ""
        try:
            async for chunk in ai_service.draft_reply(
                email=dict(email),
                thread_history=thread_history,
                contact=contact,
                style_profile=style_profile,
                user_name=user_name,
                user_intent=body.user_intent,
            ):
                full_text += chunk
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        except Exception:
            logger.exception("Draft streaming failed for email %s", email_id)
            yield f"data: {json.dumps({'error': 'Draft generation failed'})}\n\n"
            return

        # Upsert so regeneration overwrites the previous draft
        existing = await db.query_one(
            "SELECT id FROM drafts WHERE email_id = $1 AND user_id = $2",
            email_id, current_user["id"],
        )
        if existing:
            await db.execute(
                "UPDATE drafts SET draft_text = $1, status = 'pending', edited_text = NULL "
                "WHERE email_id = $2 AND user_id = $3",
                full_text, email_id, current_user["id"],
            )
            draft_id = str(existing["id"])
        else:
            row = await db.insert(
                "drafts",
                {
                    "email_id":   email_id,
                    "user_id":    current_user["id"],
                    "draft_text": full_text,
                    "status":     "pending",
                },
            )
            draft_id = str(row["id"]) if row else ""

        await db.execute(
            "UPDATE emails SET draft_generated = TRUE WHERE id = $1 AND user_id = $2",
            email_id, current_user["id"],
        )
        yield f"data: {json.dumps({'done': True, 'draft_id': draft_id})}\n\n"

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# PATCH /emails/{email_id}/draft  — save user edits
# ---------------------------------------------------------------------------

@router.patch("/{email_id}/draft")
async def update_draft(
    email_id: str,
    body: DraftEditRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Save the user's edited version of a draft.
    On send, edited_text takes priority over draft_text.
    """
    draft = await db.query_one(
        "SELECT id FROM drafts WHERE email_id = $1 AND user_id = $2",
        email_id, current_user["id"],
    )
    if not draft:
        raise HTTPException(status_code=404, detail="No draft for this email")

    await db.execute(
        "UPDATE drafts SET edited_text = $1 WHERE id = $2 AND user_id = $3",
        body.edited_text, draft["id"], current_user["id"],
    )
    return {"updated": True}


# ---------------------------------------------------------------------------
# POST /emails/{email_id}/send  — send via Gmail
# ---------------------------------------------------------------------------

@router.post("/{email_id}/send")
async def send_email(
    email_id: str,
    body: SendRequest = SendRequest(),
    current_user: dict = Depends(get_current_user),
):
    """
    Send the approved draft (or the override text in body.edited_text) via Gmail.

    Priority for what gets sent:
      1. body.edited_text (provided at send time)
      2. drafts.edited_text (saved earlier via PATCH)
      3. drafts.draft_text (AI original)
    """
    email = await db.query_one(
        "SELECT * FROM emails WHERE id = $1 AND user_id = $2",
        email_id, current_user["id"],
    )
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    draft = await db.query_one(
        "SELECT * FROM drafts WHERE email_id = $1 AND user_id = $2",
        email_id, current_user["id"],
    )
    if not draft:
        raise HTTPException(status_code=404, detail="No draft to send for this email")

    if draft["status"] == "sent":
        raise HTTPException(status_code=409, detail="This draft has already been sent")

    # Resolve final send text
    send_text: str = (
        body.edited_text
        or draft.get("edited_text")
        or draft.get("draft_text")
        or ""
    )
    if not send_text.strip():
        raise HTTPException(status_code=422, detail="Draft text is empty")

    # Send via Gmail API
    creds = await get_google_credentials(current_user["id"])
    gmail = GmailService(creds)

    try:
        result = await gmail.send_reply(
            to=email["from_email"],
            subject=email["subject"] or "",
            body=send_text,
            thread_id=email["thread_id"] or "",
            original_message_id=email.get("message_id_header") or "",
        )
    except Exception as exc:
        logger.exception("Gmail send failed for email %s", email_id)
        raise HTTPException(status_code=502, detail=f"Gmail send failed: {exc}")

    # Mark draft as sent
    sent_at = datetime.now(timezone.utc)
    await db.execute(
        "UPDATE drafts SET status = 'sent', edited_text = $1, sent_at = $2 "
        "WHERE id = $3 AND user_id = $4",
        send_text,
        sent_at,
        draft["id"],
        current_user["id"],
    )

    # Phase 5 — fire-and-forget follow-up detection on the sent email.
    # Build a minimal sent-email dict the engine can analyse.
    import asyncio as _asyncio
    from app.services.follow_up_engine import follow_up_engine as _fu_engine

    _sent_email_dict = {
        "id":        email_id,
        "subject":   email.get("subject") or "",
        "body":      send_text,
        "to":        email.get("from_email") or "",  # we're replying to the original sender
        "to_email":  email.get("from_email") or "",
        "received_at": datetime.now(timezone.utc),
    }
    _asyncio.create_task(
        _fu_engine.process_sent_email(current_user["id"], _sent_email_dict)
    )

    return {"sent": True, "gmail_message_id": result.get("id")}


# ---------------------------------------------------------------------------
# DELETE /emails/{email_id}/draft  — discard
# ---------------------------------------------------------------------------

@router.delete("/{email_id}/draft")
async def discard_draft(
    email_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Mark a draft as discarded. Does not delete the row (preserves audit trail)."""
    draft = await db.query_one(
        "SELECT id FROM drafts WHERE email_id = $1 AND user_id = $2",
        email_id, current_user["id"],
    )
    if not draft:
        raise HTTPException(status_code=404, detail="No draft for this email")

    await db.execute(
        "UPDATE drafts SET status = 'discarded' WHERE id = $1 AND user_id = $2",
        draft["id"], current_user["id"],
    )
    return {"discarded": True}
