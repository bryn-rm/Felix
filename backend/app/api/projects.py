"""Authenticated manual Project Hubs API."""

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.middleware.auth import get_current_user
from app.services.project_service import project_service
from app.services.project_knowledge_service import project_knowledge_service
from app.services.project_update_service import project_update_service
from app.services.project_suggestion_service import project_suggestion_service
from app.services.project_question_service import project_question_service
from app.middleware.rate_limit import limiter

router = APIRouter()
SourceKind = Literal["email_thread", "meeting", "commitment"]
ProjectStatus = Literal["active", "archived"]


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    target_date: date | None = None


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    target_date: date | None = None
    status: ProjectStatus | None = None

    @model_validator(mode="after")
    def reject_null_fields(self):
        for field in ("name", "description", "status"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class SourceLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: SourceKind
    source_id: str = Field(min_length=1, max_length=500)


@router.get("")
async def list_projects(
    status: ProjectStatus = "active", limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0), current_user: dict = Depends(get_current_user),
):
    return {"projects": await project_service.list(current_user["id"], status, limit, offset)}


@router.post("", status_code=201)
async def create_project(body: ProjectCreate, current_user: dict = Depends(get_current_user)):
    return {"project": await project_service.create(current_user["id"], body.model_dump())}


@router.get("/sources/search")
async def search_sources(
    kind: SourceKind, q: str = Query("", max_length=200),
    limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    return {"sources": await project_service.search(current_user["id"], kind, q, limit, offset)}


@router.get("/{project_id}")
async def get_project(project_id: UUID, current_user: dict = Depends(get_current_user)):
    return await project_service.detail(current_user["id"], project_id)


@router.patch("/{project_id}")
async def edit_project(project_id: UUID, body: ProjectPatch, current_user: dict = Depends(get_current_user)):
    return {"project": await project_service.edit(current_user["id"], project_id, body.model_dump(exclude_unset=True))}


@router.post("/{project_id}/sources")
async def link_source(project_id: UUID, body: SourceLink, current_user: dict = Depends(get_current_user)):
    return await project_service.link(current_user["id"], project_id, body.kind, body.source_id)


@router.delete("/{project_id}/sources/{kind}/{link_id}", status_code=204)
async def unlink_source(project_id: UUID, kind: SourceKind, link_id: UUID, current_user: dict = Depends(get_current_user)):
    await project_service.unlink(current_user["id"], project_id, kind, link_id)


@router.get("/{project_id}/activity")
async def project_activity(
    project_id: UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    return {"activity": await project_service.activity(current_user["id"], project_id, limit, offset)}


@router.get("/{project_id}/threads/{thread_id}")
async def project_thread(project_id: UUID, thread_id: str, current_user: dict = Depends(get_current_user)):
    return {"messages": await project_service.thread(current_user["id"], project_id, thread_id)}


class ScopeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=10000)
    expected_version: int = Field(ge=0)


class RecordCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    kind: Literal["decision", "approval", "milestone"]
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=3000)
    owner: str = Field(default="", max_length=200)
    event_date: date | None = None
    deadline: date | None = None
    supersedes_id: UUID | None = None
    evidence: list[SourceLink] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_kind(self):
        if self.kind in ("decision", "milestone") and (self.event_date is None or self.deadline is not None or self.owner):
            raise ValueError("Decisions/milestones require a date and do not have an approval owner/deadline")
        if self.kind == "approval" and self.event_date is not None:
            raise ValueError("Use deadline for approvals")
        if self.supersedes_id and self.kind != "decision":
            raise ValueError("Only decisions can supersede a record")
        return self


class RecordPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=3000)
    owner: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, max_length=30)
    event_date: date | None = None
    deadline: date | None = None

    @model_validator(mode="after")
    def reject_null_text(self):
        for key in ("title", "description", "owner", "status"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        return self


class ImportDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    summary_id: UUID
    decision_index: int = Field(ge=0)
    decision_date: date


class GenerateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class AskProject(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    question: str = Field(min_length=1, max_length=2000)


@router.get("/{project_id}/ask")
async def get_project_answer(project_id: UUID, current_user: dict = Depends(get_current_user)):
    return await project_question_service.get(current_user["id"], project_id)


@router.post("/{project_id}/ask")
@limiter.limit("3/minute")
async def ask_project(project_id: UUID, body: AskProject, request: Request, current_user: dict = Depends(get_current_user)):
    return await project_question_service.ask(current_user["id"], project_id, body.request_id, body.question, current_user.get("email"))


@router.get("/{project_id}/knowledge")
async def get_knowledge(project_id: UUID, current_user: dict = Depends(get_current_user)):
    return await project_knowledge_service.list(current_user["id"], project_id)


@router.put("/{project_id}/scope")
async def set_scope(project_id: UUID, body: ScopeBody, current_user: dict = Depends(get_current_user)):
    return {"scope": await project_knowledge_service.scope(current_user["id"], project_id, body.content, body.expected_version)}


@router.post("/{project_id}/records", status_code=201)
async def create_record(project_id: UUID, body: RecordCreate, current_user: dict = Depends(get_current_user)):
    return await project_knowledge_service.create(current_user["id"], project_id, body.model_dump())


@router.patch("/{project_id}/records/{record_id}")
async def edit_record(project_id: UUID, record_id: UUID, body: RecordPatch, current_user: dict = Depends(get_current_user)):
    values = body.model_dump(exclude_unset=True)
    version = values.pop("expected_version")
    return await project_knowledge_service.edit(current_user["id"], project_id, record_id, values, version)


@router.get("/{project_id}/meeting-decisions")
async def meeting_decisions(project_id: UUID, current_user: dict = Depends(get_current_user)):
    return {"decisions": await project_knowledge_service.meeting_decisions(current_user["id"], project_id)}


@router.post("/{project_id}/decisions/import", status_code=201)
async def import_decision(project_id: UUID, body: ImportDecision, current_user: dict = Depends(get_current_user)):
    return await project_knowledge_service.create(current_user["id"], project_id,
        {"kind": "decision", "event_date": body.decision_date, "request_id": body.request_id}, import_summary=(body.summary_id, body.decision_index))


@router.get("/{project_id}/update")
async def get_update(project_id: UUID, current_user: dict = Depends(get_current_user)):
    return await project_update_service.get(current_user["id"], project_id)


@router.post("/{project_id}/update")
@limiter.limit("3/minute")
async def generate_update(project_id: UUID, body: GenerateUpdate, request: Request, current_user: dict = Depends(get_current_user)):
    return await project_update_service.generate(current_user["id"], project_id, body.request_id, current_user.get("email"))


@router.get("/{project_id}/evidence")
async def get_evidence(project_id: UUID, key: str = Query(max_length=200), current_user: dict = Depends(get_current_user)):
    return await project_update_service.evidence(current_user["id"], project_id, key)


@router.get("/{project_id}/suggestions")
async def list_suggestions(project_id: UUID, current_user: dict = Depends(get_current_user)):
    return await project_suggestion_service.list(current_user["id"], project_id)


class DiscoverSuggestions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


@router.post("/{project_id}/suggestions/reset-dismissals")
async def reset_suggestion_dismissals(project_id: UUID, current_user: dict = Depends(get_current_user)):
    return await project_suggestion_service.reset_dismissals(current_user["id"], project_id)


@router.post("/{project_id}/suggestions/discover")
@limiter.limit("3/minute")
async def discover_suggestions(project_id: UUID, body: DiscoverSuggestions, request: Request, current_user: dict = Depends(get_current_user)):
    return await project_suggestion_service.discover(current_user["id"], project_id, body.request_id, current_user.get("email"))


@router.post("/{project_id}/suggestions/{suggestion_id}/accept")
async def accept_suggestion(project_id: UUID, suggestion_id: UUID, current_user: dict = Depends(get_current_user)):
    return await project_suggestion_service.resolve(current_user["id"], project_id, suggestion_id, True)


@router.post("/{project_id}/suggestions/{suggestion_id}/dismiss")
async def dismiss_suggestion(project_id: UUID, suggestion_id: UUID, current_user: dict = Depends(get_current_user)):
    return await project_suggestion_service.resolve(current_user["id"], project_id, suggestion_id, False)


@router.get("/{project_id}/suggestions/{suggestion_id}/thread")
async def suggested_thread(project_id: UUID, suggestion_id: UUID, current_user: dict = Depends(get_current_user)):
    return {"messages": await project_suggestion_service.thread(current_user["id"], project_id, suggestion_id)}
