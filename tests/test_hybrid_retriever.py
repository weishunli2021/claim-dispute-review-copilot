"""Tests for the hybrid GraphRAG evidence-assembly layer (build_evidence_package)."""

from __future__ import annotations

from context.hybrid_retriever import build_evidence_package

FORBIDDEN_CASE2_NODE_IDS = {
    "claim:CLM-1002",
    "member:M-1002",
    "authorization:PA-1501",
    "authorization:PA-2001",
}


def test_unknown_claim_returns_empty_package_without_raising():
    # Regression test: build_evidence_package used to call
    # graph.retriever.get_claim_neighborhood unconditionally, which raised
    # NodeNotFoundError for a claim_id with no graph node -- discovered via
    # skills/investigate_claim.py's golden-set test for an unknown claim,
    # since that skill calls this function directly without a prior
    # existence check (unlike agents/nodes.py's load_case, which always
    # checks first and never exercised this path).
    package = build_evidence_package("CLM-9999", "anything")
    assert package.structured_facts.claim is None
    assert package.graph_relationships == []
    assert package.missing_evidence
    assert package.missing_evidence[0].category == "claim"


def test_case1_evidence_assembly():
    package = build_evidence_package("CLM-1001", "My claim was denied. What's wrong with it?")

    assert package.claim_id == "CLM-1001"
    assert package.original_query == "My claim was denied. What's wrong with it?"
    assert "Service: MRI-KNEE." in package.enriched_query
    assert "Denial reason: AUTH_REQUIRED." in package.enriched_query

    assert package.structured_facts.claim is not None
    assert package.structured_facts.claim.status == "DENIED"
    assert package.structured_facts.claim.denial_reason_code == "AUTH_REQUIRED"

    assert package.policy_chunks  # non-empty
    assert package.graph_relationships  # non-empty

    categories = {item.category for item in package.missing_evidence}
    assert categories == {"prior_authorization"}


def test_case2_authorization_evidence():
    package = build_evidence_package("CLM-1002", "Was this claim paid correctly?")

    assert package.structured_facts.claim.status == "PAID"
    assert len(package.structured_facts.prior_authorizations) == 2
    auth_ids = {a.authorization_id for a in package.structured_facts.prior_authorizations}
    assert auth_ids == {"PA-1501", "PA-2001"}

    # Nothing about this claim should be reported missing.
    assert package.missing_evidence == []


def test_case3_coverage_evidence():
    package = build_evidence_package("CLM-1003", "Why was my lab claim denied?")

    assert package.structured_facts.claim.denial_reason_code == "SERVICE_NOT_COVERED"
    assert package.structured_facts.benefit is not None
    assert package.structured_facts.benefit.covered is False

    section_ids = {chunk.section_id for chunk in package.policy_chunks}
    assert section_ids & {"BEN-3", "CLM-3"}


def test_case4_network_evidence():
    package = build_evidence_package("CLM-1004", "Why was my physical therapy claim denied?")

    assert package.structured_facts.claim.denial_reason_code == "OUT_OF_NETWORK_PROVIDER"
    assert package.structured_facts.servicing_provider is not None
    assert package.structured_facts.servicing_provider.network_status == "out-of-network"

    relations = {(r.source_id, r.relation, r.target_id) for r in package.graph_relationships}
    assert ("claim:CLM-1004", "SERVICED_BY", "provider:PRV-2002") in relations
    assert not any(
        source == "provider:PRV-2002" and relation == "PARTICIPATES_IN"
        for source, relation, _ in relations
    )


def test_case5_missing_information():
    package = build_evidence_package("CLM-1005", "Why was my specialist visit claim denied?")

    assert package.structured_facts.claim.denial_reason_code is None
    assert package.structured_facts.benefit is None
    assert package.structured_facts.servicing_provider is None
    assert package.structured_facts.ordering_provider is not None

    categories = {item.category for item in package.missing_evidence}
    assert categories == {"benefit", "servicing_provider", "prior_authorization"}

    # No missing-evidence description may read as a business conclusion.
    for item in package.missing_evidence:
        lowered = item.description.lower()
        assert "denied" not in lowered
        assert "not covered" not in lowered

    node_ids = {rel.source_id for rel in package.graph_relationships} | {
        rel.target_id for rel in package.graph_relationships
    }
    assert "provider:PRV-9999" not in node_ids


def test_no_case1_case2_contamination():
    package = build_evidence_package("CLM-1001", "My claim was denied. What's wrong with it?")

    node_ids = {rel.source_id for rel in package.graph_relationships} | {
        rel.target_id for rel in package.graph_relationships
    }
    assert node_ids.isdisjoint(FORBIDDEN_CASE2_NODE_IDS)


def test_deterministic_ordering_across_repeated_calls():
    first = build_evidence_package("CLM-1001", "My claim was denied. What's wrong with it?")
    second = build_evidence_package("CLM-1001", "My claim was denied. What's wrong with it?")

    first_rel_keys = [(r.source_id, r.relation, r.target_id) for r in first.graph_relationships]
    second_rel_keys = [(r.source_id, r.relation, r.target_id) for r in second.graph_relationships]
    assert first_rel_keys == second_rel_keys

    first_chunk_ids = [c.chunk_id for c in first.policy_chunks]
    second_chunk_ids = [c.chunk_id for c in second.policy_chunks]
    assert first_chunk_ids == second_chunk_ids


def test_provenance_preservation():
    package = build_evidence_package("CLM-1001", "My claim was denied. What's wrong with it?", top_k_policy=2, graph_max_hops=2)

    assert package.provenance.policy_provider == "semantic"
    assert package.provenance.policy_chunking_config == "LARGE"
    assert package.provenance.policy_top_k == 2
    assert package.provenance.graph_max_hops == 2

    # Every policy chunk and graph relationship carries its own source ids.
    for chunk in package.policy_chunks:
        assert chunk.chunk_id
        assert chunk.document_id
        assert chunk.section_id
    for rel in package.graph_relationships:
        assert rel.source_id
        assert rel.target_id
