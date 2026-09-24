"""Shared identifier validation for the deterministic tools layer.

Centralizes the "invalid input" vs "not found" distinction so every tool
enforces it the same way: a syntactically valid identifier with no
matching record is a normal, expected result (None, or an empty list);
a blank, non-string, or whitespace-containing identifier is a caller bug
and is rejected immediately, before any lookup is attempted.
"""

from __future__ import annotations


def require_identifier(value: object, field_name: str) -> str:
    """Return value unchanged if it is a well-formed identifier string.

    Raises ValueError if value is None, not a string, empty, or contains
    any whitespace (leading, trailing, or internal). This is a format
    check only -- it says nothing about whether a record with this
    identifier actually exists.
    """
    if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
        raise ValueError(
            f"{field_name} must be a non-blank identifier string with no whitespace, "
            f"got {value!r}"
        )
    return value
