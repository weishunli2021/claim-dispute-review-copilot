"""Tests for skills.investigate_dispute: the SkillResult framing around
context.dispute_evidence_retriever.build_dispute_evidence_package."""

from __future__ import annotations

from skills.base import SkillStatus
from skills.investigate_dispute import investigate_dispute
from dispute_review.billing_fixtures import BILLING_CLAIM_ID
from dispute_review.models import BillingCorrectionSubmission
from dispute_review.presets import billing_correction_example_preset


def test_completed_for_known_claim():
    result = investigate_dispute(BILLING_CLAIM_ID, billing_correction_example_preset())
    assert result.status == SkillStatus.COMPLETED
    assert "dispute_evidence_package" in result.evidence


def test_not_found_for_unknown_claim():
    result = investigate_dispute("CLM-DOES-NOT-EXIST", billing_correction_example_preset())
    assert result.status == SkillStatus.NOT_FOUND


def test_missing_information_reflects_insufficient_evidence_fields():
    result = investigate_dispute(BILLING_CLAIM_ID, billing_correction_example_preset())
    assert result.missing_information == []  # the default example is fully supported


def test_evidence_package_round_trips_through_model_dump():
    from context.dispute_evidence_models import DisputeEvidencePackage

    result = investigate_dispute(BILLING_CLAIM_ID, billing_correction_example_preset())
    package = DisputeEvidencePackage.model_validate(result.evidence["dispute_evidence_package"])
    assert package.claim_id == BILLING_CLAIM_ID


def test_never_raises_on_a_blank_submission():
    result = investigate_dispute(BILLING_CLAIM_ID, BillingCorrectionSubmission())
    assert result.status == SkillStatus.COMPLETED
