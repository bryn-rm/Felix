"""Tests for admin access helpers and probe endpoint."""

import pytest
from fastapi import HTTPException

from app.api import eval as eval_api


def test_require_admin_accepts_any_configured_admin(monkeypatch):
    monkeypatch.setattr(eval_api.settings, "ADMIN_EMAILS", "alice@example.com, bob@example.com")
    monkeypatch.setattr(eval_api.settings, "ADMIN_EMAIL", "")

    eval_api._require_admin({"email": "BOB@example.com"})


def test_require_admin_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(eval_api.settings, "ADMIN_EMAILS", "alice@example.com, bob@example.com")
    monkeypatch.setattr(eval_api.settings, "ADMIN_EMAIL", "")

    with pytest.raises(HTTPException) as exc:
        eval_api._require_admin({"email": "carol@example.com"})

    assert exc.value.status_code == 403


async def test_admin_me_returns_200_for_admin(monkeypatch):
    monkeypatch.setattr(eval_api.settings, "ADMIN_EMAILS", "alice@example.com, bob@example.com")
    monkeypatch.setattr(eval_api.settings, "ADMIN_EMAIL", "")

    result = await eval_api.get_admin_me({"id": "user-1", "email": "alice@example.com"})

    assert result == {"admin": True}
