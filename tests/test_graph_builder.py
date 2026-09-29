"""Tests for the knowledge graph builder."""

from __future__ import annotations

import json

import pytest

from graph.builder import (
    GraphBuildError,
    authorization_node_id,
    build_graph,
    claim_node_id,
    member_node_id,
    network_node_id,
    plan_node_id,
    provider_node_id,
    service_node_id,
)
from tools.data_store import DataStore


def test_graph_builds_successfully():
    graph = build_graph()
    assert graph.number_of_nodes() > 0
    assert graph.number_of_edges() > 0


def test_deterministic_node_ids():
    assert member_node_id("M-1001") == "member:M-1001"
    assert plan_node_id("PLAN-GOLD") == "plan:PLAN-GOLD"
    assert claim_node_id("CLM-1001") == "claim:CLM-1001"
    assert service_node_id("MRI-KNEE") == "service:MRI-KNEE"
    assert provider_node_id("PRV-1001") == "provider:PRV-1001"
    assert authorization_node_id("PA-2001") == "authorization:PA-2001"
    assert network_node_id("Meridian Preferred Network") == "network:meridian-preferred-network"


def test_no_duplicate_nodes_across_repeated_builds():
    graph1 = build_graph()
    graph2 = build_graph()
    assert sorted(graph1.nodes) == sorted(graph2.nodes)
    assert graph1.number_of_nodes() == graph2.number_of_nodes()
    assert graph1.number_of_edges() == graph2.number_of_edges()


def test_expected_node_types_present():
    graph = build_graph()
    node_types = {data["node_type"] for _, data in graph.nodes(data=True)}
    assert node_types == {
        "Member",
        "Plan",
        "Claim",
        "Benefit",
        "Service",
        "Provider",
        "Network",
        "PriorAuthorization",
    }


def test_expected_edge_types_present():
    graph = build_graph()
    relations = {data["relation"] for _, _, data in graph.edges(data=True)}
    assert relations == {
        "ENROLLED_IN",
        "HAS_BENEFIT",
        "FOR_SERVICE",
        "BELONGS_TO",
        "SERVICED_BY",
        "ORDERED_BY",
        "PARTICIPATES_IN",
        "USES_NETWORK",
        "HAS_AUTHORIZATION",
    }


def test_expected_node_and_edge_counts():
    # 5 members, 3 plans, 5 claims, 3 benefits, 7 providers, 3 networks,
    # 4 distinct service codes, 2 prior authorizations = 32 nodes.
    graph = build_graph()
    assert graph.number_of_nodes() == 32
    assert graph.number_of_edges() == 43


def test_out_of_network_provider_has_no_network_edge():
    graph = build_graph()
    sunrise = provider_node_id("PRV-2002")
    assert graph.nodes[sunrise]["network_status"] == "out-of-network"
    out_relations = [data["relation"] for _, _, data in graph.out_edges(sunrise, data=True)]
    assert "PARTICIPATES_IN" not in out_relations


def test_case5_incomplete_data_does_not_invent_a_provider_node():
    graph = build_graph()
    assert provider_node_id("PRV-9999") not in graph  # dangling reference, never materialized

    claim5 = claim_node_id("CLM-1005")
    servicing_targets = [
        target
        for _, target, data in graph.out_edges(claim5, data=True)
        if data["relation"] == "SERVICED_BY"
    ]
    assert servicing_targets == []  # no edge at all -- not invented, just absent

    ordering_targets = [
        target
        for _, target, data in graph.out_edges(claim5, data=True)
        if data["relation"] == "ORDERED_BY"
    ]
    assert ordering_targets == [provider_node_id("PRV-4004")]  # this one does exist


def test_build_graph_raises_on_structurally_impossible_member_reference(tmp_path):
    (tmp_path / "members.json").write_text("[]", encoding="utf-8")
    (tmp_path / "providers.json").write_text("[]", encoding="utf-8")
    (tmp_path / "benefits.json").write_text("[]", encoding="utf-8")
    (tmp_path / "prior_authorizations.json").write_text("[]", encoding="utf-8")
    (tmp_path / "plans.json").write_text(
        json.dumps(
            [{"plan_id": "PLAN-X", "plan_name": "X", "plan_type": "PPO", "network_name": "Net X"}]
        ),
        encoding="utf-8",
    )
    (tmp_path / "claims.json").write_text(
        json.dumps(
            [
                {
                    "claim_id": "CLM-X",
                    "member_id": "M-DOES-NOT-EXIST",
                    "plan_id": "PLAN-X",
                    "service_code": "X",
                    "provider_id": "PRV-X",
                    "date_of_service": "2026-01-01",
                    "status": "DENIED",
                    "billed_amount": 100.0,
                }
            ]
        ),
        encoding="utf-8",
    )

    store = DataStore(data_dir=tmp_path)
    with pytest.raises(GraphBuildError, match="unknown member_id"):
        build_graph(store)


def test_build_graph_raises_on_structurally_impossible_authorization_member_reference(tmp_path):
    (tmp_path / "members.json").write_text("[]", encoding="utf-8")
    (tmp_path / "providers.json").write_text("[]", encoding="utf-8")
    (tmp_path / "benefits.json").write_text("[]", encoding="utf-8")
    (tmp_path / "claims.json").write_text("[]", encoding="utf-8")
    (tmp_path / "plans.json").write_text(
        json.dumps(
            [{"plan_id": "PLAN-X", "plan_name": "X", "plan_type": "PPO", "network_name": "Net X"}]
        ),
        encoding="utf-8",
    )
    (tmp_path / "prior_authorizations.json").write_text(
        json.dumps(
            [
                {
                    "authorization_id": "PA-X",
                    "member_id": "M-DOES-NOT-EXIST",
                    "service_code": "X",
                    "plan_id": "PLAN-X",
                    "status": "APPROVED",
                    "effective_date": "2026-01-01",
                    "expiration_date": "2026-06-30",
                }
            ]
        ),
        encoding="utf-8",
    )

    store = DataStore(data_dir=tmp_path)
    with pytest.raises(GraphBuildError, match="unknown member_id"):
        build_graph(store)
