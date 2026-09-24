"""Tests for application/context_assembler.py.

All fully offline/deterministic -- no network, no API key required. Uses
the real agent workflow (run_case_agent) against the real synthetic
fixtures, exactly like every other layer's tests in this project.
"""

from __future__ import annotations

import copy

import pytest

from agents.case_agent import run_case_agent
from application.context_assembler import assemble_context


def test_context_provenance_every_reference_traces_to_a_real_field():
    result = run_case_agent("CLM-1001", "Why was this claim denied?")
    context = assemble_context(result)

    ref_ids = {ref.ref_id for ref in context.references}
    assert "claim:CLM-1001" in ref_ids
    assert "member:M-1001" in ref_ids
    assert "plan:PLAN-GOLD" in ref_ids
    assert "benefit:BEN-GOLD-MRI-KNEE" in ref_ids
    assert "provider:servicing:PRV-1001" in ref_ids
    assert "provider:ordering:PRV-4001" in ref_ids
    # Policy chunk ref ids must be the retriever's own chunk_id, not an
    # invented label.
    policy_refs = [r for r in context.references if r.kind == "policy"]
    assert policy_refs
    for ref in policy_refs:
        assert ref.ref_id.startswith("policy:")
    package_chunk_ids = {c.chunk_id for c in result.evidence_package.policy_chunks}
    assert {r.ref_id.removeprefix("policy:") for r in policy_refs} == package_chunk_ids
    # Graph relationship ref ids must be the composite of real source/relation/target fields.
    graph_refs = {r.ref_id for r in context.references if r.kind == "graph_relationship"}
    for rel in result.evidence_package.graph_relationships:
        assert f"graph:{rel.source_id}--{rel.relation}-->{rel.target_id}" in graph_refs


def test_no_mutation_of_source_evidence_package():
    result = run_case_agent("CLM-1002", "Was this claim paid correctly?")
    before = copy.deepcopy(result.evidence_package.model_dump())

    assemble_context(result)

    after = result.evidence_package.model_dump()
    assert before == after


def test_case2_preserves_both_authorization_candidates():
    result = run_case_agent("CLM-1002", "Is there an approved authorization on file for this MRI?")
    context = assemble_context(result)

    auth_refs = {ref.ref_id: ref for ref in context.references if ref.kind == "authorization"}
    assert set(auth_refs) == {"auth:PA-1501", "auth:PA-2001"}
    assert "EXPIRED" in auth_refs["auth:PA-1501"].detail
    assert "APPROVED" in auth_refs["auth:PA-2001"].detail


def test_case3_present_but_not_covered_benefit_is_a_reference_not_a_gap():
    result = run_case_agent("CLM-1003", "Why was my lab claim denied?")
    context = assemble_context(result)

    benefit_refs = [r for r in context.references if r.kind == "benefit"]
    assert len(benefit_refs) == 1
    assert "covered=False" in benefit_refs[0].detail
    assert "benefit" not in context.missing_information


def test_case4_out_of_network_provider_is_resolved_not_missing():
    result = run_case_agent("CLM-1004", "Why was my physical therapy claim denied?")
    context = assemble_context(result)

    provider_refs = [r for r in context.references if r.kind == "servicing_provider"]
    assert len(provider_refs) == 1
    assert "out-of-network" in provider_refs[0].detail
    assert "servicing_provider" not in context.missing_information


def test_paid_vs_denied_and_null_denial_reason_are_preserved():
    denied = assemble_context(run_case_agent("CLM-1001", "Why was this claim denied?"))
    assert denied.claim_status == "DENIED"
    assert denied.denial_reason_code == "AUTH_REQUIRED"

    paid = assemble_context(run_case_agent("CLM-1002", "Was this claim paid correctly?"))
    assert paid.claim_status == "PAID"
    assert paid.denial_reason_code is None
    assert paid.denial_reason_description is None


def test_retrieval_config_is_read_verbatim_not_assumed_from_env_default():
    # RAG_EMBEDDING_PROVIDER defaults to "tfidf" (see .env.example / H0), but
    # context.hybrid_retriever.build_evidence_package hardcodes its own
    # DEFAULT_POLICY_PROVIDER ("semantic") -- the assembled context must
    # reflect what the EvidencePackage actually recorded, not the env
    # default.
    result = run_case_agent("CLM-1001", "Why was this claim denied?")
    context = assemble_context(result)
    assert context.retrieval_config["policy_provider"] == result.evidence_package.provenance.policy_provider


def test_assemble_context_requires_an_evidence_package():
    result = run_case_agent("CLM-9999", "What happened with this claim?")
    assert result.evidence_package is None
    with pytest.raises(ValueError):
        assemble_context(result)


def test_dedup_keeps_distinct_ids_and_collapses_true_duplicates():
    from application.context_assembler import _dedupe_by_ref_id
    from application.models import ContextReference

    refs = [
        ContextReference(ref_id="auth:PA-1", kind="authorization", label="a", detail="1"),
        ContextReference(ref_id="auth:PA-2", kind="authorization", label="a", detail="2"),
        ContextReference(ref_id="auth:PA-1", kind="authorization", label="a", detail="1 (dup)"),
    ]
    deduped = _dedupe_by_ref_id(refs)
    assert [r.ref_id for r in deduped] == ["auth:PA-1", "auth:PA-2"]
    assert deduped[0].detail == "1"  # first occurrence kept


def test_policy_context_budget_truncates_flagged_not_silent():
    from application.context_assembler import MAX_POLICY_CONTEXT_CHARS, assemble_context

    result = run_case_agent("CLM-1001", "Why was this claim denied?")
    package = result.evidence_package
    oversized_chunk = package.policy_chunks[0].model_copy(
        update={"text": "x" * (MAX_POLICY_CONTEXT_CHARS + 500)}
    )
    patched_package = package.model_copy(update={"policy_chunks": [oversized_chunk]})
    patched_result = result.model_copy(update={"evidence_package": patched_package})

    context = assemble_context(patched_result)

    assert context.context_truncated is True
    assert context.truncation_notes
    policy_ref = next(r for r in context.references if r.kind == "policy")
    assert len(policy_ref.detail) < MAX_POLICY_CONTEXT_CHARS + 500
    assert "truncated" in policy_ref.detail
