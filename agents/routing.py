"""Explicit conditional routing for the claim-investigation LangGraph workflow.

Every routing DECISION in the graph lives here, in one place. Node
functions (agents/nodes.py) only update state; they never decide which
node runs next.

The evidence-sufficiency rule itself no longer lives here: as of the
Skills-layer integration, build_evidence (agents/nodes.py) delegates the
full claim-investigation capability -- including the sufficiency judgment
-- to the investigate_claim skill (skills/investigate_claim.py:
is_evidence_sufficient), and records the skill's resulting SkillStatus on
state["skill_status"]. route_after_assess_evidence below just reads that
value. This keeps the actual business rule in exactly one place (the
skill, which owns the claim-investigation capability), rather than
duplicated between a skill and its calling agent.

Step-limit safety: every routing function checks state["step_count"]
against state["max_steps"] BEFORE making its normal decision. The graph
is currently acyclic (default max_steps=8 is never reached by the longest
6-step path), but this check is implemented as a harness pattern for
future looping/retry behavior, per the project's agent-safety convention.
"""

from __future__ import annotations

from agents.state import AgentState, AgentStatus
from skills.base import SkillStatus


def _step_limit_exceeded(state: AgentState) -> bool:
    return state["step_count"] >= state["max_steps"]


def route_after_validate_request(state: AgentState) -> str:
    if _step_limit_exceeded(state):
        return "max_steps_exceeded"
    if state["status"] == AgentStatus.ERROR.value:
        return "error"
    return "load_case"


def route_after_load_case(state: AgentState) -> str:
    if _step_limit_exceeded(state):
        return "max_steps_exceeded"
    if state["status"] == AgentStatus.ERROR.value:
        return "error"
    return "assess_case"


def route_after_assess_case(state: AgentState) -> str:
    if _step_limit_exceeded(state):
        return "max_steps_exceeded"
    return "build_evidence"


def route_after_build_evidence(state: AgentState) -> str:
    if _step_limit_exceeded(state):
        return "max_steps_exceeded"
    if state["status"] == AgentStatus.ERROR.value:
        return "error"
    return "assess_evidence"


def route_after_assess_evidence(state: AgentState) -> str:
    if _step_limit_exceeded(state):
        return "max_steps_exceeded"
    if state.get("skill_status") == SkillStatus.COMPLETED.value:
        return "complete"
    return "needs_review"
