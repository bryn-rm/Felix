"""Unit tests for JWT validation and Google OAuth/credential handling."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException

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
    assert "not connected" in exc.value.detail.lower()


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
        patch("app.api.auth.db.upsert", new_callable=AsyncMock),
        patch("app.api.auth.encrypt_token", side_effect=lambda value: value),
        patch("app.api.auth.httpx.AsyncClient", return_value=client_context),
    ):
        response = await auth_api.google_callback(
            code="google-code", state=state, error=None,
        )

    assert response.headers["location"] == auth_api._frontend_redirect_url(VIEWER_PATH)
