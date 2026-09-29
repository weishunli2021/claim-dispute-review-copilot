"""Pure comparison logic for the billing-correction scenario: a proposed
BillingCorrectionSubmission versus the isolated original BillingClaimSnapshot,
checked against independently recorded BillingSupportRecords.

compare_billing_correction() performs no I/O of any kind: no fixture read,
no LLM call. It never mutates any input (all are frozen pydantic models)
and never fetches anything external; every fact it reports comes from the
objects it was given.

build_billing_claim_snapshot() is the one function in this module that
touches an existing fixture record type (dispute_review.billing_fixtures.
RawBillingClaimRecord) -- it only maps already-loaded fields, never reads
the JSON file itself (that is billing_fixtures' job).

Four billing fields are compared, always in this order: Service Code,
Modifier, Units, Servicing Provider. For each field, exactly one of four
outcomes is reported (see dispute_review.models.BillingFindingStatus) --
never a fifth, never a match score, and a changed value is never treated
as automatically correct.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional

from dispute_review.billing_fixtures import BillingSupportRecord, RawBillingClaimRecord
from dispute_review.models import (
    BILLING_AUTHENTICITY_DISCLAIMER,
    BillingClaimSnapshot,
    BillingComparisonResult,
    BillingComparisonRow,
    BillingCorrectionSubmission,
    BillingFindingStatus,
)


def build_billing_claim_snapshot(record: RawBillingClaimRecord) -> BillingClaimSnapshot:
    """Build a BillingClaimSnapshot from an already-loaded, already-validated
    RawBillingClaimRecord (see dispute_review.billing_fixtures.get_billing_claim_record).
    A pure field-for-field mapping -- never invents or infers a value the
    fixture does not carry.
    """
    return BillingClaimSnapshot(**record.model_dump())


def check_claim_member_linkage(
    claim: BillingClaimSnapshot, submission: BillingCorrectionSubmission
) -> Optional[str]:
    """If the submission's own claim/member identifiers have been changed
    to something OTHER than this fixed claim's actual identifiers, return
    a block reason instead of allowing evidence retrieval/comparison to
    silently proceed against a now-ambiguous identity. Returns None when
    linkage is safe (either the submission leaves these fields blank, or
    they match).
    """
    if submission.original_claim_reference and submission.original_claim_reference != claim.claim_id:
        return (
            f"The submitted original claim reference ({submission.original_claim_reference!r}) does not "
            f"match this scenario's fixed claim ({claim.claim_id!r}); evidence retrieval and comparison "
            "are blocked rather than proceeding against a different, ambiguous claim identity."
        )
    if submission.member_id and submission.member_id != claim.member_id:
        return (
            f"The submitted member ID ({submission.member_id!r}) does not match the original claim's "
            f"recorded member ({claim.member_id!r}); evidence retrieval and comparison are blocked "
            "rather than silently retrieving supporting records for a different member."
        )
    return None


def _resolve_support_value(
    records: list[BillingSupportRecord], attr: str
) -> tuple[Optional[str], bool, list[str]]:
    """Scan every independent supporting record for a non-missing value of
    `attr`. Returns (resolved_value, ambiguous, contributing_record_ids).

    `resolved_value` is None both when NO record establishes this
    attribute (missing evidence) and when records DISAGREE with each
    other (conflicting evidence) -- `ambiguous` distinguishes the two so
    callers never silently pick one disagreeing record's value.
    """
    values: dict[str, list[str]] = {}
    for record in records:
        raw_value = getattr(record, attr)
        if raw_value is None:
            continue
        values.setdefault(str(raw_value), []).append(record.record_id)

    if not values:
        return None, False, []
    if len(values) > 1:
        contributing = [record_id for ids in values.values() for record_id in ids]
        return None, True, contributing

    (only_value, contributing_ids), = values.items()
    return only_value, False, contributing_ids


def _compare_billing_field(
    field_label: str,
    original: Optional[object],
    proposed: Optional[object],
    support_records: list[BillingSupportRecord],
    support_attr: str,
) -> BillingComparisonRow:
    """Shared classification for one of the four billing fields. See
    dispute_review.models.BillingFindingStatus for the exact four
    outcomes; this function derives which one applies. Exact-value
    equality only (via string comparison) -- no fuzzy matching.
    """
    original_str = str(original) if original is not None else None
    proposed_str = str(proposed) if proposed is not None else None
    support_value, ambiguous, contributing_ids = _resolve_support_value(support_records, support_attr)
    evidence_refs = [f"support:{record_id}" for record_id in contributing_ids]
    lower_label = field_label.lower()

    if support_value is None:
        if ambiguous:
            explanation = (
                f"Independent supporting records disagree with each other on {lower_label}; no single "
                "supporting value could be established, so this cannot be resolved automatically."
            )
        else:
            explanation = f"No independent supporting record establishes {lower_label} for this claim."
        return BillingComparisonRow(
            field=field_label,
            original_value=original_str,
            proposed_value=proposed_str,
            supporting_value=None,
            status=BillingFindingStatus.INSUFFICIENT_EVIDENCE,
            explanation=explanation,
            evidence_refs=evidence_refs,
        )

    correction_proposed = proposed_str is not None and proposed_str != original_str

    if not correction_proposed:
        if original_str is None:
            return BillingComparisonRow(
                field=field_label,
                original_value=original_str,
                proposed_value=proposed_str,
                supporting_value=support_value,
                status=BillingFindingStatus.INSUFFICIENT_EVIDENCE,
                explanation=(
                    f"The original claim does not record a value for {lower_label}, and no correction "
                    "was proposed; whether a correction is needed cannot be determined."
                ),
                evidence_refs=evidence_refs,
            )
        if original_str == support_value:
            return BillingComparisonRow(
                field=field_label,
                original_value=original_str,
                proposed_value=proposed_str,
                supporting_value=support_value,
                status=BillingFindingStatus.CONSISTENT_NO_CORRECTION_NEEDED,
                explanation=(
                    f"No correction was proposed for {lower_label}; the original claim value already "
                    "agrees with the independently recorded value."
                ),
                evidence_refs=evidence_refs,
            )
        return BillingComparisonRow(
            field=field_label,
            original_value=original_str,
            proposed_value=proposed_str,
            supporting_value=support_value,
            status=BillingFindingStatus.CONFLICTS,
            explanation=(
                f"No correction was proposed for {lower_label}, but the original claim value "
                f"({original_str!r}) conflicts with the independently recorded value ({support_value!r})."
            ),
            evidence_refs=evidence_refs,
        )

    if proposed_str == support_value:
        return BillingComparisonRow(
            field=field_label,
            original_value=original_str,
            proposed_value=proposed_str,
            supporting_value=support_value,
            status=BillingFindingStatus.SUPPORTED,
            explanation=(
                f"The proposed correction to {lower_label} ({proposed_str!r}) agrees with the "
                "independently recorded value."
            ),
            evidence_refs=evidence_refs,
        )
    return BillingComparisonRow(
        field=field_label,
        original_value=original_str,
        proposed_value=proposed_str,
        supporting_value=support_value,
        status=BillingFindingStatus.CONFLICTS,
        explanation=(
            f"The proposed correction to {lower_label} ({proposed_str!r}) conflicts with the "
            f"independently recorded value ({support_value!r})."
        ),
        evidence_refs=evidence_refs,
    )


def _build_summary(rows: list[BillingComparisonRow]) -> str:
    counts = Counter(row.status for row in rows)
    supported = counts.get(BillingFindingStatus.SUPPORTED, 0)
    consistent = counts.get(BillingFindingStatus.CONSISTENT_NO_CORRECTION_NEEDED, 0)
    conflicts = counts.get(BillingFindingStatus.CONFLICTS, 0)
    insufficient = counts.get(BillingFindingStatus.INSUFFICIENT_EVIDENCE, 0)

    parts = [f"{supported} of {len(rows)} proposed correction(s) supported by independent records"]
    if consistent:
        parts.append(f"{consistent} field(s) already consistent with no correction needed")
    if conflicts:
        conflicting_fields = ", ".join(row.field for row in rows if row.status == BillingFindingStatus.CONFLICTS)
        parts.append(f"{conflicts} field(s) in conflict ({conflicting_fields})")
    if insufficient:
        insufficient_fields = ", ".join(
            row.field for row in rows if row.status == BillingFindingStatus.INSUFFICIENT_EVIDENCE
        )
        parts.append(f"{insufficient} field(s) with insufficient evidence ({insufficient_fields})")
    return "; ".join(parts) + "."


def _build_verification_guidance(rows: list[BillingComparisonRow]) -> list[str]:
    """Concise, conditional next steps. Never says the claim should be
    approved, paid, reversed, or denied, and never implies the original
    denial record has changed -- guidance is always about what to verify,
    reconcile, obtain, or route, never a claim/payment decision.
    """
    guidance: list[str] = [
        "Confirm the independent supporting records were authored independently of this correction "
        "request and were not edited to match it."
    ]

    conflicting_fields = [row.field for row in rows if row.status == BillingFindingStatus.CONFLICTS]
    if conflicting_fields:
        guidance.append("Reconcile the following field(s) in conflict with independent records: " + ", ".join(conflicting_fields) + ".")

    insufficient_fields = [row.field for row in rows if row.status == BillingFindingStatus.INSUFFICIENT_EVIDENCE]
    if insufficient_fields:
        guidance.append(
            "Obtain additional independent documentation for: " + ", ".join(insufficient_fields) + "."
        )

    guidance.append("Route this comparison for human (analyst) review under the applicable corrected-claim review process.")
    return guidance


RECOMMENDED_ROUTE_FOR_REVIEW = (
    "The proposed billing corrections agree with the available service records. Route for "
    "analyst review and preparation of a corrected claim under the applicable submission "
    "requirements. Payment is not determined."
)


def build_recommended_next_action(result: BillingComparisonResult) -> str:
    """A deterministic (never model-authored) recommended next action,
    derived directly from the comparison rows -- never a claim, payment,
    or authorization decision. Returns the exact required sentence when
    every field is SUPPORTED or CONSISTENT_NO_CORRECTION_NEEDED (a fully
    supported correction); otherwise recommends clarification or manual
    review of the specific unresolved field(s).
    """
    unresolved = [
        row.field
        for row in result.rows
        if row.status in (BillingFindingStatus.CONFLICTS, BillingFindingStatus.INSUFFICIENT_EVIDENCE)
    ]
    if not unresolved:
        return RECOMMENDED_ROUTE_FOR_REVIEW
    return (
        "The proposed billing corrections are not yet fully supported by the available service "
        "records. Recommend clarification or manual analyst review of the following field(s) "
        "before any corrected-claim preparation: " + ", ".join(unresolved) + ". Payment is not "
        "determined."
    )


def compare_billing_correction(
    claim: BillingClaimSnapshot,
    submission: BillingCorrectionSubmission,
    support_records: list[BillingSupportRecord],
) -> BillingComparisonResult:
    """Compare a validated BillingCorrectionSubmission against the isolated
    original BillingClaimSnapshot and the independent BillingSupportRecords,
    and return a typed BillingComparisonResult.

    Exactly four comparison rows, always in this order: Service Code,
    Modifier, Units, Servicing Provider. Pure function: does not fetch
    external data, write records, or mutate any input (all frozen).
    `submission.correction_explanation` is never inspected by this
    function -- it is only copied through to the result as context.

    Caller's responsibility: check_claim_member_linkage must have already
    returned None (safe linkage) before this function is called -- this
    function does not itself re-check linkage.
    """
    rows = [
        _compare_billing_field("Service Code", claim.service_code, submission.service_code, support_records, "supports_service_code"),
        _compare_billing_field("Modifier", claim.modifier, submission.modifier, support_records, "supports_modifier"),
        _compare_billing_field("Units", claim.units, submission.units, support_records, "supports_units"),
        _compare_billing_field(
            "Servicing Provider",
            claim.servicing_provider_id,
            submission.servicing_provider_id,
            support_records,
            "supports_servicing_provider_id",
        ),
    ]

    provenance_label = (
        f"{submission.supplied_by} — unverified" if submission.supplied_by else "Unverified — submitter not specified"
    )

    return BillingComparisonResult(
        claim_id=claim.claim_id,
        submission_provenance=provenance_label,
        rows=rows,
        summary=_build_summary(rows),
        verification_guidance=_build_verification_guidance(rows),
        correction_explanation=submission.correction_explanation,
        supporting_record_references=list(submission.supporting_record_references),
    )
