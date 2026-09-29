"""Tests for application/dispute_context.py's assemble_dispute_context."""

from __future__ import annotations

from application.dispute_context import MAX_POLICY_CONTEXT_CHARS, assemble_dispute_context
from application.dispute_models import EvidenceGateResult, EvidenceGateStatus
from context.dispute_evidence_models import DisputeEvidencePackage, EvidenceReference
from context.dispute_evidence_models import EvidenceProvenanceCategory as Provenance
from context.dispute_evidence_retriever import build_dispute_evidence_package
from dispute_review.billing_fixtures import BILLING_CLAIM_ID
from dispute_review.presets import billing_correction_example_preset


def _ready() -> EvidenceGateResult:
    return EvidenceGateResult(status=EvidenceGateStatus.READY_FOR_SCOPED_GENERATION, reasons=[])


def test_context_combines_all_non_policy_references_verbatim():
    package = build_dispute_evidence_package(BILLING_CLAIM_ID, billing_correction_example_preset())
    context = assemble_dispute_context(package, _ready())
    ref_ids = {ref.ref_id for ref in context.references}
    for ref in package.recorded_facts + package.submitted_fields + package.comparison_findings + package.support_records:
        assert ref.ref_id in ref_ids


def test_context_reports_linkage_block_as_comparison_summary_when_comparison_is_none():
    package = DisputeEvidencePackage(
        claim_id=BILLING_CLAIM_ID,
        claim_snapshot=build_dispute_evidence_package(BILLING_CLAIM_ID, billing_correction_example_preset()).claim_snapshot,
        submission=billing_correction_example_preset(),
        comparison_result=None,
        linkage_issue="blocked for a test",
    )
    context = assemble_dispute_context(package, EvidenceGateResult(status=EvidenceGateStatus.BLOCKED, reasons=["blocked for a test"]))
    assert "blocked for a test" in context.comparison_summary


def test_policy_passage_truncated_past_budget_and_flagged_not_silent():
    package = build_dispute_evidence_package(BILLING_CLAIM_ID, billing_correction_example_preset())
    huge_ref = EvidenceReference(
        ref_id="policy:HUGE",
        source_type="policy",
        provenance=Provenance.SYNTHETIC_BILLING_POLICY,
        label="huge",
        detail="x" * (MAX_POLICY_CONTEXT_CHARS + 500),
    )
    package = package.model_copy(update={"policy_passages": list(package.policy_passages) + [huge_ref]})
    context = assemble_dispute_context(package, _ready())
    assert context.context_truncated is True
    assert context.truncation_notes
    kept = next(ref for ref in context.references if ref.ref_id == "policy:HUGE")
    assert len(kept.detail) <= MAX_POLICY_CONTEXT_CHARS + len(" …[truncated]")
