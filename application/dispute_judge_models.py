"""Module 6B: typed contracts for the OPTIONAL, separate dispute-review
judge. Mirrors application/judge_models.py's exact shape and discipline for
the ORIGINAL investigation judge, but is its own, wholly separate set of
types -- never reused directly -- for the same reason judge_models.py
itself gives for not reusing application/models.py's types: "a judge
failure must never be confused with, or reported through" a different
pipeline's status. The same applies here in both directions: a dispute
judge failure must never be confused with the original investigation
judge's JudgeStatus, and vice versa.

Two existing, fully generic, dispute-agnostic PRIMITIVES ARE reused
directly (no dispute-specific semantics, no "which pipeline" ambiguity):
  - application.judge_models.JudgeVerdict (PASS/FAIL/UNCERTAIN)

DimensionResult itself is NOT reused: the dispute judge's per-dimension
result additionally requires `evidence_refs` and `cited_draft_text`
(Module 6B Step 8's explicit requirement) that the original DimensionResult
has no field for -- see DisputeDimensionResult below.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from application.judge_models import JudgeVerdict


class DisputeDimensionResult(BaseModel):
    """One rubric dimension's judged result: an integer 1-5 RUBRIC SCORE
    (never a confidence/probability/accuracy percentage -- see
    application/dispute_judge_models.py's module docstring and
    prompts/dispute_judge_prompt.py's anchor definitions for what each
    integer means), the PASS/FAIL/UNCERTAIN verdict, a rationale, and --
    unlike the original investigation judge's DimensionResult -- the
    specific evidence reference ids and/or draft-brief text excerpts the
    judge is pointing to.

    A materially self-contradictory score/verdict combination is rejected
    at construction time, identical rule to
    application.judge_models.DimensionResult's own coherence check: score
    4-5 (normally PASS) can never pair with FAIL, and score 1-2 (normally
    FAIL) can never pair with PASS. Score 3 may freely pair with any
    verdict. UNCERTAIN is allowed to pair with any score -- it exists
    precisely for when the judge cannot substantiate a firm PASS/FAIL
    assessment, at any score level.
    """

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)
    verdict: JudgeVerdict
    rationale: str
    evidence_refs: list[str] = Field(default_factory=list)
    cited_draft_text: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_score_verdict_coherence(self) -> "DisputeDimensionResult":
        if self.score >= 4 and self.verdict == JudgeVerdict.FAIL:
            raise ValueError(
                f"Incoherent dimension result: score={self.score} (4-5, normally PASS) cannot "
                "pair with verdict=FAIL."
            )
        if self.score <= 2 and self.verdict == JudgeVerdict.PASS:
            raise ValueError(
                f"Incoherent dimension result: score={self.score} (1-2, normally FAIL) cannot "
                "pair with verdict=PASS."
            )
        return self


class RawDisputeJudgeDimensions(BaseModel):
    """The ONLY schema ever handed to the LLM as `text_format` (see
    application.dispute_judge.OpenAIDisputeJudgeAdapter.evaluate).
    Deliberately excludes overall_result/overall_score -- both are
    calculated deterministically in application code from these four
    dimensions (see DisputeJudgeResult.from_dimensions), never invented by
    the model.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_grounding: DisputeDimensionResult
    coverage: DisputeDimensionResult
    uncertainty_and_provenance: DisputeDimensionResult
    authority_boundaries: DisputeDimensionResult
    rationale: str
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_key_points: list[str] = Field(default_factory=list)


class DisputeJudgeResult(BaseModel):
    """The full, public dispute-judge result. `overall_score` is the
    unweighted arithmetic mean of the four dimension scores (1.0-5.0),
    ALWAYS computed in Python (never by the model) -- a rubric-score
    aggregate, never a percentage, never labeled as confidence/accuracy.
    `overall_result` is computed the same way as the original investigation
    judge's rule (any FAIL -> FAIL; else any UNCERTAIN -> UNCERTAIN; else
    PASS) -- a dimension FAIL always remains visible on its own field
    regardless of what the average says; the average never overrides or
    hides it.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_grounding: DisputeDimensionResult
    coverage: DisputeDimensionResult
    uncertainty_and_provenance: DisputeDimensionResult
    authority_boundaries: DisputeDimensionResult
    overall_result: JudgeVerdict
    overall_score: float
    rationale: str
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_key_points: list[str] = Field(default_factory=list)

    @staticmethod
    def compute_overall_result(dimensions: list[DisputeDimensionResult]) -> JudgeVerdict:
        verdicts = [d.verdict for d in dimensions]
        if JudgeVerdict.FAIL in verdicts:
            return JudgeVerdict.FAIL
        if JudgeVerdict.UNCERTAIN in verdicts:
            return JudgeVerdict.UNCERTAIN
        return JudgeVerdict.PASS

    @staticmethod
    def compute_overall_score(dimensions: list[DisputeDimensionResult]) -> float:
        return round(sum(d.score for d in dimensions) / len(dimensions), 2)

    @classmethod
    def from_dimensions(cls, raw: "RawDisputeJudgeDimensions") -> "DisputeJudgeResult":
        """The single source of truth for turning four judged dimensions
        into a full DisputeJudgeResult -- used identically by the real
        adapter and by test fixtures, so overall_result/overall_score are
        never hand-computed (and potentially miscomputed) in more than one
        place."""
        dims = [raw.evidence_grounding, raw.coverage, raw.uncertainty_and_provenance, raw.authority_boundaries]
        return cls(
            evidence_grounding=raw.evidence_grounding,
            coverage=raw.coverage,
            uncertainty_and_provenance=raw.uncertainty_and_provenance,
            authority_boundaries=raw.authority_boundaries,
            overall_result=cls.compute_overall_result(dims),
            overall_score=cls.compute_overall_score(dims),
            rationale=raw.rationale,
            unsupported_claims=raw.unsupported_claims,
            missing_key_points=raw.missing_key_points,
        )


class DisputeJudgeStatus(str, Enum):
    """Whether the judge CALL itself succeeded -- distinct from the
    verdicts inside DisputeJudgeResult, and a wholly separate enum from
    application.judge_models.JudgeStatus (see module docstring)."""

    NOT_RUN = "NOT_RUN"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DisputeJudgeFailureCategory(str, Enum):
    """Set only when DisputeJudgeStatus == FAILED. Mirrors
    application.judge_models.JudgeFailureCategory's shape, plus
    UNKNOWN_REFERENCE -- unique to the dispute judge (Module 6B Step 8's
    "validate judge references against its supplied context" requirement;
    the original semantic judge performs no equivalent check on its own
    output today)."""

    CONFIGURATION = "CONFIGURATION"
    PROVIDER = "PROVIDER"
    TIMEOUT = "TIMEOUT"
    STRUCTURED_PARSING = "STRUCTURED_PARSING"
    UNKNOWN_REFERENCE = "UNKNOWN_REFERENCE"


class DisputeJudgeMetadata(BaseModel):
    """Present only when judge_status == COMPLETED. Never carries a secret
    value -- model name and prompt version only."""

    model_config = ConfigDict(extra="forbid")

    model: str
    prompt_version: str
    evaluated_at: datetime


class DisputeJudgeEnvelope(BaseModel):
    """The full result of one dispute-judge invocation.

    `run_id` MUST be the exact DisputeWorkflowResult.run_id this judgment
    was computed against -- Module 6C (and any other caller) detects a
    stale judge result the same way the original investigation's
    SemanticJudgeEnvelope is already staleness-guarded: compare
    `envelope.run_id != current_result.run_id` and discard the envelope if
    they differ. Any change to the input submission, evidence, or brief
    produces a NEW run_id (a fresh workflow run), so this binding is
    exact, not approximate.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    claim_id: str
    judge_status: DisputeJudgeStatus
    judge_failure_category: Optional[DisputeJudgeFailureCategory] = None
    result: Optional[DisputeJudgeResult] = None
    error: Optional[str] = None
    metadata: Optional[DisputeJudgeMetadata] = None
