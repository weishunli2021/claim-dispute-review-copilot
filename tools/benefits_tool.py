"""Deterministic lookup for Benefit records. No reasoning, no fuzzy matching."""

from __future__ import annotations

from typing import Optional

from tools.data_store import get_data_store
from tools.models import Benefit
from tools.validation import require_identifier


def get_benefits(plan_id: str, service_code: str) -> Optional[Benefit]:
    """Return the Benefit rule for this exact (plan_id, service_code) pair.

    Returns None when no Benefit record exists for a well-formed pair.
    This is a distinct fact from a Benefit record that exists with
    `covered=False`: None means the data has no coverage rule on file for
    this combination at all, and must not be treated as either "covered"
    or "not covered". Callers must branch on None explicitly rather than
    assuming a default.

    Raises ValueError if plan_id or service_code is None, not a string,
    blank, or contains whitespace -- that is a caller/input bug, not a
    "not found" result.
    """
    require_identifier(plan_id, "plan_id")
    require_identifier(service_code, "service_code")
    return get_data_store().benefits.get((plan_id, service_code))
