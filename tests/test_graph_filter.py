"""Tests for claim-scoped graph context filtering, including the explicit
cross-case contamination check required for this stage."""

from __future__ import annotations

from context.graph_filter import filter_claim_graph_context
from graph.retriever import get_claim_neighborhood

FORBIDDEN_CASE2_NODE_IDS = {
    "claim:CLM-1002",
    "member:M-1002",
    "authorization:PA-1501",
    "authorization:PA-2001",
}


def test_raw_neighborhood_does_contain_case2_entities_via_shared_hub():
    # Sanity check on the premise: the RAW (unfiltered) 2-hop neighborhood
    # really does reach Case 2's claim and both its authorization records
    # through the shared MRI-KNEE service hub -- otherwise the filtering
    # test below would be vacuous. (member:M-1002 itself is one hop
    # further out -- via an authorization's HAS_AUTHORIZATION edge -- so it
    # is not part of this particular sanity check, but is still asserted
    # absent, alongside everything else, in the filtered-context tests.)
    raw = get_claim_neighborhood("CLM-1001", max_hops=2)
    raw_node_ids = {node.node_id for node in raw.nodes}
    assert {"claim:CLM-1002", "authorization:PA-1501", "authorization:PA-2001"} <= raw_node_ids


def test_filtered_claim1_context_excludes_case2_entities():
    raw = get_claim_neighborhood("CLM-1001", max_hops=2)
    filtered = filter_claim_graph_context(raw)

    filtered_node_ids = {node.node_id for node in filtered.nodes}
    assert filtered_node_ids.isdisjoint(FORBIDDEN_CASE2_NODE_IDS)

    filtered_relationship_ids = set()
    for rel in filtered.relationships:
        filtered_relationship_ids.add(rel.source_id)
        filtered_relationship_ids.add(rel.target_id)
    assert filtered_relationship_ids.isdisjoint(FORBIDDEN_CASE2_NODE_IDS)


def test_filtered_claim1_context_retains_its_own_facts():
    raw = get_claim_neighborhood("CLM-1001", max_hops=2)
    filtered = filter_claim_graph_context(raw)

    relations = {(r.source_id, r.relation, r.target_id) for r in filtered.relationships}
    assert ("claim:CLM-1001", "BELONGS_TO", "member:M-1001") in relations
    assert ("claim:CLM-1001", "SERVICED_BY", "provider:PRV-1001") in relations
    assert ("claim:CLM-1001", "ORDERED_BY", "provider:PRV-4001") in relations
    assert ("member:M-1001", "ENROLLED_IN", "plan:PLAN-GOLD") in relations
    assert ("plan:PLAN-GOLD", "HAS_BENEFIT", "benefit:BEN-GOLD-MRI-KNEE") in relations
    assert ("plan:PLAN-GOLD", "USES_NETWORK", "network:meridian-preferred-network") in relations


def test_filtered_claim2_context_retains_both_authorizations():
    # The symmetric case: CLM-1002's OWN filtered context legitimately
    # includes its own member's authorization records -- this is correct
    # evidence, not contamination, when CLM-1002 is the one being
    # investigated.
    raw = get_claim_neighborhood("CLM-1002", max_hops=2)
    filtered = filter_claim_graph_context(raw)

    node_ids = {node.node_id for node in filtered.nodes}
    assert {"authorization:PA-1501", "authorization:PA-2001"} <= node_ids

    relations = {(r.source_id, r.relation, r.target_id) for r in filtered.relationships}
    assert ("member:M-1002", "HAS_AUTHORIZATION", "authorization:PA-1501") in relations
    assert ("member:M-1002", "HAS_AUTHORIZATION", "authorization:PA-2001") in relations

    # And CLM-1002's own filtered context must not contain Case 1's entities.
    assert "claim:CLM-1001" not in node_ids
    assert "member:M-1001" not in node_ids


def test_out_of_network_provider_has_no_network_edge_after_filtering():
    raw = get_claim_neighborhood("CLM-1004", max_hops=2)
    filtered = filter_claim_graph_context(raw)

    relations = [(r.source_id, r.relation, r.target_id) for r in filtered.relationships]
    assert ("claim:CLM-1004", "SERVICED_BY", "provider:PRV-2002") in relations
    assert not any(source == "provider:PRV-2002" and relation == "PARTICIPATES_IN" for source, relation, _ in relations)


def test_case5_filtered_context_has_no_invented_provider():
    raw = get_claim_neighborhood("CLM-1005", max_hops=2)
    filtered = filter_claim_graph_context(raw)

    node_ids = {node.node_id for node in filtered.nodes}
    assert "provider:PRV-9999" not in node_ids
    assert "provider:PRV-4004" in node_ids


def test_filtered_relationships_are_deterministically_ordered():
    raw = get_claim_neighborhood("CLM-1001", max_hops=2)
    first = filter_claim_graph_context(raw)
    second = filter_claim_graph_context(raw)

    first_keys = [(r.source_id, r.relation, r.target_id) for r in first.relationships]
    second_keys = [(r.source_id, r.relation, r.target_id) for r in second.relationships]
    assert first_keys == second_keys
    assert first_keys == sorted(first_keys)
