"""Tests for application/dispute_models.py's own shape/validation and
is_accepted_dispute_draft."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from application.dispute_models import (
    DisputeBrief,
    DisputeGenerationStatus,
    DisputeValidationResult,
    DisputeValidationStatus,
    DisputeWorkflowResult,
    EvidenceGateResult,
    EvidenceGateStatus,
    is_accepted_dispute_draft,
)
from application.models import ActionCode, Finding, SuggestedNextStep


def _brief(**overrides) -> DisputeBrief:
    defaults = dict(
        summary="s",
        findings=[Finding(statement="f", evidence_refs=["claim:CLM-1001"])],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(action_code=ActionCode.HUMAN_REVIEW, rationale="r", evidence_refs=[]),
    )
    defaults.update(overrides)
    return DisputeBrief(**defaults)


def test_dispute_brief_rejects_unknown_field():
    with pytest.raises(ValidationError):
        DisputeBrief(
            summary="s",
            findings=[],
            missing_or_conflicting_evidence=[],
            verification_questions=[],
            suggested_next_step=SuggestedNextStep(action_code=ActionCode.HUMAN_REVIEW, rationale="r", evidence_refs=[]),
            comparison_status="MATCH",  # not a real field -- must be rejected
        )


def test_dispute_brief_has_no_case_identity_or_confidence_field():
    fields = set(DisputeBrief.model_fields)
    assert "claim_id" not in fields
    assert "confidence" not in fields
    # and, per design, no field for the four comparison verdicts either
    assert "comparison_result" not in fields
    assert "comparison_status" not in fields


def _minimal_workflow_result(**overrides) -> DisputeWorkflowResult:
    defaults = dict(
        run_id="run-1",
        claim_id="CLM-1001",
        skill_status="COMPLETED",
        gate_result=EvidenceGateResult(status=EvidenceGateStatus.READY_FOR_SCOPED_GENERATION),
        generation_status=DisputeGenerationStatus.NOT_ATTEMPTED,
    )
    defaults.update(overrides)
    return DisputeWorkflowResult(**defaults)


def test_is_accepted_dispute_draft_false_when_not_attempted():
    result = _minimal_workflow_result()
    assert is_accepted_dispute_draft(result) is False


def test_is_accepted_dispute_draft_false_when_drafted_but_not_validated():
    result = _minimal_workflow_result(
        generation_status=DisputeGenerationStatus.DRAFTED,
        brief=_brief(),
        validation_result=DisputeValidationResult(status=DisputeValidationStatus.FAILED),
    )
    assert is_accepted_dispute_draft(result) is False


def test_is_accepted_dispute_draft_true_when_drafted_and_passed():
    result = _minimal_workflow_result(
        generation_status=DisputeGenerationStatus.DRAFTED,
        brief=_brief(),
        validation_result=DisputeValidationResult(status=DisputeValidationStatus.PASSED),
    )
    assert is_accepted_dispute_draft(result) is True


def test_workflow_result_validation_defaults_to_not_run():
    result = _minimal_workflow_result()
    assert result.validation_result.status == DisputeValidationStatus.NOT_RUN


def test_workflow_result_rejects_unknown_field():
    with pytest.raises(ValidationError):
        _minimal_workflow_result(judge_status="COMPLETED")
