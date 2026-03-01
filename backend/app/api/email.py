"""
Email routes — Phase 2.

Inbox listing, email detail, draft management, send.
"""

from fastapi import APIRouter, Depends
from app.middleware.auth import get_current_user, get_google_credentials

router = APIRouter()


@router.get("")
async def list_emails(
    category: str | None = None,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
):
    # TODO Phase 2: query emails table filtered by user_id + optional category
    raise NotImplementedError


@router.get("/{email_id}")
async def get_email(email_id: str, current_user: dict = Depends(get_current_user)):
    # TODO Phase 2: fetch single email + its triage data + draft if any
    raise NotImplementedError


@router.get("/{email_id}/draft")
async def get_draft(email_id: str, current_user: dict = Depends(get_current_user)):
    # TODO Phase 2: return the AI-generated draft for this email
    raise NotImplementedError


@router.post("/{email_id}/draft/send")
async def send_draft(email_id: str, current_user: dict = Depends(get_current_user)):
    # TODO Phase 2: send the (optionally edited) draft via Gmail API
    raise NotImplementedError


@router.delete("/{email_id}/draft")
async def discard_draft(email_id: str, current_user: dict = Depends(get_current_user)):
    # TODO Phase 2: mark draft as discarded
    raise NotImplementedError
