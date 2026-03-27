"""Unit tests for local_date_for_user around midnight boundaries."""
from datetime import datetime, date, timezone, timedelta

import pytest
from freezegun import freeze_time

from app.services.timezone_utils import local_date_for_user


# ---------------------------------------------------------------------------
# Helper: a UTC datetime that is "23:30 UTC on 2024-03-15"
# ---------------------------------------------------------------------------
UTC_2330 = "2024-03-15 23:30:00"  # 23:30 UTC


@freeze_time(UTC_2330)
def test_utc_user_sees_march_15():
    """User in UTC at 23:30 UTC → still March 15."""
    assert local_date_for_user("UTC") == date(2024, 3, 15)


@freeze_time(UTC_2330)
def test_positive_offset_user_sees_next_day():
    """User in UTC+5:30 at 23:30 UTC → local time is 05:00 on March 16."""
    result = local_date_for_user("Asia/Kolkata")  # UTC+5:30
    assert result == date(2024, 3, 16)


@freeze_time(UTC_2330)
def test_negative_offset_user_sees_same_day():
    """User in UTC-8 at 23:30 UTC → local time is 15:30 on March 15."""
    result = local_date_for_user("US/Pacific")  # UTC-8 (no DST active in March)
    assert result == date(2024, 3, 15)


# ---------------------------------------------------------------------------
# UTC_0030: 00:30 UTC on 2024-03-16 (just past midnight UTC)
# ---------------------------------------------------------------------------
UTC_0030 = "2024-03-16 00:30:00"


@freeze_time(UTC_0030)
def test_utc_user_sees_march_16_after_midnight():
    """User in UTC at 00:30 UTC → March 16."""
    assert local_date_for_user("UTC") == date(2024, 3, 16)


@freeze_time(UTC_0030)
def test_negative_offset_user_still_sees_previous_day():
    """User in UTC-8 at 00:30 UTC → local time is 16:30 on March 15 (still yesterday)."""
    result = local_date_for_user("America/Los_Angeles")  # UTC-8 (no DST)
    assert result == date(2024, 3, 15)


@freeze_time(UTC_0030)
def test_positive_offset_user_sees_march_16():
    """User in UTC+5:30 at 00:30 UTC → local time is 06:00 on March 16."""
    result = local_date_for_user("Asia/Kolkata")
    assert result == date(2024, 3, 16)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_unknown_timezone_falls_back_to_utc():
    """Invalid timezone string falls back to UTC without raising."""
    result = local_date_for_user("Not/A/Timezone")
    utc_today = datetime.now(timezone.utc).date()
    assert result == utc_today


def test_empty_string_falls_back_to_utc():
    """Empty string treated as UTC."""
    result = local_date_for_user("")
    utc_today = datetime.now(timezone.utc).date()
    assert result == utc_today


def test_none_falls_back_to_utc():
    """None treated as UTC."""
    result = local_date_for_user(None)
    utc_today = datetime.now(timezone.utc).date()
    assert result == utc_today
