"""Unit tests for JWT validation and Google OAuth/credential handling."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from google.auth.exceptions import RefreshError, TransportError

from app.api import auth as auth_api
from app.middleware.auth import get_current_user, get_google_credentials


# ---------------------------------------------------------------------------
# get_current_user
# ---------------------------------------------------------------------------


async def test_missing_token_returns_401():
    """Empty Bearer value → 401 before Supabase is ever called."""
    with pytest.raises(HTTPException) as exc:
        await get_current_user(authorization="Bearer ")
    assert exc.value.status_code == 401


async def test_expired_token_returns_401(mock_supabase_user):
    """Supabase auth.get_user raises (e.g. token expired) → 401."""
    with patch("app.middleware.auth._get_supabase") as mock_sb:
        mock_sb.return_value.auth.get_user.side_effect = Exception("Token has expired")
        with pytest.raises(HTTPException) as exc:
            await get_current_user(authorization="Bearer expired-token-abc")
    assert exc.value.status_code == 401


async def test_valid_token_returns_user(mock_supabase_user):
    """Valid token → dict with id and email from Supabase user."""
    result = MagicMock()
    result.user = mock_supabase_user

    with patch("app.middleware.auth._get_supabase") as mock_sb:
        mock_sb.return_value.auth.get_user.return_value = result
        user = await get_current_user(authorization="Bearer valid-token-abc")

    assert user["id"] == mock_supabase_user.id
    assert user["email"] == mock_supabase_user.email


# ---------------------------------------------------------------------------
# get_google_credentials
# ---------------------------------------------------------------------------


async def test_google_credentials_not_found_returns_403():
    """No google_connections row for user → 403 with helpful message."""
    with patch("app.db.query_one", new_callable=AsyncMock, return_value=None):
        with pytest.raises(HTTPException) as exc:
            await get_google_credentials("user-without-google")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "google_reconnect_required"


# ---------------------------------------------------------------------------
# Google connect return destination
# ---------------------------------------------------------------------------

VIEWER_PATH = "/meetings/live/m-1/viewer?from=phone"


async def test_connect_google_carries_a_safe_return_path_in_oauth_state():
    with (
        patch("app.api.auth.db.execute", new_callable=AsyncMock),
        patch("app.api.auth.secrets.token_urlsafe", return_value="nonce-1"),
    ):
        result = await auth_api.connect_google(
            next=VIEWER_PATH,
            current_user={"id": "user-1"},
        )

    state = parse_qs(urlsplit(result["auth_url"]).query)["state"][0]
    assert auth_api._parse_oauth_state(state) == (
        "user-1", "nonce-1", VIEWER_PATH,
    )


def test_oauth_state_rejects_an_off_site_return_path():
    state = auth_api._oauth_state(
        "user-1", "nonce-1", "https://evil.example/steal",
    )

    assert auth_api._parse_oauth_state(state) == ("user-1", "nonce-1", None)


async def test_google_callback_returns_to_the_protected_destination():
    state = auth_api._oauth_state("user-1", "nonce-1", VIEWER_PATH)
    client = AsyncMock()
    client.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 3600,
        },
    )
    client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"email": "user@example.com"},
    )
    client_context = MagicMock()
    client_context.__aenter__ = AsyncMock(return_value=client)
    client_context.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "app.api.auth.db.query_one",
            new_callable=AsyncMock,
            return_value={
                "user_id": "user-1",
                "created_at": datetime.now(timezone.utc),
                "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
        ),
        patch("app.api.auth.db.execute", new_callable=AsyncMock),
        patch("app.api.auth.db.upsert", new_callable=AsyncMock) as upsert,
        patch("app.api.auth.encrypt_token", side_effect=lambda value: value),
        patch("app.api.auth.httpx.AsyncClient", return_value=client_context),
    ):
        response = await auth_api.google_callback(
            code="google-code", state=state, error=None,
        )

    assert response.headers["location"] == auth_api._frontend_redirect_url(VIEWER_PATH)
    assert upsert.call_args.args[1]["needs_reconnect"] is False


@pytest.mark.parametrize("needs_reconnect", [False, True])
async def test_google_status_distinguishes_expired_connection(needs_reconnect):
    row = {"google_email": "user@example.com", "connected_at": None,
           "last_sync": None, "needs_reconnect": needs_reconnect}
    with patch("app.db.query_one", new=AsyncMock(return_value=row)) as query:
        result = await auth_api.google_connection_status({"id": "user-1"})
    assert result["connected"] is (not needs_reconnect)
    assert result["needs_reconnect"] is needs_reconnect
    assert result["google_email"] == "user@example.com"
    assert query.call_args.args[1] == "user-1"


@pytest.mark.parametrize("failure, expected_status, persisted", [
    (RefreshError("revoked", {"error": "invalid_grant"}), 403, True),
    (RefreshError("server error", {"error": "temporarily_unavailable"}, retryable=True), 503, False),
    (RefreshError("client configuration", {"error": "invalid_client"}), 503, False),
    (TransportError("connection timeout"), 503, False),
])
async def test_refresh_failure_only_marks_revoked_credentials(failure, expected_status, persisted):
    row = {"access_token": "encrypted-access", "refresh_token": "encrypted-refresh",
           "token_expiry": datetime.now(timezone.utc) - timedelta(hours=1),
           "needs_reconnect": False}
    with (
        patch("app.db.query_one", new=AsyncMock(return_value=row)),
        patch("app.db.execute", new_callable=AsyncMock) as execute,
        patch("app.middleware.auth.decrypt_token", side_effect=lambda token: token),
        patch("app.middleware.auth.Credentials.refresh", side_effect=failure),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_google_credentials("user-1")
    assert exc.value.status_code == expected_status
    assert execute.called is persisted
    if persisted:
        assert exc.value.detail["code"] == "google_reconnect_required"
        # A failure from old credentials must not invalidate a newer reconnect.
        sql, *args = execute.call_args.args
        assert "user_id = $1" in sql
        assert "access_token = $2" in sql and "refresh_token = $3" in sql
        assert args == ["user-1", "encrypted-access", "encrypted-refresh"]


async def test_known_revoked_credentials_do_not_retry_google():
    with (
        patch("app.db.query_one", new=AsyncMock(return_value={"needs_reconnect": True})),
        patch("app.middleware.auth.Credentials.refresh") as refresh,
    ):
        with pytest.raises(HTTPException) as exc:
            await get_google_credentials("user-1")
    assert exc.value.status_code == 403
    refresh.assert_not_called()


async def test_cancelled_google_consent_preserves_return_path_and_credentials():
    with patch("app.db.upsert", new_callable=AsyncMock) as upsert:
        response = await auth_api.google_callback(
            code=None, error="access_denied",
            state=auth_api._oauth_state("user-1", "nonce-1", VIEWER_PATH),
        )
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params == {"error": ["google_denied"], "next": [VIEWER_PATH]}
    upsert.assert_not_called()


@pytest.mark.parametrize("path", ["/calendar/events", "/calendar/today"])
async def test_reconnect_error_survives_production_error_envelope(path):
    import httpx
    from app.main import app

    async def identity():
        return {"id": "user-1"}

    app.dependency_overrides[get_current_user] = identity
    try:
        with patch("app.db.query_one", new=AsyncMock(return_value={"needs_reconnect": True})):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(path)
        assert response.status_code == 403
        assert response.json()["code"] == "google_reconnect_required"
        assert isinstance(response.json()["message"], str)
        assert "Settings" in response.json()["message"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)
