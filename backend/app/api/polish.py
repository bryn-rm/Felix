"""Phase 7 API routes: digest, weekly review, templates, style evolution."""

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.middleware.auth import get_current_user
from app.middleware.rate_limit import check_monthly_ai_budget, limiter
from app.services.polish_service import polish_service

router = APIRouter()


class PolishDraftRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20_000)


class PolishDraftResponse(BaseModel):
    polished: str


@router.post("/draft", response_model=PolishDraftResponse)
@limiter.limit("10/minute")
async def polish_draft(
    request: Request,
    body: PolishDraftRequest,
    current_user: dict = Depends(get_current_user),
) -> PolishDraftResponse:
    """
    Polish draft email text — fix tone, grammar and clarity without changing
    the underlying meaning. Used by the inline DraftPanel "Polish" button.
    """
    await check_monthly_ai_budget(current_user["id"], current_user.get("email"))

    polished = await polish_service.polish_draft_text(
        current_user["id"], body.text
    )
    return PolishDraftResponse(polished=polished)


@router.get("/digest")
async def get_digest(
    window_hours: int = Query(6, ge=1, le=24),
    current_user: dict = Depends(get_current_user),
):
    digest = await polish_service.build_digest(current_user["id"], window_hours=window_hours)
    return digest


@router.get("/weekly-review")
@limiter.limit("5/minute")
async def get_weekly_review(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """
    Preview the user's current weekly review email.

    Returns the same payload the Sunday job sends: subject, full HTML body,
    plaintext alternative, and the supplementary stats.
    """
    await check_monthly_ai_budget(current_user["id"], current_user.get("email"))
    return await polish_service.generate_weekly_review_email(current_user["id"])


@router.get("/templates/suggestions")
async def get_template_suggestions(current_user: dict = Depends(get_current_user)):
    templates = await polish_service.suggest_templates(current_user["id"])
    return {"templates": templates, "count": len(templates)}


@router.get("/style-evolution")
async def get_style_evolution(current_user: dict = Depends(get_current_user)):
    return await polish_service.style_evolution_report(current_user["id"])
