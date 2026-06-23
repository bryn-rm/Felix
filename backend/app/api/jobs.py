"""Job Search Mode API routes.

Every route requires auth. The feature is gated by settings.job_search_mode;
when off, the frontend hides the surface, and detection never runs server-side.
The board endpoints themselves stay reachable (they just return whatever the
user has) so toggling the flag back on still shows prior data.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.middleware.auth import get_current_user
from app.services.job_tracker_service import job_tracker_service

router = APIRouter()


JobStatus = Literal[
    "saved", "applied", "phone_screen", "interview",
    "offer", "rejected", "accepted", "withdrawn",
]


class JobCreate(BaseModel):
    company: str
    role_title: str
    location: str | None = None
    job_url: str | None = None
    status: JobStatus | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    compensation: str | None = None
    notes: str | None = None


class JobPatch(BaseModel):
    company: str | None = None
    role_title: str | None = None
    location: str | None = None
    job_url: str | None = None
    status: JobStatus | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    compensation: str | None = None
    notes: str | None = None
    next_action: str | None = None


class EventCreate(BaseModel):
    event_type: Literal["note", "applied", "status_change", "interview_scheduled"] = "note"
    title: str | None = None
    detail: str | None = None


class SuggestionResolve(BaseModel):
    accept: bool


@router.get("")
async def get_board(current_user: dict = Depends(get_current_user)):
    """Kanban board: active jobs grouped by status, plus due-badge counts."""
    return await job_tracker_service.list_board(current_user["id"])


@router.post("")
async def create_job(body: JobCreate, current_user: dict = Depends(get_current_user)):
    try:
        job = await job_tracker_service.create_manual(current_user["id"], body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"job": job}


@router.get("/suggestions")
async def list_suggestions(current_user: dict = Depends(get_current_user)):
    rows = await job_tracker_service.list_suggestions(current_user["id"])
    return {"suggestions": rows, "count": len(rows)}


@router.post("/suggestions/{suggestion_id}")
async def resolve_suggestion(
    suggestion_id: str,
    body: SuggestionResolve,
    current_user: dict = Depends(get_current_user),
):
    job = await job_tracker_service.resolve_suggestion(
        current_user["id"], suggestion_id, accept=body.accept,
    )
    if body.accept and not job:
        raise HTTPException(status_code=404, detail="suggestion not found or already resolved")
    return {"job": job, "accepted": body.accept}


@router.get("/{job_id}")
async def get_job(job_id: str, current_user: dict = Depends(get_current_user)):
    result = await job_tracker_service.get(current_user["id"], job_id)
    if not result:
        raise HTTPException(status_code=404, detail="job not found")
    return result


@router.patch("/{job_id}")
async def patch_job(
    job_id: str, body: JobPatch, current_user: dict = Depends(get_current_user),
):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        job = await job_tracker_service.update(current_user["id"], job_id, patch)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job": job}


@router.delete("/{job_id}")
async def delete_job(job_id: str, current_user: dict = Depends(get_current_user)):
    ok = await job_tracker_service.delete(current_user["id"], job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="job not found")
    return {"deleted": True}


@router.post("/{job_id}/events")
async def add_event(
    job_id: str, body: EventCreate, current_user: dict = Depends(get_current_user),
):
    row = await job_tracker_service.add_event(
        current_user["id"], job_id, body.event_type,
        title=body.title, detail=body.detail,
    )
    if not row:
        raise HTTPException(status_code=404, detail="job not found")
    return {"event": row}


@router.post("/{job_id}/draft-follow-up")
async def draft_follow_up(job_id: str, current_user: dict = Depends(get_current_user)):
    result = await job_tracker_service.draft_follow_up(current_user["id"], job_id)
    if not result:
        raise HTTPException(status_code=404, detail="job not found")
    return result
