"""
Contact / relationship intelligence routes — Phase 6.
"""

from fastapi import APIRouter, Depends
from app.middleware.auth import get_current_user

router = APIRouter()


@router.get("")
async def list_contacts(current_user: dict = Depends(get_current_user)):
    # TODO Phase 6: return all contacts for this user, ordered by relationship_strength
    raise NotImplementedError


@router.get("/{email}")
async def get_contact(email: str, current_user: dict = Depends(get_current_user)):
    # TODO Phase 6: return full contact profile + relationship card
    raise NotImplementedError


@router.patch("/{email}")
async def update_contact(email: str, current_user: dict = Depends(get_current_user)):
    # TODO Phase 6: update personal_notes, tags, vip status, vip_rules
    raise NotImplementedError
