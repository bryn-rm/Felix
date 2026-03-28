"""Shared pytest fixtures for the Felix backend test suite."""
import os
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Inject stub environment variables so app.config.Settings() can instantiate
# during test collection without a real .env file.
# os.environ.setdefault only sets a key when it isn't already present, so
# real credentials in the environment are never overwritten.
# ---------------------------------------------------------------------------
_TEST_ENV = {
    "GOOGLE_CLIENT_ID":      "test-google-client-id",
    "GOOGLE_CLIENT_SECRET":  "test-google-client-secret",
    "GOOGLE_REDIRECT_URI":   "http://localhost:8000/auth/google/callback",
    "GCP_PROJECT_ID":        "test-gcp-project",
    "ANTHROPIC_API_KEY":     "test-anthropic-key",
    "ELEVENLABS_API_KEY":    "test-elevenlabs-key",
    "FELIX_VOICE_ID":        "test-voice-id",
    "SUPABASE_URL":          "https://test.supabase.co",
    "SUPABASE_SERVICE_KEY":  "test-service-key",
    "DATABASE_URL":          "postgresql://test:test@localhost/testdb",
    # Must be a valid 64-char hex string for Fernet key derivation
    "TOKEN_ENCRYPTION_KEY":  "a" * 64,
    "BACKEND_SECRET_KEY":    "test-backend-secret",
}
for _key, _val in _TEST_ENV.items():
    os.environ.setdefault(_key, _val)

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
