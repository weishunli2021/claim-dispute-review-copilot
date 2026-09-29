"""Tests for the H4 end-to-end product-scenario evaluation harness
(evals/e2e_eval.py). Fully offline -- run_all() uses only mocked adapters."""

from __future__ import annotations

from evals.e2e_eval import ALL_SCENARIOS, run_all


def test_exactly_eight_scenarios_defined():
    assert len(ALL_SCENARIOS) == 8


def test_all_scenario_ids_are_e01_through_e08():
    results = run_all()
    assert [r.scenario_id for r in results] == [f"E0{i}" for i in range(1, 9)]


def test_every_scenario_has_at_least_one_check():
    results = run_all()
    for result in results:
        assert result.checks, f"{result.scenario_id} defines no checks"


def test_all_eight_scenarios_currently_pass():
    # Deterministic over the existing synthetic fixtures and mocked
    # adapter responses -- a genuine implementation regression should fail
    # this, not a flaky/non-deterministic condition.
    results = run_all()
    failing = [r.scenario_id for r in results if not r.passed]
    assert not failing, f"Failing E2E scenarios: {failing}"
