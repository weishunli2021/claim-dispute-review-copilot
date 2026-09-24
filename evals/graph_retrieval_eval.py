"""Evaluate graph retrieval quality against evals/graph_golden_set.json.

Two metrics, computed against a *fixed* ground-truth set of expected nodes
and relationships per query, at each of three traversal depths
(max_hops = 1, 2, 3):

- Node Recall: fraction of a query's expected_nodes actually present among
  the nodes returned at this max_hops.
- Relationship Recall: fraction of a query's expected_relationships
  actually present among the relationships returned at this max_hops.

Both necessarily improve (or stay flat) as max_hops grows -- a query whose
expected node is 3 hops from the root cannot be found at max_hops=1. The
point of comparing all three depths side by side is to make that tradeoff
concrete: higher max_hops recovers more evidence (higher recall) but also
pulls in more nodes and relationships that are NOT relevant to the
specific query (more noise, larger context) -- see the
avg_nodes_returned/avg_relationships_returned figures in the report, which
grow much faster than recall does once a query's expected items are
already within reach.

Run with:

    python -m evals.graph_retrieval_eval
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from graph.retriever import get_claim_neighborhood, get_member_neighborhood

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "graph_golden_set.json"
HOP_VALUES = (1, 2, 3)

_ROOT_FUNCTIONS = {
    "claim": get_claim_neighborhood,
    "member": get_member_neighborhood,
}


@dataclass
class QueryResult:
    query_id: str
    root_type: str
    root_id: str
    max_hops: int
    node_recall: float
    relationship_recall: float
    missing_nodes: list[str]
    missing_relationships: list[tuple[str, str, str]]
    returned_node_count: int
    returned_relationship_count: int


def load_golden_set(path: Path | None = None) -> list[dict]:
    """Load the graph golden-set queries: query_id, root_type ("claim" or
    "member"), root_id, expected_nodes, expected_relationships."""
    return json.loads((path or GOLDEN_SET_PATH).read_text(encoding="utf-8"))


def _relationship_tuple(rel: dict) -> tuple[str, str, str]:
    return (rel["source"], rel["relation"], rel["target"])


def evaluate_at_hops(max_hops: int, golden_set: list[dict] | None = None) -> list[QueryResult]:
    """Run every golden-set query's retrieval at a fixed max_hops and score
    Node Recall and Relationship Recall against its expected ground truth."""
    golden_set = golden_set if golden_set is not None else load_golden_set()

    results: list[QueryResult] = []
    for item in golden_set:
        root_function = _ROOT_FUNCTIONS[item["root_type"]]
        context = root_function(item["root_id"], max_hops=max_hops)

        found_node_ids = {node.node_id for node in context.nodes}
        found_relationships = {
            (rel.source_id, rel.relation, rel.target_id) for rel in context.relationships
        }

        expected_nodes = item["expected_nodes"]
        expected_relationships = [_relationship_tuple(r) for r in item["expected_relationships"]]

        missing_nodes = [n for n in expected_nodes if n not in found_node_ids]
        missing_relationships = [r for r in expected_relationships if r not in found_relationships]

        node_recall = (
            (len(expected_nodes) - len(missing_nodes)) / len(expected_nodes)
            if expected_nodes
            else 1.0
        )
        relationship_recall = (
            (len(expected_relationships) - len(missing_relationships)) / len(expected_relationships)
            if expected_relationships
            else 1.0
        )

        results.append(
            QueryResult(
                query_id=item["query_id"],
                root_type=item["root_type"],
                root_id=item["root_id"],
                max_hops=max_hops,
                node_recall=node_recall,
                relationship_recall=relationship_recall,
                missing_nodes=missing_nodes,
                missing_relationships=missing_relationships,
                returned_node_count=len(context.nodes),
                returned_relationship_count=len(context.relationships),
            )
        )
    return results


def summarize(results: list[QueryResult]) -> dict[str, float]:
    n = len(results)
    if n == 0:
        return {"node_recall": 0.0, "relationship_recall": 0.0}
    return {
        "node_recall": sum(r.node_recall for r in results) / n,
        "relationship_recall": sum(r.relationship_recall for r in results) / n,
    }


def print_report(max_hops: int, results: list[QueryResult]) -> None:
    summary = summarize(results)
    avg_nodes = sum(r.returned_node_count for r in results) / len(results)
    avg_rels = sum(r.returned_relationship_count for r in results) / len(results)

    print(f"=== max_hops={max_hops} ({len(results)} queries) ===")
    print(f"  Node Recall:         {summary['node_recall']:.2%}")
    print(f"  Relationship Recall: {summary['relationship_recall']:.2%}")
    print(f"  Avg nodes returned per query:         {avg_nodes:.1f}")
    print(f"  Avg relationships returned per query: {avg_rels:.1f}")

    failures = [r for r in results if r.missing_nodes or r.missing_relationships]
    if failures:
        print(f"  Queries with misses ({len(failures)}):")
        for r in failures:
            print(f"    [{r.query_id}] root={r.root_type}/{r.root_id}")
            if r.missing_nodes:
                print(f"        missing nodes: {r.missing_nodes}")
            if r.missing_relationships:
                print(f"        missing relationships: {r.missing_relationships}")
    print()


def _main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate graph retrieval Node Recall / Relationship Recall at max_hops 1, 2, 3."
    )
    parser.parse_args(argv)

    golden_set = load_golden_set()
    for max_hops in HOP_VALUES:
        results = evaluate_at_hops(max_hops, golden_set)
        print_report(max_hops, results)


if __name__ == "__main__":
    _main(sys.argv[1:])
