"""Tests for dispute_review.models: normalization, date validation, and
immutability of the dispute-review contracts."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from dispute_review.models import (
    ClaimSnapshot,
    ComparisonRow,
    ComparisonStatus,
    DISPUTE_AUTHENTICITY_DISCLAIMER,
    DisputeComparisonResult,
    DisputeSubmission,
    SubmissionSource,
)


# --- blank/whitespace normalization -----------------------------------------------------


def test_blank_strings_normalize_to_missing():
    submission = DisputeSubmission(
        authorization_reference_number="   ",
        member_id="",
        service_code="\t\n",
        servicing_provider_id="   ",
        dispute_explanation="  ",
    )
    assert submission.authorization_reference_number is None
    assert submission.member_id is None
    assert submission.service_code is None
    assert submission.servicing_provider_id is None
    assert submission.dispute_explanation is None


def test_identifiers_are_trimmed_of_surrounding_whitespace():
    submission = DisputeSubmission(member_id="  M-1001  ", service_code="  MRI-KNEE ")
    assert submission.member_id == "M-1001"
    assert submission.service_code == "MRI-KNEE"


def test_supplied_by_defaults_to_provider():
    submission = DisputeSubmission()
    assert submission.supplied_by == SubmissionSource.PROVIDER


# --- date parsing / validation ----------------------------------------------------------


def test_valid_iso_date_strings_parse_to_date_objects():
    submission = DisputeSubmission(
        authorization_start_date="2026-01-01", authorization_end_date="2026-06-30"
    )
    assert submission.authorization_start_date == date(2026, 1, 1)
    assert submission.authorization_end_date == date(2026, 6, 30)


def test_real_date_objects_pass_through_unchanged():
    start = date(2026, 1, 1)
    submission = DisputeSubmission(authorization_start_date=start)
    assert submission.authorization_start_date == start


def test_blank_date_string_becomes_missing():
    submission = DisputeSubmission(authorization_start_date="   ")
    assert submission.authorization_start_date is None


def test_malformed_date_string_raises_validation_error():
    with pytest.raises(ValidationError):
        DisputeSubmission(authorization_start_date="not-a-date")


def test_impossible_date_raises_validation_error():
    with pytest.raises(ValidationError):
        DisputeSubmission(authorization_start_date="2026-02-30")


def test_start_date_after_end_date_raises_validation_error():
    with pytest.raises(ValidationError):
        DisputeSubmission(
            authorization_start_date="2026-06-01", authorization_end_date="2026-01-01"
        )


def test_dates_are_never_silently_swapped():
    # A reversed range must raise, not get quietly corrected into a valid
    # (earlier, later) pair -- assert the exception, then confirm no
    # DisputeSubmission with these values was ever produced.
    with pytest.raises(ValidationError):
        DisputeSubmission(
            authorization_start_date="2026-06-01", authorization_end_date="2026-01-01"
        )


def test_equal_start_and_end_date_is_valid():
    submission = DisputeSubmission(
        authorization_start_date="2026-03-01", authorization_end_date="2026-03-01"
    )
    assert submission.authorization_start_date == submission.authorization_end_date


# --- immutability / schema strictness ---------------------------------------------------


def test_dispute_submission_is_frozen():
    submission = DisputeSubmission(member_id="M-1001")
    with pytest.raises(ValidationError):
        submission.member_id = "M-9999"


def test_claim_snapshot_is_frozen():
    snapshot = ClaimSnapshot(claim_id="CLM-1001", member_id="M-1001")
    with pytest.raises(ValidationError):
        snapshot.member_id = "M-9999"


def test_dispute_submission_rejects_unknown_field():
    with pytest.raises(ValidationError):
        DisputeSubmission(unexpected_field="value")


def test_claim_snapshot_allows_missing_fields():
    snapshot = ClaimSnapshot(claim_id="CLM-TEST")
    assert snapshot.member_id is None
    assert snapshot.service_code is None
    assert snapshot.date_of_service is None
    assert snapshot.servicing_provider_id is None


# --- DisputeComparisonResult shape -------------------------------------------------------


def _row(field: str, status: ComparisonStatus) -> ComparisonRow:
    return ComparisonRow(field=field, claim_value="x", submitted_value="x", status=status, explanation="explanation")


def test_dispute_comparison_result_requires_exactly_four_rows():
    three_rows = [_row("Member", ComparisonStatus.MATCH), _row("Service", ComparisonStatus.MATCH), _row("Validity Dates", ComparisonStatus.MATCH)]
    with pytest.raises(ValidationError):
        DisputeComparisonResult(
            claim_id="CLM-1001",
            submission_provenance="Provider-supplied—unverified",
            rows=three_rows,
            summary="summary",
        )


def test_dispute_comparison_result_authenticity_disclaimer_defaults_to_fixed_text():
    four_rows = [
        _row("Member", ComparisonStatus.MATCH),
        _row("Service", ComparisonStatus.MATCH),
        _row("Validity Dates", ComparisonStatus.MATCH),
        _row("Servicing Provider", ComparisonStatus.MATCH),
    ]
    result = DisputeComparisonResult(
        claim_id="CLM-1001",
        submission_provenance="Provider-supplied—unverified",
        rows=four_rows,
        summary="summary",
    )
    assert result.authenticity_disclaimer == DISPUTE_AUTHENTICITY_DISCLAIMER
