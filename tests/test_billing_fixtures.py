"""Tests for dispute_review.billing_fixtures and dispute_review.billing_policy
-- the isolated, separately-seeded fixtures for the billing-correction
scenario. Confirms they never touch tools.data_store or the golden-dataset
claims, and that supporting records are read-only (never mutated by the
loader)."""

from __future__ import annotations

import pytest

from dispute_review.billing_fixtures import (
    BILLING_CLAIM_ID,
    BillingFixtureLoadError,
    get_billing_claim_record,
    get_billing_support_records,
)
from dispute_review.billing_policy import get_billing_policy_sections


def test_claim_record_matches_fixed_claim_id():
    record = get_billing_claim_record()
    assert record.claim_id == BILLING_CLAIM_ID


def test_claim_id_is_isolated_from_golden_dataset_claims():
    assert BILLING_CLAIM_ID not in {"CLM-1001", "CLM-1002", "CLM-1003", "CLM-1004", "CLM-1005"}


def test_claim_record_never_read_via_data_store():
    from tools.data_store import get_data_store

    store = get_data_store()
    assert BILLING_CLAIM_ID not in store.claims


def test_support_records_have_stable_ids_and_summaries():
    records = get_billing_support_records()
    assert len(records) >= 3
    ids = {r.record_id for r in records}
    assert {"BILLREC-001", "BILLREC-002", "BILLREC-003"}.issubset(ids)
    for record in records:
        assert record.summary


def test_support_records_are_immutable():
    from pydantic import ValidationError

    record = get_billing_support_records()[0]
    with pytest.raises(ValidationError):
        record.summary = "mutated"


def test_missing_fixture_raises_billing_fixture_load_error(tmp_path):
    with pytest.raises(BillingFixtureLoadError):
        get_billing_claim_record(tmp_path / "does_not_exist.json")


def test_billing_policy_sections_are_parsed_and_non_empty():
    sections = get_billing_policy_sections()
    assert len(sections) >= 5
    ids = [s.section_id for s in sections]
    assert ids == sorted(ids, key=lambda s: int(s.split("-")[1]))  # in document order
    for section in sections:
        assert section.title
        assert section.text
