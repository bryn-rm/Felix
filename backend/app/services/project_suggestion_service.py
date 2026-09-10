"""Explicit discovery over Felix's canonical catalog and existing memory retrieval."""

import asyncio
import json
import re
import time
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app import db
from app.config import settings
from app.middleware.rate_limit import check_monthly_ai_budget
from app.prompts._helpers import wrap_untrusted
from app.prompts.project_suggestions import PROJECT_SUGGESTIONS_PROMPT
from app.services import ai_service as ai, memory_service as memory
from app.services.chat_tools import _search_local_email_cache, _search_terms
from app.services.project_evidence_service import digest, project_snapshot
from app.services.project_service import ASSOCIATIONS, CATALOG, CATALOG_TEMPLATE, project_service

FEATURE = "project_suggestions"
MAX_CANDIDATES = 18
MAX_SUGGESTIONS = 5
MAX_INPUT_CHARACTERS = 60000
THRESHOLD = 0.85
STOP_WORDS = set("this that with from have will your about project meeting email status notes none current confirmed scope title description untitled".split())


def key(source):
    return f"{source['kind']}:{source['source_id']}"


def terms_for(context):
    # Distinct bounded literal words, ordered by their prominence in context.
    words = re.findall(r"[^\W_][\w@.+-]{3,}", " ".join(c["text"] for c in context).lower())
    return list(dict.fromkeys(w for w in words if w not in STOP_WORDS))[:32]


async def context_for(user_id, project_id, manifest=None):
    snapshot = await project_snapshot(user_id, project_id, evidence_keys=manifest or (), select_candidates=manifest is None, context_only=True)
    if manifest is not None:
        return {k: digest(snapshot["items"][k]["text"]) for k in manifest if k in snapshot["items"]}
    selected = []
    size = 0
    for item in snapshot["selected"]:
        full = snapshot["items"][item["id"]]
        if item["kind"] not in {"project", "scope", "decision", "email", "meeting", "meeting_summary", "commitment"} or not full["candidate"]:
            continue
        if item["kind"] in {"scope", "decision"} and full["priority"] == 5:
            continue
        text = item["text"]
        if len(selected) == 16:
            break
        if size + len(text) > 12000:
            continue
        selected.append({"id": item["id"], "text": text})
        size += len(text)
    return selected


async def live_sources(user_id, sources):
    """Resolve only requested identities; never trust episode or stored prose."""
    if not sources:
        return []
    ids = {kind: [s["source_id"] for s in sources if s["kind"] == kind] for kind in ("email_thread", "meeting", "commitment")}
    catalog = CATALOG_TEMPLATE.format(
        thread_scope="AND thread_id = ANY($2::text[])",
        meeting_scope="AND id::text = ANY($3::text[])",
        commitment_scope="AND id::text = ANY($4::text[])",
    )
    # One round trip for the bounded batch; each excerpt follows the canonical
    # ownership/access gate and uses the existing source indexes.
    rows = await db.query(catalog + """
        SELECT c.*, CASE c.kind
            WHEN 'email_thread' THEN (SELECT string_agg(COALESCE(e.text, ''), E'\\n' ORDER BY e.at DESC NULLS LAST, e.id)
                FROM (SELECT left(body, 1200) AS text, received_at AS at, id
                      FROM emails WHERE user_id = $1 AND thread_id = c.source_id
                      UNION ALL SELECT left(body, 1200), sent_at, id
                      FROM sent_emails WHERE user_id = $1 AND thread_id = c.source_id
                      ORDER BY at DESC NULLS LAST, id LIMIT 2) e)
            WHEN 'meeting' THEN (SELECT COALESCE(left(m.user_notes, 1000), '') || E'\\n' ||
                COALESCE((SELECT left(s.tldr, 600) FROM meeting_summaries s
                          WHERE s.user_id = $1 AND s.meeting_id = m.id
                          ORDER BY s.created_at DESC, s.id DESC LIMIT 1), '')
                FROM meetings m WHERE m.user_id = $1 AND m.id::text = c.source_id
                AND EXISTS (SELECT 1 FROM settings WHERE user_id = $1 AND meeting_capture_mode IS TRUE))
            ELSE left(c.title, 1600) END AS preview
        FROM catalog c
    """, user_id, ids["email_thread"], ids["meeting"], ids["commitment"])
    for row in rows:
        # Plain text on both sides of quote validation, serialized only once.
        row["preview"] = re.sub(r"<[^>]*>", " ", (row["preview"] or "")[:1600]).strip()[:1600]
        row["title"] = row["title"][:500]
        row["detail"] = (row["detail"] or "")[:300]
        row["text"] = "\n".join(f"{k}: {row[k] or ''}" for k in ("title", "detail", "occurred_at", "status", "preview"))
    by_key = {key(r): r for r in rows if "text" in r}
    return [by_key[key(s)] for s in sources if key(s) in by_key]


async def candidates_for(user_id, project_id, context):
    project = await project_service.get(user_id, project_id)
    # Reuse the existing indexed local body/subject/participant search. Never
    # invoke the chat tool's live Gmail fallback or add a second email index.
    local_mail = await _search_local_email_cache(user_id, project["name"], _search_terms(project["name"]), MAX_CANDIDATES)
    started = time.monotonic()
    episodes = []
    try:
        episodes = await asyncio.wait_for(memory.retrieve_episodes(
            user_id=user_id, query="\n".join(c["text"] for c in context)[:4000], top_k=18,
        ), timeout=3)
    except Exception:
        pass  # Existing memory is optional; canonical catalog search still works.
    await memory.log_memory_op(user_id=user_id, operation="retrieve", feature=FEATURE,
                                episodes_hit=len(episodes), latency_ms=int((time.monotonic() - started) * 1000))
    # Existing producers store email message IDs (including commitment episodes).
    # Use IDs only; stale memory summaries NEVER become model input.
    email_ids = [str(e["source_id"]) for e in episodes if e.get("source_type") == "email" and e.get("source_id")]
    email_ids.extend(str(e["id"]) for e in local_mail)
    rows = await db.query(
        CATALOG + ", associations AS (" + ASSOCIATIONS + "), memory_threads AS ("
        "SELECT thread_id FROM emails WHERE user_id = $1 AND id = ANY($4::text[]) UNION "
        "SELECT thread_id FROM sent_emails WHERE user_id = $1 AND id = ANY($4::text[])), related_commitments AS ("
        "SELECT c.id::text AS source_id FROM commitments c WHERE c.user_id = $1 AND ("
        "EXISTS (SELECT 1 FROM project_meeting_links l WHERE l.user_id = $1 AND l.project_id = $2 AND l.meeting_id = c.source_meeting_id "
        "AND EXISTS (SELECT 1 FROM settings WHERE user_id = $1 AND meeting_capture_mode IS TRUE)) OR "
        "EXISTS (SELECT 1 FROM emails e JOIN project_thread_links l ON l.user_id = e.user_id AND l.thread_id = e.thread_id "
        "WHERE e.user_id = $1 AND l.user_id = $1 AND l.project_id = $2 AND c.source_kind = 'inbound' AND e.id = c.source_email_id) OR "
        "EXISTS (SELECT 1 FROM sent_emails e JOIN project_thread_links l ON l.user_id = e.user_id AND l.thread_id = e.thread_id "
        "WHERE e.user_id = $1 AND l.user_id = $1 AND l.project_id = $2 AND c.source_kind = 'sent' AND e.id = c.source_email_id))) "
        "SELECT c.*, (SELECT count(*) FROM unnest($3::text[]) t WHERE strpos(lower(c.title || ' ' || COALESCE(c.detail, '')), t) > 0) "
        "+ CASE WHEN c.kind = 'commitment' AND c.source_id IN (SELECT source_id FROM related_commitments) THEN 4 ELSE 0 END AS matches "
        "FROM catalog c WHERE NOT EXISTS (SELECT 1 FROM associations a WHERE a.user_id = $1 AND a.project_id = $2 AND a.kind = c.kind AND a.source_id = c.source_id) "
        "AND NOT EXISTS (SELECT 1 FROM project_suggestions s WHERE s.user_id = $1 AND s.project_id = $2 AND s.kind = c.kind AND s.source_id = c.source_id AND s.state IN ('dismissed', 'accepted')) "
        "AND (EXISTS (SELECT 1 FROM unnest($3::text[]) t WHERE strpos(lower(c.title || ' ' || COALESCE(c.detail, '')), t) > 0) "
        "OR (c.kind = 'email_thread' AND c.source_id IN (SELECT thread_id FROM memory_threads)) "
        "OR (c.kind = 'commitment' AND c.source_id IN (SELECT source_id FROM related_commitments))) "
        "ORDER BY matches DESC, c.occurred_at DESC NULLS LAST, c.kind, c.source_id LIMIT $5",
        user_id, project_id, terms_for(context), email_ids, MAX_CANDIDATES,
    )
    candidates = await live_sources(user_id, rows)
    while candidates and len(model_input(context, candidates)) > MAX_INPUT_CHARACTERS:
        candidates.pop()
    return candidates


def model_input(context, candidates):
    return json.dumps({"context": context, "candidates": [{"id": key(c), "text": c["text"]} for c in candidates]}, ensure_ascii=False)


class Judgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    candidate_id: str
    score: float = Field(ge=0, le=1)
    explanation: str = Field(min_length=1, max_length=300)
    candidate_quote: str = Field(min_length=5, max_length=300)
    context_id: str
    context_quote: str = Field(min_length=5, max_length=300)


class Judgements(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    suggestions: list[object]


def validate_judgements(raw, context, candidates):
    parsed = Judgements.model_validate(json.loads(ai._strip_markdown_fences(raw)))
    texts = {key(c): c["text"] for c in candidates}
    context_texts = {c["id"]: c["text"] for c in context}
    seen = set()
    result = []
    for raw_item in parsed.suggestions:
        try:
            item = Judgement.model_validate(raw_item)
        except ValidationError:
            continue
        if item.score < THRESHOLD:
            continue
        if (item.candidate_id in seen or item.candidate_id not in texts or item.context_id not in context_texts
                or not item.explanation.strip() or not item.candidate_quote.strip() or not item.context_quote.strip()
                or item.candidate_quote not in texts[item.candidate_id] or item.context_quote not in context_texts[item.context_id]):
            continue
        seen.add(item.candidate_id)
        result.append(item.model_dump())
    return sorted(result, key=lambda item: -item["score"])[:MAX_SUGGESTIONS]


async def judge(user_id, context, candidates):
    response = None
    started = time.monotonic()
    success = parse_error = False
    error_message = None
    try:
        response = await ai.call_fast(feature=FEATURE, max_tokens=2400, timeout=40,
            system=PROJECT_SUGGESTIONS_PROMPT, messages=[{"role": "user", "content": wrap_untrusted(
                model_input(context, candidates), "association_evidence")}])
        try:
            result = validate_judgements(response.text, context, candidates)
        except (ValueError, TypeError, AttributeError):
            parse_error = True
            raise
        success = True
        return result
    except BaseException as error:
        error_message = type(error).__name__
        raise
    finally:
        await ai.log_ai_call(feature=FEATURE, model=settings.AI_MODEL_FAST, response=response, started_at=started,
                             user_id=user_id, success=success, parse_error=parse_error,
                             error_message=error_message, quota_scope="interactive")


class ProjectSuggestionService:
    async def list(self, user_id, project_id):
        # Validate without holding a project lock across source reads. At commit,
        # compare the discovery generation under the same lock as its writer.
        # IDs alone are insufficient: discovery upserts reuse suggestion IDs.
        for _ in range(3):
            project = await db.query_one(
                "SELECT discovery_completed_at, "
                "EXISTS (SELECT 1 FROM project_suggestions s WHERE s.user_id = $1 AND s.project_id = $2 "
                "AND s.state = 'invalid' AND s.generated_at >= p.discovery_completed_at) AS stale, "
                "(SELECT count(*) FROM project_suggestions s WHERE s.user_id = $1 AND s.project_id = $2 AND s.state = 'dismissed') AS dismissed_count "
                "FROM projects p WHERE p.user_id = $1 AND p.id = $2", user_id, project_id,
            )
            if not project:
                raise HTTPException(404, "Project not found")
            response = {"suggestions": [], "last_discovered_at": project["discovery_completed_at"],
                        "stale": project["stale"], "dismissed_count": project["dismissed_count"]}
            rows = await db.query("SELECT * FROM project_suggestions WHERE user_id = $1 AND project_id = $2 AND state = 'pending' ORDER BY score DESC, id LIMIT 5", user_id, project_id)
            if not rows:
                return response
            # Every model input may have influenced every explanation.
            manifest = rows[0]["candidate_manifest"]
            current = await live_sources(user_id, manifest)
            hashes = {key(c): digest(c["text"]) for c in current}
            context_manifest = rows[0]["context_manifest"]
            valid = (await context_for(user_id, project_id, context_manifest) == context_manifest
                     and all(hashes.get(key(c)) == c["hash"] for c in manifest)
                     and rows[0]["model"] == settings.AI_MODEL_FAST and rows[0]["prompt_version"] == ai.PROMPT_VERSIONS[FEATURE])
            linked_keys = {key(c) for c in await db.query(ASSOCIATIONS, user_id, project_id)} if valid else set()
            pool = await db.get_pool()
            async with pool.acquire() as conn, conn.transaction():
                latest = await conn.fetchrow("SELECT discovery_completed_at FROM projects WHERE user_id = $1 AND id = $2 FOR UPDATE", user_id, project_id)
                if not latest:
                    raise HTTPException(404, "Project not found")
                if latest["discovery_completed_at"] != project["discovery_completed_at"]:
                    continue
                pending = await conn.fetch("SELECT id FROM project_suggestions WHERE user_id = $1 AND project_id = $2 AND state = 'pending' AND id = ANY($3::uuid[])", user_id, project_id, [r["id"] for r in rows])
                pending_ids = {r["id"] for r in pending}
                if not valid:
                    await conn.execute("UPDATE project_suggestions SET state = 'invalid', explanation = '', context_manifest = '{}'::jsonb, candidate_manifest = '[]'::jsonb "
                                       "WHERE user_id = $1 AND project_id = $2 AND state = 'pending' AND id = ANY($3::uuid[])", user_id, project_id, list(pending_ids))
                    response["stale"] = response["stale"] or bool(pending_ids)
                    return response
            by_key = {key(c): c for c in current}
            for row in rows:
                if row["id"] not in pending_ids or key(row) in linked_keys:
                    continue
                source = by_key[key(row)]
                response["suggestions"].append({**{k: v for k, v in source.items() if k != "text"}, "preview": source["preview"][:300],
                                                "id": row["id"], "explanation": row["explanation"]})
            return response
        raise HTTPException(409, "Suggestions changed. Please refresh.")

    async def discover(self, user_id, project_id, request_id, email=None):
        try:
            async with asyncio.timeout(75):
                return await self._discover(user_id, project_id, request_id, email)
        except TimeoutError:
            raise HTTPException(504, "Discovery timed out. Please retry.") from None

    async def _discover(self, user_id, project_id, request_id, email=None):
        await project_service.get(user_id, project_id)
        token = uuid4()
        claimed = await db.query_one(
            "UPDATE projects SET discovery_token = $3, discovery_started_at = now() WHERE user_id = $1 AND id = $2 "
            "AND (discovery_token IS NULL OR discovery_started_at < now() - interval '2 minutes') RETURNING discovery_request_id",
            user_id, project_id, token,
        )
        if not claimed:
            raise HTTPException(409, "Discovery is already running. Please try again shortly.")
        try:
            if claimed["discovery_request_id"] == request_id:
                return await self.list(user_id, project_id)
            context = await context_for(user_id, project_id)
            candidates = await candidates_for(user_id, project_id, context)
            context_manifest = {c["id"]: digest(c["text"]) for c in context}
            candidate_manifest = [{"kind": c["kind"], "source_id": c["source_id"], "hash": digest(c["text"])} for c in candidates]
            results = []
            if candidates:
                await check_monthly_ai_budget(user_id, email)
                # Refresh authorization/content immediately before model use.
                current = await live_sources(user_id, candidates)
                if ({key(c): digest(c["text"]) for c in current} != {key(c): c["hash"] for c in candidate_manifest}
                        or await context_for(user_id, project_id, context_manifest) != context_manifest):
                    raise HTTPException(409, "Sources changed. Please run discovery again.")
                try:
                    results = await judge(user_id, context, current)
                except Exception:
                    raise HTTPException(502, "Could not find related items. Please retry; your saved suggestions are preserved.") from None
            pool = await db.get_pool()
            async with pool.acquire() as conn, conn.transaction():
                claim = await conn.fetchval("SELECT id FROM projects WHERE user_id = $1 AND id = $2 AND discovery_token = $3 FOR UPDATE", user_id, project_id, token)
                if not claim:
                    raise HTTPException(409, "Discovery expired. Please retry.")
                await conn.execute("UPDATE project_suggestions SET state = 'invalid', explanation = '', context_manifest = '{}'::jsonb, candidate_manifest = '[]'::jsonb WHERE user_id = $1 AND project_id = $2 AND state = 'pending'", user_id, project_id)
                by_key = {key(c): c for c in candidates}
                for result in results:
                    source = by_key[result["candidate_id"]]
                    await conn.execute(
                        "INSERT INTO project_suggestions (user_id, project_id, kind, source_id, explanation, score, context_manifest, candidate_manifest, model, prompt_version) "
                        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) ON CONFLICT (user_id, project_id, kind, source_id) DO UPDATE "
                        "SET state = 'pending', explanation = EXCLUDED.explanation, score = EXCLUDED.score, context_manifest = EXCLUDED.context_manifest, "
                        "candidate_manifest = EXCLUDED.candidate_manifest, model = EXCLUDED.model, prompt_version = EXCLUDED.prompt_version, generated_at = now() "
                        "WHERE project_suggestions.user_id = $1 AND project_suggestions.state NOT IN ('dismissed', 'accepted')",
                        user_id, project_id, source["kind"], source["source_id"], result["explanation"], result["score"],
                        context_manifest, candidate_manifest, settings.AI_MODEL_FAST, ai.PROMPT_VERSIONS[FEATURE],
                    )
                await conn.execute("UPDATE projects SET discovery_request_id = $3, discovery_completed_at = now() WHERE user_id = $1 AND id = $2", user_id, project_id, request_id)
            return await self.list(user_id, project_id)
        finally:
            await db.execute("UPDATE projects SET discovery_token = NULL, discovery_started_at = NULL WHERE user_id = $1 AND id = $2 AND discovery_token = $3", user_id, project_id, token)

    async def resolve(self, user_id, project_id, suggestion_id, accept):
        await project_service.get(user_id, project_id)
        # Revalidate both actions so a stale card cannot hide a source permanently.
        await self.list(user_id, project_id)
        failure = False
        result = None
        pool = await db.get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.fetchval("SELECT id FROM projects WHERE user_id = $1 AND id = $2 FOR UPDATE", user_id, project_id)
            row = await conn.fetchrow("SELECT * FROM project_suggestions WHERE user_id = $1 AND project_id = $2 AND id = $3 FOR UPDATE", user_id, project_id, suggestion_id)
            if not row:
                raise HTTPException(404, "Suggestion unavailable")
            if accept:
                if row["state"] not in {"pending", "accepted"}:
                    raise HTTPException(409, "Suggestion no longer available. Find related items again.")
                # Accepted replay must not re-link a subsequently unlinked source.
                if row["state"] == "accepted":
                    return {"accepted": True}
                try:
                    async with conn.transaction():
                        result = await project_service.link_in_transaction(conn, user_id, project_id, row["kind"], row["source_id"])
                except HTTPException as error:
                    if error.status_code != 404:
                        raise
                    failure = True
            elif row["state"] not in {"pending", "dismissed"}:
                raise HTTPException(409, "Suggestion no longer available. Find related items again.")
            await conn.execute("UPDATE project_suggestions SET state = $4, explanation = '', context_manifest = '{}'::jsonb, candidate_manifest = '[]'::jsonb WHERE user_id = $1 AND project_id = $2 AND id = $3",
                               user_id, project_id, suggestion_id, "invalid" if failure else "accepted" if accept else "dismissed")
        if failure:
            raise HTTPException(404, "Source unavailable")
        return result or {"dismissed": True}

    async def reset_dismissals(self, user_id, project_id):
        pool = await db.get_pool()
        async with pool.acquire() as conn, conn.transaction():
            if not await conn.fetchval("SELECT id FROM projects WHERE user_id = $1 AND id = $2 FOR UPDATE", user_id, project_id):
                raise HTTPException(404, "Project not found")
            # Do not restore generated prose; make these sources eligible for the
            # next explicit discovery. Preserve accepted rows and source links.
            await conn.execute("DELETE FROM project_suggestions WHERE user_id = $1 AND project_id = $2 AND state = 'dismissed'", user_id, project_id)
        return {"reset": True}

    async def thread(self, user_id, project_id, suggestion_id):
        await project_service.get(user_id, project_id)
        # This endpoint returns canonical mail only, never generated prose.
        # Source ownership is enforced again in the mail query below.
        row = await db.query_one("SELECT source_id FROM project_suggestions WHERE user_id = $1 AND project_id = $2 AND id = $3 AND kind = 'email_thread' AND state = 'pending'", user_id, project_id, suggestion_id)
        if not row:
            raise HTTPException(404, "Suggestion unavailable")
        return await db.query(
            "SELECT id, subject, from_email AS participant, body, received_at AS occurred_at, 'inbound' AS direction FROM emails WHERE user_id = $1 AND thread_id = $2 "
            "UNION ALL SELECT id, subject, array_to_string(to_emails, ', '), body, sent_at, 'sent' FROM sent_emails WHERE user_id = $1 AND thread_id = $2 "
            "ORDER BY occurred_at DESC NULLS LAST, id LIMIT 100", user_id, row["source_id"],
        )


project_suggestion_service = ProjectSuggestionService()
