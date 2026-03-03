"""
Contact / relationship intelligence routes — Phase 6.

All endpoints are scoped to the requesting user's JWT — contacts are
never shared between users. Contact emails in URL paths are URL-decoded.

Endpoints:
  GET   /contacts              — list all contacts, ordered by relationship strength
  GET   /contacts/{email}      — full contact profile + recent emails + meetings
  PATCH /contacts/{email}      — update manual fields (notes, tags, VIP status, etc.)
"""

import logging
from datetime import datetime, timezone
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import db
from app.middleware.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class ContactUpdateRequest(BaseModel):
    name: str | None = None
    company: str | None = None
    role: str | None = None
    vip: bool | None = None
    vip_rules: dict | None = None
    personal_notes: str | None = None
    tags: list[str] | None = None
    open_commitments: list[str] | None = None
    their_open_commitments: list[str] | None = None
    known_facts: dict | None = None


# ---------------------------------------------------------------------------
# GET /contacts
# ---------------------------------------------------------------------------

@router.get("")
async def list_contacts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """
    Return all contact profiles for this user, ordered by relationship strength
    (strongest relationship first). Excludes heavy JSONB fields for performance.
    """
    rows = await db.query(
        """
        SELECT
            email, name, company, role, vip,
            relationship_strength, total_emails, last_contacted,
            meeting_count, last_meeting,
            sentiment_trend, tags, open_commitments, updated_at
        FROM contacts
        WHERE user_id = $1
        ORDER BY
            relationship_strength DESC NULLS LAST,
            last_contacted DESC NULLS LAST
        LIMIT $2 OFFSET $3
        """,
        current_user["id"],
        limit,
        offset,
    )
    return {"contacts": rows, "count": len(rows), "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# GET /contacts/{email}
# ---------------------------------------------------------------------------

@router.get("/{raw_email:path}")
async def get_contact(
    raw_email: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Return the full contact profile including all JSONB fields, plus
    the 5 most recent emails from this contact and 3 most recent meetings.
    """
    user_id = current_user["id"]
    contact_email = unquote(raw_email).lower()

    contact = await db.query_one(
        "SELECT * FROM contacts WHERE email = $1 AND user_id = $2",
        contact_email, user_id,
    )
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Recent emails from this contact
    recent_emails = await db.query(
        """
        SELECT id, subject, snippet, received_at, category, urgency, sentiment
        FROM emails
        WHERE user_id = $1 AND from_email = $2
        ORDER BY received_at DESC
        LIMIT 5
        """,
        user_id, contact_email,
    )

    # Recent meetings with this contact
    recent_meetings = await db.query(
        """
        SELECT id, title, date, summary, action_items
        FROM meetings
        WHERE user_id = $1 AND $2 = ANY(attendees)
        ORDER BY date DESC
        LIMIT 3
        """,
        user_id, contact_email,
    )

    return {
        "contact": contact,
        "recent_emails": recent_emails,
        "recent_meetings": recent_meetings,
    }


# ---------------------------------------------------------------------------
# PATCH /contacts/{email}
# ---------------------------------------------------------------------------

@router.patch("/{raw_email:path}")
async def update_contact(
    raw_email: str,
    body: ContactUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Update manual fields on a contact profile.
    Only non-None fields in the request body are applied.
    Computed fields (relationship_strength, total_emails, etc.) are never overwritten here.
    """
    user_id = current_user["id"]
    contact_email = unquote(raw_email).lower()

    # Verify ownership
    existing = await db.query_one(
        "SELECT email FROM contacts WHERE email = $1 AND user_id = $2",
        contact_email, user_id,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Build update dict from only the non-None fields supplied
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return {"updated": False}

    # Build parameterised SET clause — handles TEXT, BOOLEAN, TEXT[], JSONB
    set_parts = []
    values = []
    for i, (col, val) in enumerate(updates.items(), start=1):
        set_parts.append(f"{col} = ${i}")
        values.append(val)

    # Add updated_at
    set_parts.append(f"updated_at = ${len(values) + 1}")
    values.append(datetime.now(timezone.utc))

    # WHERE clause parameters
    values.append(contact_email)          # email
    values.append(user_id)                # user_id

    sql = (
        f"UPDATE contacts SET {', '.join(set_parts)} "
        f"WHERE email = ${len(values) - 1} AND user_id = ${len(values)} "
        f"RETURNING *"
    )

    updated = await db.query_one(sql, *values)
    return {"updated": True, "contact": updated}
