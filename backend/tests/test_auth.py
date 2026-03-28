"""Unit tests for JWT validation and Google credential loading."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

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
