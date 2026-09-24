"""Module 6D Step 4: offline plumbing verification for
evals/dispute_judge_qualitative_examples.py.

Confirms two things for each hand-authored, NOT-a-calibration-dataset
example: (1) the deterministic validator reaches the expected PASSED/FAILED
outcome (and, when FAILED, the expected rule), and (2) for every example
that is an ACCEPTED draft, run_dispute_judge correctly threads a
FakeDisputeJudgeAdapter's response carrying the developer's illustrative
judge dimensions through to a bound DisputeJudgeEnvelope -- i.e. the
plumbing, not any live model's judgment, is what is being tested here.
"""

from __future__ import annotations

import pytest

from application.dispute_judge import run_dispute_judge
from application.dispute_judge_models import DisputeJudgeResult, DisputeJudgeStatus
from application.dispute_models import is_accepted_dispute_draft
from application.judge_models import JudgeVerdict
from evals.dispute_judge_qualitative_examples import build_examples


def test_four_examples_are_defined_and_distinct():
    examples = build_examples()
    assert len(examples) == 4
    assert len({e.example_id for e in examples}) == 4


@pytest.mark.parametrize("example_id", ["well_grounded", "invented_policy", "omitted_mismatch", "false_verification_claim"])
def test_deterministic_validation_matches_expectation(example_id):
    example = next(e for e in build_examples() if e.example_id == example_id)
    vr = example.workflow_result.validation_result
    assert vr.status.value == example.expected_deterministic_status
    observed_rules = {issue.rule for issue in vr.issues}
    for expected_rule in example.expected_deterministic_rules:
        assert expected_rule in observed_rules
    if example.expected_deterministic_status == "PASSED":
        assert vr.issues == []


def test_well_grounded_and_omitted_mismatch_and_invented_policy_are_accepted_drafts():
    """The two judge-only-catchable examples (invented_policy,
    omitted_mismatch) deliberately PASS deterministic validation -- that is
    the entire point of including them: a human or LLM judge is the only
    layer that can flag what they get wrong."""
    for example_id in ("well_grounded", "invented_policy", "omitted_mismatch"):
        example = next(e for e in build_examples() if e.example_id == example_id)
        assert is_accepted_dispute_draft(example.workflow_result)


def test_false_verification_claim_is_rejected_before_reaching_the_judge():
    example = next(e for e in build_examples() if e.example_id == "false_verification_claim")
    assert not is_accepted_dispute_draft(example.workflow_result)
    assert example.illustrative_judge_dimensions is None
    with pytest.raises(ValueError):
        run_dispute_judge(
            example.workflow_result,
            adapter=_fake_judge_adapter_returning(None),
        )


def _fake_judge_adapter_returning(result):
    from application.dispute_judge import FakeDisputeJudgeAdapter

    return FakeDisputeJudgeAdapter(response=result)


@pytest.mark.parametrize("example_id", ["well_grounded", "invented_policy", "omitted_mismatch"])
def test_judge_plumbing_carries_illustrative_dimensions_through_to_a_bound_envelope(example_id):
    example = next(e for e in build_examples() if e.example_id == example_id)
    judge_result = DisputeJudgeResult.from_dimensions(example.illustrative_judge_dimensions)
    envelope = run_dispute_judge(example.workflow_result, adapter=_fake_judge_adapter_returning(judge_result))

    assert envelope.judge_status == DisputeJudgeStatus.COMPLETED
    assert envelope.run_id == example.workflow_result.run_id
    assert envelope.result is judge_result


def test_invented_policy_and_omitted_mismatch_illustrative_dimensions_reflect_a_real_concern():
    """Regression guard on the evaluation set's own internal consistency:
    an example claiming to be judge-only-catchable must actually carry a
    non-PASS illustrative verdict somewhere, or the example would be
    vacuous."""
    for example_id in ("invented_policy", "omitted_mismatch"):
        example = next(e for e in build_examples() if e.example_id == example_id)
        dims = example.illustrative_judge_dimensions
        all_verdicts = [
            dims.evidence_grounding.verdict,
            dims.coverage.verdict,
            dims.uncertainty_and_provenance.verdict,
            dims.authority_boundaries.verdict,
        ]
        assert any(v != JudgeVerdict.PASS for v in all_verdicts)
        assert example.expected_qualitative_concerns  # never an empty concerns list


def test_well_grounded_illustrative_dimensions_are_all_pass():
    example = next(e for e in build_examples() if e.example_id == "well_grounded")
    dims = example.illustrative_judge_dimensions
    assert all(
        d.verdict == JudgeVerdict.PASS
        for d in (dims.evidence_grounding, dims.coverage, dims.uncertainty_and_provenance, dims.authority_boundaries)
    )
    assert example.expected_qualitative_concerns == []
