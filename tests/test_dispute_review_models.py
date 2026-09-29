"""Tests for dispute_review/models.py's billing-correction contracts."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from dispute_review.models import (
    BillingClaimSnapshot,
    BillingComparisonResult,
    BillingComparisonRow,
    BillingCorrectionSubmission,
    BillingFindingStatus,
)


def test_submission_blank_strings_become_missing():
    submission = BillingCorrectionSubmission(
        supplied_by="  ", member_id="", service_code="   ", modifier="", servicing_provider_id=""
    )
    assert submission.supplied_by is None
    assert submission.member_id is None
    assert submission.service_code is None
    assert submission.modifier is None
    assert submission.servicing_provider_id is None


def test_submission_blank_units_and_amount_become_missing():
    submission = BillingCorrectionSubmission(units="", proposed_billed_amount="")
    assert submission.units is None
    assert submission.proposed_billed_amount is None


def test_submission_parses_iso_date():
    submission = BillingCorrectionSubmission(service_date="2026-06-01")
    assert submission.service_date == date(2026, 6, 1)


def test_submission_rejects_malformed_date():
    with pytest.raises(ValidationError):
        BillingCorrectionSubmission(service_date="2026-02-30")


def test_submission_rejects_negative_units():
    with pytest.raises(ValidationError):
        BillingCorrectionSubmission(units=-1)


def test_submission_every_field_optional():
    # An incomplete submission is expected input, not a construction error.
    BillingCorrectionSubmission()


def test_submission_is_frozen():
    submission = BillingCorrectionSubmission(member_id="MEM-BILL-01")
    with pytest.raises(ValidationError):
        submission.member_id = "OTHER"


def test_claim_snapshot_distinguishes_recorded_zero_from_not_recorded():
    recorded_zero = BillingClaimSnapshot(claim_id="CLM-BILL-9001", allowed_amount=0.0, allowed_amount_recorded=True)
    not_recorded = BillingClaimSnapshot(claim_id="CLM-BILL-9001", allowed_amount=None, allowed_amount_recorded=False)
    assert recorded_zero.allowed_amount == 0.0 and recorded_zero.allowed_amount_recorded is True
    assert not_recorded.allowed_amount is None and not_recorded.allowed_amount_recorded is False


def test_comparison_result_requires_exactly_four_rows():
    row = BillingComparisonRow(field="Service Code", status=BillingFindingStatus.SUPPORTED, explanation="x")
    with pytest.raises(ValidationError):
        BillingComparisonResult(claim_id="CLM-BILL-9001", submission_provenance="p", rows=[row], summary="s")


def test_comparison_result_always_carries_authenticity_disclaimer():
    rows = [
        BillingComparisonRow(field=f, status=BillingFindingStatus.INSUFFICIENT_EVIDENCE, explanation="x")
        for f in ("Service Code", "Modifier", "Units", "Servicing Provider")
    ]
    result = BillingComparisonResult(claim_id="CLM-BILL-9001", submission_provenance="p", rows=rows, summary="s")
    assert "payment" in result.authenticity_disclaimer.lower()


def test_finding_status_has_exactly_four_values():
    assert {s.value for s in BillingFindingStatus} == {
        "SUPPORTED",
        "CONFLICTS",
        "CONSISTENT_NO_CORRECTION_NEEDED",
        "INSUFFICIENT_EVIDENCE",
    }
