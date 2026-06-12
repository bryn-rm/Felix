"""Meeting Prep API routes."""

from fastapi import APIRouter, Depends, HTTPException, Request

from app import db
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import check_monthly_ai_budget, limiter
from app.services.meeting_prep_service import meeting_prep_service

router = APIRouter()


@router.get("/next-prep")
@limiter.limit("30/minute")
async def get_next_meeting_prep(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """
    Return the prep card for the user's next upcoming meeting.

    If the next meeting is within 60 minutes and no cached prep exists yet,
    one is generated on the fly (Sonnet call). Otherwise returns the cached
    row, or a lightweight `pending` stub if the meeting is too far out to
    pre-generate.
    """
    prep = await meeting_prep_service.get_next_prep(current_user["id"])
    if not prep:
        return {"prep": None}
    return {"prep": prep}


@router.get("/{event_id}/prep")
async def get_meeting_prep_by_event(
    event_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return the cached prep card for a specific calendar event."""
    row = await db.query_one(
        """
        SELECT id, event_id, event_title, event_start, attendees,
               content_html, content_text, status, generated_at
        FROM meeting_preps
        WHERE user_id = $1 AND event_id = $2
        """,
        current_user["id"], event_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="prep not found for this event")
    return row


@router.post("/{event_id}/prep/regenerate")
@limiter.limit("5/minute")
async def regenerate_meeting_prep(
    event_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Force a regeneration of the prep card for a specific event.

    Useful when the underlying email context has changed materially after
    the initial card was generated. Costs one Sonnet call.
    """
    await check_monthly_ai_budget(current_user["id"], current_user.get("email"))

    # We need the event dict to regenerate; pull it from the cached row's
    # snapshot if we have it, or synthesise a minimal one. This avoids
    # an extra Calendar API hit on the hot path.
    cached = await db.query_one(
        """
        SELECT event_id, event_title, event_start, attendees
        FROM meeting_preps
        WHERE user_id = $1 AND event_id = $2
        """,
        current_user["id"], event_id,
    )
    if not cached:
        raise HTTPException(
            status_code=404,
            detail="no prep exists yet for this event — fetch /next-prep first",
        )

    event = {
        "id":         cached["event_id"],
        "title":      cached.get("event_title"),
        "start":      cached.get("event_start").isoformat() if cached.get("event_start") else None,
        "attendees":  cached.get("attendees") or [],
        "is_all_day": False,
    }
    prep = await meeting_prep_service.generate_for_event(
        current_user["id"], event, force=True,
    )
    return {"prep": prep}
