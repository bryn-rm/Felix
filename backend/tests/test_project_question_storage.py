"""Real PostgreSQL question ownership, migration, retrieval and concurrency tests."""

import asyncio
import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import HTTPException

from tests.test_projects import USER, OTHER, client, create, project_db
from app import db
from app.services import project_question_service as questions
from app.services.project_service import project_service as projects
from app.services.project_knowledge_service import project_knowledge_service as knowledge
from app.services.project_evidence_service import project_snapshot, MAX_ITEMS, MAX_CHARACTERS

pytestmark = pytest.mark.parametrize("project_db", ["phase4-fresh", "phase4-upgrade"], indirect=True)
UNKNOWN = {"claims": [], "unanswered": ["Why did the launch move?"]}


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(questions, "check_monthly_ai_budget", AsyncMock())
    call = AsyncMock(return_value=UNKNOWN)
    monkeypatch.setattr(questions, "generate_answer", call)
    # Other tests exercise the limiter itself; these test storage/concurrency.
    from app.middleware.rate_limit import limiter
    monkeypatch.setattr(limiter, "enabled", False)
    return call


async def test_fresh_upgrade_rerun_rls_and_foreign_keys(project_db, client, model):
    conn = project_db["conn"]
    pid = UUID(await create(client))
    await questions.project_question_service.ask(USER, pid, uuid4(), "Why did the launch move?")
    await conn.execute(project_db["migration"].read_text())
    assert (await questions.project_question_service.get(USER, pid))["answer"]["unanswered"] == UNKNOWN["unanswered"]
    if project_db["prior_project"]:
        assert (await projects.detail(USER, project_db["prior_project"]))["source_count"] == 1
        assert await conn.fetchval("SELECT status FROM commitments WHERE user_id = $1 AND id = $2", USER, project_db["commitment"]) == "done"
    assert await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE relname = 'project_answers'")
    for operation in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert not await conn.fetchval("SELECT has_table_privilege('authenticated', 'project_answers', $1)", operation)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await conn.execute("UPDATE project_answers SET user_id = $3 WHERE user_id = $1 AND project_id = $2", USER, pid, OTHER)
    await conn.execute("GRANT USAGE ON SCHEMA public, auth TO authenticated; GRANT ALL ON project_answers TO authenticated")
    await conn.execute("SELECT set_config('request.jwt.claim.sub', $1, false)", str(OTHER))
    await conn.execute("SET ROLE authenticated")
    try:
        assert await conn.fetch("SELECT * FROM project_answers") == []
        await conn.execute("SELECT set_config('request.jwt.claim.sub', $1, false)", str(USER))
        assert len(await conn.fetch("SELECT * FROM project_answers")) == 1
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE project_answers SET user_id = $2 WHERE user_id = $1", USER, OTHER)
    finally:
        await conn.execute("RESET ROLE")


async def test_api_isolation_replay_payload_validation_and_no_confirmed_changes(project_db, client, model):
    pid = UUID(await create(client))
    private = (await projects.create(OTHER, {"name": "Private"}))["id"]
    request_id = uuid4()
    body = {"request_id": str(request_id), "question": " Why did the launch move? "}
    for method, values in ((client.get, {}), (client.post, {"json": body})):
        assert (await method(f"/projects/{private}/ask", **values)).status_code == 404
    model.assert_not_awaited()
    url = f"/projects/{pid}/ask"
    assert (await client.get(url)).json() == {"answer": None, "stale": False, "withheld": False}
    before = await projects.activity(USER, pid)
    for invalid in ("", "  ", "x" * 2001):
        assert (await client.post(url, json={**body, "question": invalid})).status_code == 422
    response = await client.post(url, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["answer"]["question"] == body["question"].strip()
    assert (await client.get(url, headers={"x-test-user": str(OTHER)})).status_code == 404
    assert (await client.post(url, json=body)).json() == response.json()
    assert model.await_count == 1
    assert (await client.post(url, json={**body, "question": "Different question?"})).status_code == 409
    assert await projects.activity(USER, pid) == before
    assert (await knowledge.list(USER, pid))["records"] == []


async def test_question_ranking_finds_older_linked_evidence_and_caps(project_db, client):
    pid = UUID(await create(client))
    conn = project_db["conn"]
    await projects.link(USER, pid, "email_thread", "thread")
    await conn.execute("UPDATE emails SET body = 'The launch moved because of certification.' WHERE user_id = $1 AND id = 'inbound'", USER)
    await conn.executemany(
        "INSERT INTO sent_emails (user_id,id,thread_id,body,sent_at) VALUES ($1,$2,'thread',$3,now())",
        [(USER, f"noise-{i}", "Unrelated discussion " * 200) for i in range(100)],
    )
    await conn.execute("INSERT INTO sent_emails (user_id,id,thread_id,body,sent_at) VALUES ($1,'unlinked','other','Secret certification reason',now())", USER)
    await conn.execute("UPDATE emails SET body = 'Other user certification secret' WHERE user_id = $1", OTHER)
    await knowledge.scope(USER, pid, "Current confirmed scope", 0)
    await projects.link(USER, pid, "meeting", str(project_db["meeting"]))
    await projects.link(USER, pid, "commitment", str(project_db["commitment"]))
    await conn.execute("INSERT INTO meeting_summaries (user_id,meeting_id,tldr) VALUES ($1,$2,'Certification discussed')", USER, project_db["meeting"])
    snap = await project_snapshot(USER, pid, question="Why did certification move the launch?")
    selected = {item["id"] for item in snap["selected"]}
    assert "email:inbound:inbound" in selected
    assert any(item["kind"] == "scope" for item in snap["selected"])
    assert any(item["kind"] == "meeting_summary" for item in snap["selected"])
    assert len(selected) <= MAX_ITEMS and len(json.dumps(snap["selected"], default=str)) <= MAX_CHARACTERS
    assert snap["omitted_count"] > 0
    assert "Secret certification" not in json.dumps(snap, default=str)
    assert "Other user" not in json.dumps(snap, default=str)
    # Validation reads must keep even old selected IDs available after selection changes.
    current = await project_snapshot(USER, pid, evidence_keys=selected, select_candidates=False)
    assert selected <= current["items"].keys()
    assert current["fingerprint"] == snap["fingerprint"]


async def test_matching_mail_cannot_displace_confirmed_context(project_db, client):
    pid = UUID(await create(client))
    await projects.link(USER, pid, "email_thread", "thread")
    await projects.link(USER, pid, "commitment", str(project_db["commitment"]))
    await knowledge.scope(USER, pid, "Current confirmed scope", 0)
    records = []
    for kind in ("decision", "approval"):
        for i in range(4):
            row = await knowledge.create(USER, pid, {
                "kind": kind, "title": f"Confirmed {kind} {i}",
                **({"event_date": date(2026, 9, 16)} if kind == "decision" else {}),
            })
            records.append(f"record:{row['id']}")
    await project_db["conn"].executemany(
        "INSERT INTO sent_emails (user_id,id,thread_id,body,sent_at) VALUES ($1,$2,'thread',$3,now())",
        [(USER, f"matching-{i}", "Launch certification date discussion " * 100) for i in range(120)],
    )
    snap = await project_snapshot(USER, pid, question="Why did the launch certification date move?")
    selected = {item["id"] for item in snap["selected"]}
    assert set(records) <= selected
    assert f"commitment:{project_db['commitment']}" in selected
    assert any(item["kind"] == "scope" for item in snap["selected"])
    assert len(json.dumps(snap["selected"], default=str)) <= MAX_CHARACTERS
    assert snap["omitted_count"] > 0


@pytest.mark.parametrize("noise", ["metadata", "partial_words", "outside_excerpt", "repeated_word"])
async def test_relevance_uses_distinct_words_in_model_text_before_limit(project_db, client, noise):
    pid = UUID(await create(client))
    await projects.link(USER, pid, "email_thread", "thread")
    conn = project_db["conn"]
    question, relevant, body = {
        "metadata": ("Ada date plan?", "Ada agreed the date and plan.", "Unrelated discussion " * 100),
        "partial_words": ("date plan?", "The plan was approved.", "Candidate airplane discussed " * 100),
        "outside_excerpt": ("Ada date plan?", "Ada agreed the date and plan.", "Ada date plan " * 100),
        "repeated_word": ("date plan?", "The date and plan were approved.", "date " * 400),
    }[noise]
    await conn.execute("UPDATE emails SET body = $2 WHERE user_id = $1", USER, relevant)
    await conn.executemany(
        "INSERT INTO sent_emails (user_id,id,thread_id,body,subject,sent_at) VALUES ($1,$2,'thread',$3,$4,now())",
        [(USER, f"ada-date-plan-{i}" if noise == "metadata" else f"noise-{i}", body,
          "x" * 2000 if noise == "outside_excerpt" else None) for i in range(100)],
    )
    snap = await project_snapshot(USER, pid, question=question)
    assert "email:inbound:inbound" in {item["id"] for item in snap["selected"]}


async def test_answer_stays_current_across_local_weeks(project_db, client, model, monkeypatch):
    pid = UUID(await create(client))
    conn = project_db["conn"]
    await projects.link(USER, pid, "email_thread", "thread")
    await conn.execute("UPDATE settings SET timezone = 'America/Los_Angeles' WHERE user_id = $1", USER)
    await conn.execute("UPDATE emails SET received_at = '2026-09-16T10:00:00Z' WHERE user_id = $1", USER)
    now = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
    async def timed_snapshot(*args, **kwargs):
        return await project_snapshot(*args, now=now, **kwargs)
    monkeypatch.setattr(questions, "project_snapshot", timed_snapshot)
    service = questions.project_question_service
    await service.ask(USER, pid, uuid4(), "What was discussed?")
    original_weekly = await project_snapshot(USER, pid, now=now)
    for day in (17, 22):
        now = now.replace(day=day)
        assert not (await service.get(USER, pid))["stale"]
    assert (await project_snapshot(USER, pid, now=now))["fingerprint"] != original_weekly["fingerprint"]
    for item in model.call_args.args[2]["selected"]:
        assert "recent_event" not in item and "recent_project_action" not in item
    await conn.execute("UPDATE emails SET body = 'Changed evidence' WHERE user_id = $1", USER)
    assert (await service.get(USER, pid))["stale"]


async def test_concurrent_questions_quota_and_failure_preserve_answer(project_db, client, model, monkeypatch):
    pid = UUID(await create(client))
    service = questions.project_question_service
    await service.ask(USER, pid, uuid4(), "First?")
    saved = await service._saved(USER, pid)
    budget = AsyncMock(side_effect=HTTPException(429, "Budget exceeded"))
    monkeypatch.setattr(questions, "check_monthly_ai_budget", budget)
    with pytest.raises(HTTPException) as error:
        await service.ask(USER, pid, uuid4(), "Second?")
    assert error.value.status_code == 429 and model.await_count == 1
    budget.side_effect = None
    started, finish = asyncio.Event(), asyncio.Event()
    async def slow(*args):
        started.set()
        await finish.wait()
        return UNKNOWN
    model.side_effect = slow
    task = asyncio.create_task(service.ask(USER, pid, uuid4(), "Second?"))
    await asyncio.wait_for(started.wait(), 5)
    try:
        with pytest.raises(HTTPException) as error:
            await service.ask(USER, pid, uuid4(), "Third?")
        assert error.value.status_code == 409
    finally:
        finish.set()
    await task
    saved = await service._saved(USER, pid)
    for failure in (ValueError(), TimeoutError(), RuntimeError("private provider output"), asyncio.CancelledError()):
        model.side_effect = failure
        with pytest.raises((HTTPException, asyncio.CancelledError)) as error:
            await service.ask(USER, pid, uuid4(), "Failed?")
        if isinstance(error.value, HTTPException):
            assert error.value.status_code == 502 and "private" not in error.value.detail
        assert await service._saved(USER, pid) == saved
        assert await project_db["conn"].fetchval("SELECT question_generation_token FROM projects WHERE user_id = $1 AND id = $2", USER, pid) is None


async def test_changed_input_and_lease_recovery(project_db, client, model):
    pid = UUID(await create(client))
    service = questions.project_question_service
    async def change(*args):
        await knowledge.scope(USER, pid, "Changed while answering", 0)
        return UNKNOWN
    model.side_effect = change
    with pytest.raises(HTTPException) as error:
        await service.ask(USER, pid, uuid4(), "Why?")
    assert error.value.status_code == 409
    assert await service._saved(USER, pid) is None
    model.side_effect = None
    await project_db["conn"].execute(
        "UPDATE projects SET question_generation_token = $3, question_generation_started_at = now() - interval '3 minutes' WHERE user_id = $1 AND id = $2",
        USER, pid, uuid4(),
    )
    assert (await service.ask(USER, pid, uuid4(), "Why?"))["answer"] is not None


async def test_total_deadline_releases_lease_before_it_can_expire(project_db, client, model, monkeypatch):
    pid = UUID(await create(client))
    async def stalled_snapshot(*args, **kwargs):
        await asyncio.sleep(10)
    monkeypatch.setattr(questions, "ANSWER_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(questions, "project_snapshot", stalled_snapshot)
    with pytest.raises(HTTPException) as error:
        await questions.project_question_service.ask(USER, pid, uuid4(), "Why?")
    assert error.value.status_code == 504
    model.assert_not_awaited()
    assert await project_db["conn"].fetchval("SELECT question_generation_token FROM projects WHERE user_id = $1 AND id = $2", USER, pid) is None


@pytest.mark.parametrize("change", ["edit", "unlink", "delete", "gate"])
async def test_saved_answer_revalidates_all_inputs_even_uncited(project_db, client, model, change):
    pid = UUID(await create(client))
    conn = project_db["conn"]
    link = await projects.link(USER, pid, "email_thread", "thread")
    await projects.link(USER, pid, "meeting", str(project_db["meeting"]))
    service = questions.project_question_service
    await service.ask(USER, pid, uuid4(), "What changed?")
    if change == "edit":
        await conn.execute("UPDATE emails SET body = 'Changed' WHERE user_id = $1", USER)
    elif change == "unlink":
        await projects.unlink(USER, pid, "email_thread", link["id"])
    elif change == "delete":
        await conn.execute("DELETE FROM emails WHERE user_id = $1", USER)
    else:
        await conn.execute("UPDATE settings SET meeting_capture_mode = false WHERE user_id = $1", USER)
    result = await service.get(USER, pid)
    assert result["stale"]
    assert result["withheld"] == (change != "edit")
    assert (result["answer"] is None) == (change != "edit")
    if change != "edit":
        assert result["question"] == "What changed?"
