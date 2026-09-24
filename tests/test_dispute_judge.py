"""Tests for application/dispute_judge_models.py and
application/dispute_judge.py. Fully offline -- FakeDisputeJudgeAdapter and
FakeDisputeBriefAdapter only, never the real SDK.
"""

from __future__ import annotations

import types

import openai
import pytest
from pydantic import ValidationError

from application.dispute_generator import FakeDisputeBriefAdapter
from application.dispute_judge import (
    DisputeJudgeOutputError,
    DisputeJudgeProviderError,
    DisputeJudgeTimeoutError,
    FakeDisputeJudgeAdapter,
    OpenAIDisputeJudgeAdapter,
    run_dispute_judge,
)
from application.dispute_judge_models import (
    DisputeDimensionResult,
    DisputeJudgeFailureCategory,
    DisputeJudgeResult,
    DisputeJudgeStatus,
    RawDisputeJudgeDimensions,
)
from application.dispute_models import DisputeBrief, is_accepted_dispute_draft
from application.dispute_workflow import run_dispute_workflow
from application.judge_models import JudgeVerdict
from application.models import ActionCode, Finding, SuggestedNextStep
from dispute_review.presets import build_demo_presets
from tools.case_context import get_case_context


def _clm_1001_presets():
    return build_demo_presets(get_case_context("CLM-1001"))


def _well_formed_brief(context) -> DisputeBrief:
    real_ref = context.references[0].ref_id
    return DisputeBrief(
        summary="Summary.",
        findings=[Finding(statement="A grounded fact.", evidence_refs=[real_ref])],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.VERIFY_AUTHORIZATION_INFORMATION, rationale="Because.", evidence_refs=[real_ref]
        ),
    )


def _accepted_workflow_result():
    presets = _clm_1001_presets()
    probe = run_dispute_workflow("CLM-1001", presets.matching, adapter=FakeDisputeBriefAdapter())
    brief = _well_formed_brief(probe.generation_context)
    result = run_dispute_workflow("CLM-1001", presets.matching, adapter=FakeDisputeBriefAdapter(response=brief))
    assert is_accepted_dispute_draft(result)
    return result


def _dim(score, verdict, refs=None):
    return DisputeDimensionResult(score=score, verdict=verdict, rationale="r", evidence_refs=refs or [])


def _raw_dims(**overrides):
    defaults = dict(
        evidence_grounding=_dim(4, JudgeVerdict.PASS),
        coverage=_dim(4, JudgeVerdict.PASS),
        uncertainty_and_provenance=_dim(4, JudgeVerdict.PASS),
        authority_boundaries=_dim(4, JudgeVerdict.PASS),
        rationale="overall ok",
    )
    defaults.update(overrides)
    return RawDisputeJudgeDimensions(**defaults)


# --- DisputeDimensionResult: score bounds and coherence -------------------------------------


@pytest.mark.parametrize("score", [0, 6, -1])
def test_dimension_score_out_of_bounds_rejected(score):
    with pytest.raises(ValidationError):
        DisputeDimensionResult(score=score, verdict=JudgeVerdict.PASS, rationale="r")


def test_high_score_cannot_pair_with_fail():
    with pytest.raises(ValidationError):
        DisputeDimensionResult(score=5, verdict=JudgeVerdict.FAIL, rationale="r")
    with pytest.raises(ValidationError):
        DisputeDimensionResult(score=4, verdict=JudgeVerdict.FAIL, rationale="r")


def test_low_score_cannot_pair_with_pass():
    with pytest.raises(ValidationError):
        DisputeDimensionResult(score=1, verdict=JudgeVerdict.PASS, rationale="r")
    with pytest.raises(ValidationError):
        DisputeDimensionResult(score=2, verdict=JudgeVerdict.PASS, rationale="r")


def test_score_3_allows_any_verdict():
    for verdict in (JudgeVerdict.PASS, JudgeVerdict.FAIL, JudgeVerdict.UNCERTAIN):
        DisputeDimensionResult(score=3, verdict=verdict, rationale="r")  # must not raise


def test_uncertain_allowed_at_any_score():
    for score in (1, 2, 3, 4, 5):
        DisputeDimensionResult(score=score, verdict=JudgeVerdict.UNCERTAIN, rationale="r")  # must not raise


# --- DisputeJudgeResult.from_dimensions: Python-computed average/verdict --------------------


def test_overall_score_is_python_computed_mean():
    raw = _raw_dims(
        evidence_grounding=_dim(5, JudgeVerdict.PASS),
        coverage=_dim(4, JudgeVerdict.PASS),
        uncertainty_and_provenance=_dim(3, JudgeVerdict.UNCERTAIN),
        authority_boundaries=_dim(4, JudgeVerdict.PASS),
    )
    result = DisputeJudgeResult.from_dimensions(raw)
    assert result.overall_score == pytest.approx((5 + 4 + 3 + 4) / 4)


def test_overall_result_fail_wins_regardless_of_average():
    raw = _raw_dims(
        evidence_grounding=_dim(5, JudgeVerdict.PASS),
        coverage=_dim(5, JudgeVerdict.PASS),
        uncertainty_and_provenance=_dim(5, JudgeVerdict.PASS),
        authority_boundaries=_dim(1, JudgeVerdict.FAIL),
    )
    result = DisputeJudgeResult.from_dimensions(raw)
    assert result.overall_score == 4.0  # a high average
    assert result.overall_result == JudgeVerdict.FAIL  # but FAIL still wins -- never hidden by the average
    assert result.authority_boundaries.verdict == JudgeVerdict.FAIL  # the failing dimension stays visible


def test_overall_result_uncertain_when_no_fail_but_some_uncertain():
    raw = _raw_dims(uncertainty_and_provenance=_dim(3, JudgeVerdict.UNCERTAIN))
    result = DisputeJudgeResult.from_dimensions(raw)
    assert result.overall_result == JudgeVerdict.UNCERTAIN


def test_overall_result_pass_when_all_pass():
    result = DisputeJudgeResult.from_dimensions(_raw_dims())
    assert result.overall_result == JudgeVerdict.PASS


# --- run_dispute_judge: precondition, single call, statuses --------------------------------


def test_run_dispute_judge_requires_accepted_draft():
    presets = _clm_1001_presets()
    unaccepted = run_dispute_workflow("CLM-1001", presets.matching, adapter=FakeDisputeBriefAdapter())
    # default mocked brief has no evidence_refs -> FAILED validation -> not accepted
    assert not is_accepted_dispute_draft(unaccepted)
    with pytest.raises(ValueError):
        run_dispute_judge(unaccepted, adapter=FakeDisputeJudgeAdapter(response=DisputeJudgeResult.from_dimensions(_raw_dims())))


def test_run_dispute_judge_completes_and_binds_to_workflow_run_id():
    result = _accepted_workflow_result()
    judge_result = DisputeJudgeResult.from_dimensions(_raw_dims())
    adapter = FakeDisputeJudgeAdapter(response=judge_result)
    envelope = run_dispute_judge(result, adapter=adapter)

    assert envelope.judge_status == DisputeJudgeStatus.COMPLETED
    assert envelope.run_id == result.run_id
    assert len(adapter.calls) == 1


def test_two_workflow_runs_get_different_run_ids_for_staleness_binding():
    result_a = _accepted_workflow_result()
    result_b = _accepted_workflow_result()
    assert result_a.run_id != result_b.run_id  # Module 6C rejects a judge envelope whose run_id doesn't match


def test_judge_failure_does_not_alter_the_workflow_result():
    result = _accepted_workflow_result()
    before_brief = result.brief.model_copy(deep=True)
    before_comparison = result.comparison_result.model_copy(deep=True)
    before_validation = result.validation_result.model_copy(deep=True)

    envelope = run_dispute_judge(result, adapter=FakeDisputeJudgeAdapter(raises=DisputeJudgeProviderError("boom")))

    assert envelope.judge_status == DisputeJudgeStatus.FAILED
    assert envelope.judge_failure_category == DisputeJudgeFailureCategory.PROVIDER
    assert envelope.result is None
    # the workflow result object itself is untouched
    assert result.brief == before_brief
    assert result.comparison_result == before_comparison
    assert result.validation_result == before_validation


def test_judge_timeout_failure_category():
    result = _accepted_workflow_result()
    envelope = run_dispute_judge(result, adapter=FakeDisputeJudgeAdapter(raises=DisputeJudgeTimeoutError("timed out")))
    assert envelope.judge_status == DisputeJudgeStatus.FAILED
    assert envelope.judge_failure_category == DisputeJudgeFailureCategory.TIMEOUT


def test_judge_output_parsing_failure_category():
    result = _accepted_workflow_result()
    envelope = run_dispute_judge(result, adapter=FakeDisputeJudgeAdapter(raises=DisputeJudgeOutputError("bad output")))
    assert envelope.judge_status == DisputeJudgeStatus.FAILED
    assert envelope.judge_failure_category == DisputeJudgeFailureCategory.STRUCTURED_PARSING
    assert envelope.result is None  # no invented score


def test_judge_failure_never_invents_a_score():
    # An adapter raising something OUTSIDE the documented exception
    # hierarchy is allowed to propagate -- mirrors
    # application/semantic_judge.py's own scope: only the documented
    # exception types are translated into a FAILED envelope.
    result = _accepted_workflow_result()
    with pytest.raises(RuntimeError):
        run_dispute_judge(result, adapter=FakeDisputeJudgeAdapter(raises=RuntimeError("unexpected")))

    # for every DOCUMENTED failure category, the envelope never carries a
    # fabricated result
    envelope = run_dispute_judge(result, adapter=FakeDisputeJudgeAdapter(raises=DisputeJudgeProviderError("x")))
    assert envelope.result is None


def test_judge_rejects_unknown_reference():
    result = _accepted_workflow_result()
    bad = DisputeJudgeResult.from_dimensions(
        _raw_dims(evidence_grounding=_dim(4, JudgeVerdict.PASS, refs=["claim:DOES-NOT-EXIST"]))
    )
    envelope = run_dispute_judge(result, adapter=FakeDisputeJudgeAdapter(response=bad))
    assert envelope.judge_status == DisputeJudgeStatus.FAILED
    assert envelope.judge_failure_category == DisputeJudgeFailureCategory.UNKNOWN_REFERENCE


def test_judge_accepts_real_references_from_its_context():
    result = _accepted_workflow_result()
    real_ref = result.generation_context.references[0].ref_id
    good = DisputeJudgeResult.from_dimensions(
        _raw_dims(evidence_grounding=_dim(4, JudgeVerdict.PASS, refs=[real_ref]))
    )
    envelope = run_dispute_judge(result, adapter=FakeDisputeJudgeAdapter(response=good))
    assert envelope.judge_status == DisputeJudgeStatus.COMPLETED


def test_judge_rejects_the_exact_live_observed_prior_authorization_fabrication():
    """Regression test for the exact live smoke check finding (see
    docs/DISPUTE_AI_VALIDATION.md): the judge cited the bare missing-evidence
    CATEGORY LABEL 'prior_authorization' as if it were a reference id, even
    though it was never wrapped in brackets and never listed under EVIDENCE
    REFERENCES. This must still be rejected exactly like any other unknown
    reference -- confirms prompts/dispute_judge_prompt.py's strengthened
    citation instructions (v2) did not weaken this check."""
    result = _accepted_workflow_result()
    assert "prior_authorization" not in {r.ref_id for r in result.generation_context.references}
    bad = DisputeJudgeResult.from_dimensions(
        _raw_dims(coverage=_dim(3, JudgeVerdict.UNCERTAIN, refs=["prior_authorization"]))
    )
    envelope = run_dispute_judge(result, adapter=FakeDisputeJudgeAdapter(response=bad))
    assert envelope.judge_status == DisputeJudgeStatus.FAILED
    assert envelope.judge_failure_category == DisputeJudgeFailureCategory.UNKNOWN_REFERENCE
    assert envelope.result is None  # no fabricated score ever surfaced


def test_judge_dimension_with_empty_evidence_refs_for_absence_based_critique_is_accepted():
    """An absence-based critique (e.g. 'coverage is weak because no prior
    authorization record exists to cite') has no real supporting reference
    id -- the schema and the judge prompt both explicitly allow an empty
    evidence_refs list for exactly this case, rather than forcing a
    fabricated citation. Confirms the plumbing accepts it end-to-end."""
    result = _accepted_workflow_result()
    absence_critique = DisputeJudgeResult.from_dimensions(
        _raw_dims(
            coverage=_dim(3, JudgeVerdict.UNCERTAIN, refs=[]),
            rationale="No prior authorization record was retrieved, so coverage of that gap cannot be tied to a specific reference.",
        )
    )
    envelope = run_dispute_judge(result, adapter=FakeDisputeJudgeAdapter(response=absence_critique))
    assert envelope.judge_status == DisputeJudgeStatus.COMPLETED
    assert envelope.result.coverage.evidence_refs == []


def test_judge_unknown_reference_failure_leaves_the_validated_brief_unchanged():
    """The exact combination the live check showed: the judge fabricates a
    reference, UNKNOWN_REFERENCE is raised, and the already-drafted,
    already-validated brief/comparison/validation on `result` must remain
    completely untouched -- the brief stays usable regardless of the judge
    call's outcome."""
    result = _accepted_workflow_result()
    before_brief = result.brief.model_copy(deep=True)
    before_comparison = result.comparison_result.model_copy(deep=True)
    before_validation = result.validation_result.model_copy(deep=True)

    bad = DisputeJudgeResult.from_dimensions(
        _raw_dims(evidence_grounding=_dim(4, JudgeVerdict.PASS, refs=["prior_authorization"]))
    )
    envelope = run_dispute_judge(result, adapter=FakeDisputeJudgeAdapter(response=bad))

    assert envelope.judge_failure_category == DisputeJudgeFailureCategory.UNKNOWN_REFERENCE
    assert result.brief == before_brief
    assert result.comparison_result == before_comparison
    assert result.validation_result == before_validation


# --- zero live OpenAI calls / never runs automatically --------------------------------------


def test_judge_never_runs_automatically_when_workflow_runs():
    """The workflow itself never touches the judge module at all."""
    import application.dispute_workflow as workflow_module

    assert "dispute_judge" not in workflow_module.__file__  # sanity: distinct module
    import ast
    import inspect

    source = inspect.getsource(workflow_module)
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert "application.dispute_judge" not in imported


def test_judge_path_never_constructs_a_real_openai_client(monkeypatch):
    import openai as openai_module

    def _fail(*args, **kwargs):
        raise AssertionError("openai.OpenAI must never be constructed when a fake judge adapter is injected")

    monkeypatch.setattr(openai_module, "OpenAI", _fail)
    result = _accepted_workflow_result()
    envelope = run_dispute_judge(
        result, adapter=FakeDisputeJudgeAdapter(response=DisputeJudgeResult.from_dimensions(_raw_dims()))
    )
    assert envelope.judge_status == DisputeJudgeStatus.COMPLETED


def test_openai_judge_adapter_disables_sdk_automatic_retries(monkeypatch):
    """Module 6D Step 2B: spies on the ACTUAL openai.OpenAI(...)
    constructor call (not just reading the source) to prove max_retries=0
    is genuinely passed through for the judge adapter too -- one
    application-level evaluate() call is not necessarily one network
    attempt if the SDK's own retry logic were left at its default."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")

    captured_kwargs = {}
    good_raw = _raw_dims()

    class _SpyOpenAI:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)
            self.responses = types.SimpleNamespace(
                parse=lambda **k: types.SimpleNamespace(output_parsed=good_raw, status="completed")
            )

    monkeypatch.setattr(openai, "OpenAI", _SpyOpenAI)

    adapter = OpenAIDisputeJudgeAdapter()
    from prompts.investigation_brief_prompt import PromptBundle

    adapter.evaluate(PromptBundle(system_prompt="s", user_prompt="u", prompt_version="v"))

    assert captured_kwargs.get("max_retries") == 0


# --- shared-state preservation (Module 6D Step 2C) -------------------------------------------


def test_run_dispute_judge_does_not_mutate_shared_state():
    """Complements test_dispute_workflow.py's workflow-level check and
    tests/test_dispute_review_ui.py's combined UI-level check: this is the
    focused, BACKEND-level guarantee specifically for run_dispute_judge
    itself (not previously covered at this layer) -- checks in-memory
    DataStore/graph objects, not just source-file hashes."""
    from graph.retriever import get_claim_neighborhood
    from tools.data_store import get_data_store

    store = get_data_store()
    claims_before = {k: v.model_dump() for k, v in store.claims.items()}
    providers_before = {k: v.model_dump() for k, v in store.providers.items()}
    graph_before = get_claim_neighborhood("CLM-1001").model_dump()

    result = _accepted_workflow_result()
    run_dispute_judge(result, adapter=FakeDisputeJudgeAdapter(response=DisputeJudgeResult.from_dimensions(_raw_dims())))

    store_after = get_data_store()
    assert store_after is store
    assert {k: v.model_dump() for k, v in store_after.claims.items()} == claims_before
    assert {k: v.model_dump() for k, v in store_after.providers.items()} == providers_before
    assert get_claim_neighborhood("CLM-1001").model_dump() == graph_before
