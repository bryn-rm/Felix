from unittest.mock import AsyncMock, MagicMock, patch

from app.services.chat_tools import (
    _create_calendar_event,
    try_confirm_pending_calendar_proposals,
)
from app.services.voice_router import _general_question


def _proposal_row(row_id: str = "proposal-1") -> dict:
    return {
        "id": row_id,
        "payload": {
            "title": "Tennis booking",
            "start_iso": "2026-05-12T10:00:00",
            "end_iso": "2026-05-12T12:00:00",
            "timezone": "Europe/London",
            "attendees": [],
            "description": "Court 1",
        },
    }


def _calendar_service_mock() -> MagicMock:
    cal = MagicMock()
    cal.create_event = AsyncMock(return_value={
        "id": "cal-1",
        "title": "Tennis booking",
        "start": "2026-05-12T10:00:00",
        "end": "2026-05-12T12:00:00",
    })
    return cal


async def test_confirmation_fast_path_creates_event_and_bypasses_agent():
    row = _proposal_row()
    mock_calendar = _calendar_service_mock()
    pending_query = AsyncMock(return_value=[row])

    with patch("app.services.chat_tools.db.query", new=pending_query):
        with patch("app.services.chat_tools.db.execute", new=AsyncMock()) as execute:
            with patch("app.services.chat_tools.get_google_credentials", new=AsyncMock(return_value=object())):
                with patch("app.services.chat_tools.CalendarService", return_value=mock_calendar):
                    with patch(
                        "app.services.ai_service.ai_service.answer_with_tools",
                        new=AsyncMock(return_value="LLM should not run"),
                    ) as answer_with_tools:
                        response = await _general_question(
                            {"intent": "general_question", "raw_transcript": "yes add to calendar"},
                            user_id="user-1",
                            gmail=None,
                            user_name="Test",
                        )

    assert response == "Done, I added Tennis booking to your calendar on Tuesday 12 May at 10am."
    pending_query.assert_awaited_once()
    mock_calendar.create_event.assert_awaited_once()
    execute.assert_awaited_once_with("DELETE FROM pending_calendar_proposals WHERE id = $1", "proposal-1")
    answer_with_tools.assert_not_awaited()


async def test_repeated_pending_offers_create_one_calendar_event():
    rows = [_proposal_row("proposal-1"), _proposal_row("proposal-2")]
    mock_calendar = _calendar_service_mock()

    with patch("app.services.chat_tools.db.query", new=AsyncMock(return_value=rows)):
        with patch("app.services.chat_tools.db.execute", new=AsyncMock()) as execute:
            with patch("app.services.chat_tools.get_google_credentials", new=AsyncMock(return_value=object())):
                with patch("app.services.chat_tools.CalendarService", return_value=mock_calendar):
                    result = await _create_calendar_event("user-1", latest_user_turn="yes")

    assert result["ok"] is True
    assert result["count"] == 1
    mock_calendar.create_event.assert_awaited_once()
    assert execute.await_count == 2


async def test_non_confirmation_returns_none_without_querying_pending_offers():
    with patch("app.services.chat_tools.db.query", new=AsyncMock()) as query:
        result = await try_confirm_pending_calendar_proposals("user-1", "what time is it?")

    assert result is None
    query.assert_not_awaited()


async def test_confirmation_with_no_pending_offer_does_not_create_event():
    with patch("app.services.chat_tools.db.query", new=AsyncMock(return_value=[])):
        with patch("app.services.chat_tools.CalendarService") as calendar_service:
            result = await try_confirm_pending_calendar_proposals("user-1", "yes")

    assert result is None
    calendar_service.assert_not_called()


async def test_non_confirmation_followup_uses_normal_agent_path():
    with patch("app.services.voice_router.db.query", new=AsyncMock(return_value=[])):
        with patch("app.services.voice_router.db.query_one", new=AsyncMock(return_value=None)):
            with patch("app.services.voice_router.get_google_credentials", new=AsyncMock(side_effect=Exception("no creds"))):
                with patch("app.services.voice_router.memory_service.build_memory_context", new=AsyncMock(return_value="")):
                    with patch(
                        "app.services.ai_service.ai_service.answer_with_tools",
                        new=AsyncMock(return_value="Normal agent response"),
                    ) as answer_with_tools:
                        response = await _general_question(
                            {"intent": "general_question", "raw_transcript": "tell me more about it"},
                            user_id="user-1",
                            gmail=None,
                            user_name="Test",
                        )

    assert response == "Normal agent response"
    answer_with_tools.assert_awaited_once()
