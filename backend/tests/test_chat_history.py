from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.api.voice import _merge_chat_history
from app.services.ai_service import ai_service
from app.services.voice_router import _should_route_schedule_followup_to_agent


async def test_answer_with_tools_sends_recent_conversation_history_to_claude():
    captured: dict = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="Done.")],
        )

    long_history = [
        {"role": "user", "content": f"user turn {i}"}
        if i % 2 == 0
        else {"role": "assistant", "content": f"assistant turn {i}"}
        for i in range(18)
    ]

    with patch("app.services.ai_service.client.messages.create", new=AsyncMock(side_effect=fake_create)):
        with patch("app.services.ai_service._auto_memory", new=AsyncMock(return_value="")):
            with patch("app.services.ai_service.log_ai_call", new=AsyncMock()):
                response = await ai_service.answer_with_tools(
                    transcript="Add both to my calendar",
                    user_name="Test",
                    felix_context="",
                    today_str="Tuesday 05 May 2026",
                    user_timezone="Europe/London",
                    history=long_history,
                    tools=[],
                    tool_dispatcher=AsyncMock(),
                    user_id="user-1",
                    memory_context="",
                )

    assert response == "Done."
    messages = captured["messages"]
    assert messages[0] == {"role": "assistant", "content": "assistant turn 3"}
    assert messages[-2] == {"role": "assistant", "content": "assistant turn 17"}
    assert messages[-1]["role"] == "user"
    assert "User said: Add both to my calendar" in messages[-1]["content"]
    assert len(messages) == 16  # last 15 prior turns + current turn


def test_contextual_schedule_followup_with_history_routes_to_tool_agent():
    assert _should_route_schedule_followup_to_agent(
        {
            "intent": "schedule_meeting",
            "raw_transcript": "Add both to my calendar",
            "history": [
                {"role": "user", "content": "What tennis bookings do I have?"},
                {
                    "role": "assistant",
                    "content": (
                        "Tuesday 12 May, Court 1, 10am-12pm, GBP 10.90. "
                        "Thursday 14 May, Court 4, 11am-1pm, GBP 10.90."
                    ),
                },
            ],
        }
    )


def test_explicit_schedule_request_stays_on_direct_scheduler():
    assert not _should_route_schedule_followup_to_agent(
        {
            "intent": "schedule_meeting",
            "raw_transcript": "Add tennis to my calendar on 12 May from 10am to noon",
            "date_iso": "2026-05-12",
            "start_time": "10:00",
            "end_time": "12:00",
            "history": [{"role": "user", "content": "What's on today?"}],
        }
    )


def test_merge_chat_history_prefers_richest_bounded_history_source():
    client_history = [{"role": "user", "content": "client only"}]
    session_history = [
        {"role": "user", "content": f"session {i}"}
        for i in range(20)
    ]

    merged = _merge_chat_history(client_history, session_history)

    assert len(merged) == 15
    assert merged[0]["content"] == "session 5"
    assert merged[-1]["content"] == "session 19"
