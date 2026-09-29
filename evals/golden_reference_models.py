"""Typed schema for Module 4's investigation golden reference dataset.

This is deliberately NOT another retrieval-scoring golden set like
evals/hybrid_retrieval_golden_set.json or evals/agent_golden_set.json --
those already cover, and remain the source of truth for, structured-fact
accuracy, policy-section recall, graph-relationship recall, and agent
routing/status for the five original predefined claims (CLM-1001 through
CLM-1005). This schema is one level up: what a human-reviewable
investigation BRIEF for each of those same five claims should say, what it
must never claim, what a reviewer still has to judge for themselves, and
which of those expectations a future evaluator could check by comparing
exact fields versus which ones inherently require a person to read the
brief.

Every case drafted against this schema is AI-drafted and starts
review_status=PENDING_HUMAN_REVIEW. Nothing in this module marks a case
reviewed -- that is a human reviewer action, recorded by editing
review_status/review_notes by hand (or by a future reviewer-facing tool),
never inferred from a passing test or a generated brief.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

GOLDEN_REFERENCE_SCHEMA_VERSION = "golden_reference.v1"

DEFAULT_GOLDEN_REFERENCE_SET_PATH = (
    Path(__file__).resolve().parent / "investigation_golden_reference_set.json"
)


class ExpectationCategory(str, Enum):
    """Which of the three kinds of expectation a requirement is.

    Mirrors the three-way split the task instruction asked for, kept as
    an explicit tag on every requirement rather than three separate lists,
    so a requirement's category travels with it if the dataset is later
    filtered, exported, or partially reviewed.
    """

    SOURCE_DERIVED = "SOURCE_DERIVED"
    DETERMINISTIC_SYSTEM_BEHAVIOR = "DETERMINISTIC_SYSTEM_BEHAVIOR"
    QUALITATIVE_HUMAN_JUDGMENT = "QUALITATIVE_HUMAN_JUDGMENT"


class RequirementClassification(str, Enum):
    """How a future evaluator could check this requirement, if at all.

    DETERMINISTICALLY_CHECKABLE means an exact field comparison suffices
    (see DeterministicCheckSpec) -- never a keyword/substring match against
    generated prose, which this project's own conventions treat as
    unreliable. QUALITATIVE_HUMAN_REVIEW means a person has to read the
    brief and judge it. NOT_CURRENTLY_OBSERVABLE means no implemented
    layer currently exposes the fact needed to check this at all (e.g. it
    would require a capability this prototype does not have yet).
    """

    DETERMINISTICALLY_CHECKABLE = "DETERMINISTICALLY_CHECKABLE"
    QUALITATIVE_HUMAN_REVIEW = "QUALITATIVE_HUMAN_REVIEW"
    NOT_CURRENTLY_OBSERVABLE = "NOT_CURRENTLY_OBSERVABLE"


class ReviewStatus(str, Enum):
    """Lifecycle of a single reference case's human review.

    A case starts and stays PENDING_HUMAN_REVIEW until a human reviewer
    changes it by hand. Nothing in this codebase is authorized to set
    either of the other two values.
    """

    PENDING_HUMAN_REVIEW = "pending_human_review"
    HUMAN_REVIEWED_ACCEPTED = "human_reviewed_accepted"
    HUMAN_REVIEWED_NEEDS_REVISION = "human_reviewed_needs_revision"


class DeterministicCheckSpec(BaseModel):
    """Exact field/comparison spec for a DETERMINISTICALLY_CHECKABLE
    requirement -- never a free-text description of what to look for.

    `field_path` names a real attribute path on an existing typed object
    this codebase already produces (e.g.
    "structured_facts.benefit.covered" on context.models.EvidencePackage,
    or "status" on agents.state.AgentResult) -- not a path invented for
    this schema. `source` names which object the path is read from, so a
    future evaluator knows what to build/call to check it, without this
    schema itself importing or calling application code (this stays a
    pure data description, per AGENTS.md's rule against a future
    generation/eval layer duplicating retrieval or business logic).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(
        description="The typed object this field_path is read from, e.g. "
        "'context.models.EvidencePackage' or 'agents.state.AgentResult'."
    )
    field_path: str = Field(
        description="Dotted attribute path on `source`, e.g. "
        "'structured_facts.benefit.covered'."
    )
    comparator: Literal[
        "equals",
        "is_true",
        "is_false",
        "is_none",
        "is_not_none",
        "length_equals",
        "length_greater_than",
    ]
    expected_value: Optional[object] = None


class RequiredFinding(BaseModel):
    """One fact or conclusion a correct investigation brief for this case
    must contain, tagged with why it's expected and how (if at all) it
    could be checked automatically."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str
    category: ExpectationCategory
    classification: RequirementClassification
    statement: str
    supporting_record_ids: list[str] = Field(default_factory=list)
    supporting_policy_section_ids: list[str] = Field(default_factory=list)
    deterministic_check: Optional[DeterministicCheckSpec] = None

    @model_validator(mode="after")
    def _deterministic_findings_need_a_check_spec(self) -> "RequiredFinding":
        if (
            self.classification == RequirementClassification.DETERMINISTICALLY_CHECKABLE
            and self.deterministic_check is None
        ):
            raise ValueError(
                f"finding {self.finding_id!r} is classified "
                "DETERMINISTICALLY_CHECKABLE but has no deterministic_check spec"
            )
        return self


class GoldenReferenceCase(BaseModel):
    """One AI-drafted, human-review-pending reference for one of the five
    original predefined investigation claims (CLM-1001..CLM-1005).

    This is never treated as ground truth on its own until a human
    reviewer changes review_status away from PENDING_HUMAN_REVIEW. Current
    application output (a generated brief, a judge score) is never a
    source for any field here -- see each case's `rationale` /
    `source_fingerprint` for what was actually used instead.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    reference_case_id: str
    claim_id: str
    scenario_title: str
    scenario_description: str

    required_findings: list[RequiredFinding]
    missing_information: list[str] = Field(default_factory=list)
    required_uncertainty_or_qualifications: list[str] = Field(default_factory=list)
    verification_or_human_review_actions: list[str] = Field(default_factory=list)
    unsupported_or_prohibited_conclusions: list[str]
    known_ambiguities_or_questions_for_human_review: list[str] = Field(default_factory=list)

    reference_origin: Literal["AI-drafted"] = "AI-drafted"
    review_status: ReviewStatus = ReviewStatus.PENDING_HUMAN_REVIEW
    schema_version: str = GOLDEN_REFERENCE_SCHEMA_VERSION
    source_fingerprint: str = Field(
        description="Explicit, reproducible list of the exact source files "
        "(with a content identifier) this case was drafted from -- not a "
        "hash of application output. See docs/V2_GOLDEN_REFERENCE_REVIEW.md "
        "for how to regenerate/verify it."
    )
    review_notes: Optional[str] = Field(
        default=None,
        description="Free-text reviewer notes -- open questions, partial-review "
        "observations, or a completed review's findings. May be present while "
        "review_status is still PENDING_HUMAN_REVIEW (a reviewer partway through, "
        "or flagging a question, has not necessarily finished the review) -- "
        "presence of notes is never itself treated as evidence that a case has "
        "been reviewed or accepted. Only a human changing review_status is that.",
    )


class GoldenReferenceSet(BaseModel):
    """The full collection -- one entry per original predefined claim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = GOLDEN_REFERENCE_SCHEMA_VERSION
    cases: list[GoldenReferenceCase]

    @model_validator(mode="after")
    def _cases_are_unique(self) -> "GoldenReferenceSet":
        case_ids = [c.reference_case_id for c in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("duplicate reference_case_id in golden reference set")
        claim_ids = [c.claim_id for c in self.cases]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("duplicate claim_id in golden reference set")
        return self


class GoldenReferenceLoadError(RuntimeError):
    """Raised when the golden reference dataset file is missing, malformed,
    or fails schema validation. Mirrors the LoadError convention used by
    dispute_review.authorization_source_lookup and
    dispute_review.scenario_synthetic_claim_decision_events."""


def load_golden_reference_set(path: Optional[Path] = None) -> GoldenReferenceSet:
    """Load and validate the golden reference dataset (Module 5 reads this
    to build the Golden Dataset & Evaluation tab; the evaluator reads it to
    resolve which reference a given claim's run should be checked against).
    Never caches -- the dataset is small and this keeps a session always
    reading the current file on disk, matching this project's existing
    convention of re-reading small fixtures each call rather than adding
    caching complexity."""
    target = path or DEFAULT_GOLDEN_REFERENCE_SET_PATH
    if not target.is_file():
        raise GoldenReferenceLoadError(f"Missing golden reference dataset: {target}")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GoldenReferenceLoadError(f"{target.name} is not valid JSON: {exc}") from exc
    try:
        return GoldenReferenceSet.model_validate(raw)
    except ValidationError as exc:
        raise GoldenReferenceLoadError(f"{target.name} failed schema validation: {exc}") from exc
