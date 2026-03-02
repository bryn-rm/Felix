"""Phase 7 API routes: digest, weekly review, templates, style evolution."""

from fastapi import APIRouter, Depends, Query

from app.middleware.auth import get_current_user
from app.services.polish_service import polish_service

router = APIRouter()


@router.get("/digest")
async def get_digest(
    window_hours: int = Query(6, ge=1, le=24),
    current_user: dict = Depends(get_current_user),
):
    digest = await polish_service.build_digest(current_user["id"], window_hours=window_hours)
    return digest


@router.get("/weekly-review")
async def get_weekly_review(current_user: dict = Depends(get_current_user)):
    return await polish_service.build_weekly_review(current_user["id"])


@router.get("/templates/suggestions")
async def get_template_suggestions(current_user: dict = Depends(get_current_user)):
    templates = await polish_service.suggest_templates(current_user["id"])
    return {"templates": templates, "count": len(templates)}


@router.get("/style-evolution")
async def get_style_evolution(current_user: dict = Depends(get_current_user)):
    return await polish_service.style_evolution_report(current_user["id"])
