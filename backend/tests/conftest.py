"""Shared pytest fixtures for the Felix backend test suite."""
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Supabase user fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_supabase_user():
    """Return a fake Supabase User-like object for auth mocking."""
    user = MagicMock()
    user.id = "test-user-id-123"
    user.email = "test@example.com"
    user.user_metadata = {"full_name": "Test User"}
    return user


# ---------------------------------------------------------------------------
# Database pool fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db_pool():
    """Return a mock asyncpg connection pool."""
    pool = AsyncMock()

    # Simulate pool.acquire() as an async context manager
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    # Common asyncpg methods
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="OK")

    pool._conn = conn  # convenience handle for tests that need to configure returns
    return pool


# ---------------------------------------------------------------------------
# Google credentials fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_google_credentials():
    """Return a mock google.oauth2.credentials.Credentials object."""
    creds = MagicMock()
    creds.token = "mock-access-token"
    creds.refresh_token = "mock-refresh-token"
    creds.client_id = "mock-client-id"
    creds.client_secret = "mock-client-secret"
    creds.token_uri = "https://oauth2.googleapis.com/token"
    creds.expired = False
    creds.valid = True
    creds.scopes = frozenset([
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/calendar.readonly",
    ])
    return creds
