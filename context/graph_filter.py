"""Filters a raw claim graph neighborhood down to claim-relevant relationships.

Why this cannot be "keep any edge whose relation type is on an allowed
list": a raw N-hop neighborhood (graph.retriever.get_claim_neighborhood)
is computed over an UNDIRECTED view of the graph, so shared hub nodes --
most notably a Service node referenced by many claims, members, and
authorizations -- pull in everything else that touches that hub. For
example, CLM-1001 and CLM-1002 both reference service:MRI-KNEE, so
CLM-1001's raw 2-hop neighborhood also contains CLM-1002, its member
M-1002, and M-1002's prior-authorization records (PA-1501, PA-2001) --
none of which have anything to do with investigating CLM-1001. A naive
filter that keeps every edge whose relation is e.g. "FOR_SERVICE" would
keep CLM-1002's FOR_SERVICE edge to that same service too, since the
relation type alone can't distinguish "this claim's own service edge"
from "some other claim's service edge to the same shared node."

The fix used here: walk outward from the claim root through a fixed,
explicit set of relationship patterns, where each step's allowed sources
are exactly the specific node ids already validated by the previous step
-- never "any node of the right type found anywhere in the raw
neighborhood." Service is always a terminal/leaf in this walk (we record
edges INTO it, but never treat it as a hub to expand further FROM), which
is what actually prevents hopping from this claim's service to a
different claim's or member's records that merely happen to reference the
same service.

Retained patterns (see graph/builder.py for the relation-type constants):

    Claim   --BELONGS_TO-->        Member
    Member  --ENROLLED_IN-->       Plan
    Plan    --HAS_BENEFIT-->       Benefit   (only the benefit FOR the claim's own service)
    Benefit --FOR_SERVICE-->       Service   (only that same service)
    Claim   --FOR_SERVICE-->       Service   (the claim's own service, directly)
    Claim   --SERVICED_BY-->       Provider
    Claim   --ORDERED_BY-->        Provider
    Provider--PARTICIPATES_IN-->   Network   (for each provider the claim itself references)
    Plan    --USES_NETWORK-->      Network
    Member  --HAS_AUTHORIZATION--> PriorAuthorization  (only authorizations FOR the claim's own service)
    PriorAuthorization--FOR_SERVICE--> Service          (only that same service)

All relationship provenance (source_id, relation, target_id, properties)
is preserved unchanged for every retained edge -- this module only
decides what to keep, never rewrites what a kept edge says.
"""

from __future__ import annotations

from graph.builder import (
    BELONGS_TO,
    ENROLLED_IN,
    FOR_SERVICE,
    HAS_AUTHORIZATION,
    HAS_BENEFIT,
    ORDERED_BY,
    PARTICIPATES_IN,
    SERVICED_BY,
    USES_NETWORK,
)
from graph.models import GraphContext, GraphNode, GraphRelationship


def filter_claim_graph_context(context: GraphContext) -> GraphContext:
    """Return a new GraphContext containing only claim-relevant relationships.

    `context` is expected to be rooted at a Claim node (as returned by
    graph.retriever.get_claim_neighborhood) -- context.root_id is used as
    the starting point for the walk described in the module docstring.
    """
    claim_id = context.root_id
    nodes_by_id = {node.node_id: node for node in context.nodes}

    def edges_from(source_id: str, relation: str) -> list[GraphRelationship]:
        return [
            rel
            for rel in context.relationships
            if rel.source_id == source_id and rel.relation == relation
        ]

    kept_edges: dict[tuple[str, str, str], GraphRelationship] = {}
    kept_node_ids: set[str] = {claim_id}

    def keep(edges: list[GraphRelationship]) -> None:
        for edge in edges:
            kept_edges[(edge.source_id, edge.relation, edge.target_id)] = edge
            kept_node_ids.add(edge.source_id)
            kept_node_ids.add(edge.target_id)

    # Claim -> Member
    member_edges = edges_from(claim_id, BELONGS_TO)
    keep(member_edges)
    member_ids = {edge.target_id for edge in member_edges}

    # Claim -> Service (the claim's own service -- everything else is scoped to it)
    claim_service_edges = edges_from(claim_id, FOR_SERVICE)
    keep(claim_service_edges)
    claim_service_ids = {edge.target_id for edge in claim_service_edges}

    # Claim -> Provider (servicing and ordering)
    provider_ids: set[str] = set()
    for relation in (SERVICED_BY, ORDERED_BY):
        provider_edges = edges_from(claim_id, relation)
        keep(provider_edges)
        provider_ids |= {edge.target_id for edge in provider_edges}

    # Provider -> Network, only for providers this claim itself references
    for provider_id in provider_ids:
        keep(edges_from(provider_id, PARTICIPATES_IN))

    # Member -> Plan, Member -> PriorAuthorization, only for this claim's own member(s)
    plan_ids: set[str] = set()
    for member_id in member_ids:
        plan_edges = edges_from(member_id, ENROLLED_IN)
        keep(plan_edges)
        plan_ids |= {edge.target_id for edge in plan_edges}

        for auth_edge in edges_from(member_id, HAS_AUTHORIZATION):
            auth_id = auth_edge.target_id
            auth_service_edges = edges_from(auth_id, FOR_SERVICE)
            # Only keep this authorization if it is for the claim's own
            # service -- mirrors tools.prior_auth_tool.get_prior_authorizations'
            # own (member_id, service_code) scoping, so graph evidence never
            # shows an authorization structured_facts wouldn't also show.
            if any(edge.target_id in claim_service_ids for edge in auth_service_edges):
                keep([auth_edge])
                keep(auth_service_edges)

    # Plan -> Benefit -> Service, only for this claim's own service; Plan -> Network always
    for plan_id in plan_ids:
        for benefit_edge in edges_from(plan_id, HAS_BENEFIT):
            benefit_id = benefit_edge.target_id
            benefit_service_edges = edges_from(benefit_id, FOR_SERVICE)
            if any(edge.target_id in claim_service_ids for edge in benefit_service_edges):
                keep([benefit_edge])
                keep(benefit_service_edges)

        keep(edges_from(plan_id, USES_NETWORK))

    filtered_nodes = [
        nodes_by_id[node_id] for node_id in sorted(kept_node_ids) if node_id in nodes_by_id
    ]
    filtered_relationships = sorted(
        kept_edges.values(), key=lambda edge: (edge.source_id, edge.relation, edge.target_id)
    )

    return GraphContext(
        root_id=context.root_id,
        max_hops=context.max_hops,
        nodes=filtered_nodes,
        relationships=filtered_relationships,
    )
