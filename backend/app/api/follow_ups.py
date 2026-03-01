"""
Follow-up tracking routes — Phase 5.
"""

from fastapi import APIRouter, Depends
from app.middleware.auth import get_current_user

router = APIRouter()


@router.get("")
async def list_follow_ups(
    status: str = "waiting",
    current_user: dict = Depends(get_current_user),
):
    # TODO Phase 5: return follow-ups filtered by status for this user
    raise NotImplementedError


@router.post("/{follow_up_id}/send")
async def send_follow_up(
    follow_up_id: str,
    current_user: dict = Depends(get_current_user),
):
    # TODO Phase 5: send the pre-drafted follow-up email via Gmail API
    raise NotImplementedError


@router.post("/{follow_up_id}/close")
async def close_follow_up(
    follow_up_id: str,
    current_user: dict = Depends(get_current_user),
):
    # TODO Phase 5: mark as closed / no longer tracking
    raise NotImplementedError
