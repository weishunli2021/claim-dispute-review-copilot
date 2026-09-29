"""Tests for graph retrieval: bounded-depth neighborhoods around a claim or member."""

from __future__ import annotations

import pytest

from graph.retriever import (
    NodeNotFoundError,
    get_claim_neighborhood,
    get_member_neighborhood,
    get_provider_neighborhood,
)


def test_claim1_neighborhood_at_hops_1_has_core_facts():
    context = get_claim_neighborhood("CLM-1001", max_hops=1)
    node_ids = {n.node_id for n in context.nodes}
    assert {
        "claim:CLM-1001",
        "member:M-1001",
        "service:MRI-KNEE",
        "provider:PRV-1001",
        "provider:PRV-4001",
    } <= node_ids

    relations = {(r.source_id, r.relation, r.target_id) for r in context.relationships}
    assert ("claim:CLM-1001", "BELONGS_TO", "member:M-1001") in relations
    assert ("claim:CLM-1001", "SERVICED_BY", "provider:PRV-1001") in relations
    assert ("claim:CLM-1001", "ORDERED_BY", "provider:PRV-4001") in relations


def test_member2_neighborhood_shows_both_authorization_records():
    context = get_member_neighborhood("M-1002", max_hops=1)
    node_ids = {n.node_id for n in context.nodes}
    assert {"authorization:PA-1501", "authorization:PA-2001"} <= node_ids

    relations = {(r.source_id, r.relation, r.target_id) for r in context.relationships}
    assert ("member:M-1002", "HAS_AUTHORIZATION", "authorization:PA-1501") in relations
    assert ("member:M-1002", "HAS_AUTHORIZATION", "authorization:PA-2001") in relations


def test_out_of_network_provider_represented_in_claim4_neighborhood():
    context = get_claim_neighborhood("CLM-1004", max_hops=1)
    providers = {n.node_id: n for n in context.nodes if n.node_type == "Provider"}
    assert "provider:PRV-2002" in providers
    assert providers["provider:PRV-2002"].properties["network_status"] == "out-of-network"


def test_case5_neighborhood_has_no_invented_provider_node():
    context = get_claim_neighborhood("CLM-1005", max_hops=1)
    node_ids = {n.node_id for n in context.nodes}
    assert "provider:PRV-9999" not in node_ids
    assert "provider:PRV-4004" in node_ids  # the ordering provider does exist


def test_traversal_depth_increases_reachable_nodes():
    hop1 = get_member_neighborhood("M-1001", max_hops=1)
    hop2 = get_member_neighborhood("M-1001", max_hops=2)
    hop3 = get_member_neighborhood("M-1001", max_hops=3)

    assert len(hop1.nodes) <= len(hop2.nodes) <= len(hop3.nodes)
    hop1_ids = {n.node_id for n in hop1.nodes}
    hop2_ids = {n.node_id for n in hop2.nodes}
    assert hop1_ids <= hop2_ids  # every hop-1 node is still present at hop-2


def test_result_ordering_is_stable_across_repeated_calls():
    first = get_claim_neighborhood("CLM-1001", max_hops=2)
    second = get_claim_neighborhood("CLM-1001", max_hops=2)
    assert [n.node_id for n in first.nodes] == [n.node_id for n in second.nodes]
    assert [(r.source_id, r.relation, r.target_id) for r in first.relationships] == [
        (r.source_id, r.relation, r.target_id) for r in second.relationships
    ]


def test_unknown_claim_raises_node_not_found():
    with pytest.raises(NodeNotFoundError):
        get_claim_neighborhood("CLM-9999")


def test_unknown_member_raises_node_not_found():
    with pytest.raises(NodeNotFoundError):
        get_member_neighborhood("M-9999")


def test_negative_max_hops_raises_value_error():
    with pytest.raises(ValueError):
        get_claim_neighborhood("CLM-1001", max_hops=-1)


# ---- get_provider_neighborhood (Module 6A) --------------------------------------


def test_provider_neighborhood_includes_its_own_network_participation():
    context = get_provider_neighborhood("PRV-1001", max_hops=1)
    node_ids = {n.node_id for n in context.nodes}
    assert "provider:PRV-1001" in node_ids
    relations = {(r.source_id, r.relation, r.target_id) for r in context.relationships}
    assert ("provider:PRV-1001", "PARTICIPATES_IN", "network:meridian-preferred-network") in relations


def test_provider_neighborhood_at_hop_1_includes_referencing_claims():
    # Unfiltered raw neighborhood -- includes reverse edges from claims
    # that reference this provider (e.g. CLM-1001's SERVICED_BY edge).
    # Callers that want ONLY the provider's own forward relationships must
    # filter to source_id == the provider node themselves (see
    # context/dispute_evidence_retriever.py's gather_graph_evidence).
    context = get_provider_neighborhood("PRV-1001", max_hops=1)
    node_ids = {n.node_id for n in context.nodes}
    assert "claim:CLM-1001" in node_ids


def test_unknown_provider_raises_node_not_found():
    with pytest.raises(NodeNotFoundError):
        get_provider_neighborhood("PRV-9999")
