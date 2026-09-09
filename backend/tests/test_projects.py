"""Project API, real PostgreSQL constraints/RLS, and fresh/upgrade migrations.

Set PROJECT_TEST_DATABASE_URL to a disposable PostgreSQL 15+ admin connection.
Each test creates/drops its own uniquely named database; DATABASE_URL is never
used for this purpose. CI supplies a local PostgreSQL service.
"""

import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI, Header

from app import db
from app.api.projects import ProjectCreate, ProjectPatch, router
from app.middleware.auth import get_current_user
from app.services.project_service import project_service as svc

ROOT = Path(__file__).resolve().parents[2]
USER = UUID("11111111-1111-1111-1111-111111111111")
OTHER = UUID("22222222-2222-2222-2222-222222222222")

BOOTSTRAP = """
CREATE SCHEMA auth;
CREATE TABLE auth.users (id uuid PRIMARY KEY, email text);
CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
CREATE FUNCTION auth.role() RETURNS text LANGUAGE sql STABLE AS
$$ SELECT current_setting('request.jwt.claim.role', true) $$;
DO $$ BEGIN CREATE ROLE authenticated NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
"""


@pytest.fixture
async def project_db(monkeypatch, request):
    url = os.environ.get("PROJECT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set PROJECT_TEST_DATABASE_URL for real PostgreSQL project/migration tests")
    admin = await asyncpg.connect(url)
    name = "felix_projects_test_" + uuid4().hex
    await admin.execute(f'CREATE DATABASE "{name}"')
    parsed = urlsplit(url)
    test_url = urlunsplit(parsed._replace(path="/" + name))
    pool = None
    conn = await asyncpg.connect(test_url)
    try:
        await db._init_connection(conn)
        await conn.execute(BOOTSTRAP)
        await conn.execute((ROOT / "infra/schema.sql").read_text())
        migrations = sorted((ROOT / "infra/migrations").glob("*.sql"))
        mode = getattr(request, "param", "upgrade")
        project_migration = ROOT / ("infra/migrations/023_project_knowledge.sql" if mode.startswith("phase2") else "infra/migrations/022_projects.sql")
        for path in migrations:
            if path.name < project_migration.name:
                await conn.execute(path.read_text())
        # Upgrade tests have real source data before 022 is applied. Fresh
        # installs apply the complete migration chain before inserting data.
        fresh = mode.endswith("fresh")
        if fresh:
            await conn.execute(project_migration.read_text())
        for user in (USER, OTHER):
            await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, $2)", user, f"{user}@test.local")
            await conn.execute("INSERT INTO settings (user_id, meeting_capture_mode) VALUES ($1, true)", user)
            await conn.execute(
                "INSERT INTO emails (id, user_id, thread_id, subject, received_at) "
                "VALUES ('inbound', $1, $2, $3, '2026-01-01T10:00:00Z')",
                user, "thread" if user == USER else "private-thread", "Launch" if user == USER else "Secret",
            )
        meeting = await conn.fetchval("INSERT INTO meetings (user_id, title, date) VALUES ($1, 'Launch meeting', '2026-01-02') RETURNING id", USER)
        commitment = await conn.fetchval(
            "INSERT INTO commitments (user_id, source_kind, direction, text) "
            "VALUES ($1, 'inbound', 'owed_by_user', 'Send launch plan') RETURNING id", USER,
        )
        prior_project = None
        if mode == "phase2-upgrade":
            prior_project = await conn.fetchval("INSERT INTO projects (user_id, name) VALUES ($1, 'Existing Phase 1 project') RETURNING id", USER)
            await conn.execute("INSERT INTO project_meeting_links (user_id, project_id, meeting_id) VALUES ($1, $2, $3)", USER, prior_project, meeting)
            await conn.execute("UPDATE commitments SET status = 'done', resolved_at = '2026-09-08T15:00:00Z' WHERE user_id = $1 AND id = $2", USER, commitment)
        if not fresh:
            await conn.execute(project_migration.read_text())
        pool = await asyncpg.create_pool(test_url, min_size=1, max_size=5, init=db._init_connection)
        monkeypatch.setattr(db, "_pool", pool)
        yield {"conn": conn, "meeting": meeting, "commitment": commitment, "migration": project_migration, "prior_project": prior_project}
    finally:
        if pool:
            await pool.close()
        await conn.close()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


@pytest.fixture
async def client(project_db):
    app = FastAPI()
    app.include_router(router, prefix="/projects")

    async def identity(x_test_user: str = Header(str(USER))):
        return {"id": x_test_user}

    app.dependency_overrides[get_current_user] = identity
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def create(client, name="Website launch"):
    response = await client.post("/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["project"]["id"]


@pytest.mark.parametrize("project_db", ["fresh", "upgrade"], indirect=True)
async def test_migration_install_upgrade_and_rerun(project_db, client):
    conn = project_db["conn"]
    pid = await create(client)
    await svc.link(USER, UUID(pid), "meeting", str(project_db["meeting"]))
    await conn.execute(project_db["migration"].read_text())
    assert (await svc.detail(USER, UUID(pid)))["source_count"] == 1
    assert await conn.fetchval("SELECT text FROM commitments WHERE user_id = $1 AND id = $2", USER, project_db["commitment"]) == "Send launch plan"
    tables = await conn.fetch("SELECT relname, relrowsecurity FROM pg_class WHERE relname LIKE 'project%' AND relkind = 'r'")
    assert len(tables) == 6
    assert all(row["relrowsecurity"] for row in tables)


async def test_create_edit_archive_reopen_and_activity(client):
    pid = await create(client)
    url = f"/projects/{pid}"
    for patch in (
        {"name": "New launch", "description": "The website", "target_date": "2026-12-31"},
        {"target_date": None}, {"status": "archived"}, {"status": "active"},
    ):
        response = await client.patch(url, json=patch)
        assert response.status_code == 200
        for key, value in patch.items():
            assert response.json()["project"][key] == value
        if patch.get("status") == "archived":
            assert (await client.get("/projects")).json()["projects"] == []
            assert len((await client.get("/projects?status=archived")).json()["projects"]) == 1
    events = (await client.get(url + "/activity")).json()["activity"]
    assert {event["action"] for event in events} == {"created", "renamed", "description_changed", "target_date_changed", "archived", "reopened"}
    assert len(events) == 7
    await client.patch(url, json={"name": "New launch"})
    assert len((await client.get(url + "/activity")).json()["activity"]) == 7


@pytest.mark.parametrize("kind", ["email_thread", "meeting", "commitment"])
async def test_link_retry_unlink_and_multiple_projects(client, project_db, kind):
    pid = await create(client)
    second = await create(client, "Another project")
    sid = "thread" if kind == "email_thread" else str(project_db[kind])
    body = {"kind": kind, "source_id": sid}
    # Actual concurrent transactions, not a fake that assumes SQL is correct.
    responses = await asyncio.gather(*(client.post(f"/projects/{pid}/sources", json=body) for _ in range(3)))
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["id"] for response in responses}) == 1
    link_id = responses[0].json()["id"]
    assert (await client.post(f"/projects/{second}/sources", json=body)).status_code == 200
    assert (await client.get(f"/projects/{pid}")).json()["source_count"] == 1
    activity = (await client.get(f"/projects/{pid}/activity")).json()["activity"]
    assert sum(event["action"] == "linked" for event in activity) == 1
    assert next(event for event in activity if event["action"] == "linked")["details"]["title"]
    url = f"/projects/{pid}/sources/{kind}/{link_id}"
    assert (await client.delete(url)).status_code == 204
    assert (await client.delete(url)).status_code == 204
    assert (await client.get(f"/projects/{pid}")).json()["source_count"] == 0
    assert (await client.get(f"/projects/{second}")).json()["sources"][0]["available"]
    assert (await client.get(f"/projects/sources/search?kind={kind}")).json()["sources"]
    activity = (await client.get(f"/projects/{pid}/activity")).json()["activity"]
    assert sum(event["action"] == "unlinked" for event in activity) == 1


async def test_canonical_commitments(client, project_db):
    pid = await create(client)
    await svc.link(USER, UUID(pid), "commitment", str(project_db["commitment"]))
    assert (await svc.detail(USER, UUID(pid)))["open_commitment_count"] == 1
    # Same canonical update used by CommitmentService.resolve, without its
    # unrelated contact-memory background fan-out.
    await project_db["conn"].execute(
        "UPDATE commitments SET status = 'done', resolved_at = now(), text = 'Updated plan' WHERE user_id = $1 AND id = $2",
        USER, project_db["commitment"],
    )
    detail = await svc.detail(USER, UUID(pid))
    assert detail["open_commitment_count"] == 0
    assert detail["sources"][0]["status"] == "done"
    assert detail["sources"][0]["title"] == "Updated plan"
    assert any(event["action"] == "commitment_resolved" for event in await svc.activity(USER, UUID(pid)))


async def test_thread_search_sent_replies_and_event_dates(client, project_db):
    pid = await create(client)
    await svc.link(USER, UUID(pid), "email_thread", "thread")
    await project_db["conn"].execute(
        "INSERT INTO sent_emails (id, user_id, thread_id, subject, sent_at) "
        "VALUES ('sent1', $1, 'thread', 'Launch reply', '2026-02-01'), ('sent2', $1, 'sent-only', 'Sent only', now())", USER,
    )
    found = await svc.search(USER, "email_thread")
    assert len(found) == 2
    assert {item["source_id"] for item in found} == {"thread", "sent-only"}
    assert await svc.search(USER, "email_thread", "%") == []
    assert len(await svc.thread(USER, UUID(pid), "thread")) == 2
    assert (await svc.detail(USER, UUID(pid)))["sources"][0]["title"] == "Launch reply"
    await svc.link(USER, UUID(pid), "email_thread", "sent-only")
    events = await svc.activity(USER, UUID(pid))
    source_event = next(event for event in events if event["action"] == "email_received")
    link_event = next(event for event in events if event["action"] == "linked")
    assert source_event["event_type"] == "source"
    assert source_event["occurred_at"] < link_event["occurred_at"]
    assert link_event["event_type"] == "project"


@pytest.mark.parametrize("kind", ["email_thread", "meeting", "commitment"])
async def test_source_deletion_leaves_removable_placeholder(client, project_db, kind):
    pid = UUID(await create(client))
    sid = "thread" if kind == "email_thread" else str(project_db[kind])
    link = await svc.link(USER, pid, kind, sid)
    table = {"email_thread": "emails", "meeting": "meetings", "commitment": "commitments"}[kind]
    await project_db["conn"].execute(f"DELETE FROM {table} WHERE user_id = $1", USER)
    detail = await svc.detail(USER, pid)
    assert detail["source_count"] == 1
    source = detail["sources"][0]
    assert source["available"] is False
    assert source["title"] == "Source unavailable"
    assert source["href"] is None
    await svc.unlink(USER, pid, kind, link["id"])
    assert (await svc.detail(USER, pid))["source_count"] == 0


async def test_feature_gate_hides_meeting_content_and_blocks_linking(client, project_db):
    pid = UUID(await create(client))
    link = await svc.link(USER, pid, "meeting", str(project_db["meeting"]))
    await project_db["conn"].execute("UPDATE settings SET meeting_capture_mode = false WHERE user_id = $1", USER)
    assert await svc.search(USER, "meeting") == []
    source = (await svc.detail(USER, pid))["sources"][0]
    assert source["available"] is False and source["detail"] is None
    assert not any(event["source_kind"] == "meeting" and event["event_type"] == "source" for event in await svc.activity(USER, pid))
    assert all(not event["details"].get("title") for event in await svc.activity(USER, pid) if event["source_kind"] == "meeting")
    response = await client.post(f"/projects/{pid}/sources", json={"kind": "meeting", "source_id": str(project_db["meeting"])})
    assert response.status_code == 404
    await svc.unlink(USER, pid, "meeting", link["id"])


async def test_cross_user_api_access(client, project_db):
    pid = await create(client)
    link = await svc.link(USER, UUID(pid), "meeting", str(project_db["meeting"]))
    headers = {"x-test-user": str(OTHER)}
    for path in (f"/projects/{pid}", f"/projects/{pid}/activity", f"/projects/{pid}/threads/thread"):
        assert (await client.get(path, headers=headers)).status_code == 404
    assert (await client.patch(f"/projects/{pid}", json={"name": "Stolen"}, headers=headers)).status_code == 404
    assert (await client.delete(f"/projects/{pid}/sources/meeting/{link['id']}", headers=headers)).status_code == 404
    assert (await client.post(f"/projects/{pid}/sources", json={"kind": "email_thread", "source_id": "private-thread"}, headers=headers)).status_code == 404
    assert (await client.get("/projects", headers=headers)).json()["projects"] == []
    assert all(row["title"] != "Secret" for row in await svc.search(USER, "email_thread"))
    assert (await client.post(f"/projects/{pid}/sources", json={"kind": "email_thread", "source_id": "private-thread"})).status_code == 404
    other_pid = (await client.post("/projects", json={"name": "Other"}, headers=headers)).json()["project"]["id"]
    for kind in ("meeting", "commitment"):
        response = await client.post(f"/projects/{other_pid}/sources", json={"kind": kind, "source_id": str(project_db[kind])}, headers=headers)
        assert response.status_code == 404


async def test_database_composite_foreign_keys_and_rls(client, project_db):
    pid = UUID(await create(client))
    conn = project_db["conn"]
    # Even the bypass-RLS backend role cannot cross ownership via these FKs.
    other_pid = (await svc.create(OTHER, {"name": "Private"}))["id"]
    for table, column, key in (("project_meeting_links", "meeting_id", project_db["meeting"]), ("project_commitment_links", "commitment_id", project_db["commitment"])):
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(f"INSERT INTO {table} (user_id, project_id, {column}) VALUES ($1, $2, $3)", OTHER, other_pid, key)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(f"INSERT INTO {table} (user_id, project_id, {column}) VALUES ($1, $2, $3)", USER, other_pid, key)
    await svc.link(USER, pid, "email_thread", "thread")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await conn.execute("INSERT INTO project_thread_links (user_id, project_id, thread_id) VALUES ($1, $2, 'thread')", OTHER, other_pid)
    await conn.execute("GRANT USAGE ON SCHEMA public, auth TO authenticated; GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated")
    await conn.execute("SELECT set_config('request.jwt.claim.sub', $1, false)", str(OTHER))
    await conn.execute("SET ROLE authenticated")
    try:
        for table in ("projects", "project_email_threads", "project_thread_links", "project_meeting_links", "project_commitment_links", "project_activity"):
            rows = await conn.fetch(f"SELECT user_id FROM {table}")
            assert all(row["user_id"] == OTHER for row in rows)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("INSERT INTO projects (user_id, name) VALUES ($1, 'Forged')", USER)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("INSERT INTO project_email_threads (user_id, thread_id) VALUES ($1, 'thread')", OTHER)
        assert await conn.execute("UPDATE projects SET name = 'Stolen' WHERE id = $1", pid) == "UPDATE 0"
    finally:
        await conn.execute("RESET ROLE")


@pytest.mark.parametrize("values", [{"name": " "}, {"name": "x" * 201}, {"name": "x", "summary": "unsupported"}])
def test_create_validation(values):
    with pytest.raises(ValueError):
        ProjectCreate(**values)


@pytest.mark.parametrize("values", [{"name": None}, {"description": None}, {"status": None}, {"status": "done"}])
def test_patch_validation(values):
    with pytest.raises(ValueError):
        ProjectPatch(**values)


async def test_auth_required():
    app = FastAPI()
    app.include_router(router, prefix="/projects")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Felix's shared dependency requires the Authorization header, so an
        # absent header is a validation error; malformed bearer auth is 401.
        assert (await client.get("/projects")).status_code == 422
        assert (await client.get("/projects", headers={"Authorization": "invalid"})).status_code == 401


@pytest.mark.parametrize("project_db", ["phase2-fresh"], indirect=True)
async def test_phase2_fresh_full_chain(project_db, client):
    from app.services.project_knowledge_service import project_knowledge_service as knowledge

    pid = UUID(await create(client))
    await knowledge.scope(USER, pid, "Fresh install scope", 0)
    await knowledge.create(USER, pid, {"kind": "approval", "title": "Fresh approval"})
    await project_db["conn"].execute(project_db["migration"].read_text())
    data = await knowledge.list(USER, pid)
    assert data["scope"]["content"] == "Fresh install scope"
    assert data["records"][0]["status"] == "pending"
    tables = await project_db["conn"].fetch("SELECT relrowsecurity FROM pg_class WHERE relname LIKE 'project%' AND relkind = 'r'")
    assert len(tables) == 11 and all(row["relrowsecurity"] for row in tables)


async def test_meeting_search_plan_prunes_unrelated_source_kinds(project_db):
    from app.services.project_service import CATALOG

    plan = await project_db["conn"].fetchval("EXPLAIN (FORMAT JSON) " + CATALOG + "SELECT * FROM catalog WHERE kind = $2", USER, "meeting")
    def relations(node):
        found = {node["Relation Name"]} if "Relation Name" in node else set()
        for child in node.get("Plans", []):
            found.update(relations(child))
        return found
    names = relations(plan[0]["Plan"])
    assert "meetings" in names
    assert not names.intersection({"emails", "sent_emails", "commitments"})
