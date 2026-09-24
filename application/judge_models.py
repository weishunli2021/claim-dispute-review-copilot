"""H6 (OPTIONAL evaluation sidecar): typed contracts for the experimental
semantic judge. Extended by H8 with a 1-5 rubric SCORE per dimension
(DimensionResult, RawJudgeDimensions, SemanticJudgeResult.overall_score) --
see each class's docstring below. The existing PASS/FAIL/UNCERTAIN verdict
system and overall_result rule are unchanged; scores are an additional,
deterministically-computed evaluation signal, never a replacement.

Kept in its own module, separate from application/models.py's stable H1/H2
contract, so H6 can never accidentally touch the frozen
AgentStatus/GenerationStatus/ValidationStatus/InvestigationBrief types. A
SemanticJudgeEnvelope is produced by application.semantic_judge.run_semantic_judge
against an ALREADY-drafted, ALREADY-H2-validated InvestigationBrief -- it is
advisory evaluation metadata only, and NEVER mutates the ApplicationResult,
the InvestigationBrief, or any review-decision state it was computed from.
If judge_status != COMPLETED, the InvestigationBrief the user was already
reviewing remains fully intact and usable exactly as before.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class JudgeVerdict(str, Enum):
    """Per-dimension and overall verdict. Deliberately three coarse
    values. H8 adds a 1-5 rubric SCORE alongside this verdict (see
    DimensionResult) -- the verdict remains the categorical result; the
    score is a supplementary evaluation signal, never a replacement, and
    is never a confidence/probability/accuracy value."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"


class DimensionResult(BaseModel):
    """H8: one rubric dimension's judged result -- an integer 1-5 RUBRIC
    SCORE (not a confidence/probability/accuracy percentage), the
    existing PASS/FAIL/UNCERTAIN verdict, and a short dimension-specific
    rationale. See prompts/investigation_judge_prompt.py for the exact
    1-5 anchor definitions used to instruct the model.

    A materially self-contradictory score/verdict combination is rejected
    at construction time: score 4-5 (normally PASS) can never pair with
    FAIL, and score 1-2 (normally FAIL) can never pair with PASS. Score 3
    may freely pair with any verdict, since a "mixed/material weakness"
    can reasonably resolve to PASS, FAIL, or UNCERTAIN depending on
    materiality. This check applies uniformly to real model output and to
    test fixtures -- see application/semantic_judge.py's
    OpenAISemanticJudgeAdapter.evaluate for how a genuinely-incoherent
    live response is mapped to JudgeFailureCategory.STRUCTURED_PARSING
    rather than silently accepted.
    """

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)
    verdict: JudgeVerdict
    rationale: str

    @model_validator(mode="after")
    def _check_score_verdict_coherence(self) -> "DimensionResult":
        if self.score >= 4 and self.verdict == JudgeVerdict.FAIL:
            raise ValueError(
                f"Incoherent dimension result: score={self.score} (4-5, normally PASS) "
                "cannot pair with verdict=FAIL."
            )
        if self.score <= 2 and self.verdict == JudgeVerdict.PASS:
            raise ValueError(
                f"Incoherent dimension result: score={self.score} (1-2, normally FAIL) "
                "cannot pair with verdict=PASS."
            )
        return self


class RawJudgeDimensions(BaseModel):
    """H8: the ONLY schema ever handed to the LLM as `text_format` (see
    application.semantic_judge.OpenAISemanticJudgeAdapter.evaluate).
    Deliberately excludes overall_result/overall_score -- both are
    calculated deterministically in application code from these four
    dimensions (see SemanticJudgeResult.from_dimensions), never invented
    by the model. "Do NOT ask the LLM to invent the overall arithmetic
    result" applies structurally here: the field does not exist in the
    schema the model is constrained to produce.
    """

    model_config = ConfigDict(extra="forbid")

    factual_grounding: DimensionResult
    completeness: DimensionResult
    uncertainty_preservation: DimensionResult
    authority_boundary: DimensionResult
    rationale: str
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_key_points: list[str] = Field(default_factory=list)


class SemanticJudgeResult(BaseModel):
    """The full, public judge result. Carries everything
    RawJudgeDimensions does, plus overall_result and overall_score -- both
    always computed deterministically by from_dimensions(), never
    LLM-generated. overall_score is the unweighted arithmetic mean of the
    four dimension scores (1.0-5.0); it is a rubric-score aggregate, never
    a percentage and never labeled as confidence/accuracy. overall_result
    keeps H6's exact original rule (any FAIL -> FAIL; else any UNCERTAIN
    -> UNCERTAIN; else PASS), now enforced in code against the four
    dimension verdicts rather than trusted from the model's own summary --
    the rule itself is unchanged, only where it's evaluated.
    """

    model_config = ConfigDict(extra="forbid")

    factual_grounding: DimensionResult
    completeness: DimensionResult
    uncertainty_preservation: DimensionResult
    authority_boundary: DimensionResult
    overall_result: JudgeVerdict
    overall_score: float
    rationale: str
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_key_points: list[str] = Field(default_factory=list)

    @staticmethod
    def compute_overall_result(dimensions: list[DimensionResult]) -> JudgeVerdict:
        verdicts = [d.verdict for d in dimensions]
        if JudgeVerdict.FAIL in verdicts:
            return JudgeVerdict.FAIL
        if JudgeVerdict.UNCERTAIN in verdicts:
            return JudgeVerdict.UNCERTAIN
        return JudgeVerdict.PASS

    @staticmethod
    def compute_overall_score(dimensions: list[DimensionResult]) -> float:
        return round(sum(d.score for d in dimensions) / len(dimensions), 2)

    @classmethod
    def from_dimensions(cls, raw: "RawJudgeDimensions") -> "SemanticJudgeResult":
        """The single source of truth for turning four judged dimensions
        into a full SemanticJudgeResult -- used identically by the real
        adapter (application.semantic_judge.OpenAISemanticJudgeAdapter)
        and by test fixtures, so overall_result/overall_score are never
        hand-computed (and potentially miscomputed) in more than one
        place."""
        dims = [raw.factual_grounding, raw.completeness, raw.uncertainty_preservation, raw.authority_boundary]
        return cls(
            factual_grounding=raw.factual_grounding,
            completeness=raw.completeness,
            uncertainty_preservation=raw.uncertainty_preservation,
            authority_boundary=raw.authority_boundary,
            overall_result=cls.compute_overall_result(dims),
            overall_score=cls.compute_overall_score(dims),
            rationale=raw.rationale,
            unsupported_claims=raw.unsupported_claims,
            missing_key_points=raw.missing_key_points,
        )


class JudgeStatus(str, Enum):
    """Whether the judge CALL itself succeeded -- distinct from the
    verdict values inside SemanticJudgeResult. Mirrors
    application.models.GenerationStatus's shape on purpose (NOT_RUN /
    COMPLETED / FAILED) but is a wholly separate enum: a judge failure
    must never be confused with, or reported through, GenerationStatus."""

    NOT_RUN = "NOT_RUN"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JudgeFailureCategory(str, Enum):
    """Set only when JudgeStatus == FAILED. Mirrors
    application.models.GenerationFailureCategory's shape -- a small,
    closed set, not a general error taxonomy."""

    CONFIGURATION = "CONFIGURATION"
    PROVIDER = "PROVIDER"
    TIMEOUT = "TIMEOUT"
    STRUCTURED_PARSING = "STRUCTURED_PARSING"


class SemanticJudgeMetadata(BaseModel):
    """Present only when judge_status == COMPLETED. Never carries a
    secret value -- model name and prompt version only, same convention
    as application.models.GenerationMetadata."""

    model_config = ConfigDict(extra="forbid")

    model: str
    prompt_version: str
    evaluated_at: datetime


class SemanticJudgeEnvelope(BaseModel):
    """The full result of one H6 semantic-judge invocation.

    `run_id` is the ApplicationResult.run_id this judgment was computed
    against -- callers (app.py) use it to detect and discard a stale
    judge result the same way the main result is protected against
    staleness (see application/workbench.py: reset_investigation_state).
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    claim_id: str
    query: str
    judge_status: JudgeStatus
    judge_failure_category: Optional[JudgeFailureCategory] = None
    result: Optional[SemanticJudgeResult] = None
    error: Optional[str] = None
    metadata: Optional[SemanticJudgeMetadata] = None
