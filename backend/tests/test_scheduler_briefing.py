"""
Regression tests for the morning-briefing firing window.

The briefing checker is an APScheduler *interval* job (every 5 min), so the
minute it lands on is process_start-dependent — an exact HH:MM equality
against briefing_time only ever matched when the interval anchor happened to
align, i.e. briefings silently never fired for most process starts. The fix
treats the briefing as due whenever the target fell within the last 5 minutes.
"""

from datetime import time as dt_time
from unittest.mock import AsyncMock, patch

from freezegun import freeze_time

from app.jobs.scheduler import _maybe_generate_briefing


def _user(briefing_time=dt_time(7, 30)):
    return {"user_id": "user-1", "timezone": "UTC", "briefing_time": briefing_time}


def _spawn_recorder(calls: list):
    def fake_spawn(coro, name=None):
        calls.append(name)
        coro.close()  # never awaited — silence the warning
    return fake_spawn


async def _run(user, now_utc: str, already_briefed: bool = False) -> bool:
    """Run the checker at a frozen instant; return whether it fired."""
    spawned: list = []
    row = {"id": "b-1"} if already_briefed else None
    with freeze_time(now_utc):
        with patch("app.jobs.scheduler.db.query_one", new=AsyncMock(return_value=row)):
            with patch("app.jobs.scheduler.spawn", new=_spawn_recorder(spawned)):
                await _maybe_generate_briefing(user)
    return bool(spawned)


async def test_briefing_fires_when_job_lands_after_target_within_window():
    # Interval job landing at :33 for a 07:30 target — the exact-match bug's
    # canonical miss. Must fire under the windowed check.
    assert await _run(_user(), "2026-07-09 07:33:00")


async def test_briefing_fires_on_exact_minute():
    assert await _run(_user(), "2026-07-09 07:30:00")


async def test_briefing_does_not_fire_before_target():
    assert not await _run(_user(), "2026-07-09 07:29:00")


async def test_briefing_does_not_fire_after_window_closes():
    # 5 minutes past the target belongs to the next interval tick's window.
    assert not await _run(_user(), "2026-07-09 07:35:00")


async def test_briefing_fires_for_non_slot_aligned_target():
    # briefing_time validation accepts any minute (e.g. 07:32); the window
    # check must cover targets that never align with a 5-minute grid.
    assert await _run(_user(dt_time(7, 32)), "2026-07-09 07:33:00")


async def test_briefing_dedups_on_existing_row():
    assert not await _run(_user(), "2026-07-09 07:33:00", already_briefed=True)
