"""
Follow-up tracking routes — Phase 5.

Endpoints:
  GET    /follow-ups                     — list follow-ups by status
  POST   /follow-ups/{id}/send          — send the auto-drafted follow-up via Gmail
  POST   /follow-ups/{id}/close         — mark as closed / no longer tracking
  PATCH  /follow-ups/{id}               — edit the auto_draft text before sending
  POST   /follow-ups/{id}/draft         — (re)generate an AI draft for a follow-up
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import db
from app.middleware.auth import get_current_user, get_google_credentials
from app.services.gmail_service import GmailService

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class SendFollowUpRequest(BaseModel):
    edited_text: str | None = None


class FollowUpPatch(BaseModel):
    auto_draft: str


# ---------------------------------------------------------------------------
# GET /follow-ups
# ---------------------------------------------------------------------------

@router.get("")
async def list_follow_ups(
    status: str = Query("waiting", description="waiting | replied | followed_up | closed"),
    current_user: dict = Depends(get_current_user),
):
    """Return follow-ups for this user filtered by status, oldest deadline first."""
    valid_statuses = {"waiting", "replied", "followed_up", "closed"}
    if status not in valid_statuses:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{status}'. Must be one of: {', '.join(sorted(valid_statuses))}",
        )

    rows = await db.query(
        """
        SELECT id, email_id, to_email, subject, topic, sent_at,
               follow_up_by, status, urgency, auto_draft, reminder_count, created_at
        FROM follow_ups
        WHERE user_id = $1 AND status = $2
        ORDER BY follow_up_by ASC NULLS LAST
        """,
        current_user["id"],
        status,
    )
    return {"follow_ups": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# POST /follow-ups/{follow_up_id}/send
# ---------------------------------------------------------------------------

@router.post("/{follow_up_id}/send")
async def send_follow_up(
    follow_up_id: str,
    body: SendFollowUpRequest = SendFollowUpRequest(),
    current_user: dict = Depends(get_current_user),
):
    """
    Send the pre-drafted follow-up email via Gmail.

    Requires auto_draft to be non-empty. Use PATCH or POST /draft first if needed.
    """
    user_id = current_user["id"]

    fu = await db.query_one(
        "SELECT * FROM follow_ups WHERE id = $1 AND user_id = $2",
        follow_up_id, user_id,
    )
    if not fu:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    if fu["status"] != "waiting":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot send follow-up with status '{fu['status']}'",
        )
    if not fu.get("auto_draft"):
        raise HTTPException(
            status_code=422,
            detail="No draft text to send. Use POST /follow-ups/{id}/draft to generate one first.",
        )

    creds = await get_google_credentials(user_id)
    gmail = GmailService(creds)

    subject = fu.get("subject") or ""
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"

    try:
        await gmail.send_email(
            to=fu["to_email"],
            subject=reply_subject,
            body=fu["auto_draft"],
        )
    except Exception as exc:
        logger.exception("Gmail send failed for follow_up %s", follow_up_id)
        raise HTTPException(status_code=502, detail=f"Gmail send failed: {exc}")

    await db.execute(
        """
        UPDATE follow_ups
        SET status = 'followed_up', reminder_count = reminder_count + 1
        WHERE id = $1 AND user_id = $2
        """,
        follow_up_id, user_id,
    )

    return {"sent": True, "to": fu["to_email"]}


# ---------------------------------------------------------------------------
# POST /follow-ups/{follow_up_id}/close
# ---------------------------------------------------------------------------

@router.post("/{follow_up_id}/close")
async def close_follow_up(
    follow_up_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Mark a follow-up as closed — stop tracking it."""
    user_id = current_user["id"]

    fu = await db.query_one(
        "SELECT id FROM follow_ups WHERE id = $1 AND user_id = $2",
        follow_up_id, user_id,
    )
    if not fu:
        raise HTTPException(status_code=404, detail="Follow-up not found")

    await db.execute(
        "UPDATE follow_ups SET status = 'closed' WHERE id = $1 AND user_id = $2",
        follow_up_id, user_id,
    )
    return {"closed": True}


# ---------------------------------------------------------------------------
# PATCH /follow-ups/{follow_up_id}
# ---------------------------------------------------------------------------

@router.patch("/{follow_up_id}")
async def update_follow_up(
    follow_up_id: str,
    body: FollowUpPatch,
    current_user: dict = Depends(get_current_user),
):
    """Edit the auto_draft text for a follow-up before sending."""
    user_id = current_user["id"]

    fu = await db.query_one(
        "SELECT id FROM follow_ups WHERE id = $1 AND user_id = $2",
        follow_up_id, user_id,
    )
    if not fu:
        raise HTTPException(status_code=404, detail="Follow-up not found")

    await db.execute(
        "UPDATE follow_ups SET auto_draft = $1 WHERE id = $2 AND user_id = $3",
        body.auto_draft, follow_up_id, user_id,
    )
    return {"updated": True}


# ---------------------------------------------------------------------------
# POST /follow-ups/{follow_up_id}/draft
# ---------------------------------------------------------------------------

@router.post("/{follow_up_id}/draft")
async def generate_follow_up_draft(
    follow_up_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Generate (or regenerate) an AI draft for this follow-up.
    Uses the follow_up's topic + recipient context to craft a polite follow-up email.
    Returns the draft text and updates follow_ups.auto_draft.
    """
    from app.services.follow_up_engine import follow_up_engine

    draft_text = await follow_up_engine.draft_follow_up_text(
        user_id=current_user["id"],
        follow_up_id=follow_up_id,
    )
    if draft_text is None:
        raise HTTPException(status_code=404, detail="Follow-up not found or draft generation failed")

    return {"auto_draft": draft_text}
