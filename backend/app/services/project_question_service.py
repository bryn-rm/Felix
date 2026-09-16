"""On-demand project answers, with cited evidence and a revalidated latest result."""

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
from app.prompts.project_question import PROJECT_QUESTION_PROMPT
from app.services import ai_service as ai
from app.services.project_evidence_service import project_snapshot
from app.services.project_service import project_service
from app.services.project_update_service import Citation

ANSWER_TIMEOUT_SECONDS = 75


class AnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["answer", "conflict"]
    text: str = Field(min_length=1, max_length=700)
    citations: list[Citation] = Field(min_length=1, max_length=4)


class AnswerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    claims: list[AnswerClaim] = Field(max_length=10)
    unanswered: list[str] = Field(max_length=5)


def validate_answer(raw, selected):
    output = AnswerOutput.model_validate(json.loads(ai._strip_markdown_fences(raw)))
    evidence = {item["id"]: item for item in selected}
    if not output.claims and not output.unanswered:
        raise ValueError("Empty answer")
    for question in output.unanswered:
        if not question.strip() or len(question) > 300:
            raise ValueError("Invalid unanswered question")
    for claim in output.claims:
        if not claim.text.strip():
            raise ValueError("Empty claim")
        for citation in claim.citations:
            if (citation.evidence_id not in evidence or not citation.quote.strip()
                    or citation.quote not in evidence[citation.evidence_id]["text"]):
                raise ValueError("Unsupported citation")
    return output.model_dump()


async def generate_answer(user_id, question, snapshot):
    response = None
    started = time.monotonic()
    success = parse_error = False
    error_message = None
    try:
        payload = {key: snapshot[key] for key in ("as_of", "timezone", "omitted_count")}
        payload["evidence"] = snapshot["selected"]
        async with asyncio.timeout(60):
            response = await ai.client.messages.create(
                model=settings.ANTHROPIC_MODEL_SMART, max_tokens=4000,
                **ai.thinking_kwarg(settings.ANTHROPIC_MODEL_SMART), timeout=55.0,
                system=PROJECT_QUESTION_PROMPT,
                messages=[{"role": "user", "content": wrap_untrusted(question, "project_question") + "\n"
                           + wrap_untrusted(json.dumps(payload, default=str), "project_evidence")}],
            )
        try:
            if response.stop_reason not in ("end_turn", "stop_sequence"):
                raise ValueError("Incomplete or refused answer")
            raw = "".join(block.text for block in response.content if block.type == "text")
            answer = validate_answer(raw, snapshot["selected"])
        except (ValueError, TypeError, AttributeError):
            parse_error = True
            raise
        success = True
        return answer
    except BaseException as error:
        error_message = type(error).__name__
        raise
    finally:
        await ai.log_ai_call(feature="project_question", model=settings.ANTHROPIC_MODEL_SMART,
                             response=response, started_at=started, user_id=user_id, success=success,
                             parse_error=parse_error, error_message=error_message, quota_scope="interactive")


class ProjectQuestionService:
    async def get(self, user_id, project_id):
        await project_service.get(user_id, project_id)
        row = await self._saved(user_id, project_id)
        if not row:
            return {"answer": None, "stale": False, "withheld": False}
        snapshot = await project_snapshot(user_id, project_id, evidence_keys=row["evidence_manifest"], select_candidates=False, week_scoped=False)
        return self._result(row, snapshot)

    async def _saved(self, user_id, project_id):
        return await db.query_one("SELECT * FROM project_answers WHERE user_id = $1 AND project_id = $2", user_id, project_id)

    def _result(self, row, snapshot):
        # Every model input can influence prose, even if it wasn't cited.
        withheld = any(key not in snapshot["items"] for key in row["evidence_manifest"])
        stale = (snapshot["fingerprint"] != row["fingerprint"]
                 or row["prompt_version"] != ai.PROMPT_VERSIONS["project_question"]
                 or row["model"] != settings.ANTHROPIC_MODEL_SMART)
        if withheld:
            return {"answer": None, "stale": True, "withheld": True, "question": row["question"]}
        return {"answer": {**row["answer"], "question": row["question"], "request_id": str(row["request_id"]),
                           "generated_at": row["generated_at"], "omitted_count": row["omitted_count"]},
                "stale": stale, "withheld": False}

    def _check_replay(self, row, request_id, question):
        if row and row["request_id"] == request_id:
            if row["question"] != question:
                raise HTTPException(409, "This request ID was already used for another question.")
            return True
        return False

    async def ask(self, user_id, project_id, request_id, question, email=None):
        await project_service.get(user_id, project_id)
        if self._check_replay(await self._saved(user_id, project_id), request_id, question):
            return await self.get(user_id, project_id)
        await check_monthly_ai_budget(user_id, email)
        token = uuid4()
        claimed = await db.query_one(
            "UPDATE projects SET question_generation_token = $3, question_generation_started_at = now() "
            "WHERE user_id = $1 AND id = $2 AND (question_generation_token IS NULL "
            "OR question_generation_started_at < now() - interval '2 minutes') RETURNING id",
            user_id, project_id, token,
        )
        if not claimed:
            raise HTTPException(409, "A project answer is already being generated. Please wait and retry.")
        try:
            async with asyncio.timeout(ANSWER_TIMEOUT_SECONDS):
                if self._check_replay(await self._saved(user_id, project_id), request_id, question):
                    return await self.get(user_id, project_id)
                snapshot = await project_snapshot(user_id, project_id, question=question, week_scoped=False)
                try:
                    answer = await generate_answer(user_id, question, snapshot)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    raise HTTPException(502, "Could not generate a valid project answer. Please retry. Your last answer is preserved.") from None
                manifest = [item["id"] for item in snapshot["selected"]]
                current = await project_snapshot(user_id, project_id, evidence_keys=manifest, select_candidates=False, week_scoped=False)
                if (current["fingerprint"] != snapshot["fingerprint"]
                        or any(key not in current["items"] for key in manifest)):
                    raise HTTPException(409, "Project evidence changed while answering. Please ask again.")
                saved = await db.query_one(
                    "INSERT INTO project_answers (user_id, project_id, request_id, question, answer, evidence_manifest, fingerprint, omitted_count, model, prompt_version) "
                    "SELECT $1, $2, $4, $5, $6, $7, $8, $9, $10, $11 FROM projects "
                    "WHERE user_id = $1 AND id = $2 AND question_generation_token = $3 "
                    "ON CONFLICT (user_id, project_id) DO UPDATE SET request_id = EXCLUDED.request_id, question = EXCLUDED.question, "
                    "answer = EXCLUDED.answer, evidence_manifest = EXCLUDED.evidence_manifest, fingerprint = EXCLUDED.fingerprint, "
                    "omitted_count = EXCLUDED.omitted_count, model = EXCLUDED.model, prompt_version = EXCLUDED.prompt_version, generated_at = now() RETURNING *",
                    user_id, project_id, token, request_id, question, answer, manifest, snapshot["fingerprint"],
                    snapshot["omitted_count"], settings.ANTHROPIC_MODEL_SMART, ai.PROMPT_VERSIONS["project_question"],
                )
                if not saved:
                    raise HTTPException(409, "Answer generation expired. Please retry.")
                return self._result(saved, current)
        except TimeoutError:
            raise HTTPException(504, "Answering took too long. Please retry.") from None
        finally:
            await db.execute("UPDATE projects SET question_generation_token = NULL, question_generation_started_at = NULL "
                             "WHERE user_id = $1 AND id = $2 AND question_generation_token = $3", user_id, project_id, token)


project_question_service = ProjectQuestionService()
