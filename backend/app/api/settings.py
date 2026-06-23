"""
User settings routes — Phase 1 onboarding + preferences.

After Google connection, the onboarding flow collects name / timezone /
briefing_time and upserts them here. The settings page also reads and
updates these values.
"""

import json
import logging
import re
from datetime import datetime, time, timezone

import pytz
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from app import db
from app.config import settings
from app.middleware.auth import get_current_user, get_google_credentials
from app.services.ai_service import ai_service
from app.services.gmail_service import GmailService

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

_DIGEST_TIME_RE = re.compile(r"^([01]\d|2[0-3]):(00|30)$")
_BRIEFING_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MEETING_PREP_MODES = {"off", "email_only", "in_app_only", "both"}

# Columns in `settings` that are nullable in the DB schema — only these may
# be cleared by sending an explicit null in PATCH /settings. For every other
# field a null is dropped, matching the long-standing "null means don't update"
# contract and avoiding 500s on NOT NULL columns (timezone, briefing_time, …).
_NULLABLE_SETTINGS_FIELDS = {
    "display_name",
    "style_profile",
    "energy_profile",
    "felix_voice_id",
}


def _parse_time(value: str) -> time:
    """Convert HH:MM string into a datetime.time object for Postgres TIME columns."""
    return time.fromisoformat(value.strip())


def _configured_voice_options() -> list[dict[str, str]]:
    """Return approved ElevenLabs voices from FELIX_VOICE_CATALOG."""
    raw = (settings.FELIX_VOICE_CATALOG or "").strip()
    if not raw:
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid FELIX_VOICE_CATALOG JSON; no extra voices will be shown")
        return []

    if not isinstance(parsed, list):
        logger.warning("FELIX_VOICE_CATALOG must be a JSON array")
        return []

    voices: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        voice_id = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        if not voice_id or not label or voice_id in seen:
            continue
        seen.add(voice_id)
        voices.append({"id": voice_id, "label": label})
    return voices


def _voice_options_with_default(current_voice_id: str | None = None) -> list[dict[str, str]]:
    """Return dropdown options, preserving an already-saved legacy voice id."""
    voices = [{"id": "", "label": "System default"}, *_configured_voice_options()]
    configured_ids = {voice["id"] for voice in voices}
    if current_voice_id and current_voice_id not in configured_ids:
        voices.append({"id": current_voice_id, "label": f"Current ({current_voice_id})"})
    return voices


class SettingsUpdate(BaseModel):
    display_name: str | None = None
    timezone: str | None = None
    briefing_time: str | None = None   # "HH:MM"
    digest_mode: bool | None = None
    digest_times: list[str] | None = None
    energy_profile: dict | None = None
    felix_voice_id: str | None = None
    meeting_prep_mode: str | None = None  # off | email_only | in_app_only | both
    job_search_mode: bool | None = None   # gate for Job Search Mode (fails closed)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str | None) -> str | None:
        if v is not None and v not in pytz.all_timezones:
            raise ValueError(
                f"Unknown timezone '{v}'. Use a valid IANA timezone name (e.g. 'Europe/London')."
            )
        return v

    @field_validator("meeting_prep_mode")
    @classmethod
    def validate_meeting_prep_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in _MEETING_PREP_MODES:
            raise ValueError(
                f"Invalid meeting_prep_mode '{v}'. Must be one of "
                f"{sorted(_MEETING_PREP_MODES)}."
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


class VoiceOptionsResponse(BaseModel):
    voices: list[dict[str, str]]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/voices", response_model=VoiceOptionsResponse)
async def get_voice_options(current_user: dict = Depends(get_current_user)):
    """Return the approved ElevenLabs voice choices for the settings dropdown."""
    row = await db.query_one(
        "SELECT felix_voice_id FROM settings WHERE user_id = $1", current_user["id"]
    )
    current_voice_id = (row or {}).get("felix_voice_id") or None
    return {"voices": _voice_options_with_default(current_voice_id)}


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
    # exclude_unset → only the keys the client sent, so an explicit
    # {"felix_voice_id": null} can clear an override. We then drop nulls for
    # NOT NULL columns to preserve the older "null means don't update" contract
    # (otherwise a bad client payload like {"timezone": null} would 500).
    sent = body.model_dump(exclude_unset=True)
    updates = {
        k: v for k, v in sent.items()
        if v is not None or k in _NULLABLE_SETTINGS_FIELDS
    }
    if not updates:
        return {"updated": False}

    if "briefing_time" in updates and updates["briefing_time"] is not None:
        updates["briefing_time"] = _parse_time(updates["briefing_time"])

    if "felix_voice_id" in updates:
        requested_voice_id = (updates["felix_voice_id"] or "").strip()
        if not requested_voice_id:
            updates["felix_voice_id"] = None
        else:
            row = await db.query_one(
                "SELECT felix_voice_id FROM settings WHERE user_id = $1",
                current_user["id"],
            )
            current_voice_id = (row or {}).get("felix_voice_id") or None
            allowed_ids = {voice["id"] for voice in _configured_voice_options()}
            if requested_voice_id not in allowed_ids and requested_voice_id != current_voice_id:
                raise HTTPException(
                    status_code=422,
                    detail="felix_voice_id must be one of the configured voice options.",
                )
            updates["felix_voice_id"] = requested_voice_id

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
    result = await ai_service.analyse_writing_style(sent_emails, user_id=user_id)
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
