"""Tests for context.dispute_evidence_models: shape and validation of the
billing-correction scenario's typed evidence contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from context.dispute_evidence_models import (
    DisputeEvidencePackage,
    EvidenceProvenanceCategory,
    EvidenceReference,
)
from dispute_review.models import BillingClaimSnapshot, BillingCorrectionSubmission


def _claim() -> BillingClaimSnapshot:
    return BillingClaimSnapshot(claim_id="CLM-BILL-9001", member_id="MEM-BILL-01")


def test_provenance_category_no_longer_has_retired_authorization_values():
    values = {
        name
        for name in vars(EvidenceProvenanceCategory)
        if not name.startswith("_") and isinstance(getattr(EvidenceProvenanceCategory, name), str)
    }
    assert "AUTHORIZATION_SOURCE" not in values
    assert "DETERMINISTIC_SOURCE_COMPARISON" not in values
    assert "DETERMINISTIC_SUBMISSION_SOURCE_COMPARISON" not in values
    assert "SCENARIO_ONLY_SYNTHETIC_EVENT" not in values
    assert "INDEPENDENT_SUPPORTING_RECORD" in values
    assert "SYNTHETIC_BILLING_POLICY" in values


def test_evidence_package_allows_none_comparison_result_when_linkage_blocked():
    package = DisputeEvidencePackage(
        claim_id="CLM-BILL-9001",
        claim_snapshot=_claim(),
        submission=BillingCorrectionSubmission(member_id="MEM-OTHER"),
        comparison_result=None,
        linkage_issue="blocked for a test reason",
    )
    assert package.comparison_result is None
    assert package.linkage_issue is not None


def test_evidence_reference_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        EvidenceReference(
            ref_id="x",
            source_type="x",
            provenance=EvidenceProvenanceCategory.RECORDED,
            label="x",
            detail="x",
            unexpected_field="nope",
        )


def test_evidence_package_is_frozen():
    package = DisputeEvidencePackage(
        claim_id="CLM-BILL-9001", claim_snapshot=_claim(), submission=BillingCorrectionSubmission()
    )
    with pytest.raises(ValidationError):
        package.claim_id = "OTHER"
