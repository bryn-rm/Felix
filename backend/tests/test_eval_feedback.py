"""Tests for eval feedback rating semantics."""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.api import eval as eval_api


def test_feedback_create_rejects_old_wrong_rating_zero():
    with pytest.raises(ValidationError):
        eval_api.FeedbackCreate(feature="triage", rating=0)


def test_feedback_create_accepts_rating_model_values():
    for rating in (1, 2, 3):
        body = eval_api.FeedbackCreate(feature="triage", rating=rating)
        assert body.rating == rating


async def test_feedback_summary_counts_rating_values(monkeypatch):
    query = AsyncMock(return_value=[])
    insert = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(eval_api.db, "query", query)
    monkeypatch.setattr(eval_api.db, "insert", insert)
    monkeypatch.setattr(eval_api.settings, "ADMIN_EMAILS", "admin@example.com")
    monkeypatch.setattr(eval_api.settings, "ADMIN_EMAIL", "")

    await eval_api.get_feedback_summary({"id": "user-1", "email": "admin@example.com"})

    sql = query.await_args.args[0].lower()
    assert "ef.rating = 3              then 1 else 0 end)  as good_count" in sql
    assert "ef.rating = 2              then 1 else 0 end)  as edited_count" in sql
    assert "ef.rating = 1              then 1 else 0 end)  as wrong_count" in sql
    assert "ef.correction is not null" not in sql
