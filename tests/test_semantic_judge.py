"""H6 (OPTIONAL evaluation sidecar) tests, extended by H8 with a 1-5 rubric
score per dimension. Fully offline: every test uses FakeSemanticJudgeAdapter
or a mocked FakeInvestigationBriefAdapter for the underlying investigation --
no live model call anywhere in this file.

Fixtures below are TEST-ONLY constructions representing what a well-formed
("GOOD") and a defective ("BAD") InvestigationBrief look like for
CLM-1001. They are not live model output and are never claimed to be.
Mocked judge scores are likewise TEST-ONLY constructions, never presented
as empirical model performance. Synthetic source data (data/*.json) is
never touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.case_agent import run_case_agent
from application.context_assembler import assemble_context
from application.investigation_service import run_investigation
from application.judge_models import (
    DimensionResult,
    JudgeStatus,
    JudgeVerdict,
    RawJudgeDimensions,
    SemanticJudgeResult,
)
from application.llm_adapter import FakeInvestigationBriefAdapter
from application.models import ActionCode, Finding, InvestigationBrief, SuggestedNextStep
from application.semantic_judge import (
    JudgeOutputError,
    JudgeProviderError,
    JudgeTimeoutError,
    run_semantic_judge,
)
from application.workbench import reset_investigation_state

CLAIM_ID = "CLM-1001"
QUESTION = "Why was this claim denied, and what evidence is available?"
APP_PY_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _real_case1_refs() -> tuple[str, str]:
    """Real, current evidence-reference ids for CLM-1001 -- same discovery
    pattern used in evals/e2e_eval.py, so these test fixtures cite real
    ids rather than guessed ones."""
    agent_result = run_case_agent(CLAIM_ID, QUESTION)
    context = assemble_context(agent_result)
    claim_ref = next(r.ref_id for r in context.references if r.ref_id.startswith("claim:"))
    benefit_ref = next(r.ref_id for r in context.references if r.ref_id.startswith("benefit:"))
    return claim_ref, benefit_ref


def _good_brief() -> InvestigationBrief:
    """A controlled, well-formed brief. Preserves: claim status DENIED,
    denial_reason AUTH_REQUIRED, prior authorization required, no
    authorization record found, and explicit uncertainty that an
    authorization could exist outside the supplied records."""
    claim_ref, benefit_ref = _real_case1_refs()
    return InvestigationBrief(
        summary=(
            "The claim is recorded as DENIED for AUTH_REQUIRED. No authorization record was "
            "found in the available evidence; this absence does not establish that no "
            "authorization ever existed outside these records."
        ),
        findings=[
            Finding(statement="SOURCE FACT: claim recorded DENIED / AUTH_REQUIRED.", evidence_refs=[claim_ref]),
            Finding(statement="SOURCE FACT: benefit requires prior authorization.", evidence_refs=[benefit_ref]),
            Finding(
                statement=(
                    "INTERPRETATION: no prior-authorization record is present in the supplied "
                    "evidence; this does not establish that authorization never existed outside "
                    "these records."
                ),
                evidence_refs=[claim_ref],
            ),
        ],
        missing_or_conflicting_evidence=["No prior-authorization record is on file for this member/service."],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.VERIFY_AUTHORIZATION_INFORMATION,
            rationale="Verify whether a prior-authorization record exists outside the supplied evidence.",
            evidence_refs=[claim_ref],
        ),
    )


def _bad_unsupported_conclusion_brief() -> InvestigationBrief:
    """TEST-ONLY defective fixture: converts an absence of evidence into a
    definitive, unsupported business conclusion. Never claimed to be live
    model output."""
    claim_ref, _ = _real_case1_refs()
    return InvestigationBrief(
        summary="No authorization record exists, therefore the denial was correct.",
        findings=[
            Finding(
                statement="No authorization record exists, therefore the denial was correct.",
                evidence_refs=[claim_ref],
            )
        ],
        missing_or_conflicting_evidence=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.EXPLAIN_RECORDED_STATUS,
            rationale="The denial is correct.",
            evidence_refs=[claim_ref],
        ),
    )


def _run_drafted_result(brief: InvestigationBrief):
    adapter = FakeInvestigationBriefAdapter(response=brief)
    return run_investigation(CLAIM_ID, QUESTION, adapter=adapter)


def _dim(score: int, verdict: JudgeVerdict, rationale: str = "test-fixture rationale") -> DimensionResult:
    return DimensionResult(score=score, verdict=verdict, rationale=rationale)


def _mocked_result(
    *,
    factual_grounding: DimensionResult,
    completeness: DimensionResult,
    uncertainty_preservation: DimensionResult,
    authority_boundary: DimensionResult,
    rationale: str = "test-fixture overall rationale",
    unsupported_claims: list[str] | None = None,
    missing_key_points: list[str] | None = None,
) -> SemanticJudgeResult:
    """Builds a full SemanticJudgeResult via RawJudgeDimensions +
    from_dimensions -- the SAME path OpenAISemanticJudgeAdapter uses in
    production -- so a test fixture's overall_result/overall_score are
    never hand-computed (and potentially miscomputed) separately from the
    real deterministic-computation code."""
    raw = RawJudgeDimensions(
        factual_grounding=factual_grounding,
        completeness=completeness,
        uncertainty_preservation=uncertainty_preservation,
        authority_boundary=authority_boundary,
        rationale=rationale,
        unsupported_claims=unsupported_claims or [],
        missing_key_points=missing_key_points or [],
    )
    return SemanticJudgeResult.from_dimensions(raw)


# --- A. Good grounded brief ---------------------------------------------------------


def test_a_good_brief_judge_structure_accepted():
    result = _run_drafted_result(_good_brief())
    assert result.generation_status.value == "DRAFTED"
    assert result.validation_result.status.value == "PASSED"

    mocked_verdict = _mocked_result(
        factual_grounding=_dim(5, JudgeVerdict.PASS, "All conclusions are grounded in the supplied evidence."),
        completeness=_dim(5, JudgeVerdict.PASS, "Captures all evidence relevant to the question."),
        uncertainty_preservation=_dim(
            5, JudgeVerdict.PASS, "States absence of an authorization record as absence, not certainty."
        ),
        authority_boundary=_dim(5, JudgeVerdict.PASS, "Entirely advisory."),
        rationale="The brief's conclusions are grounded, complete, appropriately uncertain, and advisory-only.",
    )
    from application.semantic_judge import FakeSemanticJudgeAdapter

    fake_judge = FakeSemanticJudgeAdapter(response=mocked_verdict)
    envelope = run_semantic_judge(result, adapter=fake_judge)

    assert envelope.judge_status == JudgeStatus.COMPLETED
    assert envelope.result.overall_result == JudgeVerdict.PASS
    assert envelope.result.overall_score == 5.0
    assert len(fake_judge.calls) == 1
    # Regression test for a real bug found during H6 UI verification: the
    # envelope's run_id must equal the INVESTIGATION's own result.run_id
    # (not a freshly-generated, unrelated uuid) -- app.py's staleness guard
    # (`envelope.run_id != result.run_id`) compares against exactly this,
    # and a mismatch here silently suppresses ALL rendering (success and
    # failure alike). See docs/HUMANA_BUILD_STATUS.md's H6 bugfix entry.
    assert envelope.run_id == result.run_id
    assert envelope.claim_id == CLAIM_ID


# --- B. Unsupported definitive conclusion --------------------------------------------


def test_b_unsupported_conclusion_expected_factual_grounding_fail():
    result = _run_drafted_result(_bad_unsupported_conclusion_brief())

    mocked_verdict = _mocked_result(
        factual_grounding=_dim(2, JudgeVerdict.FAIL, "Asserts the denial was correct without supporting evidence."),
        completeness=_dim(4, JudgeVerdict.PASS, "Evidence coverage is otherwise adequate."),
        uncertainty_preservation=_dim(1, JudgeVerdict.FAIL, "Treats absence of a record as proof."),
        authority_boundary=_dim(5, JudgeVerdict.PASS, "Does not claim adjudication authority."),
        rationale="The brief asserts the denial was correct, which the supplied evidence does not establish.",
        unsupported_claims=["No authorization record exists, therefore the denial was correct."],
    )
    from application.semantic_judge import FakeSemanticJudgeAdapter

    envelope = run_semantic_judge(result, adapter=FakeSemanticJudgeAdapter(response=mocked_verdict))

    assert envelope.judge_status == JudgeStatus.COMPLETED
    assert envelope.result.factual_grounding.verdict == JudgeVerdict.FAIL
    assert envelope.result.factual_grounding.score in (1, 2)
    assert envelope.result.overall_result == JudgeVerdict.FAIL
    assert envelope.result.unsupported_claims


# --- C. Missing important prior-auth evidence -----------------------------------------


def test_c_missing_important_evidence_expected_completeness_fail():
    claim_ref, _ = _real_case1_refs()
    incomplete_brief = InvestigationBrief(
        summary="The claim is recorded as DENIED.",
        findings=[Finding(statement="SOURCE FACT: claim recorded DENIED.", evidence_refs=[claim_ref])],
        missing_or_conflicting_evidence=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.EXPLAIN_RECORDED_STATUS,
            rationale="Explain the recorded status.",
            evidence_refs=[claim_ref],
        ),
    )
    result = _run_drafted_result(incomplete_brief)

    mocked_verdict = _mocked_result(
        factual_grounding=_dim(5, JudgeVerdict.PASS, "What is stated is grounded."),
        completeness=_dim(2, JudgeVerdict.FAIL, "Omits AUTH_REQUIRED denial reason and prior-auth requirement."),
        uncertainty_preservation=_dim(5, JudgeVerdict.PASS, "No overreaching claims present."),
        authority_boundary=_dim(5, JudgeVerdict.PASS, "Purely descriptive."),
        rationale="The brief omits the AUTH_REQUIRED denial reason and the prior-authorization requirement entirely.",
        missing_key_points=["denial_reason_code=AUTH_REQUIRED", "benefit requires prior authorization"],
    )
    from application.semantic_judge import FakeSemanticJudgeAdapter

    envelope = run_semantic_judge(result, adapter=FakeSemanticJudgeAdapter(response=mocked_verdict))

    assert envelope.result.completeness.verdict == JudgeVerdict.FAIL
    assert envelope.result.completeness.score in (1, 2)
    assert envelope.result.missing_key_points


# --- D. Missing uncertainty -------------------------------------------------------------


def test_d_missing_uncertainty_expected_uncertainty_preservation_fail():
    result = _run_drafted_result(_bad_unsupported_conclusion_brief())

    mocked_verdict = _mocked_result(
        factual_grounding=_dim(2, JudgeVerdict.FAIL, "Unsupported conclusion."),
        completeness=_dim(4, JudgeVerdict.PASS, "Coverage otherwise adequate."),
        uncertainty_preservation=_dim(1, JudgeVerdict.FAIL, "Treats absence of a record as proof the denial was correct."),
        authority_boundary=_dim(5, JudgeVerdict.PASS, "Does not claim adjudication authority."),
        rationale="The brief treats an absence of an authorization record as proof the denial was correct.",
        unsupported_claims=["No authorization record exists, therefore the denial was correct."],
    )
    from application.semantic_judge import FakeSemanticJudgeAdapter

    envelope = run_semantic_judge(result, adapter=FakeSemanticJudgeAdapter(response=mocked_verdict))

    assert envelope.result.uncertainty_preservation.verdict == JudgeVerdict.FAIL
    assert envelope.result.uncertainty_preservation.score in (1, 2)


# --- E. Explicit adjudicative recommendation ---------------------------------------------


def test_e_adjudicative_recommendation_expected_authority_boundary_fail():
    claim_ref, _ = _real_case1_refs()
    adjudicative_brief = InvestigationBrief(
        summary="Approve the claim.",
        findings=[Finding(statement="The claim should be approved.", evidence_refs=[claim_ref])],
        missing_or_conflicting_evidence=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.EXPLAIN_RECORDED_STATUS,
            rationale="test",
            evidence_refs=[claim_ref],
        ),
    )
    result = _run_drafted_result(adjudicative_brief)
    # Note: H2's own validator (Rule D) would already reject this via the
    # normal pipeline; this test exercises the JUDGE's independent
    # authority_boundary dimension specifically, via a mocked verdict.

    mocked_verdict = _mocked_result(
        factual_grounding=_dim(5, JudgeVerdict.PASS, "Statement itself is grounded."),
        completeness=_dim(5, JudgeVerdict.PASS, "No material omission."),
        uncertainty_preservation=_dim(5, JudgeVerdict.PASS, "No overreaching uncertainty claims."),
        authority_boundary=_dim(1, JudgeVerdict.FAIL, "Explicitly recommends approving the claim."),
        rationale="The brief recommends approving the claim, which exceeds its advisory role.",
    )
    from application.semantic_judge import FakeSemanticJudgeAdapter

    envelope = run_semantic_judge(result, adapter=FakeSemanticJudgeAdapter(response=mocked_verdict))

    assert envelope.result.authority_boundary.verdict == JudgeVerdict.FAIL
    assert envelope.result.authority_boundary.score in (1, 2)
    assert envelope.result.overall_result == JudgeVerdict.FAIL


# --- F. Malformed judge response -------------------------------------------------------


def test_f_malformed_judge_response_brief_remains_intact():
    from application.semantic_judge import FakeSemanticJudgeAdapter

    result = _run_drafted_result(_good_brief())
    original_brief = result.investigation_brief

    envelope = run_semantic_judge(
        result, adapter=FakeSemanticJudgeAdapter(raises=JudgeOutputError("unparseable judge output"))
    )

    assert envelope.judge_status == JudgeStatus.FAILED
    assert envelope.judge_failure_category.value == "STRUCTURED_PARSING"
    # A FAILED envelope must still carry the investigation's run_id -- app.py's
    # staleness guard renders the FAILED message based on this match too.
    assert envelope.run_id == result.run_id
    # The original ApplicationResult/brief is untouched.
    assert result.investigation_brief is original_brief
    assert result.generation_status.value == "DRAFTED"
    assert result.validation_result.status.value == "PASSED"


# --- G. Judge provider failure -----------------------------------------------------------


def test_g_judge_provider_failure_brief_remains_intact():
    from application.semantic_judge import FakeSemanticJudgeAdapter

    result = _run_drafted_result(_good_brief())
    envelope = run_semantic_judge(
        result, adapter=FakeSemanticJudgeAdapter(raises=JudgeProviderError("simulated outage"))
    )

    assert envelope.judge_status == JudgeStatus.FAILED
    assert envelope.judge_failure_category.value == "PROVIDER"
    assert envelope.run_id == result.run_id
    assert result.investigation_brief is not None
    assert result.validation_result.status.value == "PASSED"


# --- H. Judge timeout ------------------------------------------------------------------


def test_h_judge_timeout_brief_remains_intact():
    from application.semantic_judge import FakeSemanticJudgeAdapter

    result = _run_drafted_result(_good_brief())
    fake_judge = FakeSemanticJudgeAdapter(raises=JudgeTimeoutError("simulated timeout"))
    envelope = run_semantic_judge(result, adapter=fake_judge)

    assert envelope.judge_status == JudgeStatus.FAILED
    assert envelope.judge_failure_category.value == "TIMEOUT"
    assert envelope.run_id == result.run_id
    assert len(fake_judge.calls) == 1  # no retry
    assert result.investigation_brief is not None


# --- I. Case 5 -----------------------------------------------------------------------


def test_i_case5_judge_never_invoked():
    result = run_investigation(
        "CLM-1005", "Why was my specialist visit claim denied?", adapter=FakeInvestigationBriefAdapter()
    )
    assert result.agent_result.status.value == "NEEDS_REVIEW"
    assert result.investigation_brief is None
    assert result.assembled_context is None

    with pytest.raises(ValueError):
        run_semantic_judge(result)


# --- J. Case/question change -------------------------------------------------------------


def test_j_case_change_clears_prior_judge_result():
    state: dict = {"selected_claim_id": "CLM-1001", "judge_result": "stale-judge-result"}
    from application.workbench import on_case_selected

    on_case_selected(state, "CLM-1005")
    assert "judge_result" not in state


def test_j_question_change_clears_prior_judge_result():
    state: dict = {"question_text": "original", "judge_result": "stale-judge-result"}
    from application.workbench import on_question_changed

    on_question_changed(state, "a different question")
    assert "judge_result" not in state


def test_j_reset_investigation_state_clears_judge_result_directly():
    state: dict = {"judge_result": "stale"}
    reset_investigation_state(state)
    assert "judge_result" not in state


# --- K. Existing H0-H5 regression (spot check within this file) --------------------------


def test_k_existing_case1_behavior_unaffected_by_h6_module_presence():
    """Importing/using application.semantic_judge must not change anything
    about the existing H1/H2 generation+validation path."""
    result = _run_drafted_result(_good_brief())
    assert result.agent_result.status.value == "EVIDENCE_SUFFICIENT"
    assert result.generation_status.value == "DRAFTED"
    assert result.validation_result.status.value == "PASSED"
    assert result.evidence_package is not None


# =========================================================================================
# H8 -- 1-5 rubric score tests (spec section 12, items A-M)
# =========================================================================================


# --- H8.A: scores 1-5 all accepted -----------------------------------------------------


def test_h8_a_dimension_result_accepts_scores_1_through_5():
    for score in (1, 2, 3, 4, 5):
        # Pick a verdict that is coherent with every score in range.
        verdict = JudgeVerdict.UNCERTAIN
        dim = _dim(score, verdict)
        assert dim.score == score


# --- H8.B/C: out-of-range scores rejected -----------------------------------------------


def test_h8_b_score_below_1_rejected():
    with pytest.raises(ValidationError):
        DimensionResult(score=0, verdict=JudgeVerdict.FAIL, rationale="x")


def test_h8_c_score_above_5_rejected():
    with pytest.raises(ValidationError):
        DimensionResult(score=6, verdict=JudgeVerdict.PASS, rationale="x")


# --- H8.D: overall_score is a deterministic arithmetic mean -----------------------------


def test_h8_d_overall_score_calculated_deterministically_from_dimensions():
    result = _mocked_result(
        factual_grounding=_dim(5, JudgeVerdict.PASS),
        completeness=_dim(4, JudgeVerdict.PASS),
        uncertainty_preservation=_dim(5, JudgeVerdict.PASS),
        authority_boundary=_dim(5, JudgeVerdict.PASS),
    )
    # (5 + 4 + 5 + 5) / 4 = 4.75 -- the exact worked example from the spec.
    assert result.overall_score == 4.75


def test_h8_d_overall_score_matches_manual_mean_for_mixed_scores():
    result = _mocked_result(
        factual_grounding=_dim(3, JudgeVerdict.UNCERTAIN),
        completeness=_dim(2, JudgeVerdict.FAIL),
        uncertainty_preservation=_dim(4, JudgeVerdict.PASS),
        authority_boundary=_dim(1, JudgeVerdict.FAIL),
    )
    assert result.overall_score == pytest.approx((3 + 2 + 4 + 1) / 4)


# --- H8.E/F/G: overall verdict aggregation rule preserved -------------------------------


def test_h8_e_overall_verdict_fail_if_any_dimension_fail():
    result = _mocked_result(
        factual_grounding=_dim(5, JudgeVerdict.PASS),
        completeness=_dim(5, JudgeVerdict.PASS),
        uncertainty_preservation=_dim(5, JudgeVerdict.PASS),
        authority_boundary=_dim(1, JudgeVerdict.FAIL),
    )
    assert result.overall_result == JudgeVerdict.FAIL


def test_h8_f_overall_verdict_uncertain_if_no_fail_but_uncertain_present():
    result = _mocked_result(
        factual_grounding=_dim(5, JudgeVerdict.PASS),
        completeness=_dim(3, JudgeVerdict.UNCERTAIN),
        uncertainty_preservation=_dim(5, JudgeVerdict.PASS),
        authority_boundary=_dim(4, JudgeVerdict.PASS),
    )
    assert result.overall_result == JudgeVerdict.UNCERTAIN


def test_h8_g_overall_verdict_pass_if_all_dimensions_pass():
    result = _mocked_result(
        factual_grounding=_dim(5, JudgeVerdict.PASS),
        completeness=_dim(4, JudgeVerdict.PASS),
        uncertainty_preservation=_dim(5, JudgeVerdict.PASS),
        authority_boundary=_dim(4, JudgeVerdict.PASS),
    )
    assert result.overall_result == JudgeVerdict.PASS


# --- H8.H: incoherent score/verdict combinations are rejected ---------------------------


def test_h8_h_high_score_with_fail_verdict_rejected():
    with pytest.raises(ValidationError):
        DimensionResult(score=5, verdict=JudgeVerdict.FAIL, rationale="inconsistent")


def test_h8_h_low_score_with_pass_verdict_rejected():
    with pytest.raises(ValidationError):
        DimensionResult(score=1, verdict=JudgeVerdict.PASS, rationale="inconsistent")


def test_h8_h_score_3_permits_any_verdict():
    # Score 3 ("mixed / material weakness") may coherently pair with any
    # of the three verdicts -- no ValidationError for any of them.
    for verdict in (JudgeVerdict.PASS, JudgeVerdict.FAIL, JudgeVerdict.UNCERTAIN):
        dim = _dim(3, verdict)
        assert dim.verdict == verdict


def test_h8_h_real_adapter_maps_incoherent_llm_output_to_structured_parsing_failure(monkeypatch):
    """If a live model ever returned an incoherent score/verdict pair
    (e.g. score=5 + FAIL), the SDK's pydantic construction of
    RawJudgeDimensions would raise ValidationError while building the
    nested DimensionResult -- OpenAISemanticJudgeAdapter.evaluate must
    convert that into JudgeOutputError (-> JudgeFailureCategory.
    STRUCTURED_PARSING), never let it propagate as a raw, unhandled
    exception out of run_semantic_judge."""
    import openai as openai_module

    from application.semantic_judge import OpenAISemanticJudgeAdapter

    class _FakeResponses:
        def parse(self, **kwargs):
            # Simulate the SDK raising while constructing RawJudgeDimensions
            # from JSON containing an incoherent dimension.
            RawJudgeDimensions(
                factual_grounding=DimensionResult(score=5, verdict=JudgeVerdict.FAIL, rationale="bad"),
                completeness=_dim(5, JudgeVerdict.PASS),
                uncertainty_preservation=_dim(5, JudgeVerdict.PASS),
                authority_boundary=_dim(5, JudgeVerdict.PASS),
                rationale="x",
            )

    class _FakeOpenAIClient:
        def __init__(self, *args, **kwargs):
            self.responses = _FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-test-only")
    monkeypatch.setenv("LLM_MODEL", "fake-model-for-test-only")
    monkeypatch.setattr(openai_module, "OpenAI", _FakeOpenAIClient)

    result = _run_drafted_result(_good_brief())
    adapter = OpenAISemanticJudgeAdapter()
    envelope = run_semantic_judge(result, adapter=adapter)

    assert envelope.judge_status == JudgeStatus.FAILED
    assert envelope.judge_failure_category.value == "STRUCTURED_PARSING"
    # The original brief remains untouched.
    assert result.investigation_brief is not None
    assert result.validation_result.status.value == "PASSED"


# --- H8.L: Scenario Lab compatibility ----------------------------------------------------


def test_h8_l_scenario_lab_result_supports_the_same_rubric_scored_judge():
    """H7's Scenario Lab produces an ordinary ApplicationResult through the
    real pipeline -- the H8-scored judge must work identically against it,
    with no scenario_lab.py changes required (frozen per H8 scope)."""
    from application.scenario_lab import ScenarioDraft, build_scenario_records, validate_scenario
    from application.scenario_lab import _temporary_data_overlay  # same-package test access, not a frozen edit

    draft = ScenarioDraft(template="Custom")
    assert validate_scenario(draft).level.value in ("PASS", "WARNING")
    records = build_scenario_records(draft)

    with _temporary_data_overlay(records):
        # First, a placeholder-brief probe run just to discover this
        # scenario claim's own real evidence-reference id (mirrors
        # _real_case1_refs()'s pattern, generalized to a scenario claim).
        probe_result = run_investigation(records.claim.claim_id, draft.question, adapter=FakeInvestigationBriefAdapter())
        claim_ref = next(r.ref_id for r in probe_result.assembled_context.references if r.ref_id.startswith("claim:"))

        scenario_brief = InvestigationBrief(
            summary="The claim is recorded as DENIED.",
            findings=[Finding(statement="SOURCE FACT: claim recorded DENIED.", evidence_refs=[claim_ref])],
            missing_or_conflicting_evidence=[],
            suggested_next_step=SuggestedNextStep(
                action_code=ActionCode.EXPLAIN_RECORDED_STATUS,
                rationale="Explain the recorded status.",
                evidence_refs=[claim_ref],
            ),
        )
        result = run_investigation(
            records.claim.claim_id, draft.question, adapter=FakeInvestigationBriefAdapter(response=scenario_brief)
        )

    assert result.generation_status.value == "DRAFTED"
    assert result.validation_result.status.value == "PASSED"

    mocked_verdict = _mocked_result(
        factual_grounding=_dim(5, JudgeVerdict.PASS),
        completeness=_dim(5, JudgeVerdict.PASS),
        uncertainty_preservation=_dim(5, JudgeVerdict.PASS),
        authority_boundary=_dim(5, JudgeVerdict.PASS),
    )
    from application.semantic_judge import FakeSemanticJudgeAdapter

    envelope = run_semantic_judge(result, adapter=FakeSemanticJudgeAdapter(response=mocked_verdict))
    assert envelope.judge_status == JudgeStatus.COMPLETED
    assert envelope.result.overall_score == 5.0
    assert envelope.run_id == result.run_id


# --- Full-stack UI regression (the exact bug class found during H6 verification) ---------
#
# tests A-K above exercise application/semantic_judge.py directly. This test
# additionally drives the REAL app.py through Streamlit's own AppTest
# framework, with the REAL (unmocked) run_semantic_judge/
# OpenAISemanticJudgeAdapter code path -- only openai.OpenAI's network
# transport is faked, via monkeypatching the SDK client class itself, so
# zero live calls occur. This is the layer where the run_id bug actually
# manifested (a unit test on run_semantic_judge alone could not have caught
# app.py's *rendering* guard silently discarding a correctly-computed
# envelope) -- see docs/HUMANA_BUILD_STATUS.md's H6 bugfix entry. H8 extends
# this to also assert the new score/overall-score UI elements render.


def _install_fake_openai_client(monkeypatch, raw_dimensions=None, raises=None):
    """H8: the fake client now returns a RawJudgeDimensions (the schema
    OpenAISemanticJudgeAdapter actually constrains the model to) -- NOT a
    full SemanticJudgeResult -- since overall_result/overall_score are
    computed by application code from the parsed RawJudgeDimensions, never
    returned by the model itself."""
    import openai as openai_module

    class _FakeParsedResponse:
        status = "completed"

        def __init__(self, parsed):
            self.output_parsed = parsed

    class _FakeResponses:
        def parse(self, **kwargs):
            if raises is not None:
                raise raises
            return _FakeParsedResponse(raw_dimensions)

    class _FakeOpenAIClient:
        def __init__(self, *args, **kwargs):
            self.responses = _FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-ui-test-only")
    monkeypatch.setenv("LLM_MODEL", "fake-model-for-ui-test-only")
    monkeypatch.setattr(openai_module, "OpenAI", _FakeOpenAIClient)


def test_full_stack_ui_renders_completed_judge_result_via_real_code_path(monkeypatch):
    from streamlit.testing.v1 import AppTest

    passing_dimensions = RawJudgeDimensions(
        factual_grounding=_dim(5, JudgeVerdict.PASS, "fake"),
        completeness=_dim(4, JudgeVerdict.PASS, "fake"),
        uncertainty_preservation=_dim(5, JudgeVerdict.PASS, "fake"),
        authority_boundary=_dim(5, JudgeVerdict.PASS, "fake"),
        rationale="fake",
        unsupported_claims=[],
        missing_key_points=[],
    )
    _install_fake_openai_client(monkeypatch, raw_dimensions=passing_dimensions)

    result = _run_drafted_result(_good_brief())

    at = AppTest.from_file(str(APP_PY_PATH))
    at.session_state["selected_claim_id"] = CLAIM_ID
    at.session_state["question_text"] = QUESTION
    at.session_state["investigation_result"] = result
    at.run()

    judge_button = next(b for b in at.button if b.label == "Run AI Semantic Evaluation")
    judge_button.click().run()

    assert not at.exception
    metric_labels = {m.label for m in at.metric}
    assert {
        "Factual Grounding",
        "Completeness",
        "Uncertainty Preservation",
        "Authority Boundary",
        "Overall Semantic Score",
        "Overall Verdict",
    } <= metric_labels

    metric_values = {m.label: m.value for m in at.metric}
    # H8.I: score/5, verdict, overall score, overall verdict all visible.
    assert metric_values["Factual Grounding"] == "5 / 5 — PASS"
    assert metric_values["Completeness"] == "4 / 5 — PASS"
    assert metric_values["Overall Semantic Score"] == "4.75 / 5"
    assert metric_values["Overall Verdict"] == "PASS"

    # The score is never labeled/presented as confidence.
    full_text = " ".join(m.label for m in at.metric) + " ".join(c.value for c in at.caption)
    assert "confidence" not in full_text.lower()


def test_full_stack_ui_renders_failed_judge_result_via_real_code_path(monkeypatch):
    from streamlit.testing.v1 import AppTest

    # The fake client must raise a real openai.OpenAIError subclass (what
    # OpenAISemanticJudgeAdapter's except clause actually catches) --
    # application.semantic_judge.JudgeProviderError itself is what the
    # adapter RAISES, not what the SDK raises, so it isn't used here.
    import openai as openai_module

    _install_fake_openai_client(
        monkeypatch,
        raises=openai_module.APIConnectionError(request=__import__("httpx").Request("POST", "https://example.invalid")),
    )

    result = _run_drafted_result(_good_brief())

    at = AppTest.from_file(str(APP_PY_PATH))
    at.session_state["selected_claim_id"] = CLAIM_ID
    at.session_state["question_text"] = QUESTION
    at.session_state["investigation_result"] = result
    at.run()

    judge_button = next(b for b in at.button if b.label == "Run AI Semantic Evaluation")
    judge_button.click().run()

    assert not at.exception
    error_texts = [e.value for e in at.error]
    assert any("Semantic evaluation failed" in text for text in error_texts)
    # The investigation brief/status metrics remain visible and correct.
    metric_values = {m.label: m.value for m in at.metric}
    assert metric_values["Generation Status"] == "DRAFTED"
    assert metric_values["Validation Status"] == "PASSED"
