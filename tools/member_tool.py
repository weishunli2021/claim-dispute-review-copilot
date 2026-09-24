"""Deterministic lookup for Member records. No reasoning, no fuzzy matching."""

from __future__ import annotations

from typing import Optional

from tools.data_store import get_data_store
from tools.models import Member
from tools.validation import require_identifier


def get_member(member_id: str) -> Optional[Member]:
    """Return the Member with this exact member_id, or None if none exists.

    Exact-match only: does not match on name, fuzzy IDs, or partial IDs,
    and does not infer a member from other case data. A well-formed but
    unknown member_id returns None.

    Raises ValueError if member_id is None, not a string, blank, or
    contains whitespace -- that is a caller/input bug, not a "not found"
    result.
    """
    require_identifier(member_id, "member_id")
    return get_data_store().members.get(member_id)
