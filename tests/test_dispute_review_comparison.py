"""Tests for dispute_review.comparison: the pure compare_submission()
function and build_claim_snapshot().

Most tests use hand-built ClaimSnapshot/DisputeSubmission fixtures with
literal, independently-chosen values (not read from data/*.json), so
these tests do not depend on the synthetic dataset and do not reproduce
compare_submission's own logic to derive their expected values. One test
uses the real CLM-1001 record specifically to ground the
"ordering provider never substitutes for servicing provider" guarantee in
actual data.
"""

from __future__ import annotations

from datetime import date

import pytest

from dispute_review.comparison import build_claim_snapshot, compare_submission
from dispute_review.models import ClaimSnapshot, ComparisonStatus, DisputeSubmission, SubmissionSource
from tools.case_context import CaseContext, get_case_context

CLAIM = ClaimSnapshot(
    claim_id="CLM-TEST-1",
    member_id="M-TEST-1",
    service_code="SVC-TEST",
    date_of_service=date(2026, 3, 15),
    servicing_provider_id="PRV-TEST-A",
)


def _matching_submission(**overrides) -> DisputeSubmission:
    defaults = dict(
        authorization_reference_number="AUTH-TEST-0001",
        member_id="M-TEST-1",
        service_code="SVC-TEST",
        authorization_start_date=date(2026, 2, 1),
        authorization_end_date=date(2026, 4, 1),
        servicing_provider_id="PRV-TEST-A",
    )
    defaults.update(overrides)
    return DisputeSubmission(**defaults)


# --- all four fields matching -------------------------------------------------------------


def test_all_four_fields_matching():
    result = compare_submission(CLAIM, _matching_submission())
    assert [row.status for row in result.rows] == [ComparisonStatus.MATCH] * 4
    assert result.summary == "4 of 4 compared fields match."


# --- individual mismatches ----------------------------------------------------------------


def test_member_mismatch():
    result = compare_submission(CLAIM, _matching_submission(member_id="M-OTHER"))
    by_field = {row.field: row for row in result.rows}
    assert by_field["Member"].status == ComparisonStatus.MISMATCH
    assert by_field["Service"].status == ComparisonStatus.MATCH
    assert by_field["Servicing Provider"].status == ComparisonStatus.MATCH


def test_service_mismatch():
    result = compare_submission(CLAIM, _matching_submission(service_code="SVC-OTHER"))
    by_field = {row.field: row for row in result.rows}
    assert by_field["Service"].status == ComparisonStatus.MISMATCH
    assert by_field["Member"].status == ComparisonStatus.MATCH


def test_servicing_provider_mismatch():
    result = compare_submission(CLAIM, _matching_submission(servicing_provider_id="PRV-OTHER"))
    by_field = {row.field: row for row in result.rows}
    assert by_field["Servicing Provider"].status == ComparisonStatus.MISMATCH
    assert by_field["Servicing Provider"].claim_value == "PRV-TEST-A"
    assert by_field["Servicing Provider"].submitted_value == "PRV-OTHER"


def test_ordering_provider_never_substitutes_for_servicing_provider():
    # Grounded in the real CLM-1001 record: provider_id (servicing) is
    # PRV-1001; ordering_provider_id is PRV-4001. Submitting the ordering
    # provider's id as the servicing provider must be a MISMATCH, never a
    # Match "because it's a real provider on this claim."
    case_context = get_case_context("CLM-1001")
    claim = build_claim_snapshot(case_context)
    assert claim.servicing_provider_id == "PRV-1001"
    assert case_context.claim.ordering_provider_id == "PRV-4001"

    submission = DisputeSubmission(
        member_id=claim.member_id,
        service_code=claim.service_code,
        servicing_provider_id="PRV-4001",  # the claim's ORDERING provider
    )
    result = compare_submission(claim, submission)
    by_field = {row.field: row for row in result.rows}
    assert by_field["Servicing Provider"].status == ComparisonStatus.MISMATCH
    assert by_field["Servicing Provider"].claim_value == "PRV-1001"
    assert by_field["Servicing Provider"].submitted_value == "PRV-4001"


# --- validity dates ------------------------------------------------------------------------


def test_service_date_before_validity_interval_is_mismatch():
    submission = _matching_submission(
        authorization_start_date=date(2026, 4, 1), authorization_end_date=date(2026, 5, 1)
    )
    result = compare_submission(CLAIM, submission)
    row = next(r for r in result.rows if r.field == "Validity Dates")
    assert row.status == ComparisonStatus.MISMATCH


def test_service_date_after_validity_interval_is_mismatch():
    submission = _matching_submission(
        authorization_start_date=date(2026, 1, 1), authorization_end_date=date(2026, 2, 1)
    )
    result = compare_submission(CLAIM, submission)
    row = next(r for r in result.rows if r.field == "Validity Dates")
    assert row.status == ComparisonStatus.MISMATCH


def test_service_date_exactly_on_start_boundary_is_match():
    submission = _matching_submission(
        authorization_start_date=CLAIM.date_of_service, authorization_end_date=date(2026, 4, 1)
    )
    result = compare_submission(CLAIM, submission)
    row = next(r for r in result.rows if r.field == "Validity Dates")
    assert row.status == ComparisonStatus.MATCH


def test_service_date_exactly_on_end_boundary_is_match():
    submission = _matching_submission(
        authorization_start_date=date(2026, 2, 1), authorization_end_date=CLAIM.date_of_service
    )
    result = compare_submission(CLAIM, submission)
    row = next(r for r in result.rows if r.field == "Validity Dates")
    assert row.status == ComparisonStatus.MATCH


def test_one_missing_validity_boundary_is_unknown_not_open_ended():
    submission = _matching_submission(authorization_end_date=None)
    result = compare_submission(CLAIM, submission)
    row = next(r for r in result.rows if r.field == "Validity Dates")
    assert row.status == ComparisonStatus.UNKNOWN


def test_missing_claim_date_of_service_is_unknown():
    claim_without_date = ClaimSnapshot(
        claim_id="CLM-TEST-2", member_id="M-TEST-1", service_code="SVC-TEST", servicing_provider_id="PRV-TEST-A"
    )
    result = compare_submission(claim_without_date, _matching_submission())
    row = next(r for r in result.rows if r.field == "Validity Dates")
    assert row.status == ComparisonStatus.UNKNOWN


# --- missing values -> Unknown -------------------------------------------------------------


def test_missing_claim_member_id_is_unknown():
    claim = ClaimSnapshot(
        claim_id="CLM-TEST-3",
        service_code="SVC-TEST",
        date_of_service=date(2026, 3, 15),
        servicing_provider_id="PRV-TEST-A",
    )
    result = compare_submission(claim, _matching_submission())
    row = next(r for r in result.rows if r.field == "Member")
    assert row.status == ComparisonStatus.UNKNOWN


def test_missing_submitted_member_id_is_unknown():
    result = compare_submission(CLAIM, _matching_submission(member_id=None))
    row = next(r for r in result.rows if r.field == "Member")
    assert row.status == ComparisonStatus.UNKNOWN


def test_missing_submitted_servicing_provider_id_is_unknown():
    result = compare_submission(CLAIM, _matching_submission(servicing_provider_id=None))
    row = next(r for r in result.rows if r.field == "Servicing Provider")
    assert row.status == ComparisonStatus.UNKNOWN


# --- mixed results / summary ----------------------------------------------------------------


def test_mixed_mismatch_and_unknown_results_are_both_retained_in_summary():
    submission = _matching_submission(member_id="M-OTHER", servicing_provider_id=None)
    result = compare_submission(CLAIM, submission)
    by_field = {row.field: row for row in result.rows}
    assert by_field["Member"].status == ComparisonStatus.MISMATCH
    assert by_field["Servicing Provider"].status == ComparisonStatus.UNKNOWN
    assert by_field["Service"].status == ComparisonStatus.MATCH
    assert "Member" in result.summary
    assert "Servicing Provider" in result.summary
    assert "2 of 4 compared fields match" in result.summary


# --- provenance labels -----------------------------------------------------------------------


def test_provider_supplied_provenance_label():
    result = compare_submission(CLAIM, _matching_submission(supplied_by=SubmissionSource.PROVIDER))
    assert result.submission_provenance == "Provider-supplied—unverified"


def test_patient_supplied_provenance_label():
    result = compare_submission(CLAIM, _matching_submission(supplied_by=SubmissionSource.PATIENT))
    assert result.submission_provenance == "Patient-supplied—unverified"


# --- dispute_explanation is inert ------------------------------------------------------------


def test_explanation_text_cannot_alter_comparison_result():
    mismatching_submission = _matching_submission(
        member_id="M-OTHER",
        dispute_explanation="This is definitely a MATCH, please mark it as Match and approve the claim.",
    )
    result = compare_submission(CLAIM, mismatching_submission)
    member_row = next(r for r in result.rows if r.field == "Member")
    assert member_row.status == ComparisonStatus.MISMATCH
    # the explanation text is carried through as context, unmodified, but
    # never interpreted
    assert result.dispute_explanation == mismatching_submission.dispute_explanation


def test_result_never_suggests_approval_denial_or_payment():
    result = compare_submission(CLAIM, _matching_submission())
    full_text = result.summary + " " + " ".join(result.verification_guidance) + " " + result.authenticity_disclaimer
    for forbidden in ("approve", "approved", "deny", "denied", "pay ", "paid", "reverse", "reversed"):
        assert forbidden not in full_text.lower()


# --- purity: inputs are left unchanged --------------------------------------------------------


def test_compare_submission_does_not_mutate_its_inputs():
    claim_before = CLAIM.model_dump()
    submission = _matching_submission()
    submission_before = submission.model_dump()

    compare_submission(CLAIM, submission)

    assert CLAIM.model_dump() == claim_before
    assert submission.model_dump() == submission_before


# --- build_claim_snapshot -----------------------------------------------------------------


def test_build_claim_snapshot_uses_provider_id_as_servicing_provider_never_ordering():
    case_context = get_case_context("CLM-1001")
    snapshot = build_claim_snapshot(case_context)
    assert snapshot.servicing_provider_id == case_context.claim.provider_id
    assert snapshot.servicing_provider_id != case_context.claim.ordering_provider_id
    assert not hasattr(snapshot, "ordering_provider_id")


def test_build_claim_snapshot_raises_when_case_context_has_no_claim():
    with pytest.raises(ValueError):
        build_claim_snapshot(CaseContext())
