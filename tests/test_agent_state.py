"""Tests for AgentState and AgentResult typing."""

from __future__ import annotations

from agents.state import AgentResult, AgentState, AgentStatus
from context.models import EvidencePackage
from tools.case_context import CaseContext


def test_agent_status_is_a_controlled_enum():
    values = {member.value for member in AgentStatus}
    assert values == {
        "RUNNING",
        "EVIDENCE_SUFFICIENT",
        "NEEDS_REVIEW",
        "ERROR",
        "MAX_STEPS_EXCEEDED",
    }


def test_agent_state_has_all_required_fields():
    # AgentState is a TypedDict -- check its declared annotations directly.
    field_names = set(AgentState.__annotations__)
    assert field_names == {
        "original_query",
        "claim_id",
        "claim_context",
        "evidence_package",
        "missing_information",
        "actions_taken",
        "current_step",
        "status",
        "error",
        "step_count",
        "max_steps",
        "skill_status",
    }


def test_agent_result_has_no_final_answer_field():
    field_names = set(AgentResult.model_fields)
    for forbidden in ("answer", "final_answer", "response", "resolution", "explanation"):
        assert forbidden not in field_names
    assert field_names == {
        "status",
        "actions_taken",
        "claim_context",
        "evidence_package",
        "missing_information",
        "error",
        "step_count",
    }


def test_agent_result_does_not_expose_langgraph_internals():
    # current_step, max_steps, and skill_status are internal LangGraph/
    # skill-integration bookkeeping and must never leak into the
    # product-facing result type.
    field_names = set(AgentResult.model_fields)
    assert "current_step" not in field_names
    assert "max_steps" not in field_names
    assert "skill_status" not in field_names


def test_agent_result_constructs_with_minimal_fields():
    result = AgentResult(
        status=AgentStatus.ERROR,
        actions_taken=["VALIDATE_REQUEST", "ERROR"],
        step_count=2,
        error="something went wrong",
    )
    assert result.claim_context is None
    assert result.evidence_package is None
    assert result.missing_information == []


def test_agent_result_accepts_case_context_and_evidence_package():
    result = AgentResult(
        status=AgentStatus.EVIDENCE_SUFFICIENT,
        actions_taken=["COMPLETE"],
        step_count=6,
        claim_context=CaseContext(),
        evidence_package=None,
    )
    assert isinstance(result.claim_context, CaseContext)
