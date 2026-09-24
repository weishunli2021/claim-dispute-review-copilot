"""Typed domain models for the synthetic structured-data layer.

These models describe the shape of the synthetic JSON fixtures under
data/ and are used to validate every record on load (see data_store.py).
They intentionally carry no behavior -- they are data containers only.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel


class Member(BaseModel):
    """A synthetic health-plan member."""

    member_id: str
    first_name: str
    last_name: str
    date_of_birth: date
    plan_id: str
    status: str


class Plan(BaseModel):
    """A synthetic benefit plan."""

    plan_id: str
    plan_name: str
    plan_type: str
    network_name: str


class Provider(BaseModel):
    """A synthetic servicing or ordering provider (facility or physician)."""

    provider_id: str
    name: str
    provider_type: str
    network_status: str
    specialty: Optional[str] = None
    network_name: Optional[str] = None


class Benefit(BaseModel):
    """Coverage rule for one (plan_id, service_code) pair.

    The absence of a Benefit record for a given plan/service pair is a
    distinct fact from `covered=False` -- see tools/benefits_tool.py.
    """

    benefit_id: str
    plan_id: str
    service_code: str
    covered: bool
    requires_prior_auth: bool
    network_requirement: Optional[str] = None
    notes: Optional[str] = None


class PriorAuthorization(BaseModel):
    """A single prior authorization record for one member and service code.

    A member/service pair may have zero, one, or several of these over
    time (e.g. an expired authorization from a prior plan year and a
    current one) -- `authorization_id`, not (member_id, service_code), is
    what uniquely identifies a record. "No prior authorization on file" is
    represented by an empty list of records, never by a PriorAuthorization
    instance -- see tools/prior_auth_tool.py. This model does not say
    whether a given record was valid for any particular claim's date of
    service; that comparison is left to a future reasoning layer.
    """

    authorization_id: str
    member_id: str
    service_code: str
    plan_id: str
    status: str
    effective_date: date
    expiration_date: date
    notes: Optional[str] = None


class Claim(BaseModel):
    """A synthetic claim submitted for a member's service."""

    claim_id: str
    member_id: str
    plan_id: str
    service_code: str
    provider_id: str
    date_of_service: date
    status: str
    billed_amount: float
    ordering_provider_id: Optional[str] = None
    allowed_amount: Optional[float] = None
    denial_reason_code: Optional[str] = None
    denial_reason_description: Optional[str] = None
