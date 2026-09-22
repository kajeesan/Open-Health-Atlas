"""Shared validated text input for imports and retained capture commands."""

from datetime import date


def stdin_text(stream, what):
    """Read bounded UTF-8 text; reject empty, oversized or undecodable input."""
    content = stream.read()
    if not content.strip():
        raise SystemExit(f"empty {what}")
    if len(content) > 500_000:
        raise SystemExit(f"{what} too large (>500k)")
    try:
        content.encode("utf-8")
    except UnicodeEncodeError:
        raise SystemExit(f"{what} is not valid UTF-8 text — re-send it clean")
    return content


def valid_date(s, flag="--date"):
    """Require an ISO date so imported records join date-keyed consumers."""
    try:
        return date.fromisoformat(s).isoformat()
    except (TypeError, ValueError):
        raise SystemExit(f"{flag} must be ISO YYYY-MM-DD, got: {s!r}")
