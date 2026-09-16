"""Strict, non-inferential identity normalization for Phase 2 capture."""

from __future__ import annotations

import hashlib
import re
import unicodedata


ENTITY_TYPES = {
    "person", "food", "location", "activity", "supplement", "medication", "other",
}
_KEY_RE = re.compile(
    r"^(person|food|location|activity|supplement|medication|other):[0-9a-f]{64}$"
)
_RECIPE_KEY_RE = re.compile(r"^recipe:[a-z0-9][a-z0-9._-]{0,159}$")


def normalized_label(label: str) -> str:
    """Return only the automatic normalization authorized by the design.

    NFKC, outer trim, internal whitespace collapse and casefold are the entire
    automatic equivalence policy.  No punctuation removal, transliteration,
    fuzzy matching or person inference belongs here.
    """

    if not isinstance(label, str):
        raise TypeError("label must be text")
    return " ".join(unicodedata.normalize("NFKC", label).strip().split()).casefold()


def identity_key(entity_type: str, label: str) -> str:
    """Hash a normalized label into its stable, namespaced identity key."""

    if entity_type not in ENTITY_TYPES:
        raise ValueError("unsupported entity type")
    normalized = normalized_label(label)
    if not normalized:
        raise ValueError("label must not be blank")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{entity_type}:{digest}"


def recipe_key(recipe_id: str) -> str:
    """Return a stable recipe identity without using a numeric database ID."""

    if not isinstance(recipe_id, str):
        raise TypeError("recipe_id must be text")
    value = unicodedata.normalize("NFKC", recipe_id).strip()
    key = f"recipe:{value}"
    if not _RECIPE_KEY_RE.fullmatch(key):
        raise ValueError("invalid recipe identity")
    return key


def valid_identity_key(value: str, entity_type: str | None = None) -> bool:
    """Return whether ``value`` is a full identity key of the expected type."""

    if not isinstance(value, str):
        return False
    match = _KEY_RE.fullmatch(value)
    if match:
        return entity_type is None or match.group(1) == entity_type
    return bool(entity_type in (None, "food") and _RECIPE_KEY_RE.fullmatch(value))
