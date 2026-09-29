"""Module 5: a small, deterministic evaluator that checks one completed
investigation run (`application.models.ApplicationResult`) against one
`evals.golden_reference_models.GoldenReferenceCase`.

Scope, deliberately narrow:
  - Only the field paths/comparators `evals.golden_reference_models.
    DeterministicCheckSpec` already defines are implemented. No eval(),
    no arbitrary code execution, no keyword/substring matching as a
    stand-in for semantic evaluation of generated text.
  - Only two sources are supported, matching every DeterministicCheckSpec
    in the current dataset: `context.models.EvidencePackage` (read from
    `ApplicationResult.evidence_package`) and `agents.state.AgentResult`
    (read from `ApplicationResult.agent_result`). An unrecognized source
    is reported as NOT_EVALUATED, never guessed at.
  - Every value compared is read from the ACTUAL run being evaluated.
    The reference's own `expected_value` is never used as a fallback
    actual value -- a field that cannot be resolved reports
    NOT_EVALUATED with field_available=False, never a value copied from
    the reference.
  - A present null (the field resolved and its real value is None) is
    reported differently from an unavailable field (the path could not
    be traversed at all -- an intermediate attribute was None, or the
    attribute genuinely doesn't exist on the object). An unavailable
    field can never accidentally satisfy an `is_none`/`is_not_none`
    check: `_resolve_field_path` short-circuits to unavailable BEFORE any
    comparator ever runs.
  - A PASSED deterministic check confirms one structured field on
    EvidencePackage/AgentResult. It says nothing about whether the
    GENERATED BRIEF expressed that finding correctly -- that remains a
    qualitative, human-review question, tracked separately below and
    never folded into the automated-check count.

This module never re-derives evidence sufficiency, retrieval, or gate
logic (those remain owned by skills/investigate_claim.py, context/, and
application/dispute_workflow.py, per AGENTS.md rule 4) -- it only reads
already-computed fields off an already-completed ApplicationResult.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from application.models import ApplicationResult
from evals.golden_reference_models import (
    DeterministicCheckSpec,
    GoldenReferenceCase,
    RequirementClassification,
    ReviewStatus,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

_FINGERPRINT_ENTRY = re.compile(r"([^\s;]+)@sha256:([0-9a-f]{12})")

_UNSUPPORTED_SOURCE = object()  # sentinel distinct from a legitimately-None source object


class CheckOutcome(str, Enum):
    """The result of attempting exactly one deterministic check."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_EVALUATED = "NOT_EVALUATED"


class OverallEvaluationStatus(str, Enum):
    """See module docstring / evaluate_investigation_result for the rules.
    PASSED requires every supported check to pass AND the reference to
    already be human_reviewed_accepted AND no qualitative items pending
    AND a fresh source fingerprint -- nothing in this module ever sets
    review_status, so PASSED is not reachable against the current,
    still-pending dataset. That is intentional, not a bug."""

    NOT_RUN = "NOT_RUN"
    FAILED = "FAILED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    PASSED = "PASSED"


class DeterministicCheckResult(BaseModel):
    """One finding's deterministic-check outcome, bound to the exact spec
    that was evaluated (never re-describes it from memory)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str
    statement: str
    source: str
    field_path: str
    comparator: str
    expected_value: Optional[object] = None
    field_available: bool
    actual_value: Optional[object] = None
    outcome: CheckOutcome
    reason: str


class QualitativeReviewItem(BaseModel):
    """One finding that is not automatically checkable -- always requires
    a human to read the generated brief and judge it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str
    statement: str
    category: str
    classification: str


class EvaluationRun(BaseModel):
    """One evaluation of one completed ApplicationResult against one
    GoldenReferenceCase, bound to exactly that run and that reference
    content so a caller can detect staleness (see is_stale_for)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    run_id: str
    reference_case_id: str
    reference_schema_version: str
    reference_review_status: ReviewStatus
    reference_content_fingerprint: str
    source_fingerprint_stale: bool
    stale_source_files: list[str] = Field(default_factory=list)
    deterministic_results: list[DeterministicCheckResult]
    qualitative_items: list[QualitativeReviewItem]
    overall_status: OverallEvaluationStatus
    status_reasons: list[str]
    automated_summary: str
    evaluated_at: datetime

    def is_stale_for(self, result: ApplicationResult, reference: GoldenReferenceCase) -> bool:
        """True when this evaluation no longer corresponds to the given
        run/reference -- case changed, investigation was rerun (new
        run_id), or the reference's own content changed since this
        evaluation was computed."""
        return (
            self.claim_id != result.claim_id
            or self.run_id != result.run_id
            or self.reference_case_id != reference.reference_case_id
            or self.reference_content_fingerprint != reference_content_fingerprint(reference)
        )


def reference_content_fingerprint(reference: GoldenReferenceCase) -> str:
    """A hash of the reference case's own content (not the underlying
    source files -- see check_source_fingerprint_freshness for that).
    Changes whenever the reference JSON itself is edited, so a stored
    EvaluationRun bound to the old content is recognized as stale even if
    review_status/review_notes are the only fields that changed."""
    return hashlib.sha256(reference.model_dump_json().encode("utf-8")).hexdigest()


def check_source_fingerprint_freshness(
    reference: GoldenReferenceCase, *, repo_root: Path = REPO_ROOT
) -> tuple[bool, list[str]]:
    """Recompute each source file's sha256 named in
    reference.source_fingerprint and compare against the recorded value.
    Returns (is_stale, [description per stale/missing file]). Never
    mutates the reference or writes a new fingerprint -- staleness is
    reported, not silently fixed (per this module's explicit instruction
    not to auto-update fingerprints or claim a pass)."""
    stale: list[str] = []
    for relative_path, recorded_hash in _FINGERPRINT_ENTRY.findall(reference.source_fingerprint):
        full_path = repo_root / relative_path
        if not full_path.is_file():
            stale.append(f"{relative_path} (file no longer exists)")
            continue
        actual_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()[:12]
        if actual_hash != recorded_hash:
            stale.append(f"{relative_path} (recorded {recorded_hash}, now {actual_hash})")
    return bool(stale), stale


def is_evaluation_still_fresh(
    evaluation: EvaluationRun, reference: GoldenReferenceCase, *, repo_root: Path = REPO_ROOT
) -> bool:
    """Module 6: re-checks the reference's source-fingerprint freshness
    AGAINST CURRENT source files, independent of whatever
    `source_fingerprint_stale` was recorded when `evaluation` was
    originally computed.

    Why this exists, distinct from `EvaluationRun.is_stale_for`:
    `is_stale_for` only detects a DIFFERENT case/run/reference-content --
    it says nothing about the underlying source DATA FILES changing after
    an evaluation was already computed and cached in session state. A
    cached `EvaluationRun.source_fingerprint_stale` is frozen at the
    moment `evaluate_investigation_result` ran; it does not update itself
    on a later render. Without this check, a caller could keep displaying
    an evaluation's "source fingerprint fresh" state as still current
    after the referenced source files were edited underneath it -- an
    "already stale, but doesn't know it yet" evaluation, which is exactly
    what `EvaluationRun.source_fingerprint_stale` warns about, MEANT to be
    checked live. Callers should call this on every render of an existing
    (not-just-computed) EvaluationRun and treat `False` as "this cached
    result is no longer trustworthy -- clear it and require
    re-evaluation," not merely "show an extra warning."""
    current_stale, current_stale_files = check_source_fingerprint_freshness(reference, repo_root=repo_root)
    return current_stale == evaluation.source_fingerprint_stale and current_stale_files == evaluation.stale_source_files


def _resolve_source(source: str, result: ApplicationResult) -> object:
    """Returns the root object a field_path is resolved against, `None`
    when that source is genuinely unavailable for this run (e.g. no
    EvidencePackage was ever built), or the `_UNSUPPORTED_SOURCE` sentinel
    when `source` names something this evaluator does not implement."""
    if source == "context.models.EvidencePackage":
        return result.evidence_package
    if source == "agents.state.AgentResult":
        return result.agent_result
    return _UNSUPPORTED_SOURCE


def _resolve_field_path(root: object, field_path: str) -> tuple[object, bool]:
    """Traverses `field_path` (dotted attribute access) starting at
    `root`. Returns (value, available). `available` is False whenever the
    path could not be fully traversed -- root itself is None, an
    intermediate attribute is None (so the next getattr is impossible), or
    an attribute genuinely does not exist. `available` is True whenever
    the path resolves all the way through, INCLUDING when the final value
    itself is None -- that is a present null, a real fact about the data,
    never confused with "could not be checked"."""
    value = root
    parts = field_path.split(".")
    for part in parts:
        if value is None:
            return None, False
        if not hasattr(value, part):
            return None, False
        value = getattr(value, part)
    return value, True


class _UnsupportedComparatorError(Exception):
    pass


def _apply_comparator(comparator: str, value: object, expected_value: object) -> tuple[bool, object]:
    """Returns (passed, displayable_actual_value). Raises
    _UnsupportedComparatorError for anything outside the small, explicit
    set this evaluator implements -- never falls through to eval() or a
    generic equality guess."""
    if comparator == "equals":
        return value == expected_value, value
    if comparator == "is_true":
        return value is True, value
    if comparator == "is_false":
        return value is False, value
    if comparator == "is_none":
        return value is None, value
    if comparator == "is_not_none":
        return value is not None, value
    if comparator in ("length_equals", "length_greater_than"):
        try:
            length = len(value)
        except TypeError:
            raise _UnsupportedComparatorError(
                f"comparator {comparator!r} requires a sized value; got {type(value).__name__}"
            ) from None
        if comparator == "length_equals":
            return length == expected_value, length
        return length > expected_value, length
    raise _UnsupportedComparatorError(f"unrecognized comparator: {comparator!r}")


def _evaluate_one_check(spec: DeterministicCheckSpec, result: ApplicationResult) -> tuple[bool, Optional[object], CheckOutcome, str]:
    """Returns (field_available, actual_value_for_display, outcome, reason)."""
    root = _resolve_source(spec.source, result)
    if root is _UNSUPPORTED_SOURCE:
        return False, None, CheckOutcome.NOT_EVALUATED, f"Unsupported/unrecognized source: {spec.source!r}."
    if root is None:
        return (
            False,
            None,
            CheckOutcome.NOT_EVALUATED,
            f"{spec.source} is unavailable for this run (e.g. this stage was never reached).",
        )

    value, available = _resolve_field_path(root, spec.field_path)
    if not available:
        return (
            False,
            None,
            CheckOutcome.NOT_EVALUATED,
            f"Field path {spec.field_path!r} could not be resolved on {spec.source} for this run.",
        )

    try:
        passed, displayable = _apply_comparator(spec.comparator, value, spec.expected_value)
    except _UnsupportedComparatorError as exc:
        return True, value, CheckOutcome.NOT_EVALUATED, str(exc)

    reason = (
        f"{spec.field_path} {spec.comparator} {spec.expected_value!r} -- "
        f"actual value was {displayable!r} ({'passed' if passed else 'FAILED'})."
    )
    return True, displayable, (CheckOutcome.PASSED if passed else CheckOutcome.FAILED), reason


def evaluate_investigation_result(
    result: ApplicationResult,
    reference: GoldenReferenceCase,
    *,
    repo_root: Path = REPO_ROOT,
) -> EvaluationRun:
    """Evaluate one completed run against one golden reference case.

    `result.claim_id` and `reference.claim_id` are never checked against
    each other implicitly here -- the caller (Module 5's UI) is
    responsible for selecting the matching reference for the run being
    evaluated; a mismatched pairing will simply produce checks resolved
    against the wrong claim's actual data, which is a caller error to
    avoid, not something this function silently corrects.
    """
    deterministic_results: list[DeterministicCheckResult] = []
    qualitative_items: list[QualitativeReviewItem] = []

    for finding in reference.required_findings:
        if finding.classification == RequirementClassification.DETERMINISTICALLY_CHECKABLE:
            spec = finding.deterministic_check
            assert spec is not None  # schema guarantees this
            field_available, actual_value, outcome, reason = _evaluate_one_check(spec, result)
            deterministic_results.append(
                DeterministicCheckResult(
                    finding_id=finding.finding_id,
                    statement=finding.statement,
                    source=spec.source,
                    field_path=spec.field_path,
                    comparator=spec.comparator,
                    expected_value=spec.expected_value,
                    field_available=field_available,
                    actual_value=actual_value,
                    outcome=outcome,
                    reason=reason,
                )
            )
        else:
            qualitative_items.append(
                QualitativeReviewItem(
                    finding_id=finding.finding_id,
                    statement=finding.statement,
                    category=finding.category.value,
                    classification=finding.classification.value,
                )
            )

    fingerprint_stale, stale_files = check_source_fingerprint_freshness(reference, repo_root=repo_root)

    overall_status, status_reasons = _compute_overall_status(
        deterministic_results=deterministic_results,
        qualitative_items=qualitative_items,
        reference_review_status=reference.review_status,
        fingerprint_stale=fingerprint_stale,
    )

    return EvaluationRun(
        claim_id=result.claim_id,
        run_id=result.run_id,
        reference_case_id=reference.reference_case_id,
        reference_schema_version=reference.schema_version,
        reference_review_status=reference.review_status,
        reference_content_fingerprint=reference_content_fingerprint(reference),
        source_fingerprint_stale=fingerprint_stale,
        stale_source_files=stale_files,
        deterministic_results=deterministic_results,
        qualitative_items=qualitative_items,
        overall_status=overall_status,
        status_reasons=status_reasons,
        automated_summary=_automated_summary(deterministic_results),
        evaluated_at=datetime.now(timezone.utc),
    )


def _automated_summary(deterministic_results: list[DeterministicCheckResult]) -> str:
    total = len(deterministic_results)
    passed = sum(1 for r in deterministic_results if r.outcome == CheckOutcome.PASSED)
    not_evaluated = sum(1 for r in deterministic_results if r.outcome == CheckOutcome.NOT_EVALUATED)
    summary = f"{passed} of {total} automated checks passed"
    if not_evaluated:
        summary += f" ({not_evaluated} not evaluated)"
    return summary


def _compute_overall_status(
    *,
    deterministic_results: list[DeterministicCheckResult],
    qualitative_items: list[QualitativeReviewItem],
    reference_review_status: ReviewStatus,
    fingerprint_stale: bool,
) -> tuple[OverallEvaluationStatus, list[str]]:
    if any(r.outcome == CheckOutcome.FAILED for r in deterministic_results):
        failed = [r.finding_id for r in deterministic_results if r.outcome == CheckOutcome.FAILED]
        return (
            OverallEvaluationStatus.FAILED,
            [f"Deterministic check(s) failed: {', '.join(failed)}."],
        )

    reasons: list[str] = []
    if any(r.outcome == CheckOutcome.NOT_EVALUATED for r in deterministic_results):
        not_evaluated = [r.finding_id for r in deterministic_results if r.outcome == CheckOutcome.NOT_EVALUATED]
        reasons.append(f"Deterministic check(s) not evaluated: {', '.join(not_evaluated)}.")
    if qualitative_items:
        reasons.append(f"{len(qualitative_items)} qualitative requirement(s) require human review.")
    if reference_review_status == ReviewStatus.PENDING_HUMAN_REVIEW:
        reasons.append("The reference case itself is AI-drafted and still pending human review.")
    elif reference_review_status == ReviewStatus.HUMAN_REVIEWED_NEEDS_REVISION:
        reasons.append("The reference case was human-reviewed and flagged as needing revision.")
    if fingerprint_stale:
        reasons.append(
            "The reference's source fingerprint no longer matches current source files -- it "
            "needs revalidation."
        )

    if reasons:
        return OverallEvaluationStatus.NEEDS_HUMAN_REVIEW, reasons

    return (
        OverallEvaluationStatus.PASSED,
        [
            "All supported deterministic checks passed, no qualitative requirements are pending, "
            "the reference is human_reviewed_accepted, and its source fingerprint is fresh."
        ],
    )
