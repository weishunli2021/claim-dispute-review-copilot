"""Deterministic graph retrieval: bounded-depth neighborhoods around a node.

Evidence only, like rag/retriever.py: these functions return the nodes and
relationships found within a bounded traversal depth, with stable
ordering, and never add natural-language explanation or business
conclusions (there is no "why was this denied" logic here). No LLM usage
anywhere in this module.

Traversal is computed over an UNDIRECTED view of the graph so that a
node's "neighborhood" includes both what it points to and what points at
it -- e.g. a Member's neighborhood naturally includes their Claims (which
point at the Member via BELONGS_TO), not just what the Member node itself
points to (their Plan, their PriorAuthorizations). The stored `relation`
on each edge always keeps its original directed meaning; only the
traversal that decides which nodes are "in range" is undirected.
"""

from __future__ import annotations

import argparse
import sys
from functools import lru_cache
from typing import Optional

import networkx as nx

from graph.builder import build_graph, claim_node_id, member_node_id, provider_node_id
from graph.models import GraphContext, GraphNode, GraphRelationship


class NodeNotFoundError(RuntimeError):
    """Raised when a requested root node does not exist in the graph."""


@lru_cache(maxsize=1)
def _get_graph() -> nx.MultiDiGraph:
    return build_graph()


def _neighborhood(graph: nx.MultiDiGraph, root_id: str, max_hops: int) -> GraphContext:
    if max_hops < 0:
        raise ValueError("max_hops must be >= 0")
    if root_id not in graph:
        raise NodeNotFoundError(f"No such node in the graph: {root_id!r}")

    undirected = graph.to_undirected(as_view=True)
    reachable = nx.single_source_shortest_path_length(undirected, root_id, cutoff=max_hops)
    node_ids = sorted(reachable.keys())  # stable ordering, independent of traversal order

    nodes = [
        GraphNode(
            node_id=node_id,
            node_type=graph.nodes[node_id].get("node_type", "Unknown"),
            properties={k: v for k, v in graph.nodes[node_id].items() if k != "node_type"},
        )
        for node_id in node_ids
    ]

    node_id_set = set(node_ids)
    relationships: list[GraphRelationship] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for source, target, key, data in graph.edges(keys=True, data=True):
        if source not in node_id_set or target not in node_id_set:
            continue
        edge_signature = (source, target, key)
        if edge_signature in seen_edges:
            continue
        seen_edges.add(edge_signature)
        relationships.append(
            GraphRelationship(
                source_id=source,
                relation=data.get("relation", key),
                target_id=target,
                properties={k: v for k, v in data.items() if k != "relation"},
            )
        )

    relationships.sort(key=lambda r: (r.source_id, r.relation, r.target_id))

    return GraphContext(root_id=root_id, max_hops=max_hops, nodes=nodes, relationships=relationships)


def get_claim_neighborhood(claim_id: str, max_hops: int = 2) -> GraphContext:
    """Return the graph neighborhood around one claim, out to max_hops.

    Evidence only: nodes and relationships exactly as recorded in the
    graph. Does not explain or infer why the claim was approved or denied.

    Raises NodeNotFoundError if claim_id has no corresponding Claim node.
    """
    return _neighborhood(_get_graph(), claim_node_id(claim_id), max_hops)


def get_member_neighborhood(member_id: str, max_hops: int = 2) -> GraphContext:
    """Return the graph neighborhood around one member, out to max_hops.

    Evidence only -- see get_claim_neighborhood.

    Raises NodeNotFoundError if member_id has no corresponding Member node.
    """
    return _neighborhood(_get_graph(), member_node_id(member_id), max_hops)


def get_provider_neighborhood(provider_id: str, max_hops: int = 2) -> GraphContext:
    """Return the graph neighborhood around one provider, out to max_hops.

    Evidence only -- see get_claim_neighborhood. Added for the dispute-
    evidence graph tool (context/dispute_evidence_retriever.py), which
    needs to check whether a SUBMITTED servicing-provider id resolves to a
    node in the graph at all, and if so, what that provider's own recorded
    relationships are (e.g. network participation) -- independent of which
    claim(s), if any, happen to reference it.

    Like get_claim_neighborhood/get_member_neighborhood, this function only
    bounds the traversal depth; it applies no claim-relevance filtering of
    its own. A Provider node can be a shared hub referenced by several
    claims (via SERVICED_BY/ORDERED_BY), so a caller that wants ONLY this
    provider's own forward relationships -- never an unrelated claim that
    merely happens to reference it -- must filter the returned
    relationships to source_id == provider_node_id(provider_id) itself;
    context/graph_filter.py's filter_claim_graph_context is specific to a
    CLAIM root and does not apply here.

    Raises NodeNotFoundError if provider_id has no corresponding Provider node.
    """
    return _neighborhood(_get_graph(), provider_node_id(provider_id), max_hops)


def _main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Debug utility: print a bounded-depth graph neighborhood around a claim "
            "or member. Evidence only -- no explanation is generated."
        ),
    )
    parser.add_argument("root_type", choices=["claim", "member"])
    parser.add_argument("root_id", help="e.g. CLM-1001 or M-1002")
    parser.add_argument("--max-hops", type=int, default=2)
    args = parser.parse_args(argv)

    if args.root_type == "claim":
        context = get_claim_neighborhood(args.root_id, max_hops=args.max_hops)
    else:
        context = get_member_neighborhood(args.root_id, max_hops=args.max_hops)

    print(f"ROOT: {context.root_id} (max_hops={context.max_hops})")
    print()
    print(f"NODES ({len(context.nodes)}):")
    for node in context.nodes:
        print(f"  [{node.node_type}] {node.node_id}")
        for key, value in node.properties.items():
            if value is not None:
                print(f"      {key}: {value}")
    print()
    print(f"RELATIONSHIPS ({len(context.relationships)}):")
    for rel in context.relationships:
        print(f"  {rel.source_id} --{rel.relation}--> {rel.target_id}")
        for key, value in rel.properties.items():
            if value is not None:
                print(f"      {key}: {value}")


if __name__ == "__main__":
    _main(sys.argv[1:])
