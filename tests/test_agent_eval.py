"""Tests for the agent offline evaluation harness."""

from __future__ import annotations

from evals.agent_eval import evaluate_all, load_golden_set, summarize


def test_golden_set_loads_and_covers_required_scenarios():
    golden_set = load_golden_set()
    assert 8 <= len(golden_set) <= 12
    for item in golden_set:
        assert item["case_id"]
        assert "claim_id" in item
        assert "query" in item
        assert item["terminal_status"] in {
            "RUNNING",
            "EVIDENCE_SUFFICIENT",
            "NEEDS_REVIEW",
            "ERROR",
            "MAX_STEPS_EXCEEDED",
        }
        assert item.get("rationale")


def test_evaluate_all_executes_and_produces_valid_metrics():
    results = evaluate_all()
    assert results

    summary = summarize(results)
    assert set(summary.keys()) == {
        "terminal_status_accuracy",
        "required_action_recall",
        "unexpected_action_rate",
        "human_review_recall",
        "human_review_precision",
    }
    for value in summary.values():
        assert 0.0 <= value <= 1.0


def test_all_golden_set_cases_currently_pass():
    # The agent's routing logic is fully deterministic over already-known
    # structured facts (unlike fuzzy embedding retrieval), so this golden
    # set is expected to be satisfied exactly, not approximately.
    results = evaluate_all()
    summary = summarize(results)
    assert summary["terminal_status_accuracy"] == 1.0
    assert summary["unexpected_action_rate"] == 0.0
