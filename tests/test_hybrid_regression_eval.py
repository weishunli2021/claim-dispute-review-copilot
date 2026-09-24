"""Tests for the hybrid retrieval REGRESSION suite (drift detection, not an
independent quality claim -- see tests/test_hybrid_retrieval_eval.py for that)."""

from __future__ import annotations

from evals.hybrid_regression_eval import evaluate_all, load_regression_set, summarize


def test_regression_set_loads_and_covers_all_five_cases():
    regression_set = load_regression_set()
    assert 6 <= len(regression_set) <= 15
    claim_ids = {item["claim_id"] for item in regression_set}
    assert claim_ids == {"CLM-1001", "CLM-1002", "CLM-1003", "CLM-1004", "CLM-1005"}


def test_evaluate_all_executes_and_produces_valid_metrics():
    results = evaluate_all()
    assert results

    summary = summarize(results)
    assert set(summary.keys()) == {
        "policy_section_recall",
        "graph_relationship_recall",
        "structured_completeness",
    }
    for value in summary.values():
        assert 0.0 <= value <= 1.0


def test_no_case_reports_contamination():
    results = evaluate_all()
    contaminated = [r for r in results if r.contaminating_nodes]
    assert contaminated == []
