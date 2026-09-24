"""Tests for the graph retrieval golden-set evaluation harness."""

from __future__ import annotations

from evals.graph_retrieval_eval import evaluate_at_hops, load_golden_set, summarize


def test_golden_set_loads_and_has_expected_shape():
    golden_set = load_golden_set()
    assert 6 <= len(golden_set) <= 15
    for item in golden_set:
        assert item["query_id"]
        assert item["root_type"] in ("claim", "member")
        assert item["root_id"]
        assert item["expected_nodes"] or item["expected_relationships"]


def test_evaluate_at_hops_executes_and_produces_valid_recall():
    golden_set = load_golden_set()
    results = evaluate_at_hops(2, golden_set)
    assert len(results) == len(golden_set)
    for result in results:
        assert 0.0 <= result.node_recall <= 1.0
        assert 0.0 <= result.relationship_recall <= 1.0


def test_recall_is_non_decreasing_as_hops_increase():
    golden_set = load_golden_set()
    summary_by_hops = {hops: summarize(evaluate_at_hops(hops, golden_set)) for hops in (1, 2, 3)}

    assert (
        summary_by_hops[1]["node_recall"]
        <= summary_by_hops[2]["node_recall"]
        <= summary_by_hops[3]["node_recall"]
    )
    assert (
        summary_by_hops[1]["relationship_recall"]
        <= summary_by_hops[2]["relationship_recall"]
        <= summary_by_hops[3]["relationship_recall"]
    )


def test_hops_3_achieves_perfect_recall_on_this_golden_set():
    # Documents the actual empirical result for this fixed golden set: every
    # expected node/relationship is within 3 hops of its root by design.
    summary = summarize(evaluate_at_hops(3))
    assert summary["node_recall"] == 1.0
    assert summary["relationship_recall"] == 1.0
