"""Tests for the agent's explicit conditional routing.

The evidence-sufficiency rule itself is tested in
tests/test_investigate_claim_skill.py, since it now lives in
skills/investigate_claim.py, not here -- see agents/routing.py's module
docstring for why. This file only tests that routing correctly reads the
skill's resulting status off state["skill_status"].
"""

from __future__ import annotations

from agents.routing import (
    route_after_assess_case,
    route_after_assess_evidence,
    route_after_build_evidence,
    route_after_load_case,
    route_after_validate_request,
)
from agents.state import AgentStatus
from skills.base import SkillStatus


def _state(**overrides) -> dict:
    state = {"status": AgentStatus.RUNNING.value, "step_count": 1, "max_steps": 8}
    state.update(overrides)
    return state


def test_route_after_validate_request_error():
    assert route_after_validate_request(_state(status=AgentStatus.ERROR.value)) == "error"


def test_route_after_validate_request_success():
    assert route_after_validate_request(_state()) == "load_case"


def test_route_after_load_case_error():
    assert route_after_load_case(_state(status=AgentStatus.ERROR.value)) == "error"


def test_route_after_load_case_success():
    assert route_after_load_case(_state()) == "assess_case"


def test_route_after_assess_case_always_proceeds_to_build_evidence():
    assert route_after_assess_case(_state()) == "build_evidence"


def test_route_after_build_evidence_error():
    assert route_after_build_evidence(_state(status=AgentStatus.ERROR.value)) == "error"


def test_route_after_build_evidence_success():
    assert route_after_build_evidence(_state()) == "assess_evidence"


def test_step_limit_routes_to_max_steps_exceeded_at_every_stage():
    exhausted = _state(step_count=8, max_steps=8)
    assert route_after_validate_request(exhausted) == "max_steps_exceeded"
    assert route_after_load_case(exhausted) == "max_steps_exceeded"
    assert route_after_assess_case(exhausted) == "max_steps_exceeded"
    assert route_after_build_evidence(exhausted) == "max_steps_exceeded"
    assert route_after_assess_evidence(exhausted) == "max_steps_exceeded"


def test_route_after_assess_evidence_reads_skill_status_completed():
    assert (
        route_after_assess_evidence(_state(skill_status=SkillStatus.COMPLETED.value)) == "complete"
    )


def test_route_after_assess_evidence_reads_skill_status_insufficient():
    assert (
        route_after_assess_evidence(_state(skill_status=SkillStatus.INSUFFICIENT_EVIDENCE.value))
        == "needs_review"
    )


def test_route_after_assess_evidence_defaults_to_needs_review_when_status_missing():
    # Defensive: an unset/unexpected skill_status must never silently
    # route to "complete".
    assert route_after_assess_evidence(_state()) == "needs_review"
