"""Tests for application/dispute_workflow.py: the evidence gate
(assess_evidence_gate) and the full bounded workflow (run_dispute_workflow)
for the billing-correction scenario.

Fully offline: every workflow run below injects a FakeDisputeBriefAdapter,
never the real SDK.
"""

from __future__ import annotations

import pytest

from application.dispute_generator import FakeDisputeBriefAdapter
from application.dispute_models import (
    DisputeGenerationStatus,
    DisputeValidationStatus,
    EvidenceGateStatus,
    is_accepted_dispute_draft,
)
from application.dispute_workflow import assess_evidence_gate, run_dispute_workflow
from application.models import ActionCode, Finding, SuggestedNextStep
from application.dispute_models import DisputeBrief
from context.dispute_evidence_retriever import build_dispute_evidence_package
from dispute_review.billing_fixtures import BILLING_CLAIM_ID
from dispute_review.models import BillingCorrectionSubmission
from dispute_review.presets import billing_correction_example_preset


def _well_formed_brief(context) -> DisputeBrief:
    real_ref = context.references[0].ref_id
    return DisputeBrief(
        summary="s",
        findings=[Finding(statement="f", evidence_refs=[real_ref])],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(action_code=ActionCode.HUMAN_REVIEW, rationale="r", evidence_refs=[real_ref]),
    )


# --- assess_evidence_gate --------------------------------------------------------------------


def test_gate_ready_for_scoped_generation_for_default_example():
    package = build_dispute_evidence_package(BILLING_CLAIM_ID, billing_correction_example_preset())
    gate = assess_evidence_gate(package)
    assert gate.status == EvidenceGateStatus.READY_FOR_SCOPED_GENERATION


def test_gate_blocked_when_linkage_issue_present():
    package = build_dispute_evidence_package(BILLING_CLAIM_ID, BillingCorrectionSubmission(member_id="MEM-OTHER"))
    gate = assess_evidence_gate(package)
    assert gate.status == EvidenceGateStatus.BLOCKED
    assert gate.reasons


def test_gate_blocked_when_comparison_field_set_corrupted():
    package = build_dispute_evidence_package(BILLING_CLAIM_ID, billing_correction_example_preset())
    bad_row = package.comparison_result.rows[0].model_copy(update={"field": "Not A Real Field"})
    corrupted = package.comparison_result.model_copy(update={"rows": [bad_row] + list(package.comparison_result.rows[1:])})
    package = package.model_copy(update={"comparison_result": corrupted})
    gate = assess_evidence_gate(package)
    assert gate.status == EvidenceGateStatus.BLOCKED


def test_gate_limited_brief_when_support_records_empty():
    package = build_dispute_evidence_package(BILLING_CLAIM_ID, billing_correction_example_preset())
    package = package.model_copy(update={"support_records": []})
    gate = assess_evidence_gate(package)
    assert gate.status == EvidenceGateStatus.READY_FOR_LIMITED_BRIEF


# --- run_dispute_workflow: end to end ---------------------------------------------------------


def test_workflow_completes_with_accepted_draft_for_well_formed_brief():
    submission = billing_correction_example_preset()
    probe = run_dispute_workflow(BILLING_CLAIM_ID, submission, adapter=FakeDisputeBriefAdapter())
    brief = _well_formed_brief(probe.generation_context)
    result = run_dispute_workflow(BILLING_CLAIM_ID, submission, adapter=FakeDisputeBriefAdapter(response=brief))
    assert result.generation_status == DisputeGenerationStatus.DRAFTED
    assert result.validation_result.status == DisputeValidationStatus.PASSED
    assert is_accepted_dispute_draft(result)
    assert result.trace == ["VALIDATE_REQUEST", "INVOKE_SKILL", "ASSESS_GATE", "ASSEMBLE_CONTEXT", "GENERATE", "VALIDATE_BRIEF", "COMPLETE"]


def test_workflow_makes_exactly_one_generation_call():
    submission = billing_correction_example_preset()
    adapter = FakeDisputeBriefAdapter()
    run_dispute_workflow(BILLING_CLAIM_ID, submission, adapter=adapter)
    assert len(adapter.calls) == 1


def test_workflow_blocked_never_calls_the_adapter():
    adapter = FakeDisputeBriefAdapter()
    result = run_dispute_workflow(BILLING_CLAIM_ID, BillingCorrectionSubmission(member_id="MEM-OTHER"), adapter=adapter)
    assert result.gate_result.status == EvidenceGateStatus.BLOCKED
    assert result.generation_status == DisputeGenerationStatus.NOT_ATTEMPTED
    assert len(adapter.calls) == 0
    assert result.comparison_result is None


def test_workflow_unknown_claim_id_errors_without_calling_adapter():
    adapter = FakeDisputeBriefAdapter()
    result = run_dispute_workflow("CLM-DOES-NOT-EXIST", billing_correction_example_preset(), adapter=adapter)
    assert result.skill_status != "COMPLETED"
    assert len(adapter.calls) == 0


def test_workflow_preserves_comparison_result_even_when_generation_fails():
    from application.dispute_generator import DisputeGenerationProviderError

    submission = billing_correction_example_preset()
    result = run_dispute_workflow(
        BILLING_CLAIM_ID, submission, adapter=FakeDisputeBriefAdapter(raises=DisputeGenerationProviderError("boom"))
    )
    assert result.generation_status == DisputeGenerationStatus.FAILED
    assert result.comparison_result is not None
    assert all(row.status.value for row in result.comparison_result.rows)


def test_original_claim_fixture_unchanged_after_a_full_workflow_run():
    from dispute_review.billing_fixtures import get_billing_claim_record

    before = get_billing_claim_record().model_dump()
    run_dispute_workflow(BILLING_CLAIM_ID, billing_correction_example_preset(), adapter=FakeDisputeBriefAdapter())
    after = get_billing_claim_record().model_dump()
    assert before == after


def test_wrong_proposed_values_surface_as_conflicts_not_silently_accepted():
    submission = BillingCorrectionSubmission(service_code="SURG-BOGUS", modifier="MOD-BOGUS", units=99, servicing_provider_id="PRV-BOGUS")
    result = run_dispute_workflow(BILLING_CLAIM_ID, submission, adapter=FakeDisputeBriefAdapter())
    assert result.comparison_result is not None
    statuses = {row.field: row.status.value for row in result.comparison_result.rows}
    assert all(status == "CONFLICTS" for status in statuses.values())
