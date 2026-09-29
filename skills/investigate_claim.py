"""Claim investigation: the reusable BUSINESS CAPABILITY of investigating one claim.

Reuses tools.case_context.get_case_context and
context.hybrid_retriever.build_evidence_package exactly as-is. This
module never reads data/*.json directly, never implements vector or
graph retrieval itself, and never duplicates the Hybrid GraphRAG
assembly logic -- it is the SKILL layer wrapping that existing capability
into the shared SkillResult contract, nothing more.

This is also where the evidence-sufficiency rule now lives. It used to
live in agents/routing.py; as of the Skills-layer integration
(agents/case_agent.py now calls this skill from its build_evidence node
instead of calling context.hybrid_retriever directly), "was the evidence
this investigation gathered sufficient" is a property of the
claim-investigation CAPABILITY, not something the agent should
independently re-derive from raw CaseContext/EvidencePackage objects.
The agent now just reads this skill's SkillResult.status.

No LLM. No final member-facing explanation -- this returns evidence and
status, like every other skill in this package.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from context.hybrid_retriever import build_evidence_package
from context.models import EvidencePackage
from skills._debug import print_skill_result
from skills.base import RiskLevel, SkillInput, SkillMetadata, SkillResult, SkillStatus

METADATA = SkillMetadata(
    name="investigate_claim",
    version="1.0.0",
    description=(
        "Investigate a claim: assemble deterministic structured facts, relevant policy "
        "evidence, and claim-scoped graph relationships into one evidence package, and "
        "judge whether that evidence is sufficient to proceed."
    ),
    capabilities=["claim_investigation", "evidence_assembly", "evidence_sufficiency_check"],
    allowed_tools=["context.hybrid_retriever.build_evidence_package"],
    risk_level=RiskLevel.LOW,
)

# Structured-fact categories whose absence blocks evidence sufficiency.
# "prior_authorization" and "ordering_provider" are deliberately excluded:
# their absence is itself a normal, complete finding -- e.g. "no matching
# authorization on file" IS the answer to an AUTH_REQUIRED investigation,
# not evidence that the investigation itself is incomplete.
CRITICAL_MISSING_CATEGORIES = frozenset({"member", "plan", "benefit", "servicing_provider"})


class InvestigateClaimInput(SkillInput):
    claim_id: str
    query: str


def is_evidence_sufficient(package: EvidencePackage) -> bool:
    """The prototype evidence-sufficiency rule -- deliberately coarse and
    transparent, and NOT a claim-outcome judgment.

    Evidence is judged sufficient when every category in
    CRITICAL_MISSING_CATEGORIES was resolved (member, plan, benefit, and
    servicing provider all present -- "present" only; a benefit that
    exists and says `covered=False` still counts as present, since the
    absence of a rule and a negative rule are different facts) AND at
    least one policy chunk was retrieved.

    This never checks for a specific policy section id, a specific graph
    relationship, or any other golden-set-shaped expectation -- retrieval
    quality (measured independently in evals/retrieval_eval.py and
    evals/hybrid_retrieval_eval.py) and evidence sufficiency are
    different concerns.
    """
    sf = package.structured_facts
    if sf.member is None:
        return False
    if sf.plan is None:
        return False
    if sf.benefit is None:
        return False
    if sf.servicing_provider is None:
        return False
    if not package.policy_chunks:
        return False
    return True


def investigate_claim(claim_id: str, query: str) -> SkillResult:
    """Run the claim-investigation capability for one claim.

    Delegates entirely to context.hybrid_retriever.build_evidence_package
    (which itself reuses tools.case_context, rag.retriever, and
    graph.retriever/context.graph_filter) for evidence assembly -- this
    function adds no retrieval logic of its own, only the sufficiency
    judgment and the SkillResult framing.
    """
    try:
        package = build_evidence_package(claim_id, query)
    except ValueError as exc:
        return SkillResult(
            skill_name=METADATA.name,
            skill_version=METADATA.version,
            status=SkillStatus.ERROR,
            error=str(exc),
        )

    if package.structured_facts.claim is None:
        return SkillResult(
            skill_name=METADATA.name,
            skill_version=METADATA.version,
            status=SkillStatus.NOT_FOUND,
            error=f"No matching claim was found for claim_id {claim_id!r}.",
            missing_information=[item.category for item in package.missing_evidence],
        )

    missing = [item.category for item in package.missing_evidence]
    sufficient = is_evidence_sufficient(package)
    status = SkillStatus.COMPLETED if sufficient else SkillStatus.INSUFFICIENT_EVIDENCE
    next_capability: Optional[str] = None if sufficient else "escalate_case"

    return SkillResult(
        skill_name=METADATA.name,
        skill_version=METADATA.version,
        status=status,
        evidence={"evidence_package": package.model_dump(mode="json")},
        missing_information=missing,
        next_capability=next_capability,
    )


def _main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Debug utility: run the investigate_claim skill. No final answer is generated."
    )
    parser.add_argument("claim_id", help="e.g. CLM-1001")
    parser.add_argument("query", help="e.g. 'Why was this claim denied?'")
    args = parser.parse_args(argv)

    result = investigate_claim(args.claim_id, args.query)
    print_skill_result(METADATA, result)


if __name__ == "__main__":
    _main(sys.argv[1:])
