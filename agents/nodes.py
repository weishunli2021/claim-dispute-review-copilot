"""Deterministic LangGraph nodes for the claim-investigation agent.

Every node here is a narrow, deterministic Python function: it reads
AgentState, performs exactly one step, and returns a partial state
update. No LLM call anywhere in this module. Nodes reuse existing layers
rather than reimplementing them -- in particular, build_evidence
delegates the full claim-investigation capability (evidence assembly AND
the evidence-sufficiency judgment) to the investigate_claim SKILL
(skills/investigate_claim.py), not to context.hybrid_retriever directly:

    Agent (this module) -> Skill (skills/investigate_claim.py)
                         -> Tools / GraphRAG (tools/, context/, rag/, graph/)

load_case still calls tools.case_context.get_case_context directly for
its own narrower job (a cheap existence check plus the pre-evidence
missing-fact report assess_case computes) -- that is a plain tool-level
capability an agent may use directly, distinct from investigate_claim's
business capability of full evidence assembly and sufficiency judgment.

Nodes never decide which node runs next; that is entirely
agents/routing.py's job.
"""

from __future__ import annotations

from agents.state import AgentState, AgentStatus
from context.models import EvidencePackage
from skills.base import SkillStatus
from skills.investigate_claim import investigate_claim
from tools.case_context import get_case_context
from tools.validation import require_identifier


def validate_request(state: AgentState) -> dict:
    """Ensure claim_id is a well-formed identifier and original_query is
    non-blank. No LLM -- malformed input routes to ERROR via
    agents/routing.py's route_after_validate_request.
    """
    try:
        require_identifier(state["claim_id"], "claim_id")
        query = state.get("original_query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("original_query must be a non-blank string")
    except ValueError as exc:
        return {
            "status": AgentStatus.ERROR.value,
            "error": str(exc),
            "current_step": "VALIDATE_REQUEST",
            "actions_taken": ["VALIDATE_REQUEST"],
            "step_count": state["step_count"] + 1,
        }

    return {
        "status": AgentStatus.RUNNING.value,
        "current_step": "VALIDATE_REQUEST",
        "actions_taken": ["VALIDATE_REQUEST"],
        "step_count": state["step_count"] + 1,
    }


def load_case(state: AgentState) -> dict:
    """Load CaseContext via the existing deterministic tools layer
    (tools.case_context.get_case_context) -- reused as-is, not
    reimplemented. If no matching claim exists, route to ERROR: this is a
    "not found" fact the tools layer already determined, not a decision
    made here or by an LLM.
    """
    case_context = get_case_context(state["claim_id"])

    if case_context.claim is None:
        return {
            "claim_context": case_context,
            "status": AgentStatus.ERROR.value,
            "error": f"No matching claim was found for claim_id {state['claim_id']!r}.",
            "current_step": "LOAD_CASE",
            "actions_taken": ["LOAD_CASE"],
            "step_count": state["step_count"] + 1,
        }

    return {
        "claim_context": case_context,
        "status": AgentStatus.RUNNING.value,
        "current_step": "LOAD_CASE",
        "actions_taken": ["LOAD_CASE"],
        "step_count": state["step_count"] + 1,
    }


def assess_case(state: AgentState) -> dict:
    """Inspect which basic structured records are present on the already-loaded
    CaseContext, and record the ones that are absent as plain category
    labels (mirroring context.hybrid_retriever's own missing-evidence
    categories).

    Makes no coverage or claim-resolution conclusion, and does not
    terminate the workflow just because an optional fact (e.g. a prior
    authorization) is absent -- evidence retrieval may still be useful
    regardless of what is found missing here.
    """
    case_context = state["claim_context"]
    missing: list[str] = []

    if case_context.member is None:
        missing.append("member")
    if case_context.plan is None:
        missing.append("plan")
    if case_context.benefit is None:
        missing.append("benefit")
    if not case_context.prior_authorizations:
        missing.append("prior_authorization")
    if case_context.servicing_provider is None:
        missing.append("servicing_provider")
    if case_context.claim.ordering_provider_id and case_context.ordering_provider is None:
        missing.append("ordering_provider")

    return {
        "missing_information": missing,
        "status": AgentStatus.RUNNING.value,
        "current_step": "ASSESS_CASE",
        "actions_taken": ["ASSESS_CASE"],
        "step_count": state["step_count"] + 1,
    }


def build_evidence(state: AgentState) -> dict:
    """Invoke the investigate_claim SKILL -- the reusable business
    capability of assembling a claim's evidence AND judging whether it is
    sufficient (see skills/investigate_claim.py) -- rather than calling
    context.hybrid_retriever or the evidence-sufficiency rule directly.

    This node owns no retrieval or sufficiency logic of its own: it only
    adapts the skill's SkillResult into AgentState (reconstructing the
    typed EvidencePackage from the skill's evidence dict, and recording
    the skill's status for agents/routing.py to read).

    Wrapped in a broad exception handler on purpose: this is the one node
    that reaches into external subsystems (via the skill: the embedding
    model, the vector store, the graph), so an unrecoverable exception
    here is caught and turned into a normal ERROR-status state update
    rather than crashing the whole agent run -- the "unrecoverable
    exception -> error" routing case.
    """
    try:
        skill_result = investigate_claim(state["claim_id"], state["original_query"])
    except Exception as exc:  # noqa: BLE001 -- intentional agent-harness boundary
        return {
            "status": AgentStatus.ERROR.value,
            "error": f"investigate_claim skill raised an unhandled exception: {exc}",
            "current_step": "BUILD_EVIDENCE",
            "actions_taken": ["BUILD_EVIDENCE"],
            "step_count": state["step_count"] + 1,
        }

    if skill_result.status == SkillStatus.ERROR:
        return {
            "status": AgentStatus.ERROR.value,
            "error": skill_result.error or "investigate_claim skill returned ERROR.",
            "current_step": "BUILD_EVIDENCE",
            "actions_taken": ["BUILD_EVIDENCE"],
            "step_count": state["step_count"] + 1,
        }

    evidence_package = None
    package_dict = skill_result.evidence.get("evidence_package")
    if package_dict is not None:
        evidence_package = EvidencePackage.model_validate(package_dict)

    return {
        "evidence_package": evidence_package,
        "skill_status": skill_result.status.value,
        "status": AgentStatus.RUNNING.value,
        "current_step": "BUILD_EVIDENCE",
        "actions_taken": ["BUILD_EVIDENCE"],
        "step_count": state["step_count"] + 1,
    }


def assess_evidence(state: AgentState) -> dict:
    """Merge the structured-fact gaps already known (from assess_case) with
    the categories the EvidencePackage itself flagged as missing, into one
    deduplicated, sorted report -- for transparency/debugging only.

    Does NOT decide whether evidence is sufficient to proceed. That
    judgment was already made by the investigate_claim skill (see
    skills/investigate_claim.py: is_evidence_sufficient), recorded on
    state["skill_status"] by build_evidence; agents/routing.py's
    route_after_assess_evidence reads it from there. The sufficiency rule
    itself lives in exactly one place: the skill, not the agent.
    """
    evidence_package = state["evidence_package"]
    from_evidence_package = {item.category for item in evidence_package.missing_evidence}
    combined = sorted(set(state.get("missing_information", [])) | from_evidence_package)

    return {
        "missing_information": combined,
        "status": AgentStatus.RUNNING.value,
        "current_step": "ASSESS_EVIDENCE",
        "actions_taken": ["ASSESS_EVIDENCE"],
        "step_count": state["step_count"] + 1,
    }


def complete(state: AgentState) -> dict:
    """Terminal node: evidence was judged sufficient (see
    skills/investigate_claim.py: is_evidence_sufficient)."""
    return {
        "status": AgentStatus.EVIDENCE_SUFFICIENT.value,
        "current_step": "COMPLETE",
        "actions_taken": ["COMPLETE"],
        "step_count": state["step_count"] + 1,
    }


def needs_review(state: AgentState) -> dict:
    """Terminal node: evidence was judged insufficient -- one or more
    critical structured facts remain unresolved (see
    skills/investigate_claim.py: is_evidence_sufficient). Draws no
    conclusion about the claim itself, only that this investigation
    could not gather everything the prototype rule considers necessary.
    """
    return {
        "status": AgentStatus.NEEDS_REVIEW.value,
        "current_step": "NEEDS_REVIEW",
        "actions_taken": ["NEEDS_REVIEW"],
        "step_count": state["step_count"] + 1,
    }


def error(state: AgentState) -> dict:
    """Terminal node: an unrecoverable condition was hit (invalid input,
    unknown claim, or an unhandled exception from a downstream layer).
    state["error"] is expected to already be set by whichever node routed
    here.
    """
    return {
        "status": AgentStatus.ERROR.value,
        "current_step": "ERROR",
        "actions_taken": ["ERROR"],
        "step_count": state["step_count"] + 1,
    }


def max_steps_exceeded(state: AgentState) -> dict:
    """Terminal node: the workflow hit its configured max_steps safety
    limit before reaching a natural terminal state.

    This graph is currently acyclic and, at the default max_steps=8, will
    never hit this in practice (the longest path is 6 steps). It exists as
    an agent-harness safety pattern for future looping/retry behavior --
    see agents/routing.py's step-limit check, applied before every
    conditional transition.
    """
    return {
        "status": AgentStatus.MAX_STEPS_EXCEEDED.value,
        "error": f"Maximum step count ({state['max_steps']}) exceeded before reaching a terminal state.",
        "current_step": "MAX_STEPS_EXCEEDED",
        "actions_taken": ["MAX_STEPS_EXCEEDED"],
        "step_count": state["step_count"] + 1,
    }
