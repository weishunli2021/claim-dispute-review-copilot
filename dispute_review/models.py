"""Typed contracts for Module 3's dispute-review comparison layer.

Deliberately separate from application/models.py's H1/H2 investigation
contracts (AGENTS.md rule 2: no skill or generation contract gains a
dispute-review field, and no dispute-review contract gains a generation
field). Nothing here is a claim/authorization/payment decision, a
confidence score, or an approval/denial recommendation -- see
ComparisonStatus and DisputeComparisonResult below.

All models are frozen (immutable after construction) and reject unknown
fields (`extra="forbid"`), matching the convention already used by
application/judge_models.py's SemanticJudgeResult family. Immutability is
a deliberate guarantee, not just documentation: dispute_review.comparison
never needs to construct a mutated copy of a ClaimSnapshot or
DisputeSubmission, so freezing them makes "the comparator does not mutate
its inputs" a structural fact rather than a convention callers must trust.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SubmissionSource(str, Enum):
    """Who supplied a DisputeSubmission. Affects only the provenance label
    surfaced on the result (see DisputeComparisonResult.submission_provenance)
    -- it never changes comparison logic, and neither value is treated as
    more or less trustworthy by compare_submission itself."""

    PROVIDER = "PROVIDER"
    PATIENT = "PATIENT"


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
    reinterprets a malformed or impossible date (e.g. "2026-02-30") --
    `date.fromisoformat` raising ValueError is left to propagate as a
    clear validation error, and swapped/reordered digits are never
    guessed at.
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


class DisputeSubmission(BaseModel):
    """New, unverified authorization information supplied by a provider or
    patient for dispute review. Every business field is Optional so an
    incomplete submission can still be constructed and reviewed (an
    incomplete submission is expected input, not a construction error) --
    the only errors this model raises are a malformed/impossible date or a
    start date after the end date (see _check_date_order below), never a
    merely-missing field.

    `dispute_explanation` is treated as INERT free text everywhere in this
    package: compare_submission never parses, searches, or branches on its
    contents. It cannot override an identifier, a date-validity check, a
    comparison result, or verification guidance -- it is carried through to
    DisputeComparisonResult purely as human-readable context.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    authorization_reference_number: Optional[str] = None
    member_id: Optional[str] = None
    service_code: Optional[str] = None
    authorization_start_date: Optional[date] = None
    authorization_end_date: Optional[date] = None
    servicing_provider_id: Optional[str] = None
    dispute_explanation: Optional[str] = None
    supplied_by: SubmissionSource = SubmissionSource.PROVIDER

    @field_validator(
        "authorization_reference_number",
        "member_id",
        "service_code",
        "servicing_provider_id",
        "dispute_explanation",
        mode="before",
    )
    @classmethod
    def _blank_to_missing(cls, value: object) -> object:
        return _normalize_optional_str(value)

    @field_validator("authorization_start_date", "authorization_end_date", mode="before")
    @classmethod
    def _parse_date(cls, value: object) -> object:
        return _parse_optional_iso_date(value)

    @model_validator(mode="after")
    def _check_date_order(self) -> "DisputeSubmission":
        start = self.authorization_start_date
        end = self.authorization_end_date
        if start is not None and end is not None and start > end:
            raise ValueError(
                f"authorization_start_date ({start.isoformat()}) is after "
                f"authorization_end_date ({end.isoformat()}); dates are never silently "
                "repaired or swapped -- supply a corrected submission instead."
            )
        return self


class ClaimSnapshot(BaseModel):
    """A read-only view of exactly the claim fields dispute-review
    comparisons need -- built from an existing tools.case_context.CaseContext
    (see dispute_review.comparison.build_claim_snapshot), never a second
    source of claim truth and never itself read from data/*.json.

    `servicing_provider_id` is always the claim's SERVICING provider
    (tools.models.Claim.provider_id) -- this type has no ordering-provider
    field at all, so "fall back to the ordering provider" is not just
    avoided by convention, it is structurally unrepresentable here.

    Every field besides `claim_id` is Optional: in the real synthetic
    dataset a resolved Claim always has a member_id/service_code/
    date_of_service/provider_id, but this snapshot is also constructed
    directly in tests to exercise the "claim-side data missing" comparison
    paths -- see dispute_review.comparison's Unknown-status rules.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    member_id: Optional[str] = None
    service_code: Optional[str] = None
    date_of_service: Optional[date] = None
    servicing_provider_id: Optional[str] = None


class ComparisonStatus(str, Enum):
    """The outcome of one field-level comparison. Never a confidence score
    and never an approval/denial signal -- see ComparisonRow and
    DisputeComparisonResult."""

    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"


class ComparisonRow(BaseModel):
    """One row of the four-row comparison table. `claim_value` and
    `submitted_value` are already human-readable strings (e.g. an ISO date
    or a formatted "start to end" validity window) -- see
    dispute_review.comparison for exactly how each row's values and
    explanation are derived; this model carries no comparison logic of its
    own.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    claim_value: Optional[str] = None
    submitted_value: Optional[str] = None
    status: ComparisonStatus
    explanation: str


DISPUTE_AUTHENTICITY_DISCLAIMER = (
    "Matching fields do not establish authenticity, authorization applicability, "
    "coverage, or payment."
)


class DisputeComparisonResult(BaseModel):
    """The full, deterministic output of one dispute-review comparison.

    Deliberately excludes any confidence score and any approval/denial
    recommendation (AGENTS.md rules 3, 6, 8) -- `authenticity_disclaimer`
    is always present, with fixed text, precisely so no caller can produce
    a result that omits the "matching fields prove nothing about
    authenticity/coverage/payment" statement. `authorization_reference_number`
    and `dispute_explanation` are carried through as CONTEXT ONLY: their
    presence here does not mean either has been authenticated or
    independently verified -- see `verification_guidance`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    submission_provenance: str
    rows: list[ComparisonRow] = Field(min_length=4, max_length=4)
    summary: str
    authenticity_disclaimer: str = DISPUTE_AUTHENTICITY_DISCLAIMER
    verification_guidance: list[str] = Field(default_factory=list)
    authorization_reference_number: Optional[str] = None
    dispute_explanation: Optional[str] = None
