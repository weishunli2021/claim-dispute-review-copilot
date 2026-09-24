"""Module 6B: typed contracts for the dispute-review AI workflow (bounded
evidence gate -> generation -> deterministic validation).

Mirrors application/models.py's H1/H2 shape for the ORIGINAL investigation
pipeline, but is a WHOLLY SEPARATE set of types -- never reused directly --
following the same discipline application/judge_models.py already
establishes (JudgeStatus "mirrors GenerationStatus's shape on purpose...
but is a wholly separate enum" so a judge failure can never be confused
with a generation failure). The same reasoning applies here: a dispute
generation/validation status must never be confused with, or reported
through, the original investigation's GenerationStatus/ValidationStatus.

Two kinds of existing types ARE reused directly, not duplicated, because
they are generic, closed VALUE vocabularies with zero dispute-specific
semantics and no "which pipeline is this status from" ambiguity risk:
  - application.models.ActionCode / Finding / SuggestedNextStep -- the
    advisory-action allowlist and finding/next-step shape apply identically
    to a dispute brief; duplicating them would only fragment the one
    audit surface for "what advisory actions this system may ever suggest."

Three separate status axes, exactly mirroring H1/H2's own three-way split,
extended by a fourth (the evidence gate) that H1/H2 never needed:
  1. Evidence gate:     EvidenceGateResult.status (EvidenceGateStatus)
  2. Generation status: DisputeGenerationStatus
  3. Validation status: DisputeValidationStatus
  (Judge status -- DisputeJudgeStatus -- lives in
  application/dispute_judge_models.py, its own separate module, mirroring
  judge_models.py's own separation from models.py.)

No skill in skills/ gains a final natural-language answer field (AGENTS.md
rule 2) -- DisputeBrief lives here, in application/, strictly downstream
of skills.investigate_dispute's evidence-only SkillResult.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from application.models import Finding, SuggestedNextStep
from context.dispute_evidence_models import DisputeEvidencePackage, EvidenceReference
from dispute_review.models import DisputeComparisonResult


class EvidenceGateStatus(str, Enum):
    """The workflow's own judgment of whether/how to proceed to generation
    -- a DERIVED decision, distinct from the raw per-source outcomes
    already recorded on DisputeEvidencePackage.source_outcomes (see
    application/dispute_workflow.py's assess_gate node for the exact
    rules). Never a claim-outcome judgment; only about evidence usability.
    """

    READY_FOR_SCOPED_GENERATION = "READY_FOR_SCOPED_GENERATION"
    """Core recorded facts and the deterministic comparison are usable,
    and no retrieval source came back degraded (a FAILURE, or an empty
    policy/graph result) -- the normal case."""

    READY_FOR_LIMITED_BRIEF = "READY_FOR_LIMITED_BRIEF"
    """Core recorded facts and the deterministic comparison are still
    usable, but at least one secondary source (policy/graph) came back
    empty or failed -- generation may proceed, but the brief must be
    explicitly scoped to what remains usable, not silently treated as a
    full brief."""

    BLOCKED = "BLOCKED"
    """The claim could not be resolved, the comparison result is missing
    or structurally inconsistent, or the evidence package itself is
    corrupt -- generation must not be attempted. Deterministic findings
    and gaps are still returned; there is no model call on this path."""


class EvidenceGateResult(BaseModel):
    """The output of the workflow's assess_gate step. `reasons` explains
    every degradation/block; empty only when status is
    READY_FOR_SCOPED_GENERATION."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: EvidenceGateStatus
    reasons: list[str] = Field(default_factory=list)


class DisputeBrief(BaseModel):
    """The structured output the dispute generation adapter parses a model
    response into. Deliberately carries NO field for the four comparison
    verdicts (Member/Service/Validity Dates/Servicing Provider) -- those
    are authoritative, already-computed facts from
    dispute_review.comparison.compare_submission, carried on
    DisputeWorkflowResult.comparison_result unchanged; the model is never
    asked to recompute or restate them (see application/dispute_brief_validator.py's
    comparison-context-integrity check, which validates the CONTEXT the
    model was shown, never asks the model to reproduce the verdicts
    itself).

    Reuses application.models.Finding/SuggestedNextStep/ActionCode as-is
    (see module docstring) -- no case identity field and no confidence
    field, same reasoning as InvestigationBrief.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str
    findings: list[Finding] = Field(default_factory=list)
    missing_or_conflicting_evidence: list[str] = Field(default_factory=list)
    verification_questions: list[str] = Field(default_factory=list)
    suggested_next_step: SuggestedNextStep


class DisputeGenerationStatus(str, Enum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    DRAFTED = "DRAFTED"
    FAILED = "FAILED"


class DisputeGenerationFailureCategory(str, Enum):
    """Set only when DisputeGenerationStatus == FAILED. Mirrors
    application.models.GenerationFailureCategory's shape (a small, closed
    set matching application/dispute_generator.py's own exception types),
    but is its own separate enum -- see module docstring."""

    CONFIGURATION = "CONFIGURATION"
    PROVIDER = "PROVIDER"
    TIMEOUT = "TIMEOUT"
    STRUCTURED_PARSING = "STRUCTURED_PARSING"


class DisputeGenerationMetadata(BaseModel):
    """Present only when generation_status == DRAFTED. Never carries a
    secret value -- model name and prompt version only."""

    model_config = ConfigDict(extra="forbid")

    model: str
    prompt_version: str
    adapter: str
    generated_at: datetime


class DisputeValidationStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"


class DisputeValidationIssue(BaseModel):
    """One deterministic-validation failure. `rule` names exactly which
    check in application/dispute_brief_validator.py raised it."""

    model_config = ConfigDict(extra="forbid")

    rule: str
    detail: str


class DisputeValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DisputeValidationStatus
    issues: list[DisputeValidationIssue] = Field(default_factory=list)


class DisputeGenerationContext(BaseModel):
    """The labeled, budgeted context actually rendered into the generation
    prompt -- the dispute analogue of application.models.AssembledContext.
    Built by application/dispute_context.py from a DisputeEvidencePackage;
    never re-runs retrieval or re-derives the comparison.

    `references` reuses context.dispute_evidence_models.EvidenceReference
    directly (already exactly the labeled/citable shape needed) -- no
    separate context-reference type is defined here. Only entries present
    in `references` may ever be legitimately cited by the generator;
    application/dispute_brief_validator.py's Rule A checks against this
    exact list.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    gate_status: EvidenceGateStatus
    comparison_summary: str
    references: list[EvidenceReference] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    context_truncated: bool = False
    truncation_notes: list[str] = Field(default_factory=list)


# Fixed, application-owned notices -- never model-authored, always present
# regardless of what the model said (see application/dispute_workflow.py's
# assemble_result step, and application/dispute_brief_validator.py's
# "provenance_notices_preserved" structural check).
DISPUTE_PROVENANCE_NOTICE = (
    "This brief distinguishes RECORDED facts (already on file) from SUBMITTED, unverified "
    "information (provided with this dispute and not authenticated). A record found by looking "
    "up a submitted identifier is real data, but its applicability to this claim/dispute remains "
    "unverified."
)


class DisputeWorkflowResult(BaseModel):
    """The product-facing result of application.dispute_workflow.run_dispute_workflow.

    Always carries the ORIGINAL, unmodified `comparison_result` from the
    evidence package (never re-derived) and the full `evidence_package`
    itself, on every path -- including BLOCKED and FAILED -- so deterministic
    findings and gaps are never lost just because generation didn't run or
    didn't succeed (Step 3/4's explicit requirement).

    Four separate status concepts, never collapsed into one:
      1. Skill/evidence availability: `skill_status` (mirrors
         skills.base.SkillStatus.value) + `evidence_package.source_outcomes`.
      2. Evidence gate:   `gate_result.status` (EvidenceGateStatus).
      3. Generation status: `generation_status` (DisputeGenerationStatus).
      4. Validation status: `validation_result.status` (DisputeValidationStatus).
    (Judge status is a fifth, entirely separate axis -- see
    application/dispute_judge_models.py -- never computed or stored here.)
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    claim_id: str
    skill_status: str
    skill_error: Optional[str] = None
    evidence_package: Optional[DisputeEvidencePackage] = None
    comparison_result: Optional[DisputeComparisonResult] = None
    gate_result: EvidenceGateResult
    generation_context: Optional[DisputeGenerationContext] = None
    generation_status: DisputeGenerationStatus
    generation_failure_category: Optional[DisputeGenerationFailureCategory] = None
    brief: Optional[DisputeBrief] = None
    generation_metadata: Optional[DisputeGenerationMetadata] = None
    validation_result: DisputeValidationResult = Field(
        default_factory=lambda: DisputeValidationResult(status=DisputeValidationStatus.NOT_RUN)
    )
    authenticity_disclaimer: Optional[str] = None
    provenance_notice: str = DISPUTE_PROVENANCE_NOTICE
    trace: list[str] = Field(default_factory=list)
    skip_or_error_reason: Optional[str] = None


def is_accepted_dispute_draft(result: DisputeWorkflowResult) -> bool:
    """True exactly when a DisputeBrief is eligible to be presented as an
    accepted draft -- DRAFTED generation AND PASSED validation. Mirrors
    application.workbench.is_accepted_draft exactly, so Module 6C's
    gating logic (and the judge's own precondition, see
    application/dispute_judge.py) can never drift from this one
    definition."""
    return (
        result.generation_status == DisputeGenerationStatus.DRAFTED
        and result.validation_result.status == DisputeValidationStatus.PASSED
    )
