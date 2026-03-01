"""
Follow-up tracking routes — Phase 5.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app import db
from app.middleware.auth import get_google_credentials
from app.middleware.auth import get_current_user
from app.services.follow_up_engine import follow_up_engine
from app.services.gmail_service import GmailService
from pydantic import BaseModel

router = APIRouter()


class SendFollowUpRequest(BaseModel):
    edited_text: str | None = None


@router.get("")
async def list_follow_ups(
    status: str | None = "waiting",
    current_user: dict = Depends(get_current_user),
):
    clauses = ["user_id = $1"]
    args: list = [current_user["id"]]
    if status:
        clauses.append("status = $2")
        args.append(status)

    rows = await db.query(
        f"SELECT * FROM follow_ups WHERE {' AND '.join(clauses)} ORDER BY follow_up_by ASC NULLS LAST",
        *args,
    )
    return {"follow_ups": rows, "count": len(rows), "status": status}


@router.post("/{follow_up_id}/send")
async def send_follow_up(
    follow_up_id: str,
    body: SendFollowUpRequest = SendFollowUpRequest(),
    current_user: dict = Depends(get_current_user),
):
    item = await db.query_one(
        "SELECT * FROM follow_ups WHERE id = $1 AND user_id = $2",
        follow_up_id,
        current_user["id"],
    )
    if not item:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    if item.get("status") != "waiting":
        raise HTTPException(status_code=409, detail="Follow-up is not in waiting status")

    text = (body.edited_text or item.get("auto_draft") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="No follow-up draft text available")

    creds = await get_google_credentials(current_user["id"])
    gmail = GmailService(creds)
    result = await gmail.send_email(
        to=item.get("to_email") or "",
        subject=item.get("subject") or "Follow up",
        body=text,
    )

    await db.execute(
        """
        UPDATE follow_ups
        SET status = 'followed_up', reminder_count = reminder_count + 1
        WHERE id = $1 AND user_id = $2
        """,
        follow_up_id,
        current_user["id"],
    )

    # Track the newly sent follow-up as a fresh waiting item with next deadline.
    await follow_up_engine.process_sent_email(
        current_user["id"],
        {
            "id": result.get("id"),
            "to": item.get("to_email"),
            "subject": item.get("subject") or "Follow up",
            "body": text,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {"sent": True, "gmail_message_id": result.get("id")}


@router.post("/{follow_up_id}/close")
async def close_follow_up(
    follow_up_id: str,
    current_user: dict = Depends(get_current_user),
):
    await db.execute(
        "UPDATE follow_ups SET status = 'closed' WHERE id = $1 AND user_id = $2",
        follow_up_id,
        current_user["id"],
    )
    return {"closed": True, "id": follow_up_id}
