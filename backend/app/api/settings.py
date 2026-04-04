"""
User settings routes — Phase 1 onboarding + preferences.

After Google connection, the onboarding flow collects name / timezone /
briefing_time and upserts them here. The settings page also reads and
updates these values.
"""

import re
from datetime import datetime, time, timezone

import pytz
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from app import db
from app.middleware.auth import get_current_user, get_google_credentials
from app.services.ai_service import ai_service
from app.services.gmail_service import GmailService

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

_DIGEST_TIME_RE = re.compile(r"^([01]\d|2[0-3]):(00|30)$")
_BRIEFING_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _parse_time(value: str) -> time:
    """Convert HH:MM string into a datetime.time object for Postgres TIME columns."""
    return time.fromisoformat(value.strip())


class SettingsUpdate(BaseModel):
    display_name: str | None = None
    timezone: str | None = None
    briefing_time: str | None = None   # "HH:MM"
    digest_mode: bool | None = None
    digest_times: list[str] | None = None
    energy_profile: dict | None = None
    felix_voice_id: str | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str | None) -> str | None:
        if v is not None and v not in pytz.all_timezones:
            raise ValueError(
                f"Unknown timezone '{v}'. Use a valid IANA timezone name (e.g. 'Europe/London')."
            )
        return v

    @field_validator("briefing_time")
    @classmethod
    def validate_briefing_time(cls, v: str | None) -> str | None:
        if v is not None and not _BRIEFING_TIME_RE.match(v.strip()):
            raise ValueError(
                f"Invalid briefing time '{v}'. Must be HH:MM format (e.g. '07:30')."
            )
        return v

    @field_validator("digest_times")
    @classmethod
    def validate_digest_times(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        for t in v:
            if not _DIGEST_TIME_RE.match(t.strip()):
                raise ValueError(
                    f"Invalid digest time '{t}'. Must be HH:00 or HH:30 (e.g. '08:00', '14:30')."
                )
        return v


class VIPUpdate(BaseModel):
    vip_contacts: list[str]            # list of email addresses

    @field_validator("vip_contacts")
    @classmethod
    def validate_vip_emails(cls, v: list[str]) -> list[str]:
        for addr in v:
            if not _EMAIL_RE.match(addr.strip()):
                raise ValueError(f"Invalid email address in VIP list: {addr!r}")
        return v


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
                "briefing_time": _parse_time("07:30"),
                "updated_at": datetime.now(timezone.utc),
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

    if "briefing_time" in updates:
        updates["briefing_time"] = _parse_time(updates["briefing_time"])

    updates["user_id"] = current_user["id"]
    updates["updated_at"] = datetime.now(timezone.utc)

    row = await db.upsert("settings", updates, conflict_columns=["user_id"])
    return row


@router.post("/analyse-style")
async def analyse_writing_style(
    current_user: dict = Depends(get_current_user),
):
    """Analyse the user's last 100 sent emails and save a style profile."""
    user_id = current_user["id"]
    creds = await get_google_credentials(user_id)
    gmail = GmailService(creds)
    sent_emails = await gmail.get_sent_emails(max_results=100)
    result = await ai_service.analyse_writing_style(sent_emails)
    await db.upsert(
        "settings",
        {
            "user_id": user_id,
            "style_profile": result,
            "updated_at": datetime.now(timezone.utc),
        },
        conflict_columns=["user_id"],
    )
    return {"status": "ok", "profile": result}


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
            "updated_at": datetime.now(timezone.utc),
        },
        conflict_columns=["user_id"],
    )
    return {"vip_contacts": row["vip_contacts"] if row else body.vip_contacts}
