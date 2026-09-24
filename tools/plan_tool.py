"""Deterministic lookup for Plan records. No reasoning, no fuzzy matching."""

from __future__ import annotations

from typing import Optional

from tools.data_store import get_data_store
from tools.models import Plan
from tools.validation import require_identifier


def get_plan(plan_id: str) -> Optional[Plan]:
    """Return the Plan with this exact plan_id, or None if none exists.

    Exact-match only. A well-formed but unknown plan_id returns None.

    Raises ValueError if plan_id is None, not a string, blank, or contains
    whitespace -- that is a caller/input bug, not a "not found" result.
    """
    require_identifier(plan_id, "plan_id")
    return get_data_store().plans.get(plan_id)
