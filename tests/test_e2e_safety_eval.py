"""Tests for the H4 safety/failure evaluation harness
(evals/e2e_safety_eval.py). Fully offline -- run_all() uses only mocked
adapters and controlled monkeypatches, never a live model call."""

from __future__ import annotations

from evals.e2e_safety_eval import ALL_SAFETY_SCENARIOS, run_all


def test_exactly_nine_scenarios_defined():
    assert len(ALL_SAFETY_SCENARIOS) == 9


def test_all_scenario_ids_are_s01_through_s09():
    results = run_all()
    assert [r.scenario_id for r in results] == [f"S0{i}" for i in range(1, 10)]


def test_every_scenario_has_at_least_one_check():
    results = run_all()
    for result in results:
        assert result.checks, f"{result.scenario_id} defines no checks"


def test_all_nine_safety_scenarios_currently_pass():
    results = run_all()
    failing = [r.scenario_id for r in results if not r.passed]
    assert not failing, f"Failing safety scenarios: {failing}"


def test_s09_documents_a_real_limitation_not_a_false_pass():
    # S09's whole point is a documented gap, not a clean guarantee --
    # confirm its note actually says so, so this doesn't silently become
    # a stale/false claim of full status-level distinguishability later.
    results = run_all()
    s09 = next(r for r in results if r.scenario_id == "S09")
    assert "LIMITATION" in s09.notes
