"""Module 6D Step 4: a small, DEVELOPER-AUTHORED synthetic judge
evaluation set for the dispute-review judge (application/dispute_judge.py).

*** THIS IS EXPLICITLY NOT A CALIBRATION DATASET. ***

Every brief and every "expected qualitative concern" below was written by
hand by a developer, for exactly one purpose: to verify -- fully offline,
with zero live model calls -- that the JUDGE PLUMBING (DisputeWorkflowResult
-> run_dispute_judge -> DisputeJudgeEnvelope, plus the deterministic
validator that gates the judge) actually carries a judged result of the
expected shape end to end for each illustrative scenario. It is NOT:
  - a measurement of how well a real LLM judge performs (no live model is
    called anywhere in this module);
  - a rubric-tuning/calibration set (the "illustrative_judge_dimensions"
    below are a developer's hand-picked guess at what a competent judge
    SHOULD say, not an observed or validated model output);
  - a substitute for Module 6D Step 5's live smoke check, which is the only
    place this project ever exercises a real judge call.

Four illustrative briefs, each built against REAL evidence retrieved for
CLM-1001 (real reference ids, real comparison results -- never invented
data), covering:
  1. well_grounded            -- a brief with no material defects.
  2. invented_policy           -- a brief that states an unsupported
                                   "provider-matching policy" as if it were
                                   an established rule, citing only a real
                                   comparison reference that does not
                                   actually establish any such policy.
  3. omitted_mismatch          -- a brief that never mentions a REAL
                                   servicing-provider mismatch the
                                   deterministic comparator found.
  4. false_verification_claim  -- a brief that describes unverified,
                                   provider-supplied evidence as "confirmed"
                                   and claims the claim's denial has been
                                   "reversed" -- the one example that IS
                                   caught deterministically (Rule E), which
                                   is itself the point: it never reaches the
                                   judge at all, mirroring the real
                                   is_accepted_dispute_draft gate.

Examples 2 and 3 are the deliberately interesting cases: both PASS
deterministic validation (application/dispute_brief_validator.py's own
docstring names both limitations explicitly -- Rule A only checks that a
cited reference id EXISTS, never that it supports the claim, and no rule
inspects what a brief chose not to mention) while still describing a real
defect that only a semantic judge (or a human reviewer) could catch. That
is the architectural point this evaluation set exists to illustrate and
regression-guard: the deterministic validator and the LLM judge are not
redundant, they cover disjoint failure classes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from application.dispute_brief_validator import validate_dispute_brief
from application.dispute_generator import FakeDisputeBriefAdapter
from application.dispute_judge_models import (
    DisputeDimensionResult,
    RawDisputeJudgeDimensions,
)
from application.dispute_models import DisputeBrief, DisputeWorkflowResult, is_accepted_dispute_draft
from application.dispute_workflow import run_dispute_workflow
from application.judge_models import JudgeVerdict
from application.models import ActionCode, Finding, SuggestedNextStep
from dispute_review.presets import build_demo_presets
from tools.case_context import get_case_context

_CLAIM_ID = "CLM-1001"


def _presets():
    return build_demo_presets(get_case_context(_CLAIM_ID))


def _probe(submission) -> DisputeWorkflowResult:
    """One workflow run with a placeholder adapter, used only to retrieve
    the REAL DisputeGenerationContext/comparison_result this submission
    produces -- no live model call (FakeDisputeBriefAdapter's default
    response is never inspected here, only the context it was built
    against)."""
    return run_dispute_workflow(_CLAIM_ID, submission, adapter=FakeDisputeBriefAdapter())


def _dim(score: int, verdict: JudgeVerdict, rationale: str, refs: Optional[list[str]] = None) -> DisputeDimensionResult:
    return DisputeDimensionResult(score=score, verdict=verdict, rationale=rationale, evidence_refs=refs or [])


@dataclass(frozen=True)
class QualitativeJudgeExample:
    example_id: str
    description: str
    workflow_result: DisputeWorkflowResult
    """The workflow re-run with THIS example's hand-authored brief injected
    via FakeDisputeBriefAdapter, then run through the real
    validate_dispute_brief path exactly as production does."""
    expected_deterministic_status: str  # "PASSED" or "FAILED"
    expected_deterministic_rules: list[str]
    """Rule names (application/dispute_brief_validator.py rule identifiers)
    expected among validation_result.issues -- empty when
    expected_deterministic_status == "PASSED"."""
    expected_qualitative_concerns: list[str]
    """Hand-written, human-readable concerns a competent reviewer (human or
    LLM judge) should raise. Never checked by the deterministic validator;
    documented here as the reason a judge/human layer is needed at all."""
    illustrative_judge_dimensions: Optional[RawDisputeJudgeDimensions]
    """A developer's hand-picked guess at what dimensions/scores a
    competent judge SHOULD assign, used only to exercise
    run_dispute_judge's plumbing via FakeDisputeJudgeAdapter. None for the
    one example (false_verification_claim) that never reaches the judge
    because deterministic validation already rejected the draft."""


def _well_grounded_example() -> QualitativeJudgeExample:
    submission = _presets().matching
    probe = _probe(submission)
    refs = {r.ref_id for r in probe.generation_context.references}
    assert {"comparison:member", "comparison:service", "comparison:validity_dates", "comparison:servicing_provider", "submitted:authorization_reference_number"} <= refs

    brief = DisputeBrief(
        summary="All four submitted fields match the recorded claim; the authorization reference itself remains unverified.",
        findings=[
            Finding(
                statement=(
                    "The submitted member id, service code, validity window, and servicing "
                    "provider all match the recorded claim."
                ),
                evidence_refs=[
                    "comparison:member",
                    "comparison:service",
                    "comparison:validity_dates",
                    "comparison:servicing_provider",
                ],
            ),
            Finding(
                statement=(
                    "The submitted authorization reference number requires confirmation from an "
                    "authoritative source before it can be relied upon."
                ),
                evidence_refs=["submitted:authorization_reference_number"],
            ),
        ],
        missing_or_conflicting_evidence=[
            "No prior authorization record in the synthetic dataset was cross-checked against this reference number."
        ],
        verification_questions=[
            "Can the submitted authorization reference be confirmed with an authoritative source?"
        ],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.VERIFY_AUTHORIZATION_INFORMATION,
            rationale="Matching fields do not establish authenticity; verification is still required before any dispute decision.",
            evidence_refs=["comparison:member"],
        ),
    )
    result = run_dispute_workflow(_CLAIM_ID, submission, adapter=FakeDisputeBriefAdapter(response=brief))

    dims = RawDisputeJudgeDimensions(
        evidence_grounding=_dim(5, JudgeVerdict.PASS, "Every finding cites a real, applicable reference."),
        coverage=_dim(5, JudgeVerdict.PASS, "All four comparable fields and the unverified reference are addressed."),
        uncertainty_and_provenance=_dim(5, JudgeVerdict.PASS, "Unverified provenance is stated plainly, never overclaimed."),
        authority_boundaries=_dim(5, JudgeVerdict.PASS, "No approval/denial/reversal language anywhere."),
        rationale="Well-grounded, appropriately scoped brief with no material defects.",
    )
    return QualitativeJudgeExample(
        example_id="well_grounded",
        description="A correctly grounded, appropriately hedged brief with no material defects.",
        workflow_result=result,
        expected_deterministic_status="PASSED",
        expected_deterministic_rules=[],
        expected_qualitative_concerns=[],
        illustrative_judge_dimensions=dims,
    )


def _invented_policy_example() -> QualitativeJudgeExample:
    submission = _presets().matching
    probe = _probe(submission)
    refs = {r.ref_id for r in probe.generation_context.references}
    assert "comparison:servicing_provider" in refs

    brief = DisputeBrief(
        summary="The submission satisfies Humana's provider-matching policy, so the authorization is valid.",
        findings=[
            Finding(
                statement=(
                    "Per Humana's provider-matching policy, an authorization is automatically "
                    "valid whenever the submitted servicing provider matches the claim's "
                    "servicing provider, as it does here."
                ),
                evidence_refs=["comparison:servicing_provider"],
            ),
        ],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.EXPLAIN_RECORDED_STATUS,
            rationale="The matching servicing provider satisfies the policy requirement.",
            evidence_refs=["comparison:servicing_provider"],
        ),
    )
    result = run_dispute_workflow(_CLAIM_ID, submission, adapter=FakeDisputeBriefAdapter(response=brief))

    dims = RawDisputeJudgeDimensions(
        evidence_grounding=_dim(
            2,
            JudgeVerdict.FAIL,
            "Cites a real comparison reference, but the reference only reports a field match -- "
            "it does not establish or even mention any 'provider-matching policy'. The named "
            "policy is not present anywhere in the retrieved evidence.",
            refs=["comparison:servicing_provider"],
        ),
        coverage=_dim(3, JudgeVerdict.UNCERTAIN, "Addresses the provider field but omits the unverified-authorization caveat entirely."),
        uncertainty_and_provenance=_dim(2, JudgeVerdict.FAIL, "States the authorization 'is valid' with no hedge; matching fields do not establish authenticity or applicability."),
        authority_boundaries=_dim(3, JudgeVerdict.UNCERTAIN, "Does not use a prohibited approval/denial verb, but functionally asserts a coverage-determining rule the evidence never supplied."),
        rationale=(
            "The brief invents a specific named policy ('Humana's provider-matching policy') "
            "that is not supported by any retrieved evidence reference -- only a comparison "
            "finding (a field match, not a policy statement) is cited. This is the classic "
            "evidence-grounding failure a keyword/reference-existence check cannot catch: the "
            "cited reference id is real, but it does not say what the brief claims it says."
        ),
        unsupported_claims=["Existence and content of a 'provider-matching policy' requiring exact servicing-provider match."],
        missing_key_points=["The submitted authorization reference itself remains unverified."],
    )
    return QualitativeJudgeExample(
        example_id="invented_policy",
        description="Cites a real reference but attributes to it a specific policy rule the evidence never actually established.",
        workflow_result=result,
        expected_deterministic_status="PASSED",
        expected_deterministic_rules=[],
        expected_qualitative_concerns=[
            "Asserts a named 'provider-matching policy' with no retrieved policy passage or other evidence establishing that such a rule exists.",
            "States the authorization 'is valid' -- an unhedged coverage/authenticity conclusion the evidence does not support.",
        ],
        illustrative_judge_dimensions=dims,
    )


def _omitted_mismatch_example() -> QualitativeJudgeExample:
    case_context = get_case_context(_CLAIM_ID)
    presets = build_demo_presets(case_context)
    submission = presets.different_servicing_provider
    probe = _probe(submission)
    mismatch_row = next(r for r in probe.comparison_result.rows if r.field == "Servicing Provider")
    assert mismatch_row.status.value != "Match"
    refs = {r.ref_id for r in probe.generation_context.references}
    assert {"comparison:member", "comparison:service", "comparison:validity_dates"} <= refs

    brief = DisputeBrief(
        summary="The submitted member, service, and validity window all match the recorded claim.",
        findings=[
            Finding(
                statement="The submitted member id, service code, and authorization validity window match the recorded claim.",
                evidence_refs=["comparison:member", "comparison:service", "comparison:validity_dates"],
            ),
        ],
        missing_or_conflicting_evidence=[],
        verification_questions=[
            "Can the submitted authorization reference be confirmed with an authoritative source?"
        ],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.VERIFY_AUTHORIZATION_INFORMATION,
            rationale="Verification of the authorization reference is still required.",
            evidence_refs=["comparison:member"],
        ),
    )
    result = run_dispute_workflow(_CLAIM_ID, submission, adapter=FakeDisputeBriefAdapter(response=brief))

    dims = RawDisputeJudgeDimensions(
        evidence_grounding=_dim(4, JudgeVerdict.PASS, "Every finding cites a real, applicable reference."),
        coverage=_dim(1, JudgeVerdict.FAIL, "Omits the deterministic comparator's servicing-provider MISMATCH entirely -- a material discrepancy central to this dispute is never mentioned."),
        uncertainty_and_provenance=_dim(4, JudgeVerdict.PASS, "What it does state is appropriately hedged."),
        authority_boundaries=_dim(5, JudgeVerdict.PASS, "No approval/denial/reversal language anywhere."),
        rationale=(
            "The deterministic comparator found a real servicing-provider mismatch (the "
            "submission names a different facility than the claim's recorded servicing "
            "provider), but the brief's summary and findings discuss only the three matching "
            "fields and never mention it. A reader relying on this brief alone would "
            "reasonably but incorrectly conclude the submission fully matches the claim. No "
            "reference-existence check can catch this: nothing here cites a fabricated "
            "reference, the brief simply never engages with a real one."
        ),
        unsupported_claims=[],
        missing_key_points=["The deterministic comparison found a servicing-provider MISMATCH; the brief does not mention it."],
    )
    return QualitativeJudgeExample(
        example_id="omitted_mismatch",
        description="Only discusses matching fields; silently omits a real, material servicing-provider mismatch the comparator found.",
        workflow_result=result,
        expected_deterministic_status="PASSED",
        expected_deterministic_rules=[],
        expected_qualitative_concerns=[
            "Never mentions the servicing-provider MISMATCH the deterministic comparison actually found -- a material omission, not merely an unhedged claim.",
        ],
        illustrative_judge_dimensions=dims,
    )


def _false_verification_claim_example() -> QualitativeJudgeExample:
    submission = _presets().matching
    probe = _probe(submission)
    refs = {r.ref_id for r in probe.generation_context.references}
    assert "submitted:authorization_reference_number" in refs

    brief = DisputeBrief(
        summary="The submitted authorization has been confirmed and the claim's denial has been reversed.",
        findings=[
            Finding(
                statement=(
                    "The submitted authorization reference has been confirmed, and the claim's "
                    "denial has been reversed based on this verified authorization."
                ),
                evidence_refs=["submitted:authorization_reference_number"],
            ),
        ],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.EXPLAIN_RECORDED_STATUS,
            rationale="The authorization has already been confirmed.",
            evidence_refs=["submitted:authorization_reference_number"],
        ),
    )
    result = run_dispute_workflow(_CLAIM_ID, submission, adapter=FakeDisputeBriefAdapter(response=brief))

    return QualitativeJudgeExample(
        example_id="false_verification_claim",
        description=(
            "Describes provider-supplied, unverified evidence as 'confirmed' and claims the "
            "recorded denial has already been 'reversed' -- neither is true of anything in the "
            "evidence package."
        ),
        workflow_result=result,
        expected_deterministic_status="FAILED",
        expected_deterministic_rules=["unverified_provenance_preserved"],
        expected_qualitative_concerns=[
            "Claims the submitted authorization has been 'confirmed' while citing only "
            "SUBMITTED_UNVERIFIED evidence -- exactly what Rule E exists to catch, so this "
            "example never reaches the judge at all (is_accepted_dispute_draft rejects it "
            "first, mirroring production).",
            "Separately claims the claim's denial has 'already been reversed' -- a fabricated, "
            "consequential outcome no evidence in this package supports; would independently "
            "warrant an authority_boundaries FAIL from a judge if this brief ever did reach one.",
        ],
        illustrative_judge_dimensions=None,
    )


def build_examples() -> list[QualitativeJudgeExample]:
    """All four illustrative examples, built against real CLM-1001
    evidence. Order matches this module's docstring."""
    return [
        _well_grounded_example(),
        _invented_policy_example(),
        _omitted_mismatch_example(),
        _false_verification_claim_example(),
    ]


def _print_report() -> None:  # pragma: no cover -- manual/developer use only
    print(__doc__)
    for example in build_examples():
        vr = example.workflow_result.validation_result
        accepted = is_accepted_dispute_draft(example.workflow_result)
        print(f"\n=== {example.example_id} ===")
        print(example.description)
        print(f"deterministic validation: {vr.status.value} (expected {example.expected_deterministic_status})")
        for issue in vr.issues:
            print(f"  - [{issue.rule}] {issue.detail}")
        print(f"accepted draft (judge-eligible): {accepted}")
        print("expected qualitative concerns (developer-authored, not judge output):")
        for concern in example.expected_qualitative_concerns:
            print(f"  - {concern}")


if __name__ == "__main__":  # pragma: no cover
    _print_report()
