"""Benefit explanation: the reusable capability of gathering the evidence
needed to explain whether/how a service is represented in a member's plan.

Reuses tools.member_tool.get_member, tools.plan_tool.get_plan,
tools.benefits_tool.get_benefits, and rag.retriever.search_policy exactly
as-is -- this module never reads data/*.json directly and never
implements vector retrieval itself.

Does not perform a full claim investigation (no graph traversal, no
claim-specific evidence) unless genuinely required: this skill answers
"what does this member's plan say about this service," not "what
happened with a specific claim" -- that broader question is
investigate_claim's job. Never infers coverage when no benefit record
exists: a missing benefit record is reported as missing evidence, never
silently treated as either covered or not covered.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from rag.chunking import LARGE
from rag.retriever import search_policy
from skills._debug import print_skill_result
from skills.base import RiskLevel, SkillInput, SkillMetadata, SkillResult, SkillStatus
from tools.benefits_tool import get_benefits
from tools.member_tool import get_member
from tools.plan_tool import get_plan

METADATA = SkillMetadata(
    name="explain_benefit",
    version="1.0.0",
    description=(
        "Gather the evidence needed to explain whether/how a service is represented in a "
        "member's plan: the plan's benefit rule for that service (if one exists), plus "
        "relevant policy evidence. Does not infer coverage when no benefit rule is on file."
    ),
    capabilities=["benefit_lookup", "coverage_evidence"],
    allowed_tools=[
        "tools.member_tool.get_member",
        "tools.plan_tool.get_plan",
        "tools.benefits_tool.get_benefits",
        "rag.retriever.search_policy",
    ],
    risk_level=RiskLevel.LOW,
)


class ExplainBenefitInput(SkillInput):
    member_id: str
    service_code: str


def explain_benefit(member_id: str, service_code: str, top_k_policy: int = 3) -> SkillResult:
    """Run the benefit-explanation capability for one member/service pair."""
    try:
        member = get_member(member_id)
    except ValueError as exc:
        return SkillResult(
            skill_name=METADATA.name, skill_version=METADATA.version, status=SkillStatus.ERROR, error=str(exc)
        )

    if member is None:
        return SkillResult(
            skill_name=METADATA.name,
            skill_version=METADATA.version,
            status=SkillStatus.NOT_FOUND,
            error=f"No matching member was found for member_id {member_id!r}.",
        )

    plan = get_plan(member.plan_id)
    missing: list[str] = []
    if plan is None:
        missing.append("plan")

    # A missing benefit record is reported, never inferred as covered or
    # not-covered -- get_benefits() already returns None (not a guess) for
    # "no rule on file," and this skill preserves that distinction as-is.
    benefit = get_benefits(member.plan_id, service_code) if plan is not None else None
    if benefit is None:
        missing.append("benefit")

    policy_chunks = []
    if plan is not None:
        query = f"Is {service_code} covered under {plan.plan_name}?"
        policy_chunks = search_policy(query, top_k=top_k_policy, config=LARGE, provider_name="semantic")

    status = SkillStatus.INSUFFICIENT_EVIDENCE if missing else SkillStatus.COMPLETED
    next_capability = "escalate_case" if missing else None

    return SkillResult(
        skill_name=METADATA.name,
        skill_version=METADATA.version,
        status=status,
        evidence={
            "member": member.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json") if plan else None,
            "benefit": benefit.model_dump(mode="json") if benefit else None,
            "policy_chunks": [chunk.model_dump(mode="json") for chunk in policy_chunks],
        },
        missing_information=missing,
        next_capability=next_capability,
    )


def _main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Debug utility: run the explain_benefit skill. No final answer is generated."
    )
    parser.add_argument("member_id", help="e.g. M-1001")
    parser.add_argument("service_code", help="e.g. MRI-KNEE")
    args = parser.parse_args(argv)

    result = explain_benefit(args.member_id, args.service_code)
    print_skill_result(METADATA, result)


if __name__ == "__main__":
    _main(sys.argv[1:])
