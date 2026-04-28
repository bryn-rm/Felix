"""Commitment Radar API routes."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import db
from app.middleware.auth import get_current_user
from app.services.commitment_service import commitment_service

router = APIRouter()


@router.get("")
async def list_commitments(
    direction: Literal["owed_by_user", "owed_to_user"] | None = Query(None),
    status: Literal["open", "done", "dropped", "rescued"] = Query("open"),
    within_hours: int | None = Query(None, ge=0, le=24 * 30),
    current_user: dict = Depends(get_current_user),
):
    """List commitments for the current user.

    Query params:
      direction    — filter to one direction (default: both)
      status       — open / done / dropped / rescued (default: open)
      within_hours — only commitments with a deadline ≤ N hours away
    """
    if status == "open":
        rows = await commitment_service.list_open(
            current_user["id"], direction=direction, within_hours=within_hours,
        )
    else:
        # Other statuses are simple direct queries.
        conditions = ["user_id = $1", "status = $2"]
        args: list = [current_user["id"], status]
        i = 3
        if direction:
            conditions.append(f"direction = ${i}"); args.append(direction); i += 1
        sql = (
            "SELECT * FROM commitments WHERE " + " AND ".join(conditions)
            + " ORDER BY resolved_at DESC NULLS LAST, created_at DESC LIMIT 200"
        )
        rows = await db.query(sql, *args)
    return {"commitments": rows, "count": len(rows)}


class ResolveBody(BaseModel):
    status: Literal["done", "dropped", "rescued"] = "done"


@router.post("/{commitment_id}/resolve")
async def resolve_commitment(
    commitment_id: str,
    body: ResolveBody,
    current_user: dict = Depends(get_current_user),
):
    """Mark a commitment as done / dropped / rescued."""
    row = await commitment_service.resolve(
        current_user["id"], commitment_id, status=body.status,
    )
    if not row:
        raise HTTPException(status_code=404, detail="commitment not found")
    return {"commitment": row}
