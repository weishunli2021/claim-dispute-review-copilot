"""Typed contracts for the billing-correction dispute-review scenario.

This is the ONE active Dispute Review scenario (see AGENTS.md and
docs/BILLING_CORRECTION_SCENARIO.md): a provider proposes corrected values
for four billing fields on an existing, isolated synthetic claim
(CLM-BILL-9001), and this package's job is to determine, deterministically,
whether each proposed correction is supported by independently recorded
service documentation -- never to submit a corrected claim, edit the
original claim, reverse a denial, or approve payment.

All models are frozen (immutable after construction) and reject unknown
fields (`extra="forbid"`). Nothing here is a claim/authorization/payment
decision, a confidence score, or an approval/denial recommendation -- see
BillingFindingStatus and BillingComparisonResult below.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _normalize_optional_str(value: object) -> object:
    """Trim surrounding whitespace; normalize a blank/whitespace-only
    string to None (missing). Any value that is not None and not a str is
    returned unchanged, so pydantic's own type validation (not this
    normalization step) is what rejects it as a genuine type error.
    """
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    return value


def _parse_optional_iso_date(value: object) -> object:
    """Accept a real `date`, a valid ISO YYYY-MM-DD string, or a blank/
    whitespace-only string (-> None, missing). Never silently repairs or
    reinterprets a malformed or impossible date -- ValueError propagates.
    """
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        try:
            return date.fromisoformat(trimmed)
        except ValueError as exc:
            raise ValueError(
                f"must be a valid ISO YYYY-MM-DD date string, got {value!r}"
            ) from exc
    raise ValueError(f"must be a date or an ISO YYYY-MM-DD date string, got {value!r}")


class BillingCorrectionSubmission(BaseModel):
    """New, unverified proposed billing corrections supplied by a provider
    (or patient) for one existing claim. Every business field is Optional
    so an incomplete or partially-filled submission can still be
    constructed and reviewed -- the only errors this model raises are a
    malformed/impossible date, never a merely-missing field.

    `correction_explanation` is treated as INERT free text everywhere in
    this package: compare_billing_correction never parses, searches, or
    branches on its contents. It cannot override an identifier, a
    comparison result, or verification guidance -- it is carried through
    to BillingComparisonResult purely as human-readable context.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    supplied_by: Optional[str] = None
    original_claim_reference: Optional[str] = None
    member_id: Optional[str] = None
    service_date: Optional[date] = None
    service_code: Optional[str] = None
    modifier: Optional[str] = None
    units: Optional[int] = None
    servicing_provider_id: Optional[str] = None
    proposed_billed_amount: Optional[float] = None
    correction_explanation: Optional[str] = None
    supporting_record_references: list[str] = Field(default_factory=list)

    @field_validator(
        "supplied_by",
        "original_claim_reference",
        "member_id",
        "service_code",
        "modifier",
        "servicing_provider_id",
        "correction_explanation",
        mode="before",
    )
    @classmethod
    def _blank_to_missing(cls, value: object) -> object:
        return _normalize_optional_str(value)

    @field_validator("service_date", mode="before")
    @classmethod
    def _parse_date(cls, value: object) -> object:
        return _parse_optional_iso_date(value)

    @field_validator("units", mode="before")
    @classmethod
    def _blank_units_to_missing(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("proposed_billed_amount", mode="before")
    @classmethod
    def _blank_amount_to_missing(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _check_units_non_negative(self) -> "BillingCorrectionSubmission":
        if self.units is not None and self.units < 0:
            raise ValueError(f"units must not be negative, got {self.units}")
        return self


class BillingClaimSnapshot(BaseModel):
    """A read-only, detailed view of the isolated original billing claim
    and its recorded decision -- built from
    dispute_review.billing_fixtures's isolated fixture, never from
    tools.case_context or data/claims.json, and never a second source of
    truth once built (see dispute_review.comparison.build_billing_claim_snapshot).

    `*_recorded` flags distinguish an explicitly-recorded zero amount from
    an amount that was never recorded at all -- `allowed_amount == 0.0`
    with `allowed_amount_recorded == True` is a different fact than
    `allowed_amount is None`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    claim_type: str = "Original submission"
    submission_date: Optional[date] = None

    member_id: Optional[str] = None
    member_name: Optional[str] = None
    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    billing_provider_id: Optional[str] = None
    billing_provider_name: Optional[str] = None
    servicing_provider_id: Optional[str] = None
    servicing_provider_name: Optional[str] = None
    ordering_provider_id: Optional[str] = None
    ordering_provider_name: Optional[str] = None

    service_date: Optional[date] = None
    service_code: Optional[str] = None
    service_code_description: Optional[str] = None
    modifier: Optional[str] = None
    modifier_description: Optional[str] = None
    units: Optional[int] = None
    place_of_service: Optional[str] = None
    diagnosis_code: Optional[str] = None
    diagnosis_description: Optional[str] = None
    charge_per_unit: Optional[float] = None
    total_billed: Optional[float] = None
    authorization_reference: Optional[str] = None

    decision_status: Optional[str] = None
    decision_date: Optional[date] = None
    decision_code: Optional[str] = None
    decision_reason: Optional[str] = None
    decision_explanation: Optional[str] = None
    decision_flagged_fields: list[str] = Field(default_factory=list)
    allowed_amount: Optional[float] = None
    allowed_amount_recorded: bool = False
    paid_amount: Optional[float] = None
    paid_amount_recorded: bool = False

    original_supporting_references: list[str] = Field(default_factory=list)
    original_notes: Optional[str] = None


class BillingFindingStatus(str, Enum):
    """The outcome of one billing-field comparison. A deliberately
    different, 4-value vocabulary from the retired authorization scenario's
    3-value MATCH/MISMATCH/UNKNOWN -- these are genuinely different
    questions (does a PROPOSED CORRECTION agree with independent evidence,
    versus does a submitted value match the claim). Never a confidence
    score and never an approval/denial signal.
    """

    SUPPORTED = "SUPPORTED"
    """A correction was proposed for this field, and it agrees with the
    independently recorded supporting value."""

    CONFLICTS = "CONFLICTS"
    """Either a proposed correction disagrees with the independently
    recorded supporting value, or no correction was proposed and the
    original claim value itself disagrees with that supporting value."""

    CONSISTENT_NO_CORRECTION_NEEDED = "CONSISTENT_NO_CORRECTION_NEEDED"
    """No correction was proposed for this field, and the original claim
    value already agrees with the independently recorded supporting value
    -- never labeled as an error."""

    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    """The independent supporting records do not establish a single,
    unambiguous value for this field (missing, or multiple records
    disagree with each other) -- never silently resolved one way or the
    other."""


class BillingComparisonRow(BaseModel):
    """One row of the four-row billing comparison table: Field | Original
    claim | Proposed correction | Supporting record value | Finding |
    Evidence reference. `evidence_refs` lists the citable evidence
    reference id(s) (see context.dispute_evidence_models.EvidenceReference)
    that ground this row's supporting_value and status -- never a
    fabricated id.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    original_value: Optional[str] = None
    proposed_value: Optional[str] = None
    supporting_value: Optional[str] = None
    status: BillingFindingStatus
    explanation: str
    evidence_refs: list[str] = Field(default_factory=list)


BILLING_AUTHENTICITY_DISCLAIMER = (
    "This comparison shows whether the proposed corrections agree with independently recorded "
    "service documentation. It does not submit a corrected claim, change the original claim, "
    "reverse the recorded denial, or determine payment."
)


class BillingComparisonResult(BaseModel):
    """The full, deterministic output of one billing-correction comparison.

    Deliberately excludes any confidence score and any approval/denial
    recommendation -- `authenticity_disclaimer` is always present, with
    fixed text, precisely so no caller can produce a result that omits the
    "this is not a claim/payment decision" statement.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    submission_provenance: str
    rows: list[BillingComparisonRow] = Field(min_length=4, max_length=4)
    summary: str
    authenticity_disclaimer: str = BILLING_AUTHENTICITY_DISCLAIMER
    verification_guidance: list[str] = Field(default_factory=list)
    correction_explanation: Optional[str] = None
    supporting_record_references: list[str] = Field(default_factory=list)
