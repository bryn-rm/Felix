"""Meeting Prep + Meeting Capture API routes.

This router carries two related features:
  • Meeting Prep — the pre-meeting prep card (`/next-prep`, `/{event_id}/prep`).
  • Meeting Capture — the Granola-style browser-capture lifecycle (start / notes /
    end / list / get / summarize / delete). Every capture route is gated behind
    the per-user `meeting_capture_mode` flag and fails closed (404) when off, so
    the feature stays invisible to users who haven't opted in.

The live audio socket lives in a separate, unprefixed router
(`app/api/meetings_ws.py`) — see §2.2 / Phase 6.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from app import db
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import check_monthly_ai_budget, limiter
from app.models.meeting import MeetingType, MeetingUserRole, validate_meeting_mode
from app.services.live_assist_service import (
    _assist_enabled,
    forget_meeting,
    item_to_wire,
)
from app.services.meeting_prep_service import meeting_prep_service
from app.services.meeting_service import _capture_enabled, meeting_service
from app.utils.background import spawn

router = APIRouter()

# Templates the summarizer understands (unknown values fall back to 'general'
# guidance, but we validate at the edge so the picker can't drift silently).
_TEMPLATES = {"general", "one_on_one", "interview", "sales", "standup", "user_research"}


async def _require_capture_enabled(user_id: str) -> None:
    """Fail closed: hide the whole capture surface (404) when the flag is off/unset.

    404 (not 403) so an opted-out user can't even tell the feature exists —
    matches the nav/route-hiding posture of the `meeting_capture_mode` gate.
    """
    if not await _capture_enabled(user_id):
        raise HTTPException(status_code=404, detail="Not found")


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


# ===========================================================================
# Meeting Capture — lifecycle (Phase 6)
# ---------------------------------------------------------------------------
# All routes below are gated by `_require_capture_enabled` and fail closed.
# Static routes (`/start`, list) are declared before the dynamic `/{meeting_id}`
# routes so they can't be shadowed by the path parameter.
# ===========================================================================

class StartMeetingBody(BaseModel):
    calendar_event_id: str | None = None
    title: str | None = Field(default=None, max_length=500)
    template: str = "general"
    meeting_type: MeetingType = "general"
    user_role: MeetingUserRole | None = None

    @model_validator(mode="after")
    def validate_type_and_role(self):
        validate_meeting_mode(self.meeting_type, self.user_role)
        return self


class NotesBody(BaseModel):
    content: str = Field(default="", max_length=100_000)


@router.post("/start")
@limiter.limit("20/minute")
async def start_capture(
    body: StartMeetingBody,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Open a new browser-capture meeting and return its id (status='recording')."""
    user_id = current_user["id"]
    await _require_capture_enabled(user_id)
    # Cached clients know only the old template field. Preserve their interview
    # contract as an unclassified legacy meeting so the existing template-based
    # assist fallback, interview summary, and job fan-out all still run. The
    # field-set check distinguishes omission from an authoritative explicit
    # General selection using the same apparent Pydantic value.
    legacy_interview = (
        "meeting_type" not in body.model_fields_set
        and body.template == "interview"
    )
    if legacy_interview:
        meeting_type = None
        user_role = None
        template = "interview"
    elif body.meeting_type == "interview":
        meeting_type = body.meeting_type
        user_role = body.user_role
        template = "interview"
    else:
        meeting_type = body.meeting_type
        user_role = body.user_role
        template = (
            body.template
            if body.template in _TEMPLATES and body.template != "interview"
            else "general"
        )
    try:
        return await meeting_service.start_meeting(
            user_id,
            calendar_event_id=body.calendar_event_id,
            title=body.title,
            template=template,
            meeting_type=meeting_type,
            user_role=user_role,
        )
    except PermissionError:
        # Race: flag flipped off between the gate check and start. Stay closed.
        raise HTTPException(status_code=404, detail="Not found")


@router.get("")
async def list_capture_meetings(current_user: dict = Depends(get_current_user)):
    """List the user's capture meetings, newest first."""
    user_id = current_user["id"]
    await _require_capture_enabled(user_id)
    return {"meetings": await meeting_service.list_meetings(user_id)}


@router.post("/{meeting_id}/notes")
@limiter.limit("120/minute")
async def save_capture_notes(
    meeting_id: str,
    body: NotesBody,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Persist the live notes (debounced autosave from the live page)."""
    user_id = current_user["id"]
    await _require_capture_enabled(user_id)
    await meeting_service.save_user_notes(user_id, meeting_id, body.content)
    return {"saved": True}


@router.post("/{meeting_id}/end")
@limiter.limit("20/minute")
async def end_capture(
    meeting_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Stop recording and kick off summarization in the background."""
    user_id = current_user["id"]
    await _require_capture_enabled(user_id)
    existing = await db.query_one(
        """
        SELECT id FROM meetings
        WHERE id = $1 AND user_id = $2 AND status = 'recording'
        """,
        meeting_id, user_id,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="meeting not found or not recording")
    await check_monthly_ai_budget(user_id, current_user.get("email"))
    result = await meeting_service.end_meeting(user_id, meeting_id)
    if not result:
        # Not owned, or not in 'recording' — nothing to end.
        raise HTTPException(status_code=404, detail="meeting not found or not recording")
    # The meeting-scoped assist watch-call count outlives the WS connection on
    # purpose (reconnects must not reset the cap); this is where it stops being
    # needed.
    forget_meeting(meeting_id)
    return result


@router.get("/{meeting_id}")
async def get_capture_meeting(
    meeting_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return the meeting plus its segments and latest summary."""
    user_id = current_user["id"]
    await _require_capture_enabled(user_id)
    detail = await meeting_service.get_meeting(user_id, meeting_id)
    if not detail:
        raise HTTPException(status_code=404, detail="meeting not found")
    return detail


@router.get("/{meeting_id}/assist")
async def list_assist_items(
    meeting_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List this meeting's live-assist cards (undismissed and dismissed alike;
    the client filters). Reconnect/refresh replay source. Fails closed 404 when
    live assist is off."""
    user_id = current_user["id"]
    if not await _assist_enabled(user_id):
        raise HTTPException(status_code=404, detail="Not found")
    rows = await db.query(
        "SELECT * FROM meeting_assist_items "
        "WHERE user_id = $1 AND meeting_id = $2 ORDER BY created_at",
        user_id, meeting_id,
    )
    return {"items": [item_to_wire(r) for r in rows]}


@router.post("/{meeting_id}/assist/{item_id}/dismiss")
@limiter.limit("60/minute")
async def dismiss_assist_item(
    meeting_id: str,
    item_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Mark an assist card dismissed (also the first engagement signal we log)."""
    user_id = current_user["id"]
    if not await _assist_enabled(user_id):
        raise HTTPException(status_code=404, detail="Not found")
    row = await db.query_one(
        "UPDATE meeting_assist_items SET dismissed = TRUE "
        "WHERE id = $1 AND meeting_id = $2 AND user_id = $3 RETURNING id",
        item_id, meeting_id, user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="assist item not found")
    return {"dismissed": True}


@router.post("/{meeting_id}/summarize")
@limiter.limit("5/minute")
async def resummarize_capture(
    meeting_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Re-run summarization — the recovery path for a meeting stuck in 'error'.

    Flips the row to 'processing' (only from a terminal 'error'/'done' state, so a
    still-recording meeting isn't disturbed) and spawns the summarizer. The
    per-meeting commitment dedupe keeps the retry idempotent.
    """
    user_id = current_user["id"]
    await _require_capture_enabled(user_id)
    await check_monthly_ai_budget(user_id, current_user.get("email"))
    row = await db.query_one(
        """
        UPDATE meetings
        SET status = 'processing', updated_at = NOW()
        WHERE id = $1 AND user_id = $2 AND status IN ('error', 'done')
        RETURNING id
        """,
        meeting_id, user_id,
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail="meeting not found or not in a re-summarizable state",
        )
    spawn(
        meeting_service.summarize_meeting(user_id, meeting_id),
        name="meeting_resummarize",
    )
    return {"meeting_id": meeting_id, "status": "processing"}


@router.delete("/{meeting_id}")
@limiter.limit("30/minute")
async def delete_capture_meeting(
    meeting_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Delete a capture meeting. Segments/summaries cascade; any sourced
    commitment keeps existing (source_meeting_id is set NULL by the FK)."""
    user_id = current_user["id"]
    await _require_capture_enabled(user_id)
    row = await db.query_one(
        "DELETE FROM meetings WHERE id = $1 AND user_id = $2 RETURNING id",
        meeting_id, user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="meeting not found")
    return {"deleted": True}
