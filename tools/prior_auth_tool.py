"""Deterministic lookup for PriorAuthorization records. No reasoning, no fuzzy matching."""

from __future__ import annotations

from typing import Optional

from tools.data_store import get_data_store
from tools.models import PriorAuthorization
from tools.validation import require_identifier


def get_prior_authorizations(member_id: str, service_code: str) -> list[PriorAuthorization]:
    """Return every prior-authorization record for this member and service code.

    A member/service pair may have zero, one, or several authorization
    records over time (e.g. an expired authorization from a prior plan
    year plus a current one) -- authorization_id, not (member_id,
    service_code), is what uniquely identifies a record.

    An empty list means no matching authorization records exist. This is
    the only way "no authorization on file" is represented, and must
    never be treated as, or converted into, an assumed denial. When
    records are returned, each keeps its own `status`, `effective_date`,
    and `expiration_date` exactly as recorded. This function does not
    determine which (if any) record was valid for a particular claim's
    date of service, and does not decide whether a claim should be paid --
    that comparison belongs to a future reasoning layer.

    Raises ValueError if member_id or service_code is None, not a string,
    blank, or contains whitespace -- that is a caller/input bug, not a
    "no records" result.
    """
    require_identifier(member_id, "member_id")
    require_identifier(service_code, "service_code")
    matches = get_data_store().prior_authorizations_by_member_service.get(
        (member_id, service_code), []
    )
    return list(matches)


def get_prior_authorization_by_id(authorization_id: str) -> Optional[PriorAuthorization]:
    """Return the PriorAuthorization with this exact authorization_id, or
    None if none exists.

    Exact-match only, like get_prior_authorizations -- no fuzzy matching.
    Unlike get_prior_authorizations (scoped to a member+service pair, the
    normal claim-investigation lookup), this looks a record up by its own
    unique identifier alone -- e.g. to check whether an externally-supplied
    reference number (Module 6A's dispute-evidence structured tool)
    corresponds to any record in the synthetic dataset. A match here says
    only that a record with this id exists; it never establishes that the
    record applies to any particular claim, member, or service -- callers
    must check those fields themselves before treating it as relevant, and
    a None result means "not found in the accessible synthetic dataset,"
    never proof that no such authorization exists anywhere else.

    Raises ValueError if authorization_id is None, not a string, blank, or
    contains whitespace -- that is a caller/input bug, not a "not found"
    result.
    """
    require_identifier(authorization_id, "authorization_id")
    return get_data_store().prior_authorizations.get(authorization_id)
