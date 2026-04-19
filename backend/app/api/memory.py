"""
Memory routes — the user-facing surface of Felix's three-layer memory system.

  GET    /memory                  → current profile + preferences
  PATCH  /memory/profile          → manual profile patch (takes precedence)
  PATCH  /memory/preferences      → manual preferences patch
  POST   /memory/sessions/end     → close the current chat session now
  DELETE /memory/episodes/by-topic → forget every episode mentioning a topic

Manual updates from these endpoints are tagged with `_source=manual` so the
background extractor never overwrites them.
"""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from app.middleware.auth import get_current_user
from app.services import memory_service, session_manager

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Profile + preferences
# ---------------------------------------------------------------------------

def _strip_source(d: dict | None) -> dict:
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if k != "_source"}


@router.get("")
async def get_memory(current_user: dict = Depends(get_current_user)):
    """Return the user's stored profile + preferences (minus internal metadata)."""
    row = await memory_service.get_user_profile(current_user["id"])
    return {
        "profile":     _strip_source(row.get("profile")),
        "preferences": _strip_source(row.get("preferences")),
        "updated_at":  row.get("updated_at"),
    }


@router.patch("/profile")
async def patch_profile(
    body: dict = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """
    Partial update of the user's profile. Any key set here is marked as
    manually-managed and protected from the background extractor.

    Body shape:
      { "set": {...}, "clear": ["key1", "key2"] }
    """
    if not isinstance(body, dict):
        raise HTTPException(400, "Body must be a JSON object")
    patch = body.get("set")
    clear = body.get("clear")
    if patch is not None and not isinstance(patch, dict):
        raise HTTPException(400, "'set' must be an object")
    if clear is not None and not isinstance(clear, list):
        raise HTTPException(400, "'clear' must be a list of keys")

    row = await memory_service.manual_update(
        current_user["id"],
        profile_patch=patch or None,
        clear_profile_keys=clear or None,
    )
    return {
        "profile":     _strip_source(row.get("profile")),
        "preferences": _strip_source(row.get("preferences")),
    }


@router.patch("/preferences")
async def patch_preferences(
    body: dict = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Patch the user's communication preferences (see /memory/profile)."""
    if not isinstance(body, dict):
        raise HTTPException(400, "Body must be a JSON object")
    patch = body.get("set")
    clear = body.get("clear")
    if patch is not None and not isinstance(patch, dict):
        raise HTTPException(400, "'set' must be an object")
    if clear is not None and not isinstance(clear, list):
        raise HTTPException(400, "'clear' must be a list of keys")

    row = await memory_service.manual_update(
        current_user["id"],
        preferences_patch=patch or None,
        clear_preference_keys=clear or None,
    )
    return {
        "profile":     _strip_source(row.get("profile")),
        "preferences": _strip_source(row.get("preferences")),
    }


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

@router.post("/sessions/end")
async def end_session(current_user: dict = Depends(get_current_user)):
    """
    Explicitly end the user's current chat session. Summarises + stores it.
    No-op if there is no active session.
    """
    row = await session_manager.end_session(current_user["id"], reason="explicit")
    return {"ended": bool(row), "summary": row}


# ---------------------------------------------------------------------------
# Targeted forgetting
# ---------------------------------------------------------------------------

@router.delete("/episodes/by-topic")
async def forget_by_topic(
    topic: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete every memory episode that references the given topic."""
    topic = (topic or "").strip()
    if not topic:
        raise HTTPException(400, "topic query param is required")
    removed = await memory_service.forget_by_topic(current_user["id"], topic)
    return {"removed": removed, "topic": topic}
