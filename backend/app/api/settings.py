"""
User settings routes — Phase 1 onboarding + preferences.

After Google connection, the onboarding flow collects name / timezone /
briefing_time and upserts them here. The settings page also reads and
updates these values.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import db
from app.middleware.auth import get_current_user

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class SettingsUpdate(BaseModel):
    display_name: str | None = None
    timezone: str | None = None
    briefing_time: str | None = None   # "HH:MM"
    digest_mode: bool | None = None
    digest_times: list[str] | None = None
    energy_profile: dict | None = None
    felix_voice_id: str | None = None


class VIPUpdate(BaseModel):
    vip_contacts: list[str]            # list of email addresses


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("")
async def get_settings(current_user: dict = Depends(get_current_user)):
    """Return this user's settings row, creating defaults if missing."""
    row = await db.query_one(
        "SELECT * FROM settings WHERE user_id = $1", current_user["id"]
    )
    if not row:
        # Create default settings row on first access
        row = await db.insert(
            "settings",
            {
                "user_id": current_user["id"],
                "display_name": current_user.get("metadata", {}).get("full_name", ""),
                "timezone": "Europe/London",
                "briefing_time": "07:30",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    return row


@router.patch("")
async def update_settings(
    body: SettingsUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Partially update user settings."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return {"updated": False}

    updates["user_id"] = current_user["id"]
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    row = await db.upsert("settings", updates, conflict_columns=["user_id"])
    return row


@router.put("/vip-contacts")
async def update_vip_contacts(
    body: VIPUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Replace the full list of VIP contact emails."""
    row = await db.upsert(
        "settings",
        {
            "user_id": current_user["id"],
            "vip_contacts": body.vip_contacts,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        conflict_columns=["user_id"],
    )
    return {"vip_contacts": row["vip_contacts"] if row else body.vip_contacts}
