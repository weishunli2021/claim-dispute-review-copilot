"""Tests for context.dispute_evidence_models: shape and validation of the
Module 6A typed evidence contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from context.dispute_evidence_models import (
    DisputeEvidencePackage,
    EvidenceProvenanceCategory,
    EvidenceReference,
    EvidenceSourceOutcome,
    EvidenceSourceStatus,
)
from dispute_review.comparison import build_claim_snapshot, compare_submission
from dispute_review.models import DisputeSubmission
from tools.case_context import get_case_context


def _real_claim_snapshot_and_result():
    case_context = get_case_context("CLM-1001")
    snapshot = build_claim_snapshot(case_context)
    submission = DisputeSubmission(member_id="M-1001")
    result = compare_submission(snapshot, submission)
    return snapshot, submission, result


def test_evidence_reference_rejects_unknown_field():
    with pytest.raises(ValidationError):
        EvidenceReference(
            ref_id="claim:CLM-1001",
            source_type="claim",
            provenance=EvidenceProvenanceCategory.RECORDED,
            label="x",
            detail="x",
            unexpected="oops",
        )


def test_evidence_reference_is_frozen():
    ref = EvidenceReference(
        ref_id="claim:CLM-1001", source_type="claim", provenance=EvidenceProvenanceCategory.RECORDED, label="x", detail="x"
    )
    with pytest.raises(ValidationError):
        ref.detail = "changed"


def test_evidence_source_outcome_status_values():
    outcome = EvidenceSourceOutcome(source="policy", status=EvidenceSourceStatus.SUCCESS_NO_RESULTS)
    assert outcome.status == EvidenceSourceStatus.SUCCESS_NO_RESULTS
    assert outcome.item_count == 0


def test_provenance_categories_are_distinct_strings():
    values = {
        EvidenceProvenanceCategory.RECORDED,
        EvidenceProvenanceCategory.SUBMITTED_UNVERIFIED,
        EvidenceProvenanceCategory.RECORDED_VIA_SUBMITTED_LOOKUP,
        EvidenceProvenanceCategory.RETRIEVED_POLICY,
        EvidenceProvenanceCategory.RECORDED_RELATIONSHIP,
        EvidenceProvenanceCategory.DETERMINISTIC_COMPARISON,
    }
    assert len(values) == 6


def test_dispute_evidence_package_requires_all_core_fields():
    snapshot, submission, result = _real_claim_snapshot_and_result()
    package = DisputeEvidencePackage(
        claim_id="CLM-1001",
        claim_snapshot=snapshot,
        submission=submission,
        comparison_result=result,
    )
    assert package.recorded_facts == []
    assert package.submitted_fields == []
    assert package.comparison_findings == []
    assert package.policy_passages == []
    assert package.graph_relationships == []
    assert package.source_outcomes == []
    assert package.missing_evidence == []
    assert package.conflicts == []
    assert package.limitations == []


def test_dispute_evidence_package_is_frozen_and_rejects_unknown_field():
    snapshot, submission, result = _real_claim_snapshot_and_result()
    package = DisputeEvidencePackage(
        claim_id="CLM-1001", claim_snapshot=snapshot, submission=submission, comparison_result=result
    )
    with pytest.raises(ValidationError):
        package.claim_id = "CLM-9999"
    with pytest.raises(ValidationError):
        DisputeEvidencePackage(
            claim_id="CLM-1001",
            claim_snapshot=snapshot,
            submission=submission,
            comparison_result=result,
            unexpected="oops",
        )
