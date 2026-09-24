"""H3: builds a LOCAL, synthetic human-review packet for a NEEDS_REVIEW
ApplicationResult, reusing skills.escalate_case's existing packaging
capability exactly as-is.

This is UI-facing glue, not new business logic: it only reformats fields
already produced by application.investigation_service.run_investigation
(the same AgentResult/EvidencePackage the agent workflow already built)
into escalate_case's existing typed call signature. It:

  - never re-runs the evidence workflow (agents.case_agent.run_case_agent
    is not called here);
  - never duplicates escalate_case's own packaging logic (delegates
    entirely to skills.escalate_case.escalate_case);
  - never wires escalate_case into agents/case_agent.py's LangGraph -- the
    main graph still only ever invokes investigate_claim; escalate_case
    remains an independently-callable, reusable capability that the UI
    layer invokes directly for the human-review path, exactly as
    docs/architecture.md describes it.

skills.escalate_case.escalate_case's own contract already guarantees
`evidence["external_system_contacted"] is False` -- this module adds
nothing that would imply otherwise.
"""

from __future__ import annotations

from agents.state import AgentStatus
from application.models import ApplicationResult
from skills.base import SkillResult
from skills.escalate_case import escalate_case


def build_needs_review_packet(result: ApplicationResult) -> SkillResult:
    """Package a synthetic, local human-review packet for a NEEDS_REVIEW
    ApplicationResult.

    Raises ValueError if `result.agent_result.status` is not NEEDS_REVIEW --
    this helper is only meaningful for that one evidence status; callers
    (application/workbench.py, app.py) must check the status themselves
    before calling it, the same way every other H1/H2 branch already does.
    """
    if result.agent_result.status != AgentStatus.NEEDS_REVIEW:
        raise ValueError(
            "build_needs_review_packet requires agent_result.status == NEEDS_REVIEW, "
            f"got {result.agent_result.status.value!r}."
        )

    missing = list(result.agent_result.missing_information)
    reason = (
        "Business evidence was judged insufficient by the investigate_claim skill "
        f"(missing: {', '.join(missing) or 'unspecified'}). Routed to human review; "
        "no investigation brief was generated."
    )

    evidence_summary: dict = {"claim_id": result.claim_id, "query": result.query}
    package = result.agent_result.evidence_package
    if package is not None:
        evidence_summary["structured_facts"] = package.structured_facts.model_dump(mode="json")
        evidence_summary["missing_evidence"] = [
            item.model_dump(mode="json") for item in package.missing_evidence
        ]

    return escalate_case(
        case_id=result.claim_id,
        reason=reason,
        missing_information=missing,
        evidence_summary=evidence_summary,
    )
