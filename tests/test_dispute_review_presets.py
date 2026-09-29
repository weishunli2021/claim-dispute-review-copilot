"""Tests for dispute_review.presets: the single billing-correction example,
verified end-to-end against the real isolated fixtures (never hardcoded
expected outcomes -- the comparator itself proves the example is fully
supported)."""

from __future__ import annotations

from dispute_review.billing_fixtures import (
    BILLING_CLAIM_ID,
    get_billing_claim_record,
    get_billing_support_records,
)
from dispute_review.comparison import build_billing_claim_snapshot, compare_billing_correction
from dispute_review.models import BillingCorrectionSubmission, BillingFindingStatus
from dispute_review.presets import billing_correction_example_preset


def test_preset_is_a_billing_correction_submission():
    assert isinstance(billing_correction_example_preset(), BillingCorrectionSubmission)


def test_preset_references_the_fixed_claim():
    preset = billing_correction_example_preset()
    assert preset.original_claim_reference == BILLING_CLAIM_ID


def test_preset_is_fully_supported_by_real_fixtures():
    claim = build_billing_claim_snapshot(get_billing_claim_record())
    records = get_billing_support_records()
    result = compare_billing_correction(claim, billing_correction_example_preset(), records)
    assert all(row.status == BillingFindingStatus.SUPPORTED for row in result.rows)


def test_preset_corrects_all_four_deliberate_discrepancies():
    claim = build_billing_claim_snapshot(get_billing_claim_record())
    preset = billing_correction_example_preset()
    assert preset.service_code != claim.service_code
    assert preset.modifier != claim.modifier
    assert preset.units != claim.units
    assert preset.servicing_provider_id != claim.servicing_provider_id
