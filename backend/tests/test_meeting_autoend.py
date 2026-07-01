"""
Meeting auto-end safety-net tests — Phase 7 (plan §5 test g).

Covers the stale-recording sweep:
  • silence timeout finalizes an abandoned meeting (same path as /end);
  • a recently-active meeting is left alone;
  • a meeting past its linked calendar event's scheduled end is finalized even
    while "active" by the silence measure;
  • the direct query path works for a non-Google-connected user (no
    get_active_users / google_connections dependency);
  • a freshly-started meeting with no segments yet is not ended on the first sweep;
  • a per-meeting failure does not abort the rest of the sweep.

The DB query and end_meeting / calendar-fetch boundaries are faked so the test
exercises the staleness logic, not Postgres or the Calendar API.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.jobs import meeting_autoend_checker as checker
from app.jobs.meeting_autoend_checker import SILENCE_TIMEOUT_MINUTES


def _now():
    return datetime.now(timezone.utc)


def _row(**over):
    base = {
        "id": "m-1",
        "user_id": "u-1",
        "calendar_event_id": None,
        "started_at": _now() - timedelta(minutes=30),
        "last_segment_at": _now(),
    }
    base.update(over)
    return base


def _install(monkeypatch, rows, *, end_result={"meeting_id": "m-1", "status": "processing"}):
    """Patch db.query to return rows and meeting_service.end_meeting; return the mock.

    Also stubs the budget gate to "under budget" (so the sweep proceeds to
    end_meeting) and db.execute (the over-budget UPDATE path); the over-budget
    test overrides these itself.
    """
    monkeypatch.setattr("app.db.query", AsyncMock(return_value=rows))
    monkeypatch.setattr(checker, "check_monthly_ai_budget", AsyncMock(return_value=None))
    monkeypatch.setattr("app.db.execute", AsyncMock())
    end_mock = AsyncMock(return_value=end_result)
    monkeypatch.setattr(checker.meeting_service, "end_meeting", end_mock)
    return end_mock


# ---------------------------------------------------------------------------
# Silence timeout
# ---------------------------------------------------------------------------

async def test_silence_timeout_finalizes(monkeypatch):
    stale = _row(last_segment_at=_now() - timedelta(minutes=SILENCE_TIMEOUT_MINUTES + 1))
    end_mock = _install(monkeypatch, [stale])

    n = await checker.check_stale_meetings()

    assert n == 1
    end_mock.assert_awaited_once_with("u-1", "m-1")


async def test_recent_activity_is_not_finalized(monkeypatch):
    fresh = _row(last_segment_at=_now() - timedelta(minutes=2))
    end_mock = _install(monkeypatch, [fresh])

    n = await checker.check_stale_meetings()

    assert n == 0
    end_mock.assert_not_awaited()


async def test_no_segments_yet_uses_started_at(monkeypatch):
    """A meeting just started (no segments) is not ended on the first sweep."""
    just_started = _row(last_segment_at=None, started_at=_now() - timedelta(minutes=1))
    end_mock = _install(monkeypatch, [just_started])

    n = await checker.check_stale_meetings()

    assert n == 0
    end_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Overrun meetings are judged on silence, never on the schedule (finding #2)
# ---------------------------------------------------------------------------

async def test_active_overrun_is_not_truncated(monkeypatch):
    """A meeting still transcribing but past its calendar slot must NOT be ended
    — that would silently truncate the end of the meeting (decisions + action
    items). The scheduled-end trigger was removed; only silence ends a meeting."""
    active_but_overrun = _row(
        last_segment_at=_now() - timedelta(minutes=1),   # still active
        calendar_event_id="evt-1",                        # linked to a slot
    )
    end_mock = _install(monkeypatch, [active_but_overrun])
    # The sweep must not even reach for the calendar anymore; blow up if it does.
    fetch = AsyncMock(side_effect=AssertionError("sweep must not fetch calendar"))
    monkeypatch.setattr("app.services.meeting_service._fetch_calendar_event", fetch)

    n = await checker.check_stale_meetings()

    assert n == 0
    end_mock.assert_not_awaited()
    fetch.assert_not_awaited()


async def test_idle_overrun_finalized_via_silence(monkeypatch):
    """The one real case the old scheduled-end trigger caught — past the slot AND
    quiet — is already covered by the silence timeout (the collapse into (a))."""
    idle_overrun = _row(
        last_segment_at=_now() - timedelta(minutes=SILENCE_TIMEOUT_MINUTES + 1),
        calendar_event_id="evt-1",
    )
    end_mock = _install(monkeypatch, [idle_overrun])

    n = await checker.check_stale_meetings()

    assert n == 1
    end_mock.assert_awaited_once_with("u-1", "m-1")


# ---------------------------------------------------------------------------
# Non-Google user + sweep robustness
# ---------------------------------------------------------------------------

async def test_non_google_user_path_finalizes(monkeypatch):
    """No calendar_event_id (capture without a Google connection) still sweeps via
    silence timeout — the query never touches google_connections."""
    stale = _row(
        calendar_event_id=None,
        last_segment_at=_now() - timedelta(minutes=SILENCE_TIMEOUT_MINUTES + 5),
    )
    end_mock = _install(monkeypatch, [stale])

    n = await checker.check_stale_meetings()

    assert n == 1
    end_mock.assert_awaited_once()
    # The sweep query selects from meetings/settings, not google_connections.
    sql = " ".join(__import__("app").db.query.call_args.args[0].split())
    assert "google_connections" not in sql
    assert "status = 'recording'" in sql
    assert "meeting_capture_mode = TRUE" in sql


async def test_one_failure_does_not_abort_sweep(monkeypatch):
    rows = [
        _row(id="m-1", last_segment_at=_now() - timedelta(minutes=SILENCE_TIMEOUT_MINUTES + 1)),
        _row(id="m-2", user_id="u-2",
             last_segment_at=_now() - timedelta(minutes=SILENCE_TIMEOUT_MINUTES + 1)),
    ]
    monkeypatch.setattr("app.db.query", AsyncMock(return_value=rows))
    monkeypatch.setattr(checker, "check_monthly_ai_budget", AsyncMock(return_value=None))

    async def flaky_end(user_id, meeting_id):
        if meeting_id == "m-1":
            raise RuntimeError("boom")
        return {"meeting_id": meeting_id, "status": "processing"}

    monkeypatch.setattr(checker.meeting_service, "end_meeting", AsyncMock(side_effect=flaky_end))

    n = await checker.check_stale_meetings()

    assert n == 1  # m-1 failed and was logged; m-2 still finalized


async def test_empty_sweep_is_a_noop(monkeypatch):
    end_mock = _install(monkeypatch, [])

    n = await checker.check_stale_meetings()

    assert n == 0
    end_mock.assert_not_awaited()


async def test_end_meeting_noop_does_not_count(monkeypatch):
    """If end_meeting returns None (already transitioned by a racing /end), it
    isn't counted as a finalization."""
    stale = _row(last_segment_at=_now() - timedelta(minutes=SILENCE_TIMEOUT_MINUTES + 1))
    end_mock = _install(monkeypatch, [stale], end_result=None)

    n = await checker.check_stale_meetings()

    assert n == 0
    end_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# Budget gate — the automatic path must not bypass check_monthly_ai_budget
# ---------------------------------------------------------------------------

async def test_over_budget_meeting_is_finalized_without_summarizing(monkeypatch):
    """Invariant: no path spawns summarization without passing the budget gate.
    An over-budget user's stale meeting is finalized to 'error' (terminal-but-
    retryable), and end_meeting (which spawns the summary) is never called."""
    stale = _row(last_segment_at=_now() - timedelta(minutes=SILENCE_TIMEOUT_MINUTES + 1))
    monkeypatch.setattr("app.db.query", AsyncMock(return_value=[stale]))
    end_mock = AsyncMock(return_value={"meeting_id": "m-1", "status": "processing"})
    monkeypatch.setattr(checker.meeting_service, "end_meeting", end_mock)
    # Over budget → the gate raises 429.
    monkeypatch.setattr(
        checker, "check_monthly_ai_budget",
        AsyncMock(side_effect=HTTPException(status_code=429, detail="cap reached")),
    )
    exec_mock = AsyncMock()
    monkeypatch.setattr("app.db.execute", exec_mock)

    n = await checker.check_stale_meetings()

    assert n == 0
    end_mock.assert_not_awaited()                       # summarization NOT spawned
    # Meeting finalized to 'error' via a recording-guarded UPDATE — not left
    # 'processing'/'done', not stuck 'recording'.
    exec_mock.assert_awaited_once()
    sql = " ".join(exec_mock.await_args.args[0].split())
    assert "status = 'error'" in sql
    assert "status = 'recording'" in sql               # guarded transition
    assert exec_mock.await_args.args[1:] == ("m-1", "u-1")


async def test_under_budget_meeting_summarizes_as_before(monkeypatch):
    """Under budget → the gate passes and the sweep finalizes via end_meeting."""
    stale = _row(last_segment_at=_now() - timedelta(minutes=SILENCE_TIMEOUT_MINUTES + 1))
    end_mock = _install(monkeypatch, [stale])   # budget stubbed under-cap

    n = await checker.check_stale_meetings()

    assert n == 1
    end_mock.assert_awaited_once_with("u-1", "m-1")
