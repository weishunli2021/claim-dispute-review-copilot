"""Deterministic lookup for Provider records. No reasoning, no fuzzy matching."""

from __future__ import annotations

from typing import Optional

from tools.data_store import get_data_store
from tools.models import Provider
from tools.validation import require_identifier


def get_provider(provider_id: str) -> Optional[Provider]:
    """Return the Provider with this exact provider_id, or None if none exists.

    Exact-match only. A claim can reference a provider_id that does not
    resolve to any Provider record (e.g. incomplete source data); a
    well-formed but unresolvable provider_id surfaces as None rather than
    guessing at the provider's identity or network status.

    Raises ValueError if provider_id is None, not a string, blank, or
    contains whitespace -- that is a caller/input bug, not a "not found"
    result.
    """
    require_identifier(provider_id, "provider_id")
    return get_data_store().providers.get(provider_id)
