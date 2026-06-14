from unittest.mock import AsyncMock, patch

from app.api.email import email_stats, list_emails


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


async def test_list_emails_searches_server_side_and_logs(caplog):
    async def fake_query_one(sql, *args):
        if "COUNT" in sql:
            return {"total": 1}
        return None

    with (
        patch("app.api.email._ensure_google_connected", new=AsyncMock()),
        patch("app.api.email.db.query_one", new=AsyncMock(side_effect=fake_query_one)),
        patch("app.api.email.db.query", new=AsyncMock(return_value=[{"id": "e1"}])) as mock_query,
        caplog.at_level("INFO", logger="app.api.email"),
    ):
        result = await list_emails(
            category="action_required",
            urgency=None,
            search="Acme 50%_off",
            draft_pending=None,
            include_archived=False,
            limit=25,
            offset=0,
            current_user={"id": "user-123"},
        )

    sql = mock_query.call_args.args[0]
    args = mock_query.call_args.args[1:]
    assert result["emails"] == [{"id": "e1"}]
    assert "LIKE $" in sql
    assert "ESCAPE '^'" in sql
    assert "e.category = $2" in sql
    assert args == ("user-123", "action_required", "%acme%", "%50^%^_off%", 25, 0)
    assert "email_list_search user_id=user-123" in caplog.text
