"""Module 6B: the bounded dispute-review AI workflow.

    run_dispute_workflow(claim_id, submission, adapter=None)
      -> validate_request -> invoke_skill (investigate_dispute, ONCE)
      -> assess_gate (EvidenceGateStatus: READY_FOR_SCOPED_GENERATION /
                       READY_FOR_LIMITED_BRIEF / BLOCKED)
      -> [only if not BLOCKED] assemble_context -> generate (ONE model
         call) -> validate_brief (deterministic, non-LLM)
      -> DisputeWorkflowResult

A small, fixed, ACYCLIC LangGraph StateGraph -- the same dependency and
node/routing/state split agents/case_agent.py already establishes (state
here, routing inline below, node functions above the graph builder), but
placed in application/ rather than agents/ ON PURPOSE: agents/case_agent.py
and every module under agents/ document "no LLM call anywhere in agents/"
as a load-bearing invariant (see agents/case_agent.py's own module
docstring and AGENTS.md), and this workflow DOES reach a model call (via
application/dispute_generator.py). Keeping it in application/ -- already
the one layer allowed to import the OpenAI SDK (see
tests/test_project_structure.py's test_openai_sdk_confined_to_generation_adapter,
which excludes application/ from its walk) -- preserves that invariant
exactly rather than blurring it, with zero test-allowlist change required.

No repeat-retrieval edge: invoke_skill runs exactly once per workflow
run, and no edge in this graph ever points back to it. No automatic judge
execution: the judge (application/dispute_judge.py) has its own, entirely
separate callable, never invoked from anywhere in this module. No
automatic model retries: application/dispute_generator.py's adapters are
constructed with max_retries=0, and this workflow calls generate() at
most once per run regardless of outcome.

Keeps comparison/evidence available on every path, including BLOCKED and
every generation/validation failure -- run_dispute_workflow always returns
whatever evidence_package/comparison_result the skill produced, even when
generation was never attempted or failed.
"""

from __future__ import annotations

import operator
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated, Optional
from uuid import uuid4

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from application.config import LLMConfigurationError
from application.dispute_brief_validator import validate_dispute_brief
from application.dispute_context import assemble_dispute_context
from application.dispute_generator import (
    DisputeBriefAdapter,
    DisputeGenerationOutputError,
    DisputeGenerationProviderError,
    DisputeGenerationTimeoutError,
    OpenAIDisputeBriefAdapter,
)
from application.dispute_models import (
    DisputeBrief,
    DisputeGenerationContext,
    DisputeGenerationFailureCategory,
    DisputeGenerationMetadata,
    DisputeGenerationStatus,
    DisputeValidationResult,
    DisputeValidationStatus,
    DisputeWorkflowResult,
    EvidenceGateResult,
    EvidenceGateStatus,
)
from context.dispute_evidence_models import DisputeEvidencePackage, EvidenceSourceStatus
from dispute_review.models import DisputeSubmission
from prompts.dispute_brief_prompt import build_dispute_prompt
from skills.base import SkillStatus
from skills.investigate_dispute import investigate_dispute
from tools.validation import require_identifier

DEFAULT_MAX_STEPS = 8

_EXPECTED_COMPARISON_FIELDS = frozenset({"Member", "Service", "Validity Dates", "Servicing Provider"})


# --- evidence gate (Step 4) -----------------------------------------------------------------


def assess_evidence_gate(package: DisputeEvidencePackage) -> EvidenceGateResult:
    """The exact rules distinguishing READY_FOR_SCOPED_GENERATION,
    READY_FOR_LIMITED_BRIEF, and BLOCKED -- directly callable/testable
    without running the full graph.

    BLOCKED (core recorded claim / comparison itself is unusable or
    corrupt -- generation must not be attempted):
      - the claim snapshot carries no usable structured fact at all
        (member_id, service_code, AND date_of_service all missing);
      - the comparison result's field set is not exactly the four expected
        fields (a structural corruption, never expected in practice --
        DisputeComparisonResult's own Pydantic schema already guarantees
        exactly 4 rows; this is a defensive regression guard);
      - the structured_case_context source itself FAILED (core facts
        unusable, not merely a secondary source gap);
      - any evidence reference that would be cited has a corrupt
        (blank) ref_id.

    READY_FOR_LIMITED_BRIEF (core facts/comparison usable, but a
    SECONDARY source -- policy or graph -- came back empty or failed):
      never requires all three sources to have returned evidence merely
      to justify their existence -- an incomplete SUBMISSION (e.g. the
      "incomplete" preset) is not by itself a reason to limit the brief;
      only a genuine retrieval gap/failure is.

    READY_FOR_SCOPED_GENERATION: everything above, with no degradation.
    """
    snapshot = package.claim_snapshot
    if not snapshot.member_id and not snapshot.service_code and not snapshot.date_of_service:
        return EvidenceGateResult(
            status=EvidenceGateStatus.BLOCKED,
            reasons=["The recorded claim has no usable structured facts (member, service, and date of service are all missing)."],
        )

    comparison_fields = {row.field for row in package.comparison_result.rows}
    if comparison_fields != _EXPECTED_COMPARISON_FIELDS:
        return EvidenceGateResult(
            status=EvidenceGateStatus.BLOCKED,
            reasons=["The deterministic comparison result is structurally inconsistent (unexpected field set)."],
        )

    structured_outcome = next(
        (o for o in package.source_outcomes if o.source == "structured_case_context"), None
    )
    if structured_outcome is None or structured_outcome.status == EvidenceSourceStatus.FAILURE:
        return EvidenceGateResult(
            status=EvidenceGateStatus.BLOCKED,
            reasons=["Structured evidence retrieval failed; core recorded facts are unusable."],
        )

    all_refs = (
        package.recorded_facts
        + package.submitted_fields
        + package.comparison_findings
        + package.policy_passages
        + package.graph_relationships
    )
    for ref in all_refs:
        if not ref.ref_id or not ref.ref_id.strip():
            return EvidenceGateResult(
                status=EvidenceGateStatus.BLOCKED,
                reasons=["A corrupt evidence reference (blank ref_id) was found in the evidence package."],
            )

    reasons: list[str] = []
    for outcome in package.source_outcomes:
        if outcome.status == EvidenceSourceStatus.FAILURE:
            reasons.append(f"{outcome.source}: retrieval failed ({outcome.detail or 'no detail available'}).")
    if not package.policy_passages:
        reasons.append("No policy passages were retrieved for this request.")
    if not any(ref.source_type == "graph_claim" for ref in package.graph_relationships):
        reasons.append("No claim-relevant graph relationships were found.")

    if reasons:
        return EvidenceGateResult(status=EvidenceGateStatus.READY_FOR_LIMITED_BRIEF, reasons=reasons)
    return EvidenceGateResult(status=EvidenceGateStatus.READY_FOR_SCOPED_GENERATION, reasons=[])


# --- LangGraph state --------------------------------------------------------------------


class DisputeWorkflowState(TypedDict):
    claim_id: str
    submission: DisputeSubmission
    adapter: Optional[DisputeBriefAdapter]
    trace: Annotated[list[str], operator.add]
    status: str
    step_count: int
    max_steps: int
    skill_status: Optional[str]
    skill_error: Optional[str]
    evidence_package: Optional[DisputeEvidencePackage]
    gate_result: Optional[EvidenceGateResult]
    generation_context: Optional[DisputeGenerationContext]
    generation_status: Optional[str]
    generation_failure_category: Optional[str]
    brief: Optional[DisputeBrief]
    generation_metadata: Optional[DisputeGenerationMetadata]
    validation_result: Optional[DisputeValidationResult]
    skip_or_error_reason: Optional[str]


# --- nodes -------------------------------------------------------------------------------


def validate_request(state: DisputeWorkflowState) -> dict:
    try:
        require_identifier(state["claim_id"], "claim_id")
        if not isinstance(state["submission"], DisputeSubmission):
            raise ValueError("submission must be a validated DisputeSubmission instance")
    except ValueError as exc:
        return {
            "status": "ERROR",
            "skip_or_error_reason": str(exc),
            "trace": ["VALIDATE_REQUEST"],
            "step_count": state["step_count"] + 1,
        }
    return {"status": "RUNNING", "trace": ["VALIDATE_REQUEST"], "step_count": state["step_count"] + 1}


def invoke_skill(state: DisputeWorkflowState) -> dict:
    """Calls investigate_dispute EXACTLY ONCE -- no edge in this graph
    ever routes back here."""
    try:
        result = investigate_dispute(state["claim_id"], state["submission"])
    except Exception as exc:  # noqa: BLE001 -- external-subsystem boundary, mirrors agents/nodes.py's build_evidence
        return {
            "status": "ERROR",
            "skip_or_error_reason": f"investigate_dispute skill raised an unhandled exception: {exc}",
            "trace": ["INVOKE_SKILL"],
            "step_count": state["step_count"] + 1,
        }

    evidence_package = None
    package_dict = result.evidence.get("dispute_evidence_package")
    if package_dict is not None:
        evidence_package = DisputeEvidencePackage.model_validate(package_dict)

    if result.status != SkillStatus.COMPLETED:
        return {
            "status": "ERROR",
            "skill_status": result.status.value,
            "skill_error": result.error,
            "evidence_package": evidence_package,
            "skip_or_error_reason": result.error or f"investigate_dispute returned {result.status.value}.",
            "trace": ["INVOKE_SKILL"],
            "step_count": state["step_count"] + 1,
        }

    return {
        "status": "RUNNING",
        "skill_status": result.status.value,
        "evidence_package": evidence_package,
        "trace": ["INVOKE_SKILL"],
        "step_count": state["step_count"] + 1,
    }


def assess_gate(state: DisputeWorkflowState) -> dict:
    gate_result = assess_evidence_gate(state["evidence_package"])
    new_status = "BLOCKED" if gate_result.status == EvidenceGateStatus.BLOCKED else "RUNNING"
    return {
        "status": new_status,
        "gate_result": gate_result,
        "trace": ["ASSESS_GATE"],
        "step_count": state["step_count"] + 1,
    }


def assemble_context(state: DisputeWorkflowState) -> dict:
    context = assemble_dispute_context(state["evidence_package"], state["gate_result"])
    return {
        "generation_context": context,
        "trace": ["ASSEMBLE_CONTEXT"],
        "step_count": state["step_count"] + 1,
    }


def generate(state: DisputeWorkflowState) -> dict:
    """Calls the adapter's generate() AT MOST ONCE -- no retry loop, no
    edge in this graph ever routes back here."""
    prompt_bundle = build_dispute_prompt(state["generation_context"])
    adapter: DisputeBriefAdapter = state.get("adapter") or OpenAIDisputeBriefAdapter()

    try:
        brief = adapter.generate(prompt_bundle)
    except LLMConfigurationError as exc:
        return {
            "generation_status": DisputeGenerationStatus.FAILED.value,
            "generation_failure_category": DisputeGenerationFailureCategory.CONFIGURATION.value,
            "skip_or_error_reason": str(exc),
            "trace": ["GENERATE"],
            "step_count": state["step_count"] + 1,
        }
    except DisputeGenerationTimeoutError as exc:
        # Must be caught before the broader DisputeGenerationProviderError
        # below -- DisputeGenerationTimeoutError is a subclass, and
        # category TIMEOUT is more specific than PROVIDER.
        return {
            "generation_status": DisputeGenerationStatus.FAILED.value,
            "generation_failure_category": DisputeGenerationFailureCategory.TIMEOUT.value,
            "skip_or_error_reason": str(exc),
            "trace": ["GENERATE"],
            "step_count": state["step_count"] + 1,
        }
    except DisputeGenerationProviderError as exc:
        return {
            "generation_status": DisputeGenerationStatus.FAILED.value,
            "generation_failure_category": DisputeGenerationFailureCategory.PROVIDER.value,
            "skip_or_error_reason": str(exc),
            "trace": ["GENERATE"],
            "step_count": state["step_count"] + 1,
        }
    except DisputeGenerationOutputError as exc:
        return {
            "generation_status": DisputeGenerationStatus.FAILED.value,
            "generation_failure_category": DisputeGenerationFailureCategory.STRUCTURED_PARSING.value,
            "skip_or_error_reason": str(exc),
            "trace": ["GENERATE"],
            "step_count": state["step_count"] + 1,
        }

    return {
        "generation_status": DisputeGenerationStatus.DRAFTED.value,
        "brief": brief,
        "generation_metadata": DisputeGenerationMetadata(
            model=adapter.model_name or "unknown",
            prompt_version=prompt_bundle.prompt_version,
            adapter=adapter.name,
            generated_at=datetime.now(timezone.utc),
        ),
        "trace": ["GENERATE"],
        "step_count": state["step_count"] + 1,
    }


def validate_brief(state: DisputeWorkflowState) -> dict:
    """Runs the deterministic validator ONLY when generation actually
    produced a brief -- every other path leaves validation_result at
    NOT_RUN, mirroring application/investigation_service.py's own
    "never runs brief_validator unless generation actually produced a
    brief" rule."""
    if state.get("generation_status") != DisputeGenerationStatus.DRAFTED.value:
        return {
            "validation_result": DisputeValidationResult(status=DisputeValidationStatus.NOT_RUN),
            "trace": ["VALIDATE_BRIEF"],
            "step_count": state["step_count"] + 1,
        }

    result = validate_dispute_brief(
        state["brief"], state["generation_context"], state["evidence_package"].comparison_result
    )
    return {"validation_result": result, "trace": ["VALIDATE_BRIEF"], "step_count": state["step_count"] + 1}


def complete(state: DisputeWorkflowState) -> dict:
    return {"status": "COMPLETE", "trace": ["COMPLETE"], "step_count": state["step_count"] + 1}


def blocked(state: DisputeWorkflowState) -> dict:
    return {"trace": ["BLOCKED"], "step_count": state["step_count"] + 1}


def error(state: DisputeWorkflowState) -> dict:
    return {"trace": ["ERROR"], "step_count": state["step_count"] + 1}


def max_steps_exceeded(state: DisputeWorkflowState) -> dict:
    return {
        "skip_or_error_reason": f"Maximum step count ({state['max_steps']}) exceeded before reaching a terminal state.",
        "trace": ["MAX_STEPS_EXCEEDED"],
        "step_count": state["step_count"] + 1,
    }


# --- routing (step-limit safety checked before every decision, mirroring agents/routing.py) -----


def _step_limit_exceeded(state: DisputeWorkflowState) -> bool:
    return state["step_count"] >= state["max_steps"]


def route_after_validate_request(state: DisputeWorkflowState) -> str:
    if _step_limit_exceeded(state):
        return "max_steps_exceeded"
    if state["status"] == "ERROR":
        return "error"
    return "invoke_skill"


def route_after_invoke_skill(state: DisputeWorkflowState) -> str:
    if _step_limit_exceeded(state):
        return "max_steps_exceeded"
    if state["status"] == "ERROR":
        return "error"
    return "assess_gate"


def route_after_assess_gate(state: DisputeWorkflowState) -> str:
    if _step_limit_exceeded(state):
        return "max_steps_exceeded"
    if state["status"] == "BLOCKED":
        return "blocked"
    return "assemble_context"


def route_after_assemble_context(state: DisputeWorkflowState) -> str:
    if _step_limit_exceeded(state):
        return "max_steps_exceeded"
    return "generate"


def route_after_generate(state: DisputeWorkflowState) -> str:
    if _step_limit_exceeded(state):
        return "max_steps_exceeded"
    return "validate_brief"


def route_after_validate_brief(state: DisputeWorkflowState) -> str:
    if _step_limit_exceeded(state):
        return "max_steps_exceeded"
    return "complete"


# --- graph builder -------------------------------------------------------------------------


def build_dispute_workflow_graph() -> CompiledStateGraph:
    """START -> validate_request -> {invoke_skill | error | max_steps_exceeded}
    invoke_skill -> {assess_gate | error | max_steps_exceeded}
    assess_gate -> {assemble_context | blocked | max_steps_exceeded}
    assemble_context -> {generate | max_steps_exceeded}
    generate -> {validate_brief | max_steps_exceeded}
    validate_brief -> {complete | max_steps_exceeded}
    {complete, blocked, error, max_steps_exceeded} -> END
    """
    graph = StateGraph(DisputeWorkflowState)

    graph.add_node("validate_request", validate_request)
    graph.add_node("invoke_skill", invoke_skill)
    graph.add_node("assess_gate", assess_gate)
    graph.add_node("assemble_context", assemble_context)
    graph.add_node("generate", generate)
    graph.add_node("validate_brief", validate_brief)
    graph.add_node("complete", complete)
    graph.add_node("blocked", blocked)
    graph.add_node("error", error)
    graph.add_node("max_steps_exceeded", max_steps_exceeded)

    graph.set_entry_point("validate_request")

    graph.add_conditional_edges(
        "validate_request",
        route_after_validate_request,
        {"invoke_skill": "invoke_skill", "error": "error", "max_steps_exceeded": "max_steps_exceeded"},
    )
    graph.add_conditional_edges(
        "invoke_skill",
        route_after_invoke_skill,
        {"assess_gate": "assess_gate", "error": "error", "max_steps_exceeded": "max_steps_exceeded"},
    )
    graph.add_conditional_edges(
        "assess_gate",
        route_after_assess_gate,
        {"assemble_context": "assemble_context", "blocked": "blocked", "max_steps_exceeded": "max_steps_exceeded"},
    )
    graph.add_conditional_edges(
        "assemble_context",
        route_after_assemble_context,
        {"generate": "generate", "max_steps_exceeded": "max_steps_exceeded"},
    )
    graph.add_conditional_edges(
        "generate",
        route_after_generate,
        {"validate_brief": "validate_brief", "max_steps_exceeded": "max_steps_exceeded"},
    )
    graph.add_conditional_edges(
        "validate_brief",
        route_after_validate_brief,
        {"complete": "complete", "max_steps_exceeded": "max_steps_exceeded"},
    )

    graph.add_edge("complete", END)
    graph.add_edge("blocked", END)
    graph.add_edge("error", END)
    graph.add_edge("max_steps_exceeded", END)

    return graph.compile()


@lru_cache(maxsize=1)
def _get_compiled_dispute_graph() -> CompiledStateGraph:
    return build_dispute_workflow_graph()


# --- public entry point -----------------------------------------------------------------


def run_dispute_workflow(
    claim_id: str,
    submission: DisputeSubmission,
    *,
    adapter: Optional[DisputeBriefAdapter] = None,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> DisputeWorkflowResult:
    """Run the bounded dispute-review AI workflow exactly once and return a
    product-facing DisputeWorkflowResult.

    Never raises for expected failure modes (invalid input, unknown claim,
    a blocked evidence gate, a generation/validation failure) -- all of
    those surface as a normal DisputeWorkflowResult with an appropriate
    status instead. Makes AT MOST ONE investigate_dispute call and AT MOST
    ONE generation call, regardless of outcome.
    """
    run_id = str(uuid4())
    initial_state: DisputeWorkflowState = {
        "claim_id": claim_id,
        "submission": submission,
        "adapter": adapter,
        "trace": [],
        "status": "RUNNING",
        "step_count": 0,
        "max_steps": max_steps,
        "skill_status": None,
        "skill_error": None,
        "evidence_package": None,
        "gate_result": None,
        "generation_context": None,
        "generation_status": None,
        "generation_failure_category": None,
        "brief": None,
        "generation_metadata": None,
        "validation_result": None,
        "skip_or_error_reason": None,
    }

    final_state = _get_compiled_dispute_graph().invoke(initial_state)

    evidence_package = final_state.get("evidence_package")
    comparison_result = evidence_package.comparison_result if evidence_package is not None else None

    gate_result = final_state.get("gate_result")
    if gate_result is None:
        gate_result = EvidenceGateResult(
            status=EvidenceGateStatus.BLOCKED,
            reasons=[final_state.get("skip_or_error_reason") or "Evidence could not be assembled."],
        )

    generation_status_raw = final_state.get("generation_status")
    generation_status = (
        DisputeGenerationStatus(generation_status_raw) if generation_status_raw else DisputeGenerationStatus.NOT_ATTEMPTED
    )
    failure_category_raw = final_state.get("generation_failure_category")
    generation_failure_category = (
        DisputeGenerationFailureCategory(failure_category_raw) if failure_category_raw else None
    )

    validation_result = final_state.get("validation_result") or DisputeValidationResult(
        status=DisputeValidationStatus.NOT_RUN
    )

    return DisputeWorkflowResult(
        run_id=run_id,
        claim_id=claim_id,
        skill_status=final_state.get("skill_status") or SkillStatus.ERROR.value,
        skill_error=final_state.get("skill_error"),
        evidence_package=evidence_package,
        comparison_result=comparison_result,
        gate_result=gate_result,
        generation_context=final_state.get("generation_context"),
        generation_status=generation_status,
        generation_failure_category=generation_failure_category,
        brief=final_state.get("brief"),
        generation_metadata=final_state.get("generation_metadata"),
        validation_result=validation_result,
        authenticity_disclaimer=(comparison_result.authenticity_disclaimer if comparison_result is not None else None),
        trace=list(final_state.get("trace", [])),
        skip_or_error_reason=final_state.get("skip_or_error_reason"),
    )
