"""Pure comparison logic: new, unverified authorization information
(DisputeSubmission) versus an existing claim (ClaimSnapshot).

compare_submission() performs no I/O of any kind: no data/*.json read, no
tools.data_store.DataStore access, no graph access, no Streamlit/session
state, no LLM call. It never mutates either input (both are frozen
pydantic models -- see dispute_review/models.py) and never fetches
anything external; every fact it reports comes from the two objects it was
given.

build_claim_snapshot() is the one function in this module that touches an
existing project type (tools.case_context.CaseContext) -- it only reads
already-resolved fields off it, never data/*.json directly and never the
DataStore singleton.

Deliberately NOT reused here: application/scenario_lab.py's
_temporary_data_overlay. That mechanism mutates the process-wide
DataStore/graph singletons for the duration of a call, which is the right
shape for Scenario Lab's "compose one hypothetical case and run it through
the real investigation pipeline" flow -- and the wrong shape for dispute
review, which needs to hold an ORIGINAL claim and a PROPOSED submission
side by side, read-only, without ever touching shared state other tabs or
callers depend on. See docs/DISPUTE_REVIEW_LOGIC.md for the full
rationale (carried forward from docs/DISPUTE_REVIEW_BASELINE.md's Module 2
correction).
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Optional

from dispute_review.models import (
    ClaimSnapshot,
    ComparisonRow,
    ComparisonStatus,
    DisputeComparisonResult,
    DisputeSubmission,
    SubmissionSource,
)
from tools.case_context import CaseContext

_PROVENANCE_LABELS = {
    SubmissionSource.PROVIDER: "Provider-supplied—unverified",
    SubmissionSource.PATIENT: "Patient-supplied—unverified",
}


def build_claim_snapshot(case_context: CaseContext) -> ClaimSnapshot:
    """Build a ClaimSnapshot from an already-resolved CaseContext.

    Reads only fields already present on `case_context.claim` -- never
    data/*.json, never tools.data_store directly. `servicing_provider_id`
    is always `claim.provider_id` (the SERVICING provider); this function
    never reads `claim.ordering_provider_id`, and ClaimSnapshot has no
    field to put it in even if it wanted to.

    Raises ValueError if `case_context.claim` is None -- building a
    snapshot from a case with no resolved claim is a caller error (compare
    tools/validation.py's require_identifier: a malformed precondition
    raises immediately rather than silently producing a degenerate,
    all-missing snapshot).
    """
    claim = case_context.claim
    if claim is None:
        raise ValueError(
            "CaseContext has no resolved claim; build_claim_snapshot requires a "
            "CaseContext whose .claim is not None (e.g. from "
            "tools.case_context.get_case_context on a known claim_id)."
        )
    return ClaimSnapshot(
        claim_id=claim.claim_id,
        member_id=claim.member_id,
        service_code=claim.service_code,
        date_of_service=claim.date_of_service,
        servicing_provider_id=claim.provider_id,
    )


def _format_date(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _format_validity_window(start: Optional[date], end: Optional[date]) -> Optional[str]:
    if start is None and end is None:
        return None
    start_str = start.isoformat() if start is not None else "(missing start date)"
    end_str = end.isoformat() if end is not None else "(missing end date)"
    return f"{start_str} to {end_str}"


def _compare_exact_identifier(
    field_label: str, noun: str, claim_value: Optional[str], submitted_value: Optional[str]
) -> ComparisonRow:
    """Shared shape for the three exact-identifier comparisons (Member,
    Service, Servicing Provider): present-and-equal -> Match,
    present-and-unequal -> Mismatch, either side missing -> Unknown. Exact
    string equality only -- no case folding, fuzzy matching, alias
    mapping, or inferred equivalence.
    """
    if claim_value is None or submitted_value is None:
        missing = []
        if claim_value is None:
            missing.append(f"the claim's recorded {noun}")
        if submitted_value is None:
            missing.append(f"the submitted {noun}")
        return ComparisonRow(
            field=field_label,
            claim_value=claim_value,
            submitted_value=submitted_value,
            status=ComparisonStatus.UNKNOWN,
            explanation="Cannot compare: missing " + " and ".join(missing) + ".",
        )
    if claim_value == submitted_value:
        return ComparisonRow(
            field=field_label,
            claim_value=claim_value,
            submitted_value=submitted_value,
            status=ComparisonStatus.MATCH,
            explanation=f"Submitted {noun} matches the claim's recorded {noun}.",
        )
    return ComparisonRow(
        field=field_label,
        claim_value=claim_value,
        submitted_value=submitted_value,
        status=ComparisonStatus.MISMATCH,
        explanation=(
            f"Submitted {noun} ({submitted_value!r}) does not match the claim's "
            f"recorded {noun} ({claim_value!r})."
        ),
    )


def _compare_member(claim: ClaimSnapshot, submission: DisputeSubmission) -> ComparisonRow:
    return _compare_exact_identifier("Member", "member ID", claim.member_id, submission.member_id)


def _compare_service(claim: ClaimSnapshot, submission: DisputeSubmission) -> ComparisonRow:
    return _compare_exact_identifier(
        "Service", "service code", claim.service_code, submission.service_code
    )


def _compare_servicing_provider(claim: ClaimSnapshot, submission: DisputeSubmission) -> ComparisonRow:
    # NEVER claim.ordering_provider_id -- ClaimSnapshot has no such field.
    return _compare_exact_identifier(
        "Servicing Provider",
        "servicing provider ID",
        claim.servicing_provider_id,
        submission.servicing_provider_id,
    )


def _compare_validity_dates(claim: ClaimSnapshot, submission: DisputeSubmission) -> ComparisonRow:
    """Is the claim's date of service within [start, end] (inclusive) of
    the submitted authorization window? An absent boundary is never
    treated as an open-ended authorization -- a missing start or end date
    makes this Unknown, not a one-sided Match/Mismatch.
    """
    claim_date = claim.date_of_service
    start = submission.authorization_start_date
    end = submission.authorization_end_date
    claim_value_str = _format_date(claim_date)
    submitted_value_str = _format_validity_window(start, end)

    if claim_date is None or start is None or end is None:
        missing = []
        if claim_date is None:
            missing.append("the claim's recorded date of service")
        if start is None:
            missing.append("the submitted authorization start date")
        if end is None:
            missing.append("the submitted authorization end date")
        return ComparisonRow(
            field="Validity Dates",
            claim_value=claim_value_str,
            submitted_value=submitted_value_str,
            status=ComparisonStatus.UNKNOWN,
            explanation=(
                "Cannot determine whether the claim's date of service falls within the "
                "submitted authorization period: missing " + ", ".join(missing) + ". "
                "An absent boundary is never treated as an open-ended authorization."
            ),
        )

    if start <= claim_date <= end:
        return ComparisonRow(
            field="Validity Dates",
            claim_value=claim_value_str,
            submitted_value=submitted_value_str,
            status=ComparisonStatus.MATCH,
            explanation=(
                f"The claim's date of service ({claim_value_str}) falls within the "
                f"submitted authorization period ({submitted_value_str}), inclusive."
            ),
        )
    return ComparisonRow(
        field="Validity Dates",
        claim_value=claim_value_str,
        submitted_value=submitted_value_str,
        status=ComparisonStatus.MISMATCH,
        explanation=(
            f"The claim's date of service ({claim_value_str}) falls outside the "
            f"submitted authorization period ({submitted_value_str})."
        ),
    )


def _build_summary(rows: list[ComparisonRow]) -> str:
    counts = Counter(row.status for row in rows)
    match = counts.get(ComparisonStatus.MATCH, 0)
    mismatch = counts.get(ComparisonStatus.MISMATCH, 0)
    unknown = counts.get(ComparisonStatus.UNKNOWN, 0)

    parts = [f"{match} of {len(rows)} compared fields match"]
    if mismatch:
        mismatched_fields = ", ".join(row.field for row in rows if row.status == ComparisonStatus.MISMATCH)
        parts.append(f"{mismatch} mismatch(es) requiring reconciliation ({mismatched_fields})")
    if unknown:
        unknown_fields = ", ".join(row.field for row in rows if row.status == ComparisonStatus.UNKNOWN)
        parts.append(f"{unknown} field(s) could not be compared due to missing information ({unknown_fields})")
    return "; ".join(parts) + "."


def _build_verification_guidance(rows: list[ComparisonRow], submission: DisputeSubmission) -> list[str]:
    """Concise, conditional next steps. Never says the claim should be
    approved, paid, reversed, or denied, and never implies the original
    denial record has changed -- guidance is always about what to verify,
    reconcile, obtain, or route, never a claim/authorization decision.
    """
    guidance: list[str] = []

    if submission.authorization_reference_number:
        guidance.append(
            f"Verify authorization reference {submission.authorization_reference_number!r} "
            "and its status with an authoritative source; it has not been independently "
            "verified here."
        )
    else:
        guidance.append(
            "Obtain an authorization reference number and verify it with an authoritative source."
        )

    guidance.append("Confirm authorization scope and applicability to the specific service billed on this claim.")

    mismatched_fields = [row.field for row in rows if row.status == ComparisonStatus.MISMATCH]
    if mismatched_fields:
        guidance.append("Reconcile the following mismatched field(s): " + ", ".join(mismatched_fields) + ".")

    unknown_fields = [row.field for row in rows if row.status == ComparisonStatus.UNKNOWN]
    if unknown_fields:
        guidance.append(
            "Obtain the missing information needed to complete comparison for: "
            + ", ".join(unknown_fields) + "."
        )

    guidance.append("Route this submission for human review under the applicable dispute-review process.")
    return guidance


def compare_submission(claim: ClaimSnapshot, submission: DisputeSubmission) -> DisputeComparisonResult:
    """Compare a validated DisputeSubmission against a read-only
    ClaimSnapshot and return a typed DisputeComparisonResult.

    Exactly four comparison rows, always in this order: Member, Service,
    Validity Dates, Servicing Provider. Pure function: does not fetch
    external data, write records, mutate `claim` or `submission` (both are
    frozen), or depend on Streamlit/session state. `submission.dispute_explanation`
    is never inspected by this function -- it is only copied through to
    the result as context.

    Never adds a fifth row or any other signal implying the submitted
    authorization reference itself has been matched or verified: this
    prototype has no authoritative record for a brand-new submission, and
    the absence of a matching authorization in the existing synthetic
    dataset is never treated as proof that no authorization exists
    elsewhere.
    """
    rows = [
        _compare_member(claim, submission),
        _compare_service(claim, submission),
        _compare_validity_dates(claim, submission),
        _compare_servicing_provider(claim, submission),
    ]

    return DisputeComparisonResult(
        claim_id=claim.claim_id,
        submission_provenance=_PROVENANCE_LABELS[submission.supplied_by],
        rows=rows,
        summary=_build_summary(rows),
        verification_guidance=_build_verification_guidance(rows, submission),
        authorization_reference_number=submission.authorization_reference_number,
        dispute_explanation=submission.dispute_explanation,
    )
