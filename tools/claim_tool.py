"""Deterministic lookup for Claim records. No reasoning, no fuzzy matching."""

from __future__ import annotations

from typing import Optional

from tools.data_store import get_data_store
from tools.models import Claim
from tools.validation import require_identifier


def get_claim(claim_id: str) -> Optional[Claim]:
    """Return the Claim with this exact claim_id, or None if none exists.

    Exact-match only. A well-formed but unknown claim_id returns None.
    Returns the claim's recorded status and denial fields verbatim -- this
    function does not explain or interpret why a claim was denied.

    Raises ValueError if claim_id is None, not a string, blank, or
    contains whitespace -- that is a caller/input bug, not a "not found"
    result.
    """
    require_identifier(claim_id, "claim_id")
    return get_data_store().claims.get(claim_id)
