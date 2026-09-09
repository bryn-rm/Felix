"""Explicit, bounded project updates; generated state never edits confirmed data."""

import asyncio
import json
import time
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import db
from app.config import settings
from app.middleware.rate_limit import check_monthly_ai_budget
from app.prompts._helpers import wrap_untrusted
from app.prompts.project_update import PROJECT_UPDATE_PROMPT
from app.services import ai_service as ai
from app.services.project_evidence_service import project_snapshot
from app.services.project_service import project_service


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    evidence_id: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=5, max_length=300)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    section: Literal["scope", "decisions", "approvals", "milestones", "commitments", "developments", "open_questions", "conflicts"]
    text: str = Field(min_length=1, max_length=700)
    time_basis: Literal["current_context", "source_event_this_week", "project_action_this_week"]
    citations: list[Citation] = Field(min_length=1, max_length=4)


class UpdateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    claims: list[Claim] = Field(max_length=12)


def validate_output(raw, selected):
    output = UpdateOutput.model_validate(json.loads(ai._strip_markdown_fences(raw)))
    evidence = {item["id"]: item for item in selected}
    for claim in output.claims:
        if not claim.text.strip():
            raise ValueError("Empty claim")
        for citation in claim.citations:
            if citation.evidence_id not in evidence or not citation.quote.strip() or citation.quote not in evidence[citation.evidence_id]["text"]:
                raise ValueError("Unsupported citation")
        flag = {"source_event_this_week": "recent_event", "project_action_this_week": "recent_project_action"}.get(claim.time_basis)
        if flag and not any(evidence[c.evidence_id].get(flag) for c in claim.citations):
            raise ValueError("No cited event in this week's window")
    return output.model_dump()["claims"]


async def generate_claims(user_id, snapshot):
    response = None
    started = time.monotonic()
    success = False
    parse_error = False
    error_message = None
    try:
        payload = {key: snapshot[key] for key in ("week_start", "week_end", "timezone", "as_of", "omitted_count")}
        payload["evidence"] = snapshot["selected"]
        async with asyncio.timeout(60):
            response = await ai.client.messages.create(
                model=settings.ANTHROPIC_MODEL_SMART, max_tokens=4000,
                **ai.thinking_kwarg(settings.ANTHROPIC_MODEL_SMART), timeout=55.0,
                system=PROJECT_UPDATE_PROMPT,
                messages=[{"role": "user", "content": wrap_untrusted(json.dumps(payload, default=str), "project_evidence")}],
            )
        try:
            if response.stop_reason not in ("end_turn", "stop_sequence"):
                raise ValueError("Incomplete or refused update")
            raw = "".join(block.text for block in response.content if block.type == "text")
            claims = validate_output(raw, snapshot["selected"])
        except (ValueError, TypeError, AttributeError):
            parse_error = True
            raise
        success = True
        return claims
    except BaseException as error:
        error_message = type(error).__name__  # never log provider-echoed project content
        raise
    finally:
        await ai.log_ai_call(feature="project_update", model=settings.ANTHROPIC_MODEL_SMART,
                             response=response, started_at=started, user_id=user_id, success=success,
                             parse_error=parse_error, error_message=error_message, quota_scope="interactive")


class ProjectUpdateService:
    async def get(self, user_id, project_id):
        await project_service.get(user_id, project_id)
        row = await db.query_one("SELECT * FROM project_updates WHERE user_id = $1 AND project_id = $2", user_id, project_id)
        if not row:
            return {"update": None, "stale": False, "withheld": False}
        snapshot = await project_snapshot(user_id, project_id, evidence_keys=row["evidence_manifest"], select_candidates=False)
        return self._result(row, snapshot)

    def _result(self, row, snapshot):
        # Check EVERY item passed to the model, not just its chosen citations:
        # any of that context may have influenced generated prose.
        withheld = any(key not in snapshot["items"] for key in row["evidence_manifest"])
        stale = (snapshot["fingerprint"] != row["fingerprint"] or snapshot["week_start"] != row["week_start"]
                 or row["prompt_version"] != ai.PROMPT_VERSIONS["project_update"]
                 or row["model"] != settings.ANTHROPIC_MODEL_SMART)
        if withheld:
            return {"update": None, "stale": True, "withheld": True, "last_generated_at": row["generated_at"]}
        return {"update": {"claims": row["claims"], "generated_at": row["generated_at"],
                            "week_start": row["week_start"], "week_end": row["week_end"], "timezone": row["timezone"]},
                "stale": stale, "withheld": False}

    async def evidence(self, user_id, project_id, key):
        snapshot = await project_snapshot(user_id, project_id, evidence_keys=[key], select_candidates=False)
        item = snapshot["items"].get(key)
        if not item:
            raise HTTPException(404, "Evidence unavailable")
        return {k: item[k] for k in ("id", "kind", "text", "href", "section", "record_id", "occurred_at", "recorded_at")}

    async def generate(self, user_id, project_id, request_id, email=None):
        await project_service.get(user_id, project_id)
        previous = await db.query_one("SELECT request_id FROM project_updates WHERE user_id = $1 AND project_id = $2", user_id, project_id)
        if previous and previous["request_id"] == request_id:
            return await self.get(user_id, project_id)
        await check_monthly_ai_budget(user_id, email)
        token = uuid4()
        claimed = await db.query_one(
            "UPDATE projects SET update_generation_token = $3, update_generation_started_at = now() "
            "WHERE user_id = $1 AND id = $2 AND (update_generation_token IS NULL "
            "OR update_generation_started_at < now() - interval '2 minutes') RETURNING id",
            user_id, project_id, token,
        )
        if not claimed:
            raise HTTPException(409, "An update is already being generated")
        try:
            # Recheck after the claim: a concurrent retry may have completed
            # between the initial read and this request acquiring the lease.
            previous = await db.query_one("SELECT request_id FROM project_updates WHERE user_id = $1 AND project_id = $2", user_id, project_id)
            if previous and previous["request_id"] == request_id:
                return await self.get(user_id, project_id)
            snapshot = await project_snapshot(user_id, project_id)
            try:
                claims = await generate_claims(user_id, snapshot)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise HTTPException(502, "Could not generate a valid project update. Your last successful update is preserved.") from None
            current = await project_snapshot(user_id, project_id, evidence_keys=[item["id"] for item in snapshot["selected"]], select_candidates=False)
            if current["fingerprint"] != snapshot["fingerprint"] or current["week_start"] != snapshot["week_start"]:
                raise HTTPException(409, "Project evidence changed during generation. Generate again with the latest evidence.")
            manifest = {item["id"]: {k: snapshot["items"][item["id"]][k] for k in
                                    ("kind", "version", "content_hash", "occurred_at", "recorded_at", "section", "record_id")}
                        for item in snapshot["selected"]}
            saved = await db.query_one(
                "INSERT INTO project_updates (user_id, project_id, request_id, claims, evidence_manifest, fingerprint, model, prompt_version, week_start, week_end, timezone) "
                "SELECT $1, $2, $4, $5, $6, $7, $8, $9, $10, $11, $12 FROM projects "
                "WHERE user_id = $1 AND id = $2 AND update_generation_token = $3 "
                "ON CONFLICT (user_id, project_id) DO UPDATE SET request_id = EXCLUDED.request_id, claims = EXCLUDED.claims, "
                "evidence_manifest = EXCLUDED.evidence_manifest, fingerprint = EXCLUDED.fingerprint, model = EXCLUDED.model, "
                "prompt_version = EXCLUDED.prompt_version, generated_at = now(), week_start = EXCLUDED.week_start, week_end = EXCLUDED.week_end, timezone = EXCLUDED.timezone RETURNING *",
                user_id, project_id, token, request_id, claims, manifest, snapshot["fingerprint"],
                settings.ANTHROPIC_MODEL_SMART, ai.PROMPT_VERSIONS["project_update"], snapshot["week_start"], snapshot["week_end"], snapshot["timezone"],
            )
            if not saved:
                raise HTTPException(409, "Generation expired. Please try again.")
            return self._result(saved, current)
        finally:
            await db.execute("UPDATE projects SET update_generation_token = NULL, update_generation_started_at = NULL "
                             "WHERE user_id = $1 AND id = $2 AND update_generation_token = $3", user_id, project_id, token)


project_update_service = ProjectUpdateService()
