"""Tests for voice scheduling date resolution and event creation."""
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun import freeze_time
import pytz

from app.prompts.voice_intent import VOICE_INTENT_PROMPT
from app.services.voice_router import _resolve_schedule_date, _schedule_meeting


@freeze_time("2026-04-12 09:00:00")
def test_resolve_schedule_date_prefers_explicit_date_iso():
    target_date, hint_hour, clarification = _resolve_schedule_date(
        {
            "timeframe": "monday",
            "date_iso": "2026-04-18",
            "raw_transcript": "Add tennis to my calendar on April 18th 5pm to 7pm",
        },
        pytz.timezone("Europe/London"),
    )

    assert target_date.isoformat() == "2026-04-18"
    assert hint_hour == 10
    assert clarification is None


@freeze_time("2026-04-12 09:00:00")
def test_resolve_schedule_date_asks_when_date_and_weekday_conflict():
    target_date, hint_hour, clarification = _resolve_schedule_date(
        {
            "timeframe": "saturday",
            "date_iso": "2026-04-18",
            "weekday": "monday",
            "raw_transcript": "Add tennis on Monday April 18th",
        },
        pytz.timezone("Europe/London"),
    )

    assert target_date.isoformat() == "2026-04-12"
    assert hint_hour == 10
    assert clarification == "You said April 18 and Monday, but those don't match. Which date should I use?"


@freeze_time("2026-04-12 09:00:00")
def test_resolve_schedule_date_parses_month_day_from_transcript():
    target_date, hint_hour, clarification = _resolve_schedule_date(
        {
            "timeframe": "",
            "raw_transcript": "Add tennis to my calendar on April 18th from 5pm to 7pm",
        },
        pytz.timezone("Europe/London"),
    )

    assert target_date.isoformat() == "2026-04-18"
    assert hint_hour == 10
    assert clarification is None


@freeze_time("2026-12-20 09:00:00")
def test_resolve_schedule_date_rolls_month_day_into_next_year():
    target_date, _, clarification = _resolve_schedule_date(
        {
            "timeframe": "",
            "raw_transcript": "Schedule planning on January 5th at 10am",
        },
        pytz.timezone("Europe/London"),
    )

    assert target_date.isoformat() == "2027-01-05"
    assert clarification is None


async def test_schedule_meeting_creates_event_using_structured_date():
    mock_calendar = MagicMock()
    mock_calendar.create_event = AsyncMock(return_value={})

    with patch("app.services.voice_router._get_user_timezone", new=AsyncMock(return_value="Europe/London")):
        with patch("app.services.voice_router.get_google_credentials", new=AsyncMock(return_value=MagicMock())):
            with patch("app.services.voice_router.CalendarService", return_value=mock_calendar):
                response = await _schedule_meeting(
                    {
                        "topic": "tennis",
                        "timeframe": "",
                        "date_iso": "2026-04-18",
                        "start_time": "17:00",
                        "end_time": "19:00",
                        "raw_transcript": "Add tennis to my calendar on April 18th 5pm - 7pm",
                    },
                    user_id="user-1",
                    gmail=None,
                    user_name="Felix",
                )

    mock_calendar.create_event.assert_awaited_once()
    event_body = mock_calendar.create_event.await_args.args[0]
    assert event_body["start"]["dateTime"].startswith("2026-04-18T17:00:00")
    assert event_body["end"]["dateTime"].startswith("2026-04-18T19:00:00")
    assert event_body["summary"] == "tennis"
    assert "Saturday 18 Apr at 5pm" in response


async def test_schedule_meeting_returns_clarification_without_creating_event():
    mock_calendar = MagicMock()
    mock_calendar.create_event = AsyncMock(return_value={})

    with patch("app.services.voice_router._get_user_timezone", new=AsyncMock(return_value="Europe/London")):
        with patch("app.services.voice_router.get_google_credentials", new=AsyncMock(return_value=MagicMock())):
            with patch("app.services.voice_router.CalendarService", return_value=mock_calendar):
                response = await _schedule_meeting(
                    {
                        "topic": "tennis",
                        "timeframe": "",
                        "date_iso": "2026-04-18",
                        "weekday": "monday",
                        "start_time": "17:00",
                        "end_time": "19:00",
                        "raw_transcript": "Add tennis to my calendar on Monday April 18th 5pm - 7pm",
                    },
                    user_id="user-1",
                    gmail=None,
                    user_name="Felix",
                )

    mock_calendar.create_event.assert_not_awaited()
    assert response == "You said April 18 and Monday, but those don't match. Which date should I use?"


def test_voice_intent_prompt_includes_structured_schedule_fields():
    assert '"date_iso"' in VOICE_INTENT_PROMPT
    assert '"weekday"' in VOICE_INTENT_PROMPT
    assert "prefer structured dates" in VOICE_INTENT_PROMPT
