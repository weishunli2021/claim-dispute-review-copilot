"""The H1/H2 application service: orchestrates exactly one existing-agent
execution, then (only when evidence was already judged sufficient) one
context-assembly + one LLM-adapter call to draft an InvestigationBrief,
then (only when that draft succeeds) one deterministic, non-LLM validation
pass over it.

    run_case_agent(claim_id, query)   <- the ONE evidence-workflow execution
      -> AgentResult (already carries EvidencePackage when built)
      -> application.context_assembler.assemble_context(agent_result)
      -> prompts.investigation_brief_prompt.build_prompt(context)
      -> BriefAdapter.generate(prompt_bundle)        <- the ONE LLM call
      -> application.brief_validator.validate_investigation_brief(brief, context)
      -> ApplicationResult

This module never calls skills.investigate_claim or
context.hybrid_retriever.build_evidence_package directly, and never adds a
second graph invocation -- agents.case_agent.run_case_agent is the single
entry point into the existing evidence workflow (see
tests/test_application_investigation_service.py:
test_run_investigation_executes_evidence_workflow_exactly_once). Validation
never calls the adapter and never re-runs generation -- see
application/brief_validator.py.

No LangGraph node, edge, or routing function is touched by this module.
investigate_claim's is_evidence_sufficient rule is not re-derived here:
this module only ever branches on `agent_result.status`, already computed
by the existing agent workflow.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from agents.case_agent import run_case_agent
from agents.state import AgentStatus
from application.brief_validator import validate_investigation_brief
from application.config import LLMConfigurationError
from application.context_assembler import assemble_context
from application.llm_adapter import (
    BriefAdapter,
    LLMOutputError,
    LLMProviderError,
    LLMTimeoutError,
    OpenAIInvestigationBriefAdapter,
)
from application.models import (
    ApplicationResult,
    GenerationFailureCategory,
    GenerationMetadata,
    GenerationStatus,
)
from prompts.investigation_brief_prompt import build_prompt


def run_investigation(
    claim_id: str,
    query: str,
    *,
    adapter: Optional[BriefAdapter] = None,
    max_steps: int = 8,
) -> ApplicationResult:
    """Run one claim investigation end to end at the application layer.

    Never invokes the LLM adapter unless the agent's own terminal status is
    EVIDENCE_SUFFICIENT -- NEEDS_REVIEW (business evidence insufficient),
    ERROR (unknown claim, invalid input, or an unhandled exception), and
    MAX_STEPS_EXCEEDED all short-circuit to GenerationStatus.NOT_ATTEMPTED
    with zero adapter calls, preserving the original AgentResult and
    EvidencePackage (when one exists) unchanged. Which of those three
    happened is never lost -- it's exactly `agent_result.status`, read
    directly rather than re-encoded into a second enum.

    Never runs brief_validator unless generation actually produced a brief
    (GenerationStatus.DRAFTED); every other path returns with
    validation_result defaulting to ValidationStatus.NOT_RUN.
    """
    run_id = str(uuid4())

    agent_result = run_case_agent(claim_id, query, max_steps=max_steps)

    if agent_result.status != AgentStatus.EVIDENCE_SUFFICIENT:
        # NEEDS_REVIEW (business evidence insufficient), ERROR (unknown
        # claim, invalid input), or MAX_STEPS_EXCEEDED -- agent_result.status
        # already says exactly which; GenerationStatus only needs to say
        # generation itself was never attempted.
        if agent_result.status == AgentStatus.NEEDS_REVIEW:
            reason = (
                "Business evidence was judged insufficient by the investigate_claim skill "
                f"(missing: {', '.join(agent_result.missing_information) or 'unspecified'}); "
                "the LLM was not invoked."
            )
        else:
            reason = (
                agent_result.error
                or f"Agent did not reach EVIDENCE_SUFFICIENT (status={agent_result.status.value}); "
                "the LLM was not invoked."
            )
        return ApplicationResult(
            run_id=run_id,
            claim_id=claim_id,
            query=query,
            agent_result=agent_result,
            evidence_package=agent_result.evidence_package,
            generation_status=GenerationStatus.NOT_ATTEMPTED,
            skip_or_error_reason=reason,
        )

    if agent_result.evidence_package is None:
        # Defensive only: EVIDENCE_SUFFICIENT should always carry a package.
        # Surfaced as a clear application error rather than guessed at or crashed on.
        return ApplicationResult(
            run_id=run_id,
            claim_id=claim_id,
            query=query,
            agent_result=agent_result,
            generation_status=GenerationStatus.NOT_ATTEMPTED,
            skip_or_error_reason=(
                "Agent reported EVIDENCE_SUFFICIENT but returned no EvidencePackage; "
                "the LLM was not invoked."
            ),
        )

    context = assemble_context(agent_result)
    prompt_bundle = build_prompt(context)
    resolved_adapter: BriefAdapter = adapter if adapter is not None else OpenAIInvestigationBriefAdapter()

    try:
        brief = resolved_adapter.generate(prompt_bundle)
    except LLMConfigurationError as exc:
        return ApplicationResult(
            run_id=run_id,
            claim_id=claim_id,
            query=query,
            agent_result=agent_result,
            evidence_package=agent_result.evidence_package,
            assembled_context=context,
            generation_status=GenerationStatus.FAILED,
            generation_failure_category=GenerationFailureCategory.CONFIGURATION,
            skip_or_error_reason=str(exc),
        )
    except LLMTimeoutError as exc:
        # Must be caught before the broader LLMProviderError below --
        # LLMTimeoutError is a subclass, and category TIMEOUT is more
        # specific than PROVIDER.
        return ApplicationResult(
            run_id=run_id,
            claim_id=claim_id,
            query=query,
            agent_result=agent_result,
            evidence_package=agent_result.evidence_package,
            assembled_context=context,
            generation_status=GenerationStatus.FAILED,
            generation_failure_category=GenerationFailureCategory.TIMEOUT,
            skip_or_error_reason=str(exc),
        )
    except LLMProviderError as exc:
        return ApplicationResult(
            run_id=run_id,
            claim_id=claim_id,
            query=query,
            agent_result=agent_result,
            evidence_package=agent_result.evidence_package,
            assembled_context=context,
            generation_status=GenerationStatus.FAILED,
            generation_failure_category=GenerationFailureCategory.PROVIDER,
            skip_or_error_reason=str(exc),
        )
    except LLMOutputError as exc:
        return ApplicationResult(
            run_id=run_id,
            claim_id=claim_id,
            query=query,
            agent_result=agent_result,
            evidence_package=agent_result.evidence_package,
            assembled_context=context,
            generation_status=GenerationStatus.FAILED,
            generation_failure_category=GenerationFailureCategory.STRUCTURED_PARSING,
            skip_or_error_reason=str(exc),
        )

    # Generation succeeded: run the deterministic, non-LLM validator. No
    # second adapter call happens on any path from here.
    validation_result = validate_investigation_brief(brief, context)

    return ApplicationResult(
        run_id=run_id,
        claim_id=claim_id,
        query=query,
        agent_result=agent_result,
        evidence_package=agent_result.evidence_package,
        assembled_context=context,
        generation_status=GenerationStatus.DRAFTED,
        investigation_brief=brief,
        generation_metadata=GenerationMetadata(
            model=resolved_adapter.model_name or "unknown",
            prompt_version=prompt_bundle.prompt_version,
            adapter=resolved_adapter.name,
            generated_at=datetime.now(timezone.utc),
        ),
        validation_result=validation_result,
    )
