"""
Contact / relationship intelligence routes — Phase 6.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import db
from app.middleware.auth import get_current_user

router = APIRouter()


class ContactUpdateRequest(BaseModel):
    name: str | None = None
    company: str | None = None
    role: str | None = None
    personal_notes: str | None = None
    tags: list[str] | None = None
    vip: bool | None = None
    vip_rules: dict | None = None


@router.get("")
async def list_contacts(
    vip_only: bool = Query(False),
    q: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    clauses = ["user_id = $1"]
    args: list = [current_user["id"]]
    idx = 2

    if vip_only:
        clauses.append("vip = TRUE")
    if q:
        clauses.append(f"(email ILIKE ${idx} OR name ILIKE ${idx})")
        args.append(f"%{q}%")
        idx += 1

    sql = (
        f"SELECT * FROM contacts WHERE {' AND '.join(clauses)} "
        f"ORDER BY relationship_strength DESC NULLS LAST, last_contacted DESC NULLS LAST "
        f"LIMIT ${idx} OFFSET ${idx + 1}"
    )
    args.extend([limit, offset])
    rows = await db.query(sql, *args)
    return {"contacts": rows, "count": len(rows), "limit": limit, "offset": offset}


@router.get("/{email}")
async def get_contact(email: str, current_user: dict = Depends(get_current_user)):
    contact = await db.query_one(
        "SELECT * FROM contacts WHERE email = $1 AND user_id = $2",
        email.lower(),
        current_user["id"],
    )
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    recent_emails = await db.query(
        """
        SELECT id, subject, received_at, category, sentiment, urgency
        FROM emails
        WHERE user_id = $1 AND from_email = $2
        ORDER BY received_at DESC
        LIMIT 5
        """,
        current_user["id"],
        email.lower(),
    )
    recent_meetings = await db.query(
        """
        SELECT id, title, date, summary
        FROM meetings
        WHERE user_id = $1 AND $2 = ANY(attendees)
        ORDER BY date DESC
        LIMIT 3
        """,
        current_user["id"],
        email.lower(),
    )
    return {
        **contact,
        "relationship_card": {
            "recent_emails": recent_emails,
            "recent_meetings": recent_meetings,
        },
    }


@router.patch("/{email}")
async def update_contact(
    email: str,
    body: ContactUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    existing = await db.query_one(
        "SELECT * FROM contacts WHERE email = $1 AND user_id = $2",
        email.lower(),
        current_user["id"],
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Contact not found")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return {"updated": False, "contact": existing}

    merged = {
        **existing,
        **updates,
        "updated_at": datetime.now(timezone.utc),
    }
    row = await db.upsert("contacts", merged, conflict_columns=["email", "user_id"])
    return {"updated": True, "contact": row}
