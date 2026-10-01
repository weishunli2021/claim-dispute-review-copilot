"""Tests for dispute_review.comparison: build_billing_claim_snapshot(),
check_claim_member_linkage(), compare_billing_correction(), and
build_recommended_next_action() -- the pure, deterministic core of the
billing-correction scenario.
"""

from __future__ import annotations

from dispute_review.billing_fixtures import BillingSupportRecord
from dispute_review.comparison import (
    RECOMMENDED_ROUTE_FOR_REVIEW,
    build_billing_claim_snapshot,
    build_recommended_next_action,
    check_claim_member_linkage,
    compare_billing_correction,
)
from dispute_review.billing_fixtures import get_billing_claim_record
from dispute_review.models import BillingClaimSnapshot, BillingCorrectionSubmission, BillingFindingStatus

CLAIM_ID = "CLM-BILL-9001"


def _real_claim() -> BillingClaimSnapshot:
    return build_billing_claim_snapshot(get_billing_claim_record())


def _support_records() -> list[BillingSupportRecord]:
    return [
        BillingSupportRecord(
            record_id="BILLREC-001",
            record_type="Operative note",
            summary="s",
            supports_service_code="SURG-KNEE-REPAIR",
            supports_modifier="MOD-L",
            supports_servicing_provider_id="PRV-BILL-SYNTHETICCHOICEPPO500",
        ),
        BillingSupportRecord(
            record_id="BILLREC-002",
            record_type="Units log",
            summary="s",
            supports_modifier="MOD-L",
            supports_units=1,
        ),
        BillingSupportRecord(
            record_id="BILLREC-003",
            record_type="Provider roster",
            summary="s",
            supports_servicing_provider_id="PRV-BILL-SYNTHETICCHOICEPPO500",
        ),
    ]


def test_build_billing_claim_snapshot_reads_real_fixture():
    claim = _real_claim()
    assert claim.claim_id == CLAIM_ID
    assert claim.service_code == "SURG-KNEE-ARTHRO"
    assert claim.modifier == "MOD-R"
    assert claim.units == 2
    assert claim.servicing_provider_id == "PRV-BILL-SUNRISEHMO200"


# --- check_claim_member_linkage -------------------------------------------------------------


def test_linkage_ok_when_submission_leaves_identifiers_blank():
    claim = _real_claim()
    assert check_claim_member_linkage(claim, BillingCorrectionSubmission()) is None


def test_linkage_ok_when_submission_matches():
    claim = _real_claim()
    submission = BillingCorrectionSubmission(original_claim_reference=CLAIM_ID, member_id=claim.member_id)
    assert check_claim_member_linkage(claim, submission) is None


def test_linkage_blocked_on_wrong_claim_reference():
    claim = _real_claim()
    issue = check_claim_member_linkage(claim, BillingCorrectionSubmission(original_claim_reference="CLM-OTHER"))
    assert issue is not None and "claim reference" in issue.lower()


def test_linkage_blocked_on_wrong_member_id():
    claim = _real_claim()
    issue = check_claim_member_linkage(claim, BillingCorrectionSubmission(member_id="MEM-OTHER"))
    assert issue is not None and "member" in issue.lower()


# --- compare_billing_correction: the default, fully-supported example -----------------------


def test_default_example_all_four_corrections_supported():
    claim = _real_claim()
    submission = BillingCorrectionSubmission(
        service_code="SURG-KNEE-REPAIR", modifier="MOD-L", units=1, servicing_provider_id="PRV-BILL-SYNTHETICCHOICEPPO500"
    )
    result = compare_billing_correction(claim, submission, _support_records())
    assert len(result.rows) == 4
    assert all(row.status == BillingFindingStatus.SUPPORTED for row in result.rows)
    assert all(row.evidence_refs for row in result.rows)
    assert build_recommended_next_action(result) == RECOMMENDED_ROUTE_FOR_REVIEW


def test_row_order_is_fixed():
    claim = _real_claim()
    result = compare_billing_correction(claim, BillingCorrectionSubmission(), _support_records())
    assert [row.field for row in result.rows] == ["Service Code", "Modifier", "Units", "Servicing Provider"]


# --- wrong proposed value per field is detected, never automatically correct ----------------


def test_wrong_proposed_service_code_conflicts():
    claim = _real_claim()
    submission = BillingCorrectionSubmission(service_code="SURG-BOGUS")
    result = compare_billing_correction(claim, submission, _support_records())
    row = result.rows[0]
    assert row.field == "Service Code"
    assert row.status == BillingFindingStatus.CONFLICTS
    assert row.supporting_value == "SURG-KNEE-REPAIR"


def test_wrong_proposed_modifier_conflicts():
    claim = _real_claim()
    result = compare_billing_correction(claim, BillingCorrectionSubmission(modifier="MOD-R"), _support_records())
    row = next(r for r in result.rows if r.field == "Modifier")
    # MOD-R equals the ORIGINAL value, so this counts as "no correction
    # proposed" -- and the original itself conflicts with the record.
    assert row.status == BillingFindingStatus.CONFLICTS


def test_wrong_proposed_units_conflicts():
    claim = _real_claim()
    result = compare_billing_correction(claim, BillingCorrectionSubmission(units=5), _support_records())
    row = next(r for r in result.rows if r.field == "Units")
    assert row.status == BillingFindingStatus.CONFLICTS
    assert row.supporting_value == "1"


def test_wrong_proposed_servicing_provider_conflicts():
    claim = _real_claim()
    result = compare_billing_correction(
        claim, BillingCorrectionSubmission(servicing_provider_id="PRV-BILL-BOGUS"), _support_records()
    )
    row = next(r for r in result.rows if r.field == "Servicing Provider")
    assert row.status == BillingFindingStatus.CONFLICTS


# --- missing / conflicting independent evidence never silently resolved --------------------


def test_missing_evidence_for_a_field_yields_insufficient_evidence():
    claim = _real_claim()
    records = [r for r in _support_records() if r.record_id != "BILLREC-002"]  # drop the only units evidence
    result = compare_billing_correction(claim, BillingCorrectionSubmission(units=1), records)
    row = next(r for r in result.rows if r.field == "Units")
    assert row.status == BillingFindingStatus.INSUFFICIENT_EVIDENCE
    assert row.supporting_value is None


def test_conflicting_independent_records_yield_insufficient_evidence_not_a_silent_pick():
    claim = _real_claim()
    conflicting = _support_records() + [
        BillingSupportRecord(record_id="BILLREC-999", record_type="Contradicting note", summary="s", supports_units=2)
    ]
    result = compare_billing_correction(claim, BillingCorrectionSubmission(units=1), conflicting)
    row = next(r for r in result.rows if r.field == "Units")
    assert row.status == BillingFindingStatus.INSUFFICIENT_EVIDENCE
    assert "disagree" in row.explanation.lower()
    assert set(row.evidence_refs) == {"support:BILLREC-002", "support:BILLREC-999"}


# --- unchanged, already-correct value is never labeled an error ----------------------------


def test_unchanged_value_already_matching_support_is_consistent_not_an_error():
    claim = BillingClaimSnapshot(claim_id=CLAIM_ID, member_id="MEM-BILL-01", service_code="SURG-KNEE-REPAIR")
    records = [BillingSupportRecord(record_id="R1", record_type="note", summary="s", supports_service_code="SURG-KNEE-REPAIR")]
    result = compare_billing_correction(claim, BillingCorrectionSubmission(), records)
    row = next(r for r in result.rows if r.field == "Service Code")
    assert row.status == BillingFindingStatus.CONSISTENT_NO_CORRECTION_NEEDED


def test_original_value_missing_and_no_correction_proposed_is_insufficient_not_conflicts():
    claim = BillingClaimSnapshot(claim_id=CLAIM_ID)  # service_code never recorded
    records = [BillingSupportRecord(record_id="R1", record_type="note", summary="s", supports_service_code="SURG-KNEE-REPAIR")]
    result = compare_billing_correction(claim, BillingCorrectionSubmission(), records)
    row = next(r for r in result.rows if r.field == "Service Code")
    assert row.status == BillingFindingStatus.INSUFFICIENT_EVIDENCE


# --- deterministic recommended next action --------------------------------------------------


def test_recommended_next_action_names_unresolved_fields_when_not_fully_supported():
    claim = _real_claim()
    result = compare_billing_correction(claim, BillingCorrectionSubmission(service_code="SURG-BOGUS"), _support_records())
    action = build_recommended_next_action(result)
    assert action != RECOMMENDED_ROUTE_FOR_REVIEW
    assert "Service Code" in action
    assert "not determined" in action.lower()
