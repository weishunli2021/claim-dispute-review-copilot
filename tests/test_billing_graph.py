"""Tests for dispute_review.billing_graph -- the isolated, separately-
seeded provider-network graph for the billing-correction scenario. Never
touches tools.data_store or the shared golden-dataset graph
(graph.retriever._get_graph())."""

from __future__ import annotations

import pytest

from dispute_review.billing_graph import (
    BillingNetworkLoadError,
    get_billing_network_fixture,
    get_billing_provider_network_neighborhood,
    is_provider_in_plan_network,
)
from graph.retriever import NodeNotFoundError


def test_fixture_loads_expected_providers():
    fixture = get_billing_network_fixture()
    ids = {p.provider_id for p in fixture.providers}
    assert {"PRV-BILL-ACTUAL", "PRV-BILL-WRONG"}.issubset(ids)
    assert fixture.plan_network_name


def test_actual_provider_is_in_plan_network():
    in_network, neighborhood = is_provider_in_plan_network("PRV-BILL-ACTUAL")
    assert in_network is True
    assert neighborhood is not None


def test_wrong_provider_is_known_but_out_of_network():
    in_network, neighborhood = is_provider_in_plan_network("PRV-BILL-WRONG")
    assert in_network is False  # known to the graph, just not in THIS plan's network
    assert neighborhood is not None


def test_unknown_provider_is_none_not_false():
    """None (unknown to this graph) must never be confused with False
    (known and out-of-network) -- these are different facts."""
    in_network, neighborhood = is_provider_in_plan_network("PRV-DOES-NOT-EXIST")
    assert in_network is None
    assert neighborhood is None


def test_unknown_provider_raises_node_not_found_error_directly():
    with pytest.raises(NodeNotFoundError):
        get_billing_provider_network_neighborhood("PRV-DOES-NOT-EXIST")


def test_never_reads_the_shared_golden_dataset_graph():
    from graph.retriever import get_provider_neighborhood

    # PRV-BILL-ACTUAL is fictional and specific to this isolated scenario --
    # it must not resolve against the shared golden-dataset graph at all.
    with pytest.raises(NodeNotFoundError):
        get_provider_neighborhood("PRV-BILL-ACTUAL")


def test_missing_fixture_raises_billing_network_load_error(tmp_path):
    with pytest.raises(BillingNetworkLoadError):
        get_billing_network_fixture(tmp_path / "does_not_exist.json")
