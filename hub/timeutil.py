"""Date handling.

All stored dates are ISO ``YYYY-MM-DD`` text. Timestamps are stored in UTC
(ISO 8601) and shown in the configured local timezone (default Asia/Kolkata).
Ambiguous numeric dates such as 03/04/2026 are read day-first by default
(the Indian convention); this can be changed to month-first in Settings.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Asia/Kolkata"

MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
    if m
}
MONTHS.update({calendar.month_name[i].lower(): i for i in range(1, 13)})
MONTHS["sept"] = 9

EXCEL_EPOCH = date(1899, 12, 30)


def zone(name: str | None = None) -> ZoneInfo:
    return ZoneInfo(name or DEFAULT_TZ)


def today_local(tzname: str | None = None) -> date:
    return datetime.now(zone(tzname)).date()


def now_local(tzname: str | None = None) -> datetime:
    return datetime.now(zone(tzname))


def to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def clamp_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, last_day_of_month(year, month)))


def _year(y: int) -> int:
    if y >= 100:
        return y
    pivot = (date.today().year % 100) + 1
    return 2000 + y if y <= pivot else 1900 + y


def parse_date(value, date_order: str = "DMY") -> date | None:
    """Parse many common spreadsheet date spellings. Returns None for blank.

    Raises ValueError with a readable message when the value is not a date.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 1 <= float(value) < 80000:
            return EXCEL_EPOCH + timedelta(days=int(float(value)))
        raise ValueError(f"'{value}' is not a valid date")
    text = str(value).strip()
    if not text:
        return None
    # Strip a time part such as "2024-05-01 00:00:00" or "01/05/2024 10:30"
    text = re.sub(r"[ T]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?$", "", text).strip()
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)), value)
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", text)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), _year(int(m.group(3)))
        if date_order == "MDY":
            month, day = a, b
        else:
            day, month = a, b
        # If the preferred order is impossible but the other one works, the value is unambiguous.
        if month > 12 and day <= 12:
            day, month = month, day
        return _mk(y, month, day, value)
    m = re.fullmatch(r"(\d{1,2})[-/ .]+([A-Za-z]{3,9})[-/ .,]+(\d{2,4})", text)
    if m and m.group(2).lower() in MONTHS:
        return _mk(_year(int(m.group(3))), MONTHS[m.group(2).lower()], int(m.group(1)), value)
    m = re.fullmatch(r"([A-Za-z]{3,9})[-/ .]+(\d{1,2})(?:st|nd|rd|th)?[-/ .,]+(\d{2,4})", text)
    if m and m.group(1).lower() in MONTHS:
        return _mk(_year(int(m.group(3))), MONTHS[m.group(1).lower()], int(m.group(2)), value)
    raise ValueError(f"'{value}' is not a recognised date (use DD-MM-YYYY or YYYY-MM-DD)")


def _mk(y: int, m: int, d: int, original) -> date:
    try:
        return date(y, m, d)
    except ValueError:
        raise ValueError(f"'{original}' is not a valid calendar date") from None


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def fmt_display_date(value: str | None) -> str:
    if not value:
        return ""
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return value
    return d.strftime("%d-%b-%Y")
