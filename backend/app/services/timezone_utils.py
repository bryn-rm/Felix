"""Shared timezone helpers."""
import pytz
from datetime import date, datetime, timezone


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
