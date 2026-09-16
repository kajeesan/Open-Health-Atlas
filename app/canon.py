"""The deployment-configured canonical clock decides "what day is it".

SQLite's UTC ``date('now')`` can disagree with the configured civil day. Every
date anchor is therefore computed here and bound as a parameter; date('now')
must not appear in panel SQL.
"""
from calendar import monthrange
from datetime import date, datetime, timedelta
import os
from zoneinfo import ZoneInfo

TIMEZONE_NAME = os.environ.get("HERMES_TIMEZONE", "UTC")
CANON_TZ = ZoneInfo(TIMEZONE_NAME)
RANGE_GRANULARITIES = {"day", "week", "month", "year", "all"}


class CanonicalRangeError(ValueError):
    """Raised when a panel analytical range is not the exact closed contract."""


def now():
    return datetime.now(CANON_TZ)


def today_iso():
    return now().date().isoformat()


def days_ago_iso(n):
    """ISO date n days before today — the window anchor for -N-day charts."""
    return (now().date() - timedelta(days=int(n))).isoformat()


def weekday():
    """Mon..Sun in the canonical timezone — the schedule key format."""
    return ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[now().weekday()]


def canonical_date(value, *, field="date"):
    """Return one strict ISO date; permissive forms such as 2026-7-3 fail."""
    if not isinstance(value, str):
        raise CanonicalRangeError(f"{field} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise CanonicalRangeError(f"{field} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise CanonicalRangeError(f"{field} must be an ISO date")
    return parsed


def canonical_range(value):
    """Validate and normalize the immutable all/bounded context range."""
    if not isinstance(value, dict):
        raise CanonicalRangeError("range must be an object")
    if set(value) == {"kind"} and value.get("kind") == "all":
        return {"kind": "all"}
    if set(value) != {"kind", "from", "to"} or value.get("kind") != "bounded":
        raise CanonicalRangeError("range must be all or have exact from/to dates")
    start = canonical_date(value.get("from"), field="from")
    end = canonical_date(value.get("to"), field="to")
    if start > end:
        raise CanonicalRangeError("from must not be after to")
    return {"kind": "bounded", "from": start.isoformat(), "to": end.isoformat()}


def _shift_month(anchor, months):
    total = anchor.year * 12 + anchor.month - 1 + months
    year, zero_month = divmod(total, 12)
    month = zero_month + 1
    day = min(anchor.day, monthrange(year, month)[1])
    return date(year, month, day)


def range_bounds(granularity, *, anchor=None, shift=0):
    """Compute inclusive canonical-calendar bounds for a UI range control.

    The browser supplies at most a literal ISO anchor and a small direction;
    it never derives analytical boundaries from its own timezone.
    """
    if granularity not in RANGE_GRANULARITIES:
        raise CanonicalRangeError("unknown range granularity")
    if isinstance(shift, bool) or not isinstance(shift, int) or shift not in {-1, 0, 1}:
        raise CanonicalRangeError("range shift must be -1, 0, or 1")
    if granularity == "all":
        if anchor is not None or shift:
            raise CanonicalRangeError("all time has no anchor or shift")
        return {
            "granularity": "all",
            "anchor": None,
            "range": {"kind": "all"},
            "canonical_today": today_iso(),
        }

    point = canonical_date(anchor, field="anchor") if anchor is not None else now().date()
    if granularity == "day":
        point += timedelta(days=shift)
        start = end = point
    elif granularity == "week":
        point += timedelta(days=7 * shift)
        start = point - timedelta(days=point.weekday())
        end = start + timedelta(days=6)
    elif granularity == "month":
        point = _shift_month(point, shift)
        start = point.replace(day=1)
        end = point.replace(day=monthrange(point.year, point.month)[1])
    else:
        target_year = point.year + shift
        start = date(target_year, 1, 1)
        end = date(target_year, 12, 31)
        point = start
    return {
        "granularity": granularity,
        "anchor": point.isoformat(),
        "range": {
            "kind": "bounded",
            "from": start.isoformat(),
            "to": end.isoformat(),
        },
        "canonical_today": today_iso(),
    }
