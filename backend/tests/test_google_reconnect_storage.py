"""Real PostgreSQL migration, isolation, and overlapping OAuth/refresh checks.

Uses the same disposable-database URL as the project storage tests.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import HTTPException
from google.auth.exceptions import RefreshError

from app import db
from app.api.auth import google_connection_status
from app.middleware import auth

ROOT = Path(__file__).resolve().parents[2]
USER = UUID("11111111-1111-1111-1111-111111111111")
OTHER = UUID("22222222-2222-2222-2222-222222222222")
MIGRATION = ROOT / "infra/migrations/026_google_reconnect_status.sql"


@pytest.fixture
async def google_db(monkeypatch, request):
    url = os.environ.get("PROJECT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set PROJECT_TEST_DATABASE_URL for PostgreSQL reconnect tests")
    admin = await asyncpg.connect(url)
    name = "felix_google_test_" + uuid4().hex
    await admin.execute(f'CREATE DATABASE "{name}"')
    test_url = urlunsplit(urlsplit(url)._replace(path="/" + name))
    conn = await asyncpg.connect(test_url)
    pool = None
    try:
        await conn.execute("""
            CREATE SCHEMA auth;
            CREATE TABLE auth.users (id uuid PRIMARY KEY, email text);
            CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
            $$ SELECT nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
            CREATE FUNCTION auth.role() RETURNS text LANGUAGE sql STABLE AS
            $$ SELECT current_setting('request.jwt.claim.role', true) $$;
            DO $$ BEGIN CREATE ROLE authenticated NOLOGIN;
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """)
        await conn.execute((ROOT / "infra/schema.sql").read_text())
        for path in sorted((ROOT / "infra/migrations").glob("*.sql")):
            if path.name < MIGRATION.name:
                await conn.execute(path.read_text())
        if getattr(request, "param", "upgrade") == "fresh":
            await conn.execute(MIGRATION.read_text())
        for user_id in (USER, OTHER):
            await conn.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)
            await conn.execute(
                "INSERT INTO google_connections (user_id, google_email, access_token, refresh_token, token_expiry) "
                "VALUES ($1, 'user@example.com', $2, $3, $4)", user_id,
                auth.encrypt_token("old-access"), auth.encrypt_token("old-refresh"),
                datetime.now(timezone.utc) - timedelta(hours=1),
            )
        await conn.execute(MIGRATION.read_text())
        pool = await asyncpg.create_pool(test_url, min_size=1, max_size=3, init=db._init_connection)
        monkeypatch.setattr(db, "_pool", pool)
        yield conn
    finally:
        if pool:
            await pool.close()
        await conn.close()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


@pytest.mark.parametrize("google_db", ["fresh", "upgrade"], indirect=True)
async def test_migration_preserves_tokens_and_backend_only_rls(google_db):
    conn = google_db
    await conn.execute(MIGRATION.read_text())  # rerunnable
    row = await conn.fetchrow("SELECT * FROM google_connections WHERE user_id = $1", USER)
    assert row["needs_reconnect"] is False
    assert auth.decrypt_token(row["refresh_token"]) == "old-refresh"
    await conn.execute("UPDATE google_connections SET needs_reconnect = TRUE WHERE user_id = $1", USER)
    await conn.execute(MIGRATION.read_text())
    assert await conn.fetchval("SELECT needs_reconnect FROM google_connections WHERE user_id = $1", USER)
    assert await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = 'google_connections'::regclass")
    await conn.execute("GRANT USAGE ON SCHEMA public, auth TO authenticated")
    await conn.execute("GRANT SELECT ON google_connections TO authenticated")
    async with conn.transaction():
        await conn.execute("SET LOCAL ROLE authenticated")
        await conn.execute("SELECT set_config('request.jwt.claim.sub', $1, true)", str(USER))
        await conn.execute("SELECT set_config('request.jwt.claim.role', 'authenticated', true)")
        assert await conn.fetch("SELECT * FROM google_connections") == []


async def test_revocation_is_persisted_for_only_its_user(google_db, monkeypatch):
    def revoked(*_):
        raise RefreshError("revoked", {"error": "invalid_grant"})
    monkeypatch.setattr(auth.Credentials, "refresh", revoked)
    with pytest.raises(HTTPException) as exc:
        await auth.get_google_credentials(str(USER))
    assert exc.value.status_code == 403
    assert (await google_connection_status({"id": str(USER)}))["needs_reconnect"] is True
    assert (await google_connection_status({"id": str(OTHER)}))["connected"] is True


@pytest.mark.parametrize("refresh_fails", [False, True])
@pytest.mark.parametrize("disconnect", [False, True])
async def test_old_refresh_cannot_overwrite_reconnect_or_disconnect(google_db, monkeypatch, refresh_fails, disconnect):
    async def finish_refresh_after_connection_changed(refresh, _request):
        if disconnect:
            await google_db.execute("DELETE FROM google_connections WHERE user_id = $1", USER)
        else:
            await google_db.execute(
                "UPDATE google_connections SET access_token = $2, refresh_token = $3, "
                "needs_reconnect = FALSE WHERE user_id = $1", USER,
                auth.encrypt_token("reconnected-access"), auth.encrypt_token("reconnected-refresh"),
            )
        if refresh_fails:
            raise RefreshError("revoked", {"error": "invalid_grant"})
        creds = refresh.__self__
        creds.token = "stale-refreshed-access"
        creds.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr(auth.asyncio, "to_thread", finish_refresh_after_connection_changed)
    if refresh_fails:
        with pytest.raises(HTTPException):
            await auth.get_google_credentials(str(USER))
    else:
        await auth.get_google_credentials(str(USER))
    row = await google_db.fetchrow("SELECT * FROM google_connections WHERE user_id = $1", USER)
    if disconnect:
        assert row is None
    else:
        assert row["needs_reconnect"] is False
        assert auth.decrypt_token(row["access_token"]) == "reconnected-access"
        assert auth.decrypt_token(row["refresh_token"]) == "reconnected-refresh"


async def test_successful_refresh_persists_rotated_credentials(google_db, monkeypatch):
    def refresh(creds, _request):
        creds.token = "new-access"
        creds._refresh_token = "rotated-refresh"
        creds.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    monkeypatch.setattr(auth.Credentials, "refresh", refresh)
    await auth.get_google_credentials(str(USER))
    row = await google_db.fetchrow("SELECT * FROM google_connections WHERE user_id = $1", USER)
    assert auth.decrypt_token(row["access_token"]) == "new-access"
    assert auth.decrypt_token(row["refresh_token"]) == "rotated-refresh"
    assert row["token_expiry"] > datetime.now(timezone.utc)
    assert row["needs_reconnect"] is False
