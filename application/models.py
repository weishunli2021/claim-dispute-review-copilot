"""Typed contracts for the H1 application service.

Two families of types live here, kept deliberately separate:

- AssembledContext / ContextReference: the labeled, budgeted view of an
  already-built EvidencePackage that application.context_assembler produces
  and application.llm_adapter's prompt consumes. Not evidence itself --
  a presentation of existing evidence, with every fact traceable back to a
  deterministic reference id (application/context_assembler.py).
- InvestigationBrief / Finding / SuggestedNextStep: the small, typed,
  NON-executable structured output the LLM adapter parses a model response
  into (application/llm_adapter.py). No case identity field, no agent
  status field, no numerical confidence, no claim/payment/authorization
  decision -- see AGENTS.md rules 3, 6, 8 and this module's docstrings.
- ApplicationResult / GenerationStatus / GenerationMetadata: the envelope
  application.investigation_service.run_investigation() returns. It always
  carries the original, untouched AgentResult (and EvidencePackage, when
  the agent built one) -- the model can never override case identity or
  agent status, only ever contribute the optional InvestigationBrief.
- ValidationStatus / ValidationIssue / ValidationResult (H2): the outcome of
  application.brief_validator's deterministic checks on a DRAFTED
  InvestigationBrief. Kept as its own status axis, never merged into
  GenerationStatus -- see ApplicationResult's docstring for why.

H2 status model: ApplicationResult exposes three DELIBERATELY SEPARATE
status concepts, never collapsed into one:
  1. Evidence status  -- agents.state.AgentStatus, read at
     `ApplicationResult.agent_result.status` (EVIDENCE_SUFFICIENT /
     NEEDS_REVIEW / ERROR / MAX_STEPS_EXCEEDED). Owned entirely by the
     existing agent workflow; this module never re-derives or duplicates it.
  2. Generation status -- GenerationStatus (NOT_ATTEMPTED / DRAFTED / FAILED).
  3. Validation status -- ValidationStatus (NOT_RUN / PASSED / FAILED), set
     only after a DRAFTED brief has been checked.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agents.state import AgentResult
from context.models import EvidencePackage


class ContextReference(BaseModel):
    """One citable fact surfaced to the LLM, with a deterministic local
    reference id. `ref_id` is always built from a real source field
    (claim_id, authorization_id, chunk_id, a graph edge's own
    source/relation/target triple, ...) -- see
    application/context_assembler.py's REF_ID naming -- never an invented
    database id.
    """

    ref_id: str
    kind: str
    label: str
    detail: str


class AssembledContext(BaseModel):
    """The labeled, budgeted context assembled from one EvidencePackage.

    Consumes an EvidencePackage without mutating it (see
    application/context_assembler.py: assemble_context). Carries no new
    business conclusions -- every field here is either copied verbatim from
    StructuredEvidence/PolicyEvidence/RelationshipEvidence/EvidenceProvenance
    or a plain presentation label (`references`) built from those same
    fields.
    """

    claim_id: str
    original_query: str
    enriched_query: str
    evidence_sufficiency_status: str
    missing_information: list[str] = Field(default_factory=list)
    claim_status: Optional[str] = None
    denial_reason_code: Optional[str] = None
    denial_reason_description: Optional[str] = None
    references: list[ContextReference] = Field(default_factory=list)
    retrieval_config: dict[str, object] = Field(default_factory=dict)
    context_truncated: bool = False
    truncation_notes: list[str] = Field(default_factory=list)


class ActionCode(str, Enum):
    """Deliberately small, non-executable advisory action vocabulary.
    Nothing here approves, denies, pays, reverses, or authorizes anything.
    """

    EXPLAIN_RECORDED_STATUS = "EXPLAIN_RECORDED_STATUS"
    VERIFY_AUTHORIZATION_INFORMATION = "VERIFY_AUTHORIZATION_INFORMATION"
    REQUEST_INFORMATION = "REQUEST_INFORMATION"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class Finding(BaseModel):
    """One statement in the brief, with the reference id(s) it is based on.
    An empty evidence_refs list is not rejected by this schema itself (the
    model must be able to construct a Finding at all) -- it is caught by
    application.brief_validator's deterministic Rule B instead, which is
    where "every substantive finding must cite evidence" is actually
    enforced.
    """

    model_config = ConfigDict(extra="forbid")

    statement: str
    evidence_refs: list[str] = Field(default_factory=list)


class SuggestedNextStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_code: ActionCode
    rationale: str
    evidence_refs: list[str] = Field(default_factory=list)


class InvestigationBrief(BaseModel):
    """The structured output the LLM adapter parses a model response into.

    No case identity field (claim_id/case_id) and no agent/skill status
    field, on purpose -- the model never gets to assign or override either;
    both come from the deterministic layers below it and live on
    ApplicationResult instead. No numerical confidence field, no
    chain-of-thought field. This is a draft for a human reviewer, not a
    claim decision -- see AGENTS.md rules 6 and 8.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str
    findings: list[Finding] = Field(default_factory=list)
    missing_or_conflicting_evidence: list[str] = Field(default_factory=list)
    suggested_next_step: SuggestedNextStep


class GenerationStatus(str, Enum):
    """Controlled set of generation outcomes -- distinct from
    agents.state.AgentStatus and skills.base.SkillStatus, which this
    module never re-derives or overrides (see AssembledContext /
    ApplicationResult docstrings), and distinct from ValidationStatus below.

    H2 NOTE: prior to H2 this enum had six values
    (DRAFTED/SKIPPED_INSUFFICIENT_EVIDENCE/SKIPPED_NOT_FOUND_OR_ERROR/
    CONFIGURATION_ERROR/PROVIDER_ERROR/OUTPUT_INVALID). The two SKIPPED_*
    values duplicated information already fully available on
    `agent_result.status` (NEEDS_REVIEW vs. ERROR/MAX_STEPS_EXCEEDED), and
    the three *_ERROR values collapsed into one FAILED, with WHICH failure
    kind now carried on `ApplicationResult.generation_failure_category`
    (GenerationFailureCategory) instead of being baked into this enum's
    name. Net effect on behavior: none -- every branch in
    application/investigation_service.py that used to return one of the six
    old values now returns the equivalent of {NOT_ATTEMPTED, FAILED,
    DRAFTED}, and no caller lost any information (the AgentStatus and the
    failure category are both still present, just on separate fields).
    """

    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    DRAFTED = "DRAFTED"
    FAILED = "FAILED"


class GenerationFailureCategory(str, Enum):
    """Set only when GenerationStatus == FAILED. A small, closed set --
    not a general-purpose error taxonomy -- covering exactly the failure
    kinds application/llm_adapter.py's exceptions already distinguish.
    """

    CONFIGURATION = "CONFIGURATION"
    PROVIDER = "PROVIDER"
    TIMEOUT = "TIMEOUT"
    STRUCTURED_PARSING = "STRUCTURED_PARSING"


class ValidationStatus(str, Enum):
    """Controlled set of H2 deterministic-validation outcomes. NOT_RUN
    covers both "generation was never attempted" and "generation failed" --
    validation only ever runs against a successfully DRAFTED brief."""

    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"


class ValidationIssue(BaseModel):
    """One deterministic-validation failure. `rule` names exactly which
    check in application/brief_validator.py raised it, so a failure is
    always traceable to one narrow, documented, non-semantic check --
    never a vague "looked wrong" verdict."""

    model_config = ConfigDict(extra="forbid")

    rule: str
    detail: str


class ValidationResult(BaseModel):
    """The full result of application.brief_validator.validate_investigation_brief.

    This is a NARROW, deterministic prototype guardrail: it checks that
    every evidence_ref cited by the brief actually exists in the evidence
    that was made available for this request, that every Finding cites at
    least one evidence_ref, that the suggested action stays within the
    approved advisory allowlist, and that the generated narrative text does
    not contain an explicit claim of consequential authority (approving,
    denying, reversing, or paying a claim; approving or denying an
    authorization; determining medical necessity). It does NOT check
    whether the natural-language text is semantically entailed by the
    evidence, and it is NOT a general hallucination detector -- see
    application/brief_validator.py's module docstring.
    """

    model_config = ConfigDict(extra="forbid")

    status: ValidationStatus
    issues: list[ValidationIssue] = Field(default_factory=list)


class GenerationMetadata(BaseModel):
    """Present only when generation_status == DRAFTED. Never carries a
    secret value -- model name and prompt version only."""

    model: str
    prompt_version: str
    adapter: str
    generated_at: datetime


class ApplicationResult(BaseModel):
    """The envelope application.investigation_service.run_investigation()
    returns. Always carries the ORIGINAL, unmodified AgentResult -- the
    agent's own terminal status (agents.state.AgentStatus, read at
    `.agent_result.status`) remains authoritative and is never overwritten
    by generation_status, validation_result, or anything the model said.
    investigation_brief is populated only when generation_status == DRAFTED;
    a parsed InvestigationBrief is a draft for a human reviewer, not a
    validated or "grounded" answer on its own -- validation_result records
    what application.brief_validator.py's deterministic (non-LLM) checks
    found, and is itself NOT a semantic/hallucination judgment (see
    ValidationResult's docstring).

    Three separate status concepts, never collapsed into one:
      1. Evidence status:   `agent_result.status` (AgentStatus)
      2. Generation status: `generation_status` (GenerationStatus)
      3. Validation status: `validation_result.status` (ValidationStatus)
    """

    run_id: str
    claim_id: str
    query: str
    agent_result: AgentResult
    evidence_package: Optional[EvidencePackage] = None
    assembled_context: Optional[AssembledContext] = None
    generation_status: GenerationStatus
    generation_failure_category: Optional[GenerationFailureCategory] = None
    investigation_brief: Optional[InvestigationBrief] = None
    skip_or_error_reason: Optional[str] = None
    generation_metadata: Optional[GenerationMetadata] = None
    validation_result: ValidationResult = Field(
        default_factory=lambda: ValidationResult(status=ValidationStatus.NOT_RUN)
    )
