"""Shared timezone helpers."""
import pytz
from datetime import date, datetime, time, timedelta, timezone


def _resolve(tz_name: str):
    """Resolve a timezone name, falling back to UTC for unknown/missing ones."""
    try:
        return pytz.timezone(tz_name or "UTC")
    except pytz.UnknownTimeZoneError:
        return pytz.UTC


def local_date_for_user(tz_name: str) -> date:
    """Return today's date in the user's local timezone.

    Falls back to UTC for unknown/missing timezone names.
    """
    return datetime.now(_resolve(tz_name)).date()


def local_date_of(value: datetime, tz_name: str) -> date:
    """Return the date `value` fell on in the user's local timezone.

    A stored TIMESTAMPTZ read back in UTC dates a 21:00 America/Los_Angeles
    meeting to the following day, which is the wrong label for anyone asking
    "what did we decide yesterday?". Naive values are assumed to be UTC.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_resolve(tz_name)).date()


def local_week_window(tz_name: str, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    """Monday-inclusive / next-Monday-exclusive, localized separately for DST."""
    tz = _resolve(tz_name)
    today = local_date_of(now or datetime.now(timezone.utc), tz_name)
    monday = today - timedelta(days=today.weekday())
    return (local_midnight_utc(monday, tz_name),
            local_midnight_utc(monday + timedelta(days=7), tz_name), str(tz))


def local_midnight_utc(day: date, tz_name: str) -> datetime:
    """Convert a user-entered local date to its midnight in UTC."""
    return _resolve(tz_name).localize(datetime.combine(day, time.min)).astimezone(timezone.utc)
