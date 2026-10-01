"""A tiny, ISOLATED knowledge graph for the billing-correction scenario's
provider-network evidence -- built from its own separately-seeded fixture
(data/billing_correction_network.json), never from tools.data_store or the
shared golden-dataset graph (graph.retriever._get_graph()).

Why a second, separate graph rather than adding a node to the existing
one: graph.builder.build_graph() derives the shared graph entirely from
tools.data_store.get_data_store() (data/providers.json, data/plans.json,
...), which the Golden Dataset & Evaluation tab also depends on. Adding
PRV-BILL-SYNTHETICCHOICEPPO500 there would mean editing a SHARED fixture the golden
dataset's own tests assume an exact shape for -- exactly the kind of
cross-contamination dispute_review/billing_fixtures.py was built to avoid
for the claim/decision/support-record data. This module applies the same
isolation discipline to graph evidence.

Reuses graph.builder's node-type/relation constants and node-id helpers
(PROVIDER, NETWORK, PARTICIPATES_IN, provider_node_id, network_node_id)
and graph.retriever's own bounded-neighborhood traversal (_neighborhood)
for consistency and to avoid re-implementing graph traversal -- but builds
and queries a completely separate nx.MultiDiGraph instance, never the
shared singleton.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import networkx as nx
from pydantic import BaseModel, ConfigDict, ValidationError

from graph.builder import NETWORK, PARTICIPATES_IN, PROVIDER, network_node_id, provider_node_id
from graph.models import GraphContext
from graph.retriever import NodeNotFoundError, _neighborhood

DEFAULT_BILLING_NETWORK_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "billing_correction_network.json"
)


class BillingNetworkLoadError(RuntimeError):
    """Raised when the isolated billing-network fixture is missing or malformed."""


class BillingNetworkProvider(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str
    name: str
    network_name: str


class BillingNetworkPlan(BaseModel):
    """One plan this scenario can check network participation against.
    `plan_network_name` is the ONLY network a provider must have a
    PARTICIPATES_IN edge to in order to count as in-network for this plan
    -- a provider can participate in a different network and still be
    correctly reported out-of-network for a given plan (see
    is_provider_in_plan_network)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str
    plan_name: str
    plan_network_name: str


# The claim's own actually-recorded plan (data/billing_correction_claim.json's
# plan_id) -- used whenever a caller doesn't explicitly pick a different plan
# to check network participation against (see dispute_review/ui.py's optional
# "Network plan" control, a demo-only lever that never changes the
# claim's own recorded plan_id/plan_name shown in Section 1).
DEFAULT_PLAN_ID = "PLN-BILL-01"


class BillingNetworkFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plans: list[BillingNetworkPlan]
    providers: list[BillingNetworkProvider]


@lru_cache(maxsize=4)
def _load_fixture(path_str: str) -> BillingNetworkFixture:
    path = Path(path_str)
    if not path.is_file():
        raise BillingNetworkLoadError(f"Missing billing-scenario network fixture: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BillingNetworkLoadError(f"{path.name} is not valid JSON: {exc}") from exc
    try:
        return BillingNetworkFixture.model_validate(raw)
    except ValidationError as exc:
        raise BillingNetworkLoadError(f"{path.name} failed schema validation: {exc}") from exc


def get_billing_network_fixture(path: Path = DEFAULT_BILLING_NETWORK_PATH) -> BillingNetworkFixture:
    """Load (and cache) the isolated provider-network fixture."""
    return _load_fixture(str(path))


def get_billing_network_plan(fixture: BillingNetworkFixture, plan_id: str) -> BillingNetworkPlan:
    """Look up one plan by id. Raises BillingNetworkLoadError for an
    unknown plan_id -- never silently falls back to the default plan."""
    for plan in fixture.plans:
        if plan.plan_id == plan_id:
            return plan
    raise BillingNetworkLoadError(f"Unknown plan_id {plan_id!r} in billing network fixture.")


def _build_graph(fixture: BillingNetworkFixture) -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    for provider in fixture.providers:
        graph.add_node(
            provider_node_id(provider.provider_id),
            node_type=PROVIDER,
            provider_id=provider.provider_id,
            name=provider.name,
            network_name=provider.network_name,
        )
        net_id = network_node_id(provider.network_name)
        if net_id not in graph:
            graph.add_node(net_id, node_type=NETWORK, name=provider.network_name)
        graph.add_edge(
            provider_node_id(provider.provider_id), net_id, key=PARTICIPATES_IN, relation=PARTICIPATES_IN
        )
    return graph


@lru_cache(maxsize=4)
def _get_billing_graph(path_str: str) -> nx.MultiDiGraph:
    return _build_graph(_load_fixture(path_str))


def get_billing_provider_network_neighborhood(
    provider_id: str, *, max_hops: int = 1, path: Path = DEFAULT_BILLING_NETWORK_PATH
) -> GraphContext:
    """Return the bounded neighborhood around one provider in the isolated
    billing-network graph. Raises NodeNotFoundError (the same exception
    type graph.retriever's own functions raise, for a consistent caller
    contract) if this provider is not known to this graph -- never
    silently falls back to the shared golden-dataset graph.
    """
    graph = _get_billing_graph(str(path))
    return _neighborhood(graph, provider_node_id(provider_id), max_hops)


def is_provider_in_plan_network(
    provider_id: str, *, plan_id: str = DEFAULT_PLAN_ID, path: Path = DEFAULT_BILLING_NETWORK_PATH
) -> tuple[bool, GraphContext] | tuple[None, None]:
    """Return (in_network, neighborhood) for `provider_id` against
    `plan_id`'s required network (the claim's own actually-recorded plan
    by default), or (None, None) if the provider is not known to this
    isolated graph at all (never confused with "known and out of network"
    -- that is `(False, neighborhood)`). Raises BillingNetworkLoadError
    for an unknown plan_id.
    """
    fixture = get_billing_network_fixture(path)
    plan = get_billing_network_plan(fixture, plan_id)
    try:
        neighborhood = get_billing_provider_network_neighborhood(provider_id, path=path)
    except NodeNotFoundError:
        return None, None
    target = network_node_id(plan.plan_network_name)
    root = provider_node_id(provider_id)
    in_network = any(
        rel.source_id == root and rel.relation == PARTICIPATES_IN and rel.target_id == target
        for rel in neighborhood.relationships
    )
    return in_network, neighborhood
