"""Tests for individual deterministic agent nodes."""

from __future__ import annotations

from agents.nodes import (
    assess_case,
    assess_evidence,
    build_evidence,
    complete,
    error,
    load_case,
    max_steps_exceeded,
    needs_review,
    validate_request,
)
from agents.state import AgentStatus


def _base_state(**overrides) -> dict:
    state = {
        "original_query": "My claim was denied. What's wrong with it?",
        "claim_id": "CLM-1001",
        "claim_context": None,
        "evidence_package": None,
        "missing_information": [],
        "actions_taken": [],
        "current_step": "START",
        "status": AgentStatus.RUNNING.value,
        "error": None,
        "step_count": 0,
        "max_steps": 8,
    }
    state.update(overrides)
    return state


def test_validate_request_accepts_well_formed_input():
    result = validate_request(_base_state())
    assert result["status"] == AgentStatus.RUNNING.value
    assert result["actions_taken"] == ["VALIDATE_REQUEST"]
    assert result["step_count"] == 1


def test_validate_request_rejects_blank_query():
    result = validate_request(_base_state(original_query="   "))
    assert result["status"] == AgentStatus.ERROR.value
    assert result["error"]
    assert result["actions_taken"] == ["VALIDATE_REQUEST"]


def test_validate_request_rejects_malformed_claim_id():
    result = validate_request(_base_state(claim_id="bad claim id"))
    assert result["status"] == AgentStatus.ERROR.value
    assert result["error"]


def test_load_case_found():
    result = load_case(_base_state())
    assert result["status"] == AgentStatus.RUNNING.value
    assert result["claim_context"].claim is not None
    assert result["claim_context"].claim.claim_id == "CLM-1001"


def test_load_case_not_found_routes_to_error():
    result = load_case(_base_state(claim_id="CLM-9999"))
    assert result["status"] == AgentStatus.ERROR.value
    assert "CLM-9999" in result["error"]


def test_assess_case_records_missing_prior_authorization_for_case1():
    loaded = load_case(_base_state())
    state = _base_state(claim_context=loaded["claim_context"])
    result = assess_case(state)
    assert "prior_authorization" in result["missing_information"]
    assert result["status"] == AgentStatus.RUNNING.value


def test_assess_case_does_not_terminate_on_missing_optional_fact():
    loaded = load_case(_base_state())
    state = _base_state(claim_context=loaded["claim_context"])
    result = assess_case(state)
    # Missing prior_authorization alone must never force a terminal status.
    assert result["status"] == AgentStatus.RUNNING.value


def test_build_evidence_calls_hybrid_retriever_and_preserves_ids():
    loaded = load_case(_base_state())
    state = _base_state(claim_context=loaded["claim_context"])
    result = build_evidence(state)
    assert result["status"] == AgentStatus.RUNNING.value
    package = result["evidence_package"]
    assert package.claim_id == "CLM-1001"
    assert package.policy_chunks


def test_assess_evidence_merges_missing_information():
    loaded = load_case(_base_state())
    state = _base_state(claim_context=loaded["claim_context"], missing_information=["prior_authorization"])
    built = build_evidence(state)
    state.update(built)
    result = assess_evidence(state)
    assert "prior_authorization" in result["missing_information"]
    assert result["status"] == AgentStatus.RUNNING.value


def test_complete_sets_evidence_sufficient_status():
    result = complete(_base_state())
    assert result["status"] == AgentStatus.EVIDENCE_SUFFICIENT.value
    assert result["actions_taken"] == ["COMPLETE"]


def test_needs_review_sets_needs_review_status():
    result = needs_review(_base_state())
    assert result["status"] == AgentStatus.NEEDS_REVIEW.value
    assert result["actions_taken"] == ["NEEDS_REVIEW"]


def test_error_node_sets_error_status():
    result = error(_base_state(error="something failed"))
    assert result["status"] == AgentStatus.ERROR.value
    assert result["actions_taken"] == ["ERROR"]


def test_max_steps_exceeded_sets_appropriate_status():
    result = max_steps_exceeded(_base_state(max_steps=2, step_count=2))
    assert result["status"] == AgentStatus.MAX_STEPS_EXCEEDED.value
    assert "2" in result["error"]
    assert result["actions_taken"] == ["MAX_STEPS_EXCEEDED"]
