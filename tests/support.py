"""Shared, side-effect-free panel test helpers."""

import re


def csrf_from(page_html: str) -> str:
    """Return the CSRF meta-token from a rendered page."""
    match = re.search(r'csrf-token" content="([^"]+)"', page_html)
    assert match is not None
    return match.group(1)
