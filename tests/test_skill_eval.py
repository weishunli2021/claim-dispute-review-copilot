"""Tests for the skill offline evaluation harness."""

from __future__ import annotations

from evals.skill_eval import evaluate_all, load_golden_set, summarize


def test_golden_set_loads_and_covers_required_scenarios():
    golden_set = load_golden_set()
    assert 6 <= len(golden_set) <= 12
    skill_names = {item["skill_name"] for item in golden_set}
    assert skill_names == {
        "investigate_claim",
        "explain_benefit",
        "check_prior_authorization",
        "escalate_case",
    }
    for item in golden_set:
        assert item.get("rationale")


def test_evaluate_all_executes_and_produces_valid_metrics():
    results = evaluate_all()
    assert results

    summary = summarize(results)
    assert set(summary.keys()) == {
        "skill_completion_accuracy",
        "required_evidence_recall",
        "missing_evidence_accuracy",
        "correct_next_capability_rate",
        "unexpected_dependency_usage_rate",
    }
    for value in summary.values():
        assert 0.0 <= value <= 1.0


def test_all_golden_set_cases_currently_pass():
    # Skill behavior here is fully deterministic over already-known
    # structured facts (member/plan/benefit/authorization presence), so
    # this golden set is expected to be satisfied exactly.
    results = evaluate_all()
    summary = summarize(results)
    assert summary["skill_completion_accuracy"] == 1.0
    assert summary["unexpected_dependency_usage_rate"] == 0.0
