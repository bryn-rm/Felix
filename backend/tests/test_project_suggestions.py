"""Phase 3 integration: real canonical sources, transactions, authorization/RLS."""
import asyncio
import json
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import HTTPException

from app import db
from app.services import project_suggestion_service as module
from app.services.project_suggestion_service import project_suggestion_service as svc
from app.services.project_service import project_service
from app.services.project_update_service import project_update_service
from app.services.project_evidence_service import project_snapshot
from tests.test_projects import project_db, client, create, USER, OTHER  # noqa: F401

pytestmark = pytest.mark.parametrize("project_db", ["phase3-fresh", "phase3-upgrade"], indirect=True)


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    monkeypatch.setattr(module.memory, "retrieve_episodes", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.memory, "log_memory_op", AsyncMock())
    monkeypatch.setattr(module, "check_monthly_ai_budget", AsyncMock())
    monkeypatch.setattr(module.ai, "log_ai_call", AsyncMock())

    async def respond(**kwargs):
        content = kwargs["messages"][0]["content"]
        start, end = content.index('{"context":'), content.rindex('}') + 1
        payload = json.loads(content[start:end])
        context = payload["context"][0]
        suggestions = [{"candidate_id": c["id"], "score": 0.94,
                        "explanation": "Discusses the launch work described by this project.",
                        "candidate_quote": c["text"][0:30], "context_id": context["id"],
                        "context_quote": context["text"][0:30]} for c in payload["candidates"][:5]]
        return module.ai.FastResponse(json.dumps({"suggestions": suggestions}), module.ai.FastUsage(200, 100))
    monkeypatch.setattr(module.ai, "call_fast", AsyncMock(side_effect=respond))


async def discovery(client, pid, request_id=None):
    response = await client.post(f"/projects/{pid}/suggestions/discover", json={"request_id": str(request_id or uuid4())})
    assert response.status_code == 200, response.text
    return response.json()


async def test_three_kinds_canonical_threads_and_repeat(client, project_db):
    conn = project_db["conn"]
    await conn.execute("INSERT INTO sent_emails (id, user_id, thread_id, subject, sent_at) VALUES ('sent', $1, 'thread', 'Re: Launch', now())", USER)
    pid = await create(client)
    result = await discovery(client, pid)
    assert {s["kind"] for s in result["suggestions"]} == {"email_thread", "meeting", "commitment"}
    assert len(result["suggestions"]) == 3
    assert all(s["preview"] is not None and "score" not in s for s in result["suggestions"])
    first = {s["id"] for s in result["suggestions"]}
    assert {s["id"] for s in (await discovery(client, pid))["suggestions"]} == first
    assert await conn.fetchval("SELECT count(*) FROM project_suggestions WHERE user_id = $1 AND project_id = $2", USER, UUID(pid)) == 3
    assert module.ai.call_fast.call_args.kwargs["feature"] == "project_suggestions"
    assert module.ai.log_ai_call.call_args.kwargs["model"] == module.settings.AI_MODEL_FAST
    assert module.ai.log_ai_call.call_args.kwargs["quota_scope"] == "interactive"
    assert module.ai.log_ai_call.call_args.kwargs["success"]
    module.check_monthly_ai_budget.assert_awaited()
    module.memory.retrieve_episodes.assert_awaited()


async def test_linked_exclusion_context_scope_decisions_and_memory_ids(client, project_db, monkeypatch):
    pid = UUID(await create(client, "Orion"))
    await project_service.link(USER, pid, "email_thread", "thread")
    await project_db["conn"].execute("UPDATE emails SET body = 'Launch coordination with Zephyr design' WHERE user_id = $1 AND id = 'inbound'", USER)
    from app.services.project_knowledge_service import project_knowledge_service as knowledge
    await knowledge.scope(USER, pid, "Confirm Zephyr work", 0)
    context = await module.context_for(USER, pid)
    assert any("Zephyr" in c["text"] for c in context)
    candidates = await module.candidates_for(USER, pid, context)
    assert {c["kind"] for c in candidates} == {"meeting", "commitment"}
    assert all(c["source_id"] != "thread" for c in candidates)
    # Memory provides an ID only; never send its stale/secret summary onward.
    monkeypatch.setattr(module.memory, "retrieve_episodes", AsyncMock(return_value=[
        {"source_type": "email", "source_id": "inbound", "summary": "SECRET EPISODE"},
        {"source_type": "email", "source_id": "missing", "summary": "DELETED"},
    ]))
    other_pid = UUID(await create(client, "Unrelated"))
    candidates = await module.candidates_for(USER, other_pid, [{"id": "project", "text": "Unrelated"}])
    assert [c["source_id"] for c in candidates] == ["thread"]
    assert "SECRET" not in json.dumps(candidates, default=str)


async def test_accept_canonical_idempotent_stale_update_and_dismiss_isolated(client, project_db):
    pid = UUID(await create(client))
    snapshot = await project_snapshot(USER, pid)
    # Real Phase 2 persisted update, no smart-model call needed for this invariant.
    await db.insert("project_updates", {"user_id": USER, "project_id": pid, "request_id": uuid4(), "claims": [],
        "evidence_manifest": {}, "fingerprint": snapshot["fingerprint"], "model": module.settings.ANTHROPIC_MODEL_SMART,
        "prompt_version": module.ai.PROMPT_VERSIONS["project_update"], "week_start": snapshot["week_start"],
        "week_end": snapshot["week_end"], "timezone": snapshot["timezone"]})
    result = await discovery(client, pid)
    meeting = next(s for s in result["suggestions"] if s["kind"] == "meeting")
    email = next(s for s in result["suggestions"] if s["kind"] == "email_thread")
    preview = await client.get(f"/projects/{pid}/suggestions/{email['id']}/thread")
    assert preview.status_code == 200 and len(preview.json()["messages"]) == 1
    await svc.resolve(USER, pid, UUID(email["id"]), False)
    assert (await project_snapshot(USER, pid))["fingerprint"] == snapshot["fingerprint"]
    assert not (await project_update_service.get(USER, pid))["stale"]
    await asyncio.gather(*(svc.resolve(USER, pid, UUID(meeting["id"]), True) for _ in range(2)))
    assert (await project_service.detail(USER, pid))["sources"][0]["source_id"] == str(project_db["meeting"])
    assert (await project_update_service.get(USER, pid))["stale"]
    assert len([a for a in await project_service.activity(USER, pid) if a["action"] == "linked"]) == 1
    assert [s["kind"] for s in (await discovery(client, pid))["suggestions"]] == ["commitment"]
    another = await create(client)
    assert "email_thread" in {s["kind"] for s in (await discovery(client, another))["suggestions"]}
    link = (await project_service.detail(USER, pid))["sources"][0]
    await project_service.unlink(USER, pid, "meeting", link["id"])
    await svc.resolve(USER, pid, UUID(meeting["id"]), True)
    assert (await project_service.detail(USER, pid))["source_count"] == 0


@pytest.mark.parametrize("change", ["delete", "disable", "candidate_edit", "linked_context_delete"])
async def test_unavailable_input_withholds_entire_batch(client, project_db, change):
    pid = UUID(await create(client))
    conn = project_db["conn"]
    if change == "linked_context_delete":
        await project_service.link(USER, pid, "meeting", str(project_db["meeting"]))
    result = await discovery(client, pid)
    original = result["suggestions"][0]
    if change in {"delete", "linked_context_delete"}:
        await conn.execute("DELETE FROM meetings WHERE user_id = $1 AND id = $2", USER, project_db["meeting"])
    elif change == "disable":
        await conn.execute("UPDATE settings SET meeting_capture_mode = false WHERE user_id = $1", USER)
    else:
        await conn.execute("UPDATE emails SET subject = 'Changed content' WHERE user_id = $1", USER)
    assert (await svc.list(USER, pid))["suggestions"] == []
    with pytest.raises(HTTPException):
        await svc.resolve(USER, pid, UUID(original["id"]), True)
    assert await conn.fetchval("SELECT count(*) FROM project_suggestions WHERE user_id = $1 AND project_id = $2 AND explanation <> ''", USER, pid) == 0


async def test_revalidate_canonical_path_after_list(client, project_db, monkeypatch):
    pid = UUID(await create(client))
    result = await discovery(client, pid)
    item = next(s for s in result["suggestions"] if s["kind"] == "meeting")
    original = svc.list
    async def disappear(*args):
        result = await original(*args)
        await project_db["conn"].execute("DELETE FROM meetings WHERE user_id = $1 AND id = $2", USER, project_db["meeting"])
        return result
    monkeypatch.setattr(svc, "list", disappear)
    with pytest.raises(HTTPException) as error:
        await svc.resolve(USER, pid, UUID(item["id"]), True)
    assert error.value.status_code == 404
    assert await project_db["conn"].fetchval("SELECT state FROM project_suggestions WHERE user_id = $1 AND id = $2", USER, UUID(item["id"])) == "invalid"
    assert (await project_service.detail(USER, pid))["source_count"] == 0


async def test_cross_user_and_rls(client, project_db):
    pid = await create(client)
    result = await discovery(client, pid)
    assert "Secret" not in json.dumps(result)
    sid = result["suggestions"][0]["id"]
    for method, suffix in [("get", "suggestions"), ("post", f"suggestions/{sid}/accept"), ("post", f"suggestions/{sid}/dismiss"), ("get", f"suggestions/{sid}/thread")]:
        response = await getattr(client, method)(f"/projects/{pid}/{suffix}", headers={"x-test-user": str(OTHER)})
        assert response.status_code == 404
    response = await client.post(f"/projects/{pid}/suggestions/discover", headers={"x-test-user": str(OTHER)}, json={"request_id": str(uuid4())})
    assert response.status_code == 404
    conn = project_db["conn"]
    # Browser roles cannot read cached prose, even as its owner.
    async with conn.transaction():
        await conn.execute("SET LOCAL ROLE authenticated")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.fetch("SELECT * FROM project_suggestions")
    await conn.execute("GRANT SELECT ON project_suggestions TO authenticated")
    async with conn.transaction():
        await conn.execute("SET LOCAL ROLE authenticated")
        await conn.execute("SELECT set_config('request.jwt.claim.sub', $1, true)", str(OTHER))
        assert await conn.fetch("SELECT * FROM project_suggestions") == []
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await conn.execute("INSERT INTO project_suggestions (user_id, project_id, kind, source_id) VALUES ($1,$2,'email_thread','thread')", OTHER, UUID(pid))


async def test_no_candidates_no_call_replay_and_quota(client, project_db, monkeypatch):
    pid = UUID(await create(client, "Xylophone"))
    request_id = uuid4()
    assert (await discovery(client, pid, request_id))["suggestions"] == []
    module.ai.call_fast.assert_not_awaited()
    module.check_monthly_ai_budget.assert_not_awaited()
    await discovery(client, pid, request_id)
    assert module.memory.retrieve_episodes.await_count == 1
    await project_service.edit(USER, pid, {"name": "Launch"})
    monkeypatch.setattr(module, "check_monthly_ai_budget", AsyncMock(side_effect=HTTPException(429, "Quota")))
    with pytest.raises(HTTPException) as error:
        await svc.discover(USER, pid, uuid4())
    assert error.value.status_code == 429
    module.ai.call_fast.assert_not_awaited()
    assert await project_db["conn"].fetchval("SELECT discovery_token FROM projects WHERE user_id = $1 AND id = $2", USER, pid) is None


@pytest.mark.parametrize("failure", [TimeoutError(), ValueError("private provider text")])
async def test_failure_logs_and_preserves_saved_results(client, project_db, monkeypatch, failure):
    pid = UUID(await create(client))
    before = await discovery(client, pid)
    monkeypatch.setattr(module.ai, "call_fast", AsyncMock(side_effect=failure))
    with pytest.raises(HTTPException) as error:
        await svc.discover(USER, pid, uuid4())
    assert error.value.status_code == 502 and "private" not in error.value.detail
    assert {str(s["id"]): s["explanation"] for s in (await svc.list(USER, pid))["suggestions"]} == {s["id"]: s["explanation"] for s in before["suggestions"]}
    assert not module.ai.log_ai_call.call_args.kwargs["success"]
    assert module.ai.log_ai_call.call_args.kwargs["error_message"] == type(failure).__name__
    assert await project_db["conn"].fetchval("SELECT discovery_token FROM projects WHERE user_id = $1 AND id = $2", USER, pid) is None


async def test_model_validation_empty_low_confidence_parse_logging(client, project_db, monkeypatch):
    pid = UUID(await create(client))
    context = [{"id": "project", "text": "Orion website delivery"}]
    candidates = [{"kind": "meeting", "source_id": "one", "text": "Orion website discussion"}]
    good = {"candidate_id": "meeting:one", "score": 0.9, "explanation": "Same website delivery.",
            "candidate_quote": "Orion website", "context_id": "project", "context_quote": "Orion website"}
    assert module.validate_judgements(json.dumps({"suggestions": [good]}), context, candidates)
    assert module.validate_judgements('{"suggestions": []}', context, candidates) == []
    assert module.validate_judgements(json.dumps({"suggestions": [{**good, "score": 0.84}]}), context, candidates) == []
    for bad in [{**good, "candidate_id": "meeting:other"}, {**good, "context_quote": "made up quote"}, {**good, "score": "0.9"}, {**good, "extra": True}, {**good, "explanation": " "}]:
        assert module.validate_judgements(json.dumps({"suggestions": [bad]}), context, candidates) == []
    assert len(module.validate_judgements(json.dumps({"suggestions": [good, good]}), context, candidates)) == 1
    monkeypatch.setattr(module.ai, "call_fast", AsyncMock(return_value=module.ai.FastResponse('bad json', None)))
    with pytest.raises(HTTPException):
        await svc.discover(USER, pid, uuid4())
    assert module.ai.log_ai_call.call_args.kwargs["parse_error"]


async def test_shared_claim_prevents_concurrent_model_work(client, project_db, monkeypatch):
    pid = UUID(await create(client))
    entered, finish = asyncio.Event(), asyncio.Event()
    original = module.ai.call_fast.side_effect
    async def pause(**kwargs):
        entered.set()
        await finish.wait()
        return await original(**kwargs)
    monkeypatch.setattr(module.ai, "call_fast", AsyncMock(side_effect=pause))
    first = asyncio.create_task(svc.discover(USER, pid, uuid4()))
    await entered.wait()
    try:
        with pytest.raises(HTTPException) as error:
            await svc.discover(USER, pid, uuid4())
        assert error.value.status_code == 409
    finally:
        finish.set()
        await first
    assert module.ai.call_fast.await_count == 1


async def test_migration_rerun_preserves_dismissals_and_existing_links(client, project_db):
    pid = UUID(await create(client))
    suggestion = (await discovery(client, pid))["suggestions"][0]
    await svc.resolve(USER, pid, UUID(suggestion["id"]), False)
    conn = project_db["conn"]
    await conn.execute(project_db["migration"].read_text())
    assert await conn.fetchval("SELECT state FROM project_suggestions WHERE user_id = $1 AND id = $2", USER, UUID(suggestion["id"])) == "dismissed"
    assert await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE relname = 'project_suggestions'")
    if project_db["prior_project"]:
        assert (await project_service.detail(USER, project_db["prior_project"]))["source_count"] == 1


async def test_relationship_candidates_from_linked_meeting(client, project_db):
    pid = UUID(await create(client, "Orion"))
    await project_service.link(USER, pid, "meeting", str(project_db["meeting"]))
    await project_db["conn"].execute(
        "UPDATE commitments SET text = 'Deliver the prototype', source_meeting_id = $3 WHERE user_id = $1 AND id = $2",
        USER, project_db["commitment"], project_db["meeting"],
    )
    candidates = await module.candidates_for(USER, pid, [{"id": "project", "text": "Orion"}])
    assert [c["source_id"] for c in candidates] == [str(project_db["commitment"])]


async def test_changed_access_during_generation_never_returns_cached_prose(client, project_db, monkeypatch):
    pid = UUID(await create(client))
    original = module.ai.call_fast.side_effect
    async def disable(**kwargs):
        response = await original(**kwargs)
        await project_db["conn"].execute("UPDATE settings SET meeting_capture_mode = false WHERE user_id = $1", USER)
        return response
    monkeypatch.setattr(module.ai, "call_fast", AsyncMock(side_effect=disable))
    assert (await svc.discover(USER, pid, uuid4()))["suggestions"] == []


async def test_candidate_cap_and_model_no_result(client, project_db, monkeypatch):
    pid = UUID(await create(client))
    conn = project_db["conn"]
    for index in range(25):
        await conn.execute("INSERT INTO meetings (user_id, title) VALUES ($1, $2)", USER, f"Launch topic {index}")
    context = await module.context_for(USER, pid)
    assert len(await module.candidates_for(USER, pid, context)) == module.MAX_CANDIDATES
    # Escaping grows serialized input even when individual excerpts are capped.
    await conn.execute("UPDATE meetings SET user_notes = $2 WHERE user_id = $1", USER, chr(92) * 2000)
    bounded = await module.candidates_for(USER, pid, context)
    assert 0 < len(bounded) <= module.MAX_CANDIDATES
    assert len(module.model_input(context, bounded)) <= module.MAX_INPUT_CHARACTERS
    monkeypatch.setattr(module.ai, "call_fast", AsyncMock(return_value=module.ai.FastResponse('{"suggestions": []}', module.ai.FastUsage(100, 10))))
    assert (await svc.discover(USER, pid, uuid4()))["suggestions"] == []


async def test_access_revoked_before_model_call(client, project_db, monkeypatch):
    pid = UUID(await create(client))
    async def revoke(*args):
        await project_db["conn"].execute("UPDATE settings SET meeting_capture_mode = false WHERE user_id = $1", USER)
    monkeypatch.setattr(module, "check_monthly_ai_budget", revoke)
    with pytest.raises(HTTPException) as error:
        await svc.discover(USER, pid, uuid4())
    assert error.value.status_code == 409
    module.ai.call_fast.assert_not_awaited()


async def test_cancellation_releases_shared_claim(client, project_db, monkeypatch):
    pid = UUID(await create(client))
    entered = asyncio.Event()
    async def pause(**kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(module.ai, "call_fast", AsyncMock(side_effect=pause))
    task = asyncio.create_task(svc.discover(USER, pid, uuid4()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await project_db["conn"].fetchval("SELECT discovery_token FROM projects WHERE user_id = $1 AND id = $2", USER, pid) is None
    assert not module.ai.log_ai_call.call_args.kwargs["success"]


async def test_existing_indexed_email_body_search_is_reused(client, project_db):
    pid = UUID(await create(client, "Orion website"))
    await project_db["conn"].execute("UPDATE emails SET subject = 'Quick question', body = 'Regarding the Orion website delivery' WHERE user_id = $1 AND id = 'inbound'", USER)
    candidates = await module.candidates_for(USER, pid, [{"id": "project", "text": "Orion website"}])
    assert [c["source_id"] for c in candidates] == ["thread"]
    assert "Orion website" in candidates[0]["preview"]


async def test_old_poll_cannot_invalidate_upserted_generation(client, project_db, monkeypatch):
    pid = UUID(await create(client))
    before = await discovery(client, pid)
    await project_db["conn"].execute("UPDATE emails SET subject = 'Launch revised' WHERE user_id = $1", USER)
    entered, finish = asyncio.Event(), asyncio.Event()
    original = module.live_sources
    poll = None

    async def pause(*args):
        if asyncio.current_task() is poll:
            entered.set()
            await finish.wait()
        return await original(*args)

    monkeypatch.setattr(module, "live_sources", pause)
    poll = asyncio.create_task(svc.list(USER, pid))
    await asyncio.wait_for(entered.wait(), 5)
    try:
        fresh = await asyncio.wait_for(svc.discover(USER, pid, uuid4()), 10)
        assert {str(s["id"]) for s in fresh["suggestions"]} == {s["id"] for s in before["suggestions"]}
    finally:
        finish.set()
        polled = await asyncio.wait_for(poll, 5)
    assert len(polled["suggestions"]) == 3
    assert len((await svc.list(USER, pid))["suggestions"]) == 3


@pytest.mark.parametrize("context_kind", ["scope", "project"])
async def test_multiline_quotes_round_trip(client, project_db, monkeypatch, context_kind):
    pid = UUID(await create(client))
    quote = 'Launch "Orion"\\plan\nReady for review'
    await project_db["conn"].execute("UPDATE emails SET body = $2 WHERE user_id = $1", USER, quote)
    from app.services.project_knowledge_service import project_knowledge_service as knowledge
    if context_kind == "scope":
        await knowledge.scope(USER, pid, quote, 0)
    else:
        await project_service.edit(USER, pid, {"description": quote})

    async def respond(**kwargs):
        content = kwargs["messages"][0]["content"]
        payload = json.loads(content[content.index('{"context":'):content.rindex('}') + 1])
        candidate = next(c for c in payload["candidates"] if c["id"] == "email_thread:thread")
        context = next(c for c in payload["context"] if c["id"].startswith(context_kind))
        assert quote in candidate["text"] and quote in context["text"]
        return module.ai.FastResponse(json.dumps({"suggestions": [{
            "candidate_id": candidate["id"], "score": 0.95, "explanation": "Same launch review.",
            "candidate_quote": quote, "context_id": context["id"], "context_quote": quote,
        }]}), None)

    monkeypatch.setattr(module.ai, "call_fast", AsyncMock(side_effect=respond))
    assert len((await discovery(client, pid))["suggestions"]) == 1


async def test_stale_results_are_explicit_and_cannot_be_dismissed(client, project_db):
    pid = UUID(await create(client))
    item = (await discovery(client, pid))["suggestions"][0]
    await project_db["conn"].execute("UPDATE emails SET subject = 'Changed' WHERE user_id = $1", USER)
    result = await svc.list(USER, pid)
    assert result["stale"] and not result["suggestions"]
    assert (await svc.list(USER, pid))["stale"]
    response = await client.post(f"/projects/{pid}/suggestions/{item['id']}/dismiss")
    assert response.status_code == 409
    assert not (await discovery(client, pid))["stale"]


async def test_reset_dismissals_is_scoped_and_does_not_generate(client, project_db):
    pid = UUID(await create(client))
    item = (await discovery(client, pid))["suggestions"][0]
    await svc.resolve(USER, pid, UUID(item["id"]), False)
    assert (await svc.list(USER, pid))["dismissed_count"] == 1
    calls = module.ai.call_fast.await_count
    route = f"/projects/{pid}/suggestions/reset-dismissals"
    assert (await client.post(route, headers={"x-test-user": str(OTHER)})).status_code == 404
    assert (await client.post(route)).status_code == 200
    assert (await client.post(route)).status_code == 200
    assert (await svc.list(USER, pid))["dismissed_count"] == 0
    assert module.ai.call_fast.await_count == calls
    assert (item["kind"], item["source_id"]) in {(s["kind"], s["source_id"]) for s in (await discovery(client, pid))["suggestions"]}


async def test_batched_reads_and_manifest_only_revalidation(client, project_db, monkeypatch):
    pid = UUID(await create(client))
    conn = project_db["conn"]
    for i in range(18):
        await conn.execute("INSERT INTO meetings (user_id, title) VALUES ($1, $2)", USER, f"Launch {i}")
    context = await module.context_for(USER, pid)
    candidates = await module.candidates_for(USER, pid, context)
    assert len(candidates) == 18
    query = AsyncMock(wraps=db.query)
    query_one = AsyncMock(wraps=db.query_one)
    monkeypatch.setattr(db, "query", query)
    monkeypatch.setattr(db, "query_one", query_one)
    assert len(await module.live_sources(USER, candidates)) == 18
    assert query.await_count == 1 and query_one.await_count == 0
    query.reset_mock()
    manifest = {c["id"]: module.digest(c["text"]) for c in context}
    assert set(manifest) == {"project"}
    assert await module.context_for(USER, pid, manifest) == manifest
    # Project-only context should not scan mail, meetings, records or history.
    assert query.await_count == 0 and query_one.await_count == 2


async def test_thread_preview_uses_canonical_access_without_context_scan(client, project_db, monkeypatch):
    pid = UUID(await create(client))
    item = next(s for s in (await discovery(client, pid))["suggestions"] if s["kind"] == "email_thread")
    monkeypatch.setattr(module, "context_for", AsyncMock(side_effect=AssertionError("No context scan for mail preview")))
    messages = await svc.thread(USER, pid, UUID(item["id"]))
    assert len(messages) == 1 and messages[0]["id"] == "inbound"
    await project_db["conn"].execute("DELETE FROM emails WHERE user_id = $1", USER)
    assert await svc.thread(USER, pid, UUID(item["id"])) == []
