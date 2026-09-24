"""Case escalation: a SYNTHETIC representation of what would be handed to
a human-review workflow.

Does NOT connect to any external ticketing/case-management system and
does NOT send any message -- there is no human-escalation EXECUTION at
this stage (see docs/architecture.md), only a typed, deterministic
description of the escalation package a future execution layer would
receive. `evidence["external_system_contacted"]` is always False, on
purpose, as an explicit, checkable marker of that boundary.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

from skills._debug import print_skill_result
from skills.base import RiskLevel, SkillInput, SkillMetadata, SkillResult, SkillStatus

METADATA = SkillMetadata(
    name="escalate_case",
    version="1.0.0",
    description=(
        "Assemble a synthetic escalation package for a case: reason, missing information, "
        "and an evidence summary, in the shape a human-review workflow would receive. Does "
        "not connect to any external ticketing system and does not send anything."
    ),
    capabilities=["escalation_packaging"],
    allowed_tools=[],
    risk_level=RiskLevel.LOW,
)


class EscalateCaseInput(SkillInput):
    case_id: str
    reason: str
    missing_information: list[str] = []
    evidence_summary: dict[str, Any] = {}


def escalate_case(
    case_id: str,
    reason: str,
    missing_information: Optional[list[str]] = None,
    evidence_summary: Optional[dict[str, Any]] = None,
) -> SkillResult:
    """Assemble a synthetic escalation package.

    Always COMPLETED for well-formed input: "escalating" here means
    packaging the escalation request for a human, not resolving
    anything, so there is no INSUFFICIENT_EVIDENCE outcome for this
    skill -- missing_information is carried through as context for the
    human reviewer, not evaluated or acted on here.
    """
    if not case_id or not case_id.strip():
        return SkillResult(
            skill_name=METADATA.name,
            skill_version=METADATA.version,
            status=SkillStatus.ERROR,
            error="case_id must be a non-blank string.",
        )
    if not reason or not reason.strip():
        return SkillResult(
            skill_name=METADATA.name,
            skill_version=METADATA.version,
            status=SkillStatus.ERROR,
            error="reason must be a non-blank string.",
        )

    resolved_missing = list(missing_information or [])
    resolved_summary = dict(evidence_summary or {})

    return SkillResult(
        skill_name=METADATA.name,
        skill_version=METADATA.version,
        status=SkillStatus.COMPLETED,
        evidence={
            "case_id": case_id,
            "reason": reason,
            "evidence_summary": resolved_summary,
            "synthetic": True,
            "external_system_contacted": False,
        },
        missing_information=resolved_missing,
        next_capability=None,
    )


def _main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Debug utility: run the escalate_case skill (synthetic only). No final answer is generated."
    )
    parser.add_argument("case_id", help="e.g. CLM-1005")
    parser.add_argument("reason", help="e.g. 'Critical structured evidence is unresolved.'")
    parser.add_argument("--missing-information", default="", help="comma-separated categories")
    parser.add_argument("--evidence-summary", default="{}", help="JSON object string")
    args = parser.parse_args(argv)

    missing = [item.strip() for item in args.missing_information.split(",") if item.strip()]
    summary = json.loads(args.evidence_summary)

    result = escalate_case(args.case_id, args.reason, missing_information=missing, evidence_summary=summary)
    print_skill_result(METADATA, result)


if __name__ == "__main__":
    _main(sys.argv[1:])
