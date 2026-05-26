"""Unit tests for the /settings routes."""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.settings import router
from app.middleware.auth import get_current_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(user_id: str = "user-test-001", email: str = "test@example.com") -> FastAPI:
    """Return a minimal FastAPI app with only the settings router."""
    app = FastAPI()
    app.include_router(router, prefix="/settings")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id,
        "email": email,
        "metadata": {"full_name": "Test User"},
    }
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_app())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_invalid_timezone_returns_422(client: TestClient):
    """Unrecognised IANA timezone name → 422 Unprocessable Entity."""
    resp = client.patch("/settings", json={"timezone": "Mars/Olympus_Mons"})
    assert resp.status_code == 422
    body = resp.json()
    # Pydantic should surface the validation error detail
    assert any(
        "timezone" in str(e).lower() or "unknown" in str(e).lower()
        for e in body.get("detail", [])
    )


def test_invalid_digest_time_returns_422(client: TestClient):
    """Digest times not matching HH:00 or HH:30 → 422."""
    resp = client.patch("/settings", json={"digest_times": ["25:00", "9:15"]})
    assert resp.status_code == 422


def test_valid_settings_update_succeeds(client: TestClient):
    """Valid timezone + display_name → 200 with the updated row."""
    updated_row = {
        "user_id": "user-test-001",
        "display_name": "Felix User",
        "timezone": "America/New_York",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    with patch("app.db.upsert", new_callable=AsyncMock, return_value=updated_row):
        resp = client.patch(
            "/settings",
            json={"timezone": "America/New_York", "display_name": "Felix User"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["timezone"] == "America/New_York"
    assert body["display_name"] == "Felix User"


def test_voice_options_returns_configured_catalog(client: TestClient):
    """Voice dropdown options come from the backend-approved catalog."""
    catalog = '[{"id":"voice-rachel","label":"Rachel"},{"id":"voice-adam","label":"Adam"}]'
    with (
        patch("app.api.settings.settings.FELIX_VOICE_CATALOG", catalog),
        patch("app.db.query_one", new_callable=AsyncMock, return_value={"felix_voice_id": None}),
    ):
        resp = client.get("/settings/voices")

    assert resp.status_code == 200
    assert resp.json()["voices"] == [
        {"id": "", "label": "System default"},
        {"id": "voice-rachel", "label": "Rachel"},
        {"id": "voice-adam", "label": "Adam"},
    ]


def test_voice_options_preserves_unknown_saved_voice(client: TestClient):
    """Legacy saved voice IDs remain visible even after catalog changes."""
    with (
        patch("app.api.settings.settings.FELIX_VOICE_CATALOG", "[]"),
        patch("app.db.query_one", new_callable=AsyncMock, return_value={"felix_voice_id": "legacy-id"}),
    ):
        resp = client.get("/settings/voices")

    assert resp.status_code == 200
    assert resp.json()["voices"] == [
        {"id": "", "label": "System default"},
        {"id": "legacy-id", "label": "Current (legacy-id)"},
    ]


def test_configured_voice_id_update_succeeds(client: TestClient):
    """Users can save a voice ID only when it appears in the approved catalog."""
    catalog = '[{"id":"voice-rachel","label":"Rachel"}]'
    updated_row = {
        "user_id": "user-test-001",
        "felix_voice_id": "voice-rachel",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    with (
        patch("app.api.settings.settings.FELIX_VOICE_CATALOG", catalog),
        patch("app.db.query_one", new_callable=AsyncMock, return_value={"felix_voice_id": None}),
        patch("app.db.upsert", new_callable=AsyncMock, return_value=updated_row),
    ):
        resp = client.patch("/settings", json={"felix_voice_id": "voice-rachel"})

    assert resp.status_code == 200
    assert resp.json()["felix_voice_id"] == "voice-rachel"


def test_unknown_new_voice_id_returns_422(client: TestClient):
    """Arbitrary ElevenLabs voice IDs cannot be saved unless approved."""
    with (
        patch("app.api.settings.settings.FELIX_VOICE_CATALOG", "[]"),
        patch("app.db.query_one", new_callable=AsyncMock, return_value={"felix_voice_id": None}),
        patch("app.db.upsert", new_callable=AsyncMock) as mock_upsert,
    ):
        resp = client.patch("/settings", json={"felix_voice_id": "unapproved-id"})

    assert resp.status_code == 422
    mock_upsert.assert_not_called()


def test_null_voice_id_clears_override(client: TestClient):
    """Explicit null clears the saved voice override."""
    updated_row = {
        "user_id": "user-test-001",
        "felix_voice_id": None,
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    with patch("app.db.upsert", new_callable=AsyncMock, return_value=updated_row):
        resp = client.patch("/settings", json={"felix_voice_id": None})

    assert resp.status_code == 200
    assert resp.json()["felix_voice_id"] is None


def test_settings_scoped_to_current_user():
    """Each user receives only their own settings row — user_id is never leaked."""
    row_a = {"user_id": "user-A", "timezone": "Europe/London",       "display_name": "Alice"}
    row_b = {"user_id": "user-B", "timezone": "America/Los_Angeles", "display_name": "Bob"}

    client_a = TestClient(_make_app(user_id="user-A"))
    client_b = TestClient(_make_app(user_id="user-B"))

    async def _query_one(sql: str, uid: str):
        return row_a if uid == "user-A" else row_b

    with patch("app.db.query_one", new_callable=AsyncMock) as mock_q:
        mock_q.side_effect = _query_one
        resp_a = client_a.get("/settings")
        resp_b = client_b.get("/settings")

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert resp_a.json()["timezone"] == "Europe/London"
    assert resp_b.json()["timezone"] == "America/Los_Angeles"
    # Confirm different rows were returned
    assert resp_a.json()["user_id"] != resp_b.json()["user_id"]
