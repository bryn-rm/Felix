"""Shared timezone helpers."""
import pytz
from datetime import date, datetime


def local_date_for_user(tz_name: str) -> date:
    """Return today's date in the user's local timezone.

    Falls back to UTC for unknown/missing timezone names.
    """
    try:
        tz = pytz.timezone(tz_name or "UTC")
    except pytz.UnknownTimeZoneError:
        tz = pytz.UTC
    return datetime.now(tz).date()
