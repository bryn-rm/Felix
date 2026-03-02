"""
Smart template library — Phase 7.

Per-user email templates. Templates are never shared between users.

Endpoints:
  GET    /templates              — list this user's templates
  POST   /templates              — create a template
  GET    /templates/{id}         — get a single template
  PATCH  /templates/{id}         — update a template
  DELETE /templates/{id}         — delete a template
  POST   /templates/{id}/use     — record usage (increments use_count) + return populated body
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.middleware.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class TemplateCreate(BaseModel):
    name: str
    subject_template: str = ""
    body_template: str
    tags: list[str] = []


class TemplateUpdate(BaseModel):
    name: str | None = None
    subject_template: str | None = None
    body_template: str | None = None
    tags: list[str] | None = None


class TemplateUseRequest(BaseModel):
    """Optional variable substitutions for {{placeholder}} fields."""
    variables: dict[str, str] = {}


# ---------------------------------------------------------------------------
# GET /templates
# ---------------------------------------------------------------------------

@router.get("")
async def list_templates(current_user: dict = Depends(get_current_user)):
    """Return all templates for this user, ordered by most-used then most-recent."""
    rows = await db.query(
        """
        SELECT id, name, subject_template, tags, use_count, created_at, updated_at
        FROM smart_templates
        WHERE user_id = $1
        ORDER BY use_count DESC, updated_at DESC
        """,
        current_user["id"],
    )
    return {"templates": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# POST /templates
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def create_template(
    body: TemplateCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a new email template for this user."""
    row = await db.insert(
        "smart_templates",
        {
            "user_id":          current_user["id"],
            "name":             body.name,
            "subject_template": body.subject_template,
            "body_template":    body.body_template,
            "tags":             body.tags,
            "created_at":       datetime.now(timezone.utc).isoformat(),
            "updated_at":       datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"template": row}


# ---------------------------------------------------------------------------
# GET /templates/{template_id}
# ---------------------------------------------------------------------------

@router.get("/{template_id}")
async def get_template(
    template_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return a single template including full body_template."""
    row = await db.query_one(
        "SELECT * FROM smart_templates WHERE id = $1 AND user_id = $2",
        template_id, current_user["id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template": row}


# ---------------------------------------------------------------------------
# PATCH /templates/{template_id}
# ---------------------------------------------------------------------------

@router.patch("/{template_id}")
async def update_template(
    template_id: str,
    body: TemplateUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Partially update a template."""
    existing = await db.query_one(
        "SELECT id FROM smart_templates WHERE id = $1 AND user_id = $2",
        template_id, current_user["id"],
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Template not found")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return {"updated": False}

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    set_parts = [f"{col} = ${i + 1}" for i, col in enumerate(updates.keys())]
    values = list(updates.values())
    values.append(template_id)
    values.append(current_user["id"])

    sql = (
        f"UPDATE smart_templates SET {', '.join(set_parts)} "
        f"WHERE id = ${len(values) - 1} AND user_id = ${len(values)} "
        f"RETURNING *"
    )
    updated = await db.query_one(sql, *values)
    return {"updated": True, "template": updated}


# ---------------------------------------------------------------------------
# DELETE /templates/{template_id}
# ---------------------------------------------------------------------------

@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete a template. This is permanent."""
    existing = await db.query_one(
        "SELECT id FROM smart_templates WHERE id = $1 AND user_id = $2",
        template_id, current_user["id"],
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Template not found")

    await db.execute(
        "DELETE FROM smart_templates WHERE id = $1 AND user_id = $2",
        template_id, current_user["id"],
    )
    return {"deleted": True}


# ---------------------------------------------------------------------------
# POST /templates/{template_id}/use
# ---------------------------------------------------------------------------

@router.post("/{template_id}/use")
async def use_template(
    template_id: str,
    body: TemplateUseRequest = TemplateUseRequest(),
    current_user: dict = Depends(get_current_user),
):
    """
    Record a template usage (increments use_count) and return the populated
    subject + body with any {{placeholder}} values substituted.

    Example: variables={"name": "Alice", "company": "Acme"} will replace
    {{name}} with "Alice" and {{company}} with "Acme" throughout the template.
    """
    template = await db.query_one(
        "SELECT * FROM smart_templates WHERE id = $1 AND user_id = $2",
        template_id, current_user["id"],
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Increment use counter
    await db.execute(
        "UPDATE smart_templates SET use_count = use_count + 1, updated_at = $1 "
        "WHERE id = $2 AND user_id = $3",
        datetime.now(timezone.utc),
        template_id,
        current_user["id"],
    )

    # Apply variable substitutions
    subject = template.get("subject_template") or ""
    body_text = template.get("body_template") or ""
    for key, value in (body.variables or {}).items():
        placeholder = f"{{{{{key}}}}}"  # {{key}}
        subject = subject.replace(placeholder, value)
        body_text = body_text.replace(placeholder, value)

    return {
        "subject": subject,
        "body": body_text,
        "template_id": template_id,
    }
