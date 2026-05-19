from unittest.mock import AsyncMock, patch

from app.api.email import email_stats


async def test_email_stats_excludes_archived_by_default():
    with (
        patch(
            "app.api.email.db.query",
            new=AsyncMock(return_value=[{"category": "action_required", "count": 2}]),
        ) as mock_query,
        patch(
            "app.api.email.db.query_one",
            new=AsyncMock(return_value={"count": 1}),
        ),
    ):
        result = await email_stats(
            include_archived=False,
            current_user={"id": "user-123"},
        )

    assert result == {
        "by_category": {"action_required": 2},
        "pending_drafts": 1,
    }
    stats_sql = mock_query.call_args.args[0]
    assert "archived = FALSE" in stats_sql


async def test_email_stats_can_include_archived():
    with (
        patch("app.api.email.db.query", new=AsyncMock(return_value=[])) as mock_query,
        patch("app.api.email.db.query_one", new=AsyncMock(return_value={"count": 0})),
    ):
        await email_stats(include_archived=True, current_user={"id": "user-123"})

    stats_sql = mock_query.call_args.args[0]
    assert "archived = FALSE" not in stats_sql
