"""Module 2 (v2): typed contracts for an INDEPENDENT, separately-seeded
synthetic authorization-source evidence layer.

This is the THIRD of three deliberately distinct evidence layers this
project now carries for dispute review:

  1. ORIGINAL RECORDED evidence -- the claim and its recorded decision
     (tools/, dispute_review.models.ClaimSnapshot). Already on file.
  2. SUBMITTED, UNVERIFIED evidence -- new information a provider or
     patient supplies for this dispute (dispute_review.models.DisputeSubmission).
     Never authenticated, never treated as fact.
  3. AUTHORIZATION-SOURCE evidence (this module) -- a SEPARATELY seeded
     synthetic fixture standing in for an authorization system of record,
     kept isolated from BOTH of the above. "Independent" means separate
     from the submitted form data in this demo -- it does NOT mean a real
     payer system was contacted, queried, or authenticated. Matching an
     authorization-source record does not, by itself, establish that the
     record belongs to a given claim or applies to its billed service date
     -- see AuthorizationSourceLookupResult and
     dispute_review/authorization_source_lookup.py for exactly what a
     lookup does and does not establish.

Deliberately separate from tools/models.py's PriorAuthorization (the
baseline dataset's own authorization record) and from
context/dispute_evidence_models.py's EvidenceReference/Provenance
vocabulary -- this module introduces its own record type and its own
lookup-outcome status enum, matching this project's established
convention of never sharing a status/result type across independently
evolving subsystems (see e.g. DisputeJudgeStatus vs. JudgeStatus). Module
3 is responsible for deciding how (and whether) an
AuthorizationSourceRecord is surfaced as an EvidenceReference; this module
makes no EvidenceReference of its own.

All models are frozen (immutable after construction) and reject unknown
fields, matching dispute_review/models.py's own convention.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Fixed, documented label identifying every record produced by this
# module's isolated fixture -- never shared with, or confusable with, the
# baseline dataset's own source_system-equivalent concept (the baseline
# tools/models.py.PriorAuthorization has no such field at all; its origin
# is implicitly "the baseline synthetic dataset").
SYNTHETIC_AUTHORIZATION_SOURCE_SYSTEM = "SYNTHETIC-AUTH-SOURCE-V2"


def _require_nonblank(value: object, field_name: str) -> str:
    """Reject None, a non-string, or a blank/whitespace-only string.
    Mirrors tools.validation.require_identifier's "no internal whitespace"
    strictness is deliberately NOT applied here (unlike a bare identifier,
    several of these fields -- e.g. status, source_system -- are short
    codes without embedded whitespace in practice, but this validator's
    job is only "not blank," not "identifier-shaped"; the store's own
    lookup functions apply tools.validation.require_identifier to the
    identifiers actually used as lookup keys)."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string, got {value!r}")
    return value


class AuthorizationSourceRecord(BaseModel):
    """One version of one synthetic authorization-source record.

    A single logical authorization may have MULTIPLE versions over time
    (e.g. an original decision, later amended) -- `source_record_id`, not
    `authorization_reference_number`, is what uniquely identifies ONE
    version. Multiple versions sharing the same `authorization_reference_number`
    are expected and are how an amendment chain is represented; earlier
    versions are never overwritten or discarded -- see
    dispute_review/authorization_source_lookup.py's version-chain handling.

    Field meanings, made explicit because several are easy to conflate:
      - `status` is the authorization decision recorded on THIS version
        (e.g. "APPROVED", "DENIED", "EXPIRED", "PENDING") -- a plain,
        documented string (not a closed Enum), matching
        tools.models.PriorAuthorization.status's own convention.
      - `authorized_start_date`/`authorized_end_date` describe the
        AUTHORIZED SERVICE PERIOD this version covers, if any -- a DENIED
        or PENDING version may legitimately have neither. This is
        deliberately never validated against any claim's date of service:
        an authorized period that does not cover a particular service date
        is a valid, meaningful comparison outcome for a LATER layer to
        surface, never a reason for this model to reject the record.
      - `decided_at` is when the `status` on THIS version was decided
        (approved/denied/etc.) -- optional, and never invented when the
        source fixture does not supply it (a missing original-denial
        timestamp stays missing, not backfilled).
      - `amended_at` is when THIS version was created as an amendment to
        a previous version -- always None for `record_version == 1`.
      - `amends_source_record_id` links THIS version to the immediately
        prior version's `source_record_id` -- required when
        `record_version > 1`, always None for `record_version == 1`. This
        is what makes "original and amended versions remain traceable" a
        structural property of the data, not a convention callers must
        remember.
      - `retroactive_effective` is a TRI-STATE fact about whether the
        SOURCE ITSELF asserts this amendment applies retroactively to a
        service that occurred before `amended_at`: True, False, or None
        (not established / unknown). This is never computed or inferred
        from `amended_at` by this model or by any lookup function -- a
        later amendment timestamp is NOT evidence of retroactive
        applicability on its own, and the absence of an explicit assertion
        stays represented as None, never silently treated as True or
        False.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_record_id: str
    authorization_reference_number: str
    member_id: str
    service_code: str
    servicing_provider_id: str
    status: str
    source_system: str = SYNTHETIC_AUTHORIZATION_SOURCE_SYSTEM
    record_version: int = Field(ge=1)

    authorized_start_date: Optional[date] = None
    authorized_end_date: Optional[date] = None

    decided_at: Optional[datetime] = None
    amended_at: Optional[datetime] = None
    amends_source_record_id: Optional[str] = None
    amendment_reason: Optional[str] = None

    retroactive_effective: Optional[bool] = None

    notes: Optional[str] = None

    @field_validator(
        "source_record_id",
        "authorization_reference_number",
        "member_id",
        "service_code",
        "servicing_provider_id",
        "status",
        "source_system",
        mode="before",
    )
    @classmethod
    def _required_nonblank(cls, value: object, info) -> str:
        return _require_nonblank(value, info.field_name)

    @model_validator(mode="after")
    def _check_authorized_period_order(self) -> "AuthorizationSourceRecord":
        start, end = self.authorized_start_date, self.authorized_end_date
        if start is not None and end is not None and start > end:
            raise ValueError(
                f"authorized_start_date ({start.isoformat()}) is after "
                f"authorized_end_date ({end.isoformat()}) on {self.source_record_id!r}; "
                "dates are never silently repaired or swapped."
            )
        return self

    @model_validator(mode="after")
    def _check_version_chain_integrity(self) -> "AuthorizationSourceRecord":
        if self.record_version == 1 and self.amends_source_record_id is not None:
            raise ValueError(
                f"{self.source_record_id!r} is record_version 1 but sets "
                "amends_source_record_id -- a first version cannot amend anything."
            )
        if self.record_version > 1 and self.amends_source_record_id is None:
            raise ValueError(
                f"{self.source_record_id!r} is record_version {self.record_version} but has "
                "no amends_source_record_id -- every amendment must link to the version it "
                "amends, so original and amended versions remain traceable."
            )
        if self.amends_source_record_id == self.source_record_id:
            raise ValueError(
                f"{self.source_record_id!r} cannot amend itself (amends_source_record_id "
                "equals source_record_id)."
            )
        return self


class AuthorizationSourceLookupStatus(str, Enum):
    """The outcome of one isolated authorization-source lookup. A separate
    enum from context.dispute_evidence_models.EvidenceSourceStatus and
    from every other *Status type in this project (see this module's own
    docstring) -- this lookup can additionally be AMBIGUOUS, a state none
    of the existing status enums represent."""

    FOUND = "FOUND"
    """Exactly one version-chain matched the queried reference number.
    `record` is that chain's current (unamended) version; `version_history`
    carries every version in the chain, oldest first, so an amendment is
    never presented as if it were the only fact on record."""

    NOT_FOUND = "NOT_FOUND"
    """No record in the accessible synthetic fixture matches the queried
    reference number. Never proof that no such authorization exists
    anywhere else."""

    AMBIGUOUS = "AMBIGUOUS"
    """More than one INDEPENDENT version-chain matched the queried
    reference number (a data-integrity ambiguity, not a normal amendment
    history). Nothing is silently selected -- `candidates` lists every
    matching record, unresolved."""

    ERROR = "ERROR"
    """The lookup itself could not complete (an unexpected failure in the
    underlying fixture access) -- distinct from NOT_FOUND, which means the
    lookup completed and legitimately found nothing."""


class AuthorizationSourceLookupResult(BaseModel):
    """The full, typed result of one authorization-source lookup by
    reference number. Matching a reference number is a LOOKUP KEY only --
    it is never, by itself, proof that the returned record belongs to a
    particular claim or applies to a particular billed service date; that
    determination is left to a later comparison layer (Module 3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: AuthorizationSourceLookupStatus
    queried_reference_number: str
    record: Optional[AuthorizationSourceRecord] = None
    version_history: list[AuthorizationSourceRecord] = Field(default_factory=list)
    candidates: list[AuthorizationSourceRecord] = Field(default_factory=list)
    detail: Optional[str] = None
