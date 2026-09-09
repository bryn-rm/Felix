"""Phase 2 contracts, actual database security/concurrency, and AI boundaries."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import HTTPException

from tests.test_projects import USER, OTHER, client, create, project_db  # shared disposable database/API fixtures
from app import db
from app.services import project_update_service as updates
from app.services.project_evidence_service import project_snapshot, MAX_ITEMS, MAX_CHARACTERS
from app.services.project_knowledge_service import project_knowledge_service as knowledge
from app.services.project_service import project_service as projects

pytestmark = pytest.mark.parametrize("project_db", ["phase2-upgrade"], indirect=True)


async def record(client, pid, kind="decision", **values):
    body = {"kind": kind, "title": "Use the approved launch plan", "request_id": str(uuid4())}
    if kind != "approval":
        body["event_date"] = "2026-01-02"
    body.update(values)
    response = await client.post(f"/projects/{pid}/records", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_phase2_upgrade_rerun_and_rls(project_db, client):
    conn = project_db["conn"]
    pid = UUID(await create(client))
    await knowledge.scope(USER, pid, "Confirmed by user", 0)
    rid = await record(client, pid)
    await conn.execute(project_db["migration"].read_text())
    assert (await knowledge.list(USER, pid))["scope"]["content"] == "Confirmed by user"
    assert (await projects.detail(USER, project_db["prior_project"]))["source_count"] == 1
    assert await conn.fetchval("SELECT status FROM commitments WHERE user_id = $1 AND id = $2", USER, project_db["commitment"]) == "done"
    tables = ("project_scope_versions", "project_records", "project_record_evidence", "project_updates", "project_record_requests")
    for table in tables:
        assert await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE relname = $1", table)
    for table in tables[1:]:
        assert not await conn.fetchval("SELECT has_table_privilege('authenticated', $1, 'SELECT')", table)
    other_pid = (await projects.create(OTHER, {"name": "Private"}))["id"]
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await conn.execute("INSERT INTO project_scope_versions (user_id, project_id, version, content) VALUES ($1, $2, 1, 'Stolen')", OTHER, pid)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await conn.execute("INSERT INTO project_records (user_id, project_id, kind, title, status, event_date, supersedes_id) VALUES ($1,$2,'decision','Stolen','current',current_date,$3)", OTHER, other_pid, UUID(rid))
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await conn.execute("INSERT INTO project_record_evidence (user_id, project_id, record_id, kind, meeting_id) VALUES ($1,$2,$3,'meeting',$4)", OTHER, other_pid, UUID(rid), project_db["meeting"])
    # Privileges prevent browser bypass of access revalidation. Grant temporarily
    # in this disposable DB to independently verify the RLS defence as well.
    await conn.execute("GRANT USAGE ON SCHEMA public, auth TO authenticated; GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated")
    await conn.execute("SELECT set_config('request.jwt.claim.sub', $1, false)", str(OTHER))
    await conn.execute("SET ROLE authenticated")
    try:
        for table in tables:
            assert await conn.fetch(f"SELECT * FROM {table}") == []
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("INSERT INTO project_scope_versions (user_id, project_id, version, content) VALUES ($1,$2,2,'Forged')", USER, pid)
    finally:
        await conn.execute("RESET ROLE")


async def test_snapshot_marks_current_scope_and_includes_summary_details(project_db, client):
    pid = UUID(await create(client))
    await knowledge.scope(USER, pid, "Large launch", 0)
    await knowledge.scope(USER, pid, "Small launch only", 1)
    mid = project_db["meeting"]
    await projects.link(USER, pid, "meeting", str(mid))
    await project_db["conn"].execute("UPDATE meetings SET title = NULL WHERE user_id = $1 AND id = $2", USER, mid)
    sid = await project_db["conn"].fetchval(
        "INSERT INTO meeting_summaries (user_id, meeting_id, tldr, decisions, action_items) VALUES ($1,$2,$3,$4,$5) RETURNING id",
        USER, mid, "Discussed launch", [{"text": "Ship on Friday"}], [{"text": "Send the plan", "owner": "Pat"}],
    )
    snap = await project_snapshot(USER, pid)
    scopes = [item for item in snap["selected"] if item["kind"] == "scope"]
    assert scopes[0]["text"].startswith("Current confirmed scope")
    assert scopes[1]["text"].startswith("Historical confirmed scope")
    summary = snap["items"][f"summary:{sid}"]
    assert "Ship on Friday" in summary["text"] and "Send the plan" in summary["text"]
    assert not summary["recent_event"]  # generated today from a historical meeting


async def test_scope_versions_conflicts_and_noop(project_db, client):
    pid = await create(client)
    url = f"/projects/{pid}/scope"
    responses = await asyncio.gather(*(client.put(url, json={"content": value, "expected_version": 0}) for value in ("First", "Second")))
    assert sorted(r.status_code for r in responses) == [200, 409]
    current = (await client.get(f"/projects/{pid}/knowledge")).json()["scope"]
    assert (await client.put(url, json={"content": current["content"], "expected_version": 1})).status_code == 200
    assert (await client.put(url, json={"content": "", "expected_version": 1})).status_code == 200
    data = (await client.get(f"/projects/{pid}/knowledge")).json()
    assert [s["version"] for s in data["scope_history"]] == [2, 1]
    assert data["scope"]["content"] == ""
    assert sum(e["action"] == "scope_changed" for e in await projects.activity(USER, UUID(pid))) == 2


async def test_decision_supersession_and_rollback(project_db, client):
    pid = await create(client)
    rid = await record(client, pid)
    response = await client.patch(f"/projects/{pid}/records/{rid}", json={"expected_version": 1, "title": "Rewrite history"})
    assert response.status_code == 409
    # A bad supporting source rolls back the old record's supersession too.
    body = {"request_id": str(uuid4()), "kind": "decision", "title": "Replacement", "event_date": "2026-09-09", "supersedes_id": rid}
    bad = await client.post(f"/projects/{pid}/records", json={**body, "evidence": [{"kind": "email_thread", "source_id": "private-thread"}]})
    assert bad.status_code == 404
    assert (await knowledge.list(USER, UUID(pid)))["records"][0]["status"] == "current"
    responses = await asyncio.gather(*(client.post(f"/projects/{pid}/records", json=body) for _ in range(2)))
    assert sorted(r.status_code for r in responses) == [201, 201]
    data = await knowledge.list(USER, UUID(pid))
    old = next(r for r in data["records"] if str(r["id"]) == rid)
    assert old["status"] == "superseded" and old["version"] == 2
    assert old["title"] == "Use the approved launch plan"
    assert next(r for r in data["records"] if r["status"] == "current")["supersedes_id"] == UUID(rid)


@pytest.mark.parametrize("kind,patch", [("approval", {"status": "approved", "owner": "Pat", "deadline": "2026-09-10"}), ("milestone", {"status": "done", "event_date": "2026-09-10"})])
async def test_lightweight_records_status_and_conflicts(project_db, client, kind, patch):
    pid = await create(client)
    rid = await record(client, pid, kind)
    path = f"/projects/{pid}/records/{rid}"
    assert (await client.patch(path, json={**patch, "expected_version": 1})).status_code == 200
    assert (await client.patch(path, json={"title": "Late edit", "expected_version": 1})).status_code == 409
    assert (await client.patch(path, json={"status": "invalid", "expected_version": 2})).status_code == 422
    assert (await client.patch(path, json={**patch, "expected_version": 2})).status_code == 200
    row = (await knowledge.list(USER, UUID(pid)))["records"][0]
    assert row["version"] == 2  # no-op edits don't manufacture weekly activity
    for key, value in patch.items():
        assert str(row[key]) == value


async def test_import_reference_availability_and_source_changes(project_db, client):
    pid = UUID(await create(client))
    conn = project_db["conn"]
    mid = project_db["meeting"]
    link = await projects.link(USER, pid, "meeting", str(mid))
    sid = await conn.fetchval("INSERT INTO meeting_summaries (user_id, meeting_id, decisions) VALUES ($1,$2,$3) RETURNING id", USER, mid, [{"text": "Ship the smaller launch"}])
    options = (await client.get(f"/projects/{pid}/meeting-decisions")).json()["decisions"]
    assert options[0]["summary_id"] == str(sid)
    response = await client.post(f"/projects/{pid}/decisions/import", json={"request_id": str(uuid4()), "summary_id": str(sid), "decision_index": 0, "decision_date": "2026-01-02"})
    assert response.status_code == 201, response.text
    row = (await knowledge.list(USER, pid))["records"][0]
    assert row["title"] == "Ship the smaller launch" and row["available"]
    assert row["evidence"][0]["summary_id"] == sid
    # New summaries do not rewrite the imported reference to its original version.
    await conn.execute("INSERT INTO meeting_summaries (user_id, meeting_id, decisions) VALUES ($1,$2,$3)", USER, mid, [{"text": "Different new summary"}])
    assert (await knowledge.list(USER, pid))["records"][0]["available"]
    await conn.execute("UPDATE settings SET meeting_capture_mode = false WHERE user_id = $1", USER)
    assert await knowledge.meeting_decisions(USER, pid) == []
    assert not (await knowledge.list(USER, pid))["records"][0]["available"]
    assert (await client.post(f"/projects/{pid}/decisions/import", json={"request_id": str(uuid4()), "summary_id": str(sid), "decision_index": 0, "decision_date": "2026-01-02"})).status_code == 404
    await conn.execute("UPDATE settings SET meeting_capture_mode = true WHERE user_id = $1", USER)
    await projects.unlink(USER, pid, "meeting", link["id"])
    assert not (await knowledge.list(USER, pid))["records"][0]["available"]
    await projects.link(USER, pid, "meeting", str(mid))
    assert (await knowledge.list(USER, pid))["records"][0]["available"]
    await conn.execute("UPDATE meeting_summaries SET decisions = '[]' WHERE user_id = $1 AND id = $2", USER, sid)
    row = (await knowledge.list(USER, pid))["records"][0]
    assert not row["available"] and "Ship" not in row["title"]
    await conn.execute("DELETE FROM meetings WHERE user_id = $1 AND id = $2", USER, mid)
    assert not (await knowledge.list(USER, pid))["records"][0]["available"]


async def test_phase2_cross_tenant_endpoints(project_db, client, monkeypatch):
    pid = await create(client)
    rid = await record(client, pid, "approval")
    headers = {"x-test-user": str(OTHER)}
    for suffix in ("knowledge", "meeting-decisions", "update", "evidence?key=project"):
        assert (await client.get(f"/projects/{pid}/{suffix}", headers=headers)).status_code == 404
    for method, suffix, body in (
        ("put", "scope", {"content": "Stolen", "expected_version": 0}),
        ("post", "records", {"request_id": str(uuid4()), "kind": "approval", "title": "Stolen"}),
        ("patch", f"records/{rid}", {"status": "approved", "expected_version": 1}),
        ("post", "decisions/import", {"request_id": str(uuid4()), "summary_id": str(uuid4()), "decision_index": 0, "decision_date": "2026-09-09"}),
    ):
        assert (await getattr(client, method)(f"/projects/{pid}/{suffix}", json=body, headers=headers)).status_code == 404
    model = AsyncMock()
    monkeypatch.setattr(updates, "generate_claims", model)
    with pytest.raises(HTTPException) as error:
        await updates.project_update_service.generate(OTHER, UUID(pid), uuid4())
    assert error.value.status_code == 404
    model.assert_not_called()


async def test_weekly_original_times_and_canonical_sources(project_db, client):
    pid = UUID(await create(client))
    conn = project_db["conn"]
    await conn.execute("UPDATE settings SET timezone = 'America/Los_Angeles' WHERE user_id = $1", USER)
    await projects.link(USER, pid, "email_thread", "thread")
    await projects.link(USER, pid, "meeting", str(project_db["meeting"]))
    await projects.link(USER, pid, "commitment", str(project_db["commitment"]))
    await record(client, pid, event_date="2026-01-02")
    await record(client, pid, "milestone", event_date="2026-09-08")
    now = datetime(2026, 9, 9, 20, tzinfo=timezone.utc)
    await conn.execute("UPDATE project_activity SET occurred_at = $1 WHERE user_id = $2 AND project_id = $3", now, USER, pid)
    snap = await project_snapshot(USER, pid, now)
    assert snap["week_start"] == datetime(2026, 9, 7, 7, tzinfo=timezone.utc)
    assert not snap["items"]["email:inbound:inbound"]["recent_event"]
    assert not snap["items"][f"meeting:{project_db['meeting']}"]["recent_event"]
    assert any(i["recent_project_action"] for i in snap["selected"] if i["kind"] == "project_action")
    commitment = snap["items"][f"commitment:{project_db['commitment']}"]
    assert commitment["occurred_at"] == datetime(2026, 9, 8, 15, tzinfo=timezone.utc)  # pre-023 resolution
    assert commitment["recent_event"]
    assert not next(i for i in snap["selected"] if i["kind"] == "decision")["recent_event"]
    assert next(i for i in snap["selected"] if i["kind"] == "milestone")["occurred_at"] is None
    # Later mirrored reply is included automatically; no new association required.
    await conn.execute("INSERT INTO sent_emails (id,user_id,thread_id,body,sent_at) VALUES ('reply',$1,'thread','Confirmed launch on Friday',$2)", USER, now)
    changed = await project_snapshot(USER, pid, now)
    assert changed["fingerprint"] != snap["fingerprint"]
    assert changed["items"]["email:sent:reply"]["recent_event"]
    assert len(changed["selected"]) <= MAX_ITEMS
    assert sum(len(json.dumps(i, default=str)) for i in changed["selected"]) <= MAX_CHARACTERS


async def test_generation_idempotence_stale_withholding_and_separation(project_db, client, monkeypatch):
    pid = UUID(await create(client))
    await knowledge.scope(USER, pid, "Only ship the small launch", 0)
    link = await projects.link(USER, pid, "email_thread", "thread")
    before = await knowledge.list(USER, pid)
    model = AsyncMock(return_value=[{"section": "scope", "text": "Small launch only", "time_basis": "current_context", "citations": []}])
    monkeypatch.setattr(updates, "generate_claims", model)
    monkeypatch.setattr(updates, "check_monthly_ai_budget", AsyncMock())
    request_id = uuid4()
    result = await updates.project_update_service.generate(USER, pid, request_id)
    assert result["update"] and not result["stale"]
    assert await knowledge.list(USER, pid) == before
    await updates.project_update_service.generate(USER, pid, request_id)
    assert model.await_count == 1
    await knowledge.scope(USER, pid, "Scope changed explicitly", 1)
    assert (await updates.project_update_service.get(USER, pid))["stale"]
    await projects.unlink(USER, pid, "email_thread", link["id"])
    result = await updates.project_update_service.get(USER, pid)
    assert result["withheld"] and result["update"] is None
    with pytest.raises(HTTPException) as error:
        await updates.project_update_service.evidence(USER, pid, "email:inbound:inbound")
    assert error.value.status_code == 404


async def test_generation_claim_quota_failures_and_changed_inputs(project_db, client, monkeypatch):
    pid = UUID(await create(client))
    gate = AsyncMock(side_effect=HTTPException(429, "Quota"))
    model = AsyncMock(return_value=[])
    monkeypatch.setattr(updates, "check_monthly_ai_budget", gate)
    monkeypatch.setattr(updates, "generate_claims", model)
    with pytest.raises(HTTPException) as error:
        await updates.project_update_service.generate(USER, pid, uuid4())
    assert error.value.status_code == 429
    model.assert_not_called()
    gate.side_effect = None
    started, finish = asyncio.Event(), asyncio.Event()
    async def slow(*args):
        started.set()
        await finish.wait()
        return []
    model.side_effect = slow
    first = asyncio.create_task(updates.project_update_service.generate(USER, pid, uuid4()))
    await started.wait()
    try:
        with pytest.raises(HTTPException) as error:
            await updates.project_update_service.generate(USER, pid, uuid4())
        assert error.value.status_code == 409
    finally:
        finish.set()
    await first
    assert model.await_count == 1
    saved = await db.query_one("SELECT * FROM project_updates WHERE user_id = $1 AND project_id = $2", USER, pid)
    for failure in (ValueError("bad JSON"), TimeoutError(), RuntimeError("provider text must not leak")):
        model.side_effect = failure
        with pytest.raises(HTTPException) as error:
            await updates.project_update_service.generate(USER, pid, uuid4())
        assert error.value.status_code == 502 and "provider text" not in error.value.detail
        assert await db.query_one("SELECT * FROM project_updates WHERE user_id = $1 AND project_id = $2", USER, pid) == saved
        assert await project_db["conn"].fetchval("SELECT update_generation_token FROM projects WHERE user_id = $1 AND id = $2", USER, pid) is None
    async def change(*args):
        await knowledge.scope(USER, pid, "Changed while generating", 0)
        return []
    model.side_effect = change
    with pytest.raises(HTTPException) as error:
        await updates.project_update_service.generate(USER, pid, uuid4())
    assert error.value.status_code == 409
    assert await db.query_one("SELECT * FROM project_updates WHERE user_id = $1 AND project_id = $2", USER, pid) == saved


async def test_generation_cancellation_and_expired_lease_recovery(project_db, client, monkeypatch):
    pid = UUID(await create(client))
    monkeypatch.setattr(updates, "check_monthly_ai_budget", AsyncMock())
    model = AsyncMock(return_value=[])
    monkeypatch.setattr(updates, "generate_claims", model)
    await updates.project_update_service.generate(USER, pid, uuid4())
    saved = await db.query_one("SELECT * FROM project_updates WHERE user_id = $1 AND project_id = $2", USER, pid)
    model.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await updates.project_update_service.generate(USER, pid, uuid4())
    assert await db.query_one("SELECT * FROM project_updates WHERE user_id = $1 AND project_id = $2", USER, pid) == saved
    assert await project_db["conn"].fetchval("SELECT update_generation_token FROM projects WHERE user_id = $1 AND id = $2", USER, pid) is None
    await project_db["conn"].execute(
        "UPDATE projects SET update_generation_token = $3, update_generation_started_at = now() - interval '3 minutes' WHERE user_id = $1 AND id = $2",
        USER, pid, uuid4(),
    )
    model.side_effect = None
    request_id = uuid4()
    response = await client.post(f"/projects/{pid}/update", json={"request_id": str(request_id)})
    assert response.status_code == 200, response.text
    assert response.json()["update"] is not None
    assert await project_db["conn"].fetchval("SELECT request_id FROM project_updates WHERE user_id = $1 AND project_id = $2", USER, pid) == request_id


async def test_stale_and_withheld_for_canonical_evidence_changes(project_db, client, monkeypatch):
    pid = UUID(await create(client))
    conn = project_db["conn"]
    mid, cid = project_db["meeting"], project_db["commitment"]
    await projects.link(USER, pid, "email_thread", "thread")
    await projects.link(USER, pid, "meeting", str(mid))
    await projects.link(USER, pid, "commitment", str(cid))
    await record(client, pid, evidence=[{"kind": "meeting", "source_id": str(mid)}])
    sid = await conn.fetchval("INSERT INTO meeting_summaries (user_id,meeting_id,tldr) VALUES ($1,$2,'Original summary') RETURNING id", USER, mid)
    monkeypatch.setattr(updates, "check_monthly_ai_budget", AsyncMock())
    monkeypatch.setattr(updates, "generate_claims", AsyncMock(return_value=[]))
    # Preserve the original source IDs while changing their canonical content;
    # generated prose becomes stale, and removals/access loss withhold it.
    changes = [
        ("UPDATE emails SET body = 'Updated body' WHERE user_id = $1 AND id = $2", "inbound", False),
        ("UPDATE meetings SET user_notes = 'Updated notes' WHERE user_id = $1 AND id = $2", mid, False),
        ("UPDATE meeting_summaries SET tldr = 'Revised summary' WHERE user_id = $1 AND id = $2", sid, False),
        ("UPDATE commitments SET text = 'Updated promise' WHERE user_id = $1 AND id = $2", cid, False),
        ("UPDATE settings SET meeting_capture_mode = false WHERE user_id = $1 AND user_id = $2", USER, True),
        ("DELETE FROM emails WHERE user_id = $1 AND id = $2", "inbound", True),
        ("DELETE FROM commitments WHERE user_id = $1 AND id = $2", cid, True),
    ]
    for sql, key, withheld in changes:
        await updates.project_update_service.generate(USER, pid, uuid4())
        await conn.execute(sql, USER, key)
        result = await updates.project_update_service.get(USER, pid)
        assert result["stale"] and result["withheld"] == withheld
        assert (result["update"] is None) == withheld
        await conn.execute("UPDATE settings SET meeting_capture_mode = true WHERE user_id = $1", USER)


async def test_stale_week_timezone_prompt_and_future_event_eligibility(project_db, client, monkeypatch):
    pid = UUID(await create(client))
    conn = project_db["conn"]
    await projects.link(USER, pid, "email_thread", "thread")
    instant = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
    await conn.execute("UPDATE emails SET received_at = $2 WHERE user_id = $1", USER, instant + timedelta(hours=1))
    async def snapshot(user_id, project_id, **kwargs):
        return await project_snapshot(user_id, project_id, instant, **kwargs)
    monkeypatch.setattr(updates, "project_snapshot", snapshot)
    monkeypatch.setattr(updates, "check_monthly_ai_budget", AsyncMock())
    monkeypatch.setattr(updates, "generate_claims", AsyncMock(return_value=[]))
    await updates.project_update_service.generate(USER, pid, uuid4())
    assert not (await snapshot(USER, pid))["items"]["email:inbound:inbound"]["recent_event"]
    instant += timedelta(hours=2)
    assert (await snapshot(USER, pid))["items"]["email:inbound:inbound"]["recent_event"]
    assert (await updates.project_update_service.get(USER, pid))["stale"]
    await updates.project_update_service.generate(USER, pid, uuid4())
    instant += timedelta(days=7)
    assert (await updates.project_update_service.get(USER, pid))["stale"]
    await updates.project_update_service.generate(USER, pid, uuid4())
    await conn.execute("UPDATE settings SET timezone = 'Asia/Tokyo' WHERE user_id = $1", USER)
    assert (await updates.project_update_service.get(USER, pid))["stale"]
    await updates.project_update_service.generate(USER, pid, uuid4())
    monkeypatch.setitem(updates.ai.PROMPT_VERSIONS, "project_update", "next-version")
    assert (await updates.project_update_service.get(USER, pid))["stale"]


async def test_evidence_caps_and_omitted_source_staleness(project_db, client):
    pid = UUID(await create(client))
    await projects.link(USER, pid, "email_thread", "thread")
    await project_db["conn"].executemany(
        "INSERT INTO sent_emails (user_id,id,thread_id,body,sent_at) VALUES ($1,$2,'thread',$3,$4)",
        [(USER, f"bounded-{i}", "x" * 5000, datetime(2026, 1, 1, tzinfo=timezone.utc)) for i in range(100)],
    )
    instant = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
    snap = await project_snapshot(USER, pid, instant)
    assert snap["omitted_count"] > 0
    assert len(snap["selected"]) <= MAX_ITEMS
    assert sum(len(json.dumps(item, default=str)) for item in snap["selected"]) <= MAX_CHARACTERS
    selected = {item["id"] for item in snap["selected"]}
    omitted = next(key for key in snap["items"] if key.startswith("email:sent:") and key not in selected)
    await project_db["conn"].execute("UPDATE sent_emails SET body = body || 'changed' WHERE user_id = $1 AND id = $2", USER, omitted.removeprefix("email:sent:"))
    changed = await project_snapshot(USER, pid, instant)
    assert changed["fingerprint"] != snap["fingerprint"]


@pytest.mark.parametrize("delete_table", ["meeting_summaries", "meetings"])
async def test_import_summary_reference_cleared_on_deletion(project_db, client, delete_table):
    pid = UUID(await create(client))
    mid = project_db["meeting"]
    await projects.link(USER, pid, "meeting", str(mid))
    sid = await project_db["conn"].fetchval("INSERT INTO meeting_summaries (user_id,meeting_id,decisions) VALUES ($1,$2,$3) RETURNING id", USER, mid, [{"text": "Pinned decision"}])
    await knowledge.create(USER, pid, {"kind": "decision", "event_date": datetime(2026,1,1).date()}, import_summary=(sid, 0))
    await project_db["conn"].execute(f"DELETE FROM {delete_table} WHERE user_id = $1 AND id = $2", USER, sid if delete_table == "meeting_summaries" else mid)
    ref = await project_db["conn"].fetchrow("SELECT * FROM project_record_evidence WHERE user_id = $1 AND project_id = $2", USER, pid)
    assert ref["summary_id"] is None
    assert ref["decision_index"] == 0  # retain the unavailable evidence marker


async def test_record_replay_and_cross_tab_decision_deduplication(project_db, client):
    pid = UUID(await create(client))
    body = {"kind": "decision", "title": "One decision", "event_date": "2026-09-09", "request_id": str(uuid4())}
    path = f"/projects/{pid}/records"
    replies = await asyncio.gather(*(client.post(path, json=body) for _ in range(2)))
    assert all(r.status_code == 201 for r in replies)
    rid = replies[0].json()["id"]
    assert replies[1].json()["id"] == rid
    second_tab = {**body, "request_id": str(uuid4())}
    assert (await client.post(path, json=second_tab)).json()["id"] == rid
    assert (await client.post(path, json={**second_tab, "title": "Changed request"})).status_code == 409
    assert len((await knowledge.list(USER, pid))["records"]) == 1


async def test_model_change_marks_update_stale(project_db, client, monkeypatch):
    pid = UUID(await create(client))
    monkeypatch.setattr(updates, "check_monthly_ai_budget", AsyncMock())
    monkeypatch.setattr(updates, "generate_claims", AsyncMock(return_value=[]))
    await updates.project_update_service.generate(USER, pid, uuid4())
    monkeypatch.setattr(updates.settings, "ANTHROPIC_MODEL_SMART", "different-model")
    assert (await updates.project_update_service.get(USER, pid))["stale"]


async def test_old_manifest_remains_authorized_outside_candidate_limit(project_db, client, monkeypatch):
    pid = UUID(await create(client))
    conn = project_db["conn"]
    await projects.link(USER, pid, "email_thread", "thread")
    monkeypatch.setattr(updates, "check_monthly_ai_budget", AsyncMock())
    monkeypatch.setattr(updates, "generate_claims", AsyncMock(return_value=[]))
    spy = AsyncMock(wraps=project_snapshot)
    monkeypatch.setattr(updates, "project_snapshot", spy)
    await updates.project_update_service.generate(USER, pid, uuid4())
    assert spy.await_count == 2  # no third snapshot just to format the response
    await conn.executemany(
        "INSERT INTO sent_emails (user_id,id,thread_id,body,sent_at) VALUES ($1,$2,'thread','New evidence',now())",
        [(USER, f"new-{i}") for i in range(100)],
    )
    snap = await project_snapshot(USER, pid)
    assert len([key for key in snap["items"] if key.startswith("email:")]) == MAX_ITEMS
    assert "email:inbound:inbound" not in snap["items"]
    result = await updates.project_update_service.get(USER, pid)
    assert result["stale"] and not result["withheld"]
    assert (await updates.project_update_service.evidence(USER, pid, "email:inbound:inbound"))["id"] == "email:inbound:inbound"
    await conn.execute("DELETE FROM emails WHERE user_id = $1 AND id = 'inbound'", USER)
    assert (await updates.project_update_service.get(USER, pid))["withheld"]


async def test_import_deduplicates_across_request_ids_and_projects_remain_separate(project_db, client):
    pid = UUID(await create(client))
    other_pid = UUID(await create(client, "Second project"))
    mid = project_db["meeting"]
    for project in (pid, other_pid):
        await projects.link(USER, project, "meeting", str(mid))
    sid = await project_db["conn"].fetchval("INSERT INTO meeting_summaries (user_id,meeting_id,decisions) VALUES ($1,$2,$3) RETURNING id", USER, mid, [{"text": "One imported decision"}])
    body = {"summary_id": str(sid), "decision_index": 0, "decision_date": "2026-09-09", "request_id": str(uuid4())}
    first = await client.post(f"/projects/{pid}/decisions/import", json=body)
    second = await client.post(f"/projects/{pid}/decisions/import", json={**body, "request_id": str(uuid4())})
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    other = await client.post(f"/projects/{other_pid}/decisions/import", json=body)
    assert other.status_code == 201 and other.json()["id"] != first.json()["id"]
    events = await projects.activity(USER, pid)
    assert len([e for e in events if e["action"] == "record_created"]) == 1
