"""Dispute investigation: the reusable BUSINESS CAPABILITY of assembling
evidence for one dispute-review submission against an existing claim.

Reuses context.dispute_evidence_retriever.build_dispute_evidence_package
exactly as-is -- this module never reads data/*.json directly, never
implements retrieval itself, and never re-derives the deterministic
comparison dispute_review.comparison.compare_billing_correction already
computes. It is the SKILL layer wrapping that existing capability
into the shared SkillResult contract, nothing more -- the same relationship
skills/investigate_claim.py has to context/hybrid_retriever.py.

No LLM anywhere in this module. No sufficiency judgment, and deliberately
NOT the blanket rule "all three sources returned something, therefore
evidence is sufficient" -- unlike investigate_claim.py's
is_evidence_sufficient, this skill returns SkillStatus.COMPLETED whenever
a DisputeEvidencePackage was successfully assembled (however thin), and
surfaces per-source outcomes, missing evidence, conflicts, and limitations
explicitly on the package itself for Module 6B's future workflow to judge
-- a successful tool run here never itself asserts policy applicability or
the authenticity of submitted information.

No Streamlit/session-state/model-client dependency, and this module never
calls application.investigation_service.run_investigation or any other
part of the existing LLM investigation pipeline.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from context.dispute_evidence_retriever import build_dispute_evidence_package
from dispute_review.models import BillingCorrectionSubmission
from skills._debug import print_skill_result
from skills.base import RiskLevel, SkillInput, SkillMetadata, SkillResult, SkillStatus

METADATA = SkillMetadata(
    name="investigate_dispute",
    version="2.0.0",
    description=(
        "Investigate a proposed billing correction against the original claim and independent "
        "supporting records: run the deterministic billing comparator and assemble the recorded "
        "claim/decision, submitted fields, independent support records, and synthetic policy "
        "evidence into one typed dispute evidence package."
    ),
    capabilities=["dispute_investigation", "dispute_evidence_assembly"],
    allowed_tools=["context.dispute_evidence_retriever.build_dispute_evidence_package"],
    risk_level=RiskLevel.LOW,
)


class InvestigateDisputeInput(SkillInput):
    claim_id: str
    submission: BillingCorrectionSubmission


def investigate_dispute(claim_id: str, submission: BillingCorrectionSubmission) -> SkillResult:
    """Run the dispute-investigation capability for one claim + submission.

    `submission` must already be a validated DisputeSubmission (the
    caller's responsibility -- this function does not construct or
    re-validate one from raw input). Delegates entirely to
    context.dispute_evidence_retriever.build_dispute_evidence_package for
    evidence assembly, including the comparison itself; this function adds
    no retrieval or comparison logic of its own, only the SkillResult
    framing.

    Wrapped in a broad exception handler on purpose: this is the one node
    that reaches into external subsystems (the embedding model, the vector
    store, the graph), so an unrecoverable exception here is caught and
    turned into a normal ERROR-status result rather than crashing the
    caller -- the same "unrecoverable exception -> error" pattern
    skills/investigate_claim.py and agents/nodes.py's build_evidence use.
    """
    try:
        package = build_dispute_evidence_package(claim_id, submission)
    except ValueError as exc:
        return SkillResult(
            skill_name=METADATA.name,
            skill_version=METADATA.version,
            status=SkillStatus.NOT_FOUND,
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 -- intentional agent-harness boundary
        return SkillResult(
            skill_name=METADATA.name,
            skill_version=METADATA.version,
            status=SkillStatus.ERROR,
            error=f"investigate_dispute skill raised an unhandled exception: {exc}",
        )

    return SkillResult(
        skill_name=METADATA.name,
        skill_version=METADATA.version,
        status=SkillStatus.COMPLETED,
        evidence={"dispute_evidence_package": package.model_dump(mode="json")},
        missing_information=list(package.missing_evidence),
        next_capability=None,
    )


def _main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Debug utility: run the investigate_dispute skill against the billing-correction "
            "scenario's demo example. No final answer is generated."
        ),
    )
    parser.parse_args(argv)

    from dispute_review.billing_fixtures import BILLING_CLAIM_ID
    from dispute_review.presets import billing_correction_example_preset

    submission = billing_correction_example_preset()
    result = investigate_dispute(BILLING_CLAIM_ID, submission)
    print_skill_result(METADATA, result)


if __name__ == "__main__":
    _main(sys.argv[1:])
