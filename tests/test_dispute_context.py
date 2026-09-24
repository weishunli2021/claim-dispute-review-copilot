"""Tests for application/dispute_context.py's assemble_dispute_context."""

from __future__ import annotations

from application.dispute_context import MAX_POLICY_CONTEXT_CHARS, assemble_dispute_context
from application.dispute_models import EvidenceGateResult, EvidenceGateStatus
from context.dispute_evidence_models import EvidenceProvenanceCategory, EvidenceReference
from context.dispute_evidence_retriever import build_dispute_evidence_package
from dispute_review.presets import build_demo_presets
from tools.case_context import get_case_context


def _real_package(preset_name: str = "matching"):
    case_context = get_case_context("CLM-1001")
    presets = build_demo_presets(case_context)
    return build_dispute_evidence_package("CLM-1001", getattr(presets, preset_name))


def test_context_carries_claim_id_and_gate_status():
    package = _real_package()
    gate = EvidenceGateResult(status=EvidenceGateStatus.READY_FOR_SCOPED_GENERATION)
    context = assemble_dispute_context(package, gate)
    assert context.claim_id == "CLM-1001"
    assert context.gate_status == EvidenceGateStatus.READY_FOR_SCOPED_GENERATION
    assert context.comparison_summary == package.comparison_result.summary


def test_context_includes_every_non_policy_reference():
    package = _real_package()
    gate = EvidenceGateResult(status=EvidenceGateStatus.READY_FOR_SCOPED_GENERATION)
    context = assemble_dispute_context(package, gate)
    ref_ids = {r.ref_id for r in context.references}
    for source_list in (
        package.recorded_facts,
        package.submitted_fields,
        package.comparison_findings,
        package.graph_relationships,
    ):
        for ref in source_list:
            assert ref.ref_id in ref_ids


def test_context_preserves_missing_evidence_conflicts_and_limitations():
    package = _real_package("different_servicing_provider")
    gate = EvidenceGateResult(status=EvidenceGateStatus.READY_FOR_SCOPED_GENERATION)
    context = assemble_dispute_context(package, gate)
    assert context.missing_evidence == package.missing_evidence
    assert context.conflicts == package.conflicts
    assert context.limitations == package.limitations
    # the "no policy passage addresses changing servicing provider" limitation survives
    assert any("change in servicing provider" in item for item in context.limitations)


def test_policy_passage_within_budget_is_not_truncated():
    package = _real_package()
    gate = EvidenceGateResult(status=EvidenceGateStatus.READY_FOR_SCOPED_GENERATION)
    context = assemble_dispute_context(package, gate)
    assert context.context_truncated is False
    assert context.truncation_notes == []
    policy_refs_in_context = [r for r in context.references if r.source_type == "policy"]
    assert len(policy_refs_in_context) == len(package.policy_passages)
    for original, in_context in zip(package.policy_passages, policy_refs_in_context):
        assert in_context.detail == original.detail


def test_policy_passage_over_budget_is_truncated_and_flagged():
    package = _real_package()
    gate = EvidenceGateResult(status=EvidenceGateStatus.READY_FOR_SCOPED_GENERATION)
    oversized_text = "X" * (MAX_POLICY_CONTEXT_CHARS + 500)
    oversized_ref = EvidenceReference(
        ref_id="policy:oversized-chunk",
        source_type="policy",
        provenance=EvidenceProvenanceCategory.RETRIEVED_POLICY,
        label="Oversized policy chunk",
        detail=oversized_text,
    )
    corrupted_package = package.model_copy(update={"policy_passages": [oversized_ref]})

    context = assemble_dispute_context(corrupted_package, gate)
    assert context.context_truncated is True
    assert len(context.truncation_notes) == 1
    assert "policy:oversized-chunk" in context.truncation_notes[0]

    truncated_ref = next(r for r in context.references if r.ref_id == "policy:oversized-chunk")
    assert len(truncated_ref.detail) < len(oversized_text)
    assert truncated_ref.detail.endswith("…[truncated]")


def test_only_included_references_can_be_cited():
    # A reference dropped entirely for budget reasons must not appear in
    # context.references at all -- the generator/validator can then never
    # legitimately cite it (application/dispute_brief_validator.py's Rule A).
    package = _real_package()
    gate = EvidenceGateResult(status=EvidenceGateStatus.READY_FOR_SCOPED_GENERATION)
    huge_text = "Y" * (MAX_POLICY_CONTEXT_CHARS + 100)
    two_refs = [
        EvidenceReference(
            ref_id="policy:first-huge-chunk",
            source_type="policy",
            provenance=EvidenceProvenanceCategory.RETRIEVED_POLICY,
            label="First",
            detail=huge_text,
        ),
        EvidenceReference(
            ref_id="policy:second-omitted-chunk",
            source_type="policy",
            provenance=EvidenceProvenanceCategory.RETRIEVED_POLICY,
            label="Second",
            detail="short text",
        ),
    ]
    corrupted_package = package.model_copy(update={"policy_passages": two_refs})
    context = assemble_dispute_context(corrupted_package, gate)

    ref_ids = {r.ref_id for r in context.references}
    assert "policy:first-huge-chunk" in ref_ids  # truncated, but present
    assert "policy:second-omitted-chunk" not in ref_ids  # budget exhausted -> omitted entirely
    assert any("policy:second-omitted-chunk" in note and "omitted entirely" in note for note in context.truncation_notes)
