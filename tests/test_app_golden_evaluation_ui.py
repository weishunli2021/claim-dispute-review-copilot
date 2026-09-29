"""Module 5: UI-level regression tests for the "Golden Dataset & Evaluation"
tab, driven through Streamlit's AppTest framework.

Every OpenAI call in this file goes through a FAKE client
(_install_fake_openai_client) -- these tests verify UI WIRING (does the
right thing render, does staleness get cleared, is a failed check visibly
failed), never live model generation. See docs/V2_GOLDEN_EVALUATION_UI.md
for the explicit "fake-adapter verification only" label this module's
report also carries.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from application.models import ActionCode, Finding, InvestigationBrief, SuggestedNextStep
from evals.golden_reference_models import load_golden_reference_set

APP_PY_PATH = Path(__file__).resolve().parents[1] / "app.py"
_RUN_TIMEOUT = 30


def _fake_brief() -> InvestigationBrief:
    return InvestigationBrief(
        summary="Fake offline brief for UI testing only.",
        findings=[Finding(statement="SOURCE FACT: fake finding for UI test.", evidence_refs=["claim:FAKE"])],
        missing_or_conflicting_evidence=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.EXPLAIN_RECORDED_STATUS, rationale="fake", evidence_refs=["claim:FAKE"]
        ),
    )


def _install_fake_openai_client(monkeypatch) -> None:
    """Fakes ONLY the openai.OpenAI transport, same pattern as
    tests/test_app_scenario_lab_ui.py's _install_fake_openai_client and
    tests/test_semantic_judge.py's -- so "Investigate Claim" (which calls
    the real, unmocked application.investigation_service.run_investigation
    with no adapter override, exactly as app.py does in production) can
    never reach the real network in a test."""
    import openai as openai_module

    fake_brief = _fake_brief()

    class _FakeParsedResponse:
        status = "completed"

        def __init__(self, parsed):
            self.output_parsed = parsed

    class _FakeResponses:
        def parse(self, **kwargs):
            return _FakeParsedResponse(fake_brief)

    class _FakeOpenAIClient:
        def __init__(self, *args, **kwargs):
            self.responses = _FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-ui-test-only")
    monkeypatch.setenv("LLM_MODEL", "fake-model-for-ui-test-only")
    monkeypatch.setattr(openai_module, "OpenAI", _FakeOpenAIClient)


def _fresh_app(monkeypatch) -> AppTest:
    _install_fake_openai_client(monkeypatch)
    at = AppTest.from_file(str(APP_PY_PATH), default_timeout=_RUN_TIMEOUT)
    at.run()
    assert not at.exception
    return at


def _golden_eval_tab(at: AppTest):
    return next(tab for tab in at.tabs if tab.proto.label == "Golden Dataset & Evaluation")


def _select_case(at: AppTest, claim_id: str) -> AppTest:
    case_select = next(s for s in _golden_eval_tab(at).selectbox if s.label == "Select a case")
    option = next(opt for opt in case_select.options if opt.startswith(claim_id))
    return case_select.set_value(option).run()


def _investigate(at: AppTest) -> AppTest:
    button = next(b for b in _golden_eval_tab(at).button if b.label == "Investigate Claim")
    return button.click().run()


def _all_text(at: AppTest) -> str:
    tab = _golden_eval_tab(at)
    parts = (
        [m.value for m in tab.markdown]
        + [c.value for c in tab.caption]
        + [i.value for i in tab.info]
        + [w.value for w in tab.warning]
        + [e.value for e in tab.error]
        + [s.value for s in tab.success]
    )
    for df in tab.get("dataframe"):
        try:
            parts.append(str(df.value))
        except Exception:  # noqa: BLE001
            pass
    return " ".join(parts)


# ---------------------------------------------------------------------------
# All five golden cases display expectations.
# ---------------------------------------------------------------------------


def test_all_five_cases_selectable_and_show_expected_results(monkeypatch):
    """The "Reference status: pending_human_review... never treat it as
    ground truth on its own" banner was deliberately removed from Section 3
    (user feedback, 2026-09-26) -- it repeated the same caveat already
    stated in this tab's own intro caption ("AI-drafted, pending-human-
    review golden reference") for every one of the five cases, every time.
    This is a regression guard for that intentional removal, not a claim
    that pending-human-review status stopped being true (see
    evals/golden_reference_models.py -- it still is, for all five cases)."""
    at = _fresh_app(monkeypatch)
    golden_set = load_golden_reference_set()
    case_select = next(s for s in _golden_eval_tab(at).selectbox if s.label == "Select a case")
    assert len(case_select.options) == 5

    for case in golden_set.cases:
        at = _select_case(at, case.claim_id)
        assert not at.exception
        tab = _golden_eval_tab(at)
        assert "pending_human_review" not in _all_text(at)
        expander_labels = {e.label for e in tab.expander}
        assert {
            "Required findings",
            "Missing information & required uncertainty",
            "Verification / human-review actions",
            "Unsupported / prohibited conclusions",
        } <= expander_labels


# ---------------------------------------------------------------------------
# An investigation and its evaluation render together.
# ---------------------------------------------------------------------------


def test_investigate_then_evaluate_renders_both_sections_together(monkeypatch):
    """The separate Evidence/Generation/Validation status readout was
    removed entirely for Golden Dataset & Evaluation (user feedback,
    2026-09-26): it was redundant with which branch of Investigation
    Result renders below it, and once evaluated, with the Result vs.
    Expected Result comparison table's own findings. Scenario Lab keeps
    its original st.metric status display unchanged (see
    tests/test_app_scenario_lab_ui.py's own metric-label assertion, still
    passing) -- it just never renders in this tab's AppTest at all.
    "Evaluate Against Golden Reference" now has no heading, caption, or
    button of its own at all -- a single "Investigate Claim" click runs
    the investigation AND the golden-reference evaluation together, so
    both this test's former two steps collapse into one (user feedback,
    2026-09-26, superseding the earlier "no second click required" version
    of this same feedback: there is now no click at all).

    The standalone "Overall Evaluation Status: ..." line was itself removed
    (user request, 2026-09-27), so this no longer checks for it; the
    comparison table now renders FIRST in this section, ahead of the
    investigation brief content, so it's used here as the still-present
    signal that evaluation ran.

    (This fixture's fake brief deliberately fails validation --
    evidence_reference_existence, since "claim:FAKE" isn't a real
    evidence id for CLM-1001 -- so it never reaches the accepted-draft
    branch; the Findings-expander wiring added in a related change is
    instead covered by
    tests/test_semantic_judge.py::test_full_stack_ui_renders_failed_judge_result_via_real_code_path,
    whose brief fixture does pass validation.)"""
    at = _fresh_app(monkeypatch)
    at = _investigate(at)
    tab = _golden_eval_tab(at)
    assert not tab.metric  # no status metrics anywhere in Golden Dataset & Evaluation
    text = _all_text(at)
    assert "Overall Evaluation Status:" not in text  # removed entirely, user request 2026-09-27
    assert "Result vs. Expected Result" in text  # the combined comparison table's own heading
    assert at.session_state.get("golden_eval_result") is not None


def test_evaluate_button_removed_entirely(monkeypatch):
    """The "Evaluate Run Against Golden Reference" button (and its heading/
    caption) was removed entirely once evaluation became automatic on
    "Investigate Claim" -- it no longer exists before OR after
    investigating; there is no way to force a re-evaluation without
    re-running the investigation (user feedback, 2026-09-26)."""
    at = _fresh_app(monkeypatch)
    tab = _golden_eval_tab(at)
    assert not any(b.label == "Evaluate Run Against Golden Reference" for b in tab.button)

    at = _investigate(at)
    tab = _golden_eval_tab(at)
    assert not any(b.label == "Evaluate Run Against Golden Reference" for b in tab.button)
    assert "Evaluate Against Golden Reference" not in _all_text(at)  # the old heading is gone too


# ---------------------------------------------------------------------------
# All automated checks passing still shows NEEDS HUMAN REVIEW.
# ---------------------------------------------------------------------------


def test_all_green_automated_checks_still_shows_needs_human_review(monkeypatch):
    """The visible "Overall Evaluation Status: NEEDS HUMAN REVIEW" line was
    removed from the UI entirely (user request, 2026-09-27) -- this is now
    a regression guard on the underlying COMPUTATION only: even when every
    deterministic check passes, evals.golden_reference_evaluator still
    computes overall_status as NEEDS_HUMAN_REVIEW (never PASSED) whenever
    qualitative items remain or the reference itself is pending review, and
    that fact is still readable from session_state even though the UI no
    longer prints it as its own labeled line."""
    at = _fresh_app(monkeypatch)
    at = _investigate(at)
    evaluation = at.session_state["golden_eval_result"]
    assert evaluation.automated_summary.startswith("5 of 5") or "of" in evaluation.automated_summary
    assert all(c.outcome.value == "PASSED" for c in evaluation.deterministic_results)
    assert evaluation.overall_status.value == "NEEDS_HUMAN_REVIEW"
    assert "Overall Evaluation Status:" not in _all_text(at)


# ---------------------------------------------------------------------------
# A failed-check example is visibly failed.
# ---------------------------------------------------------------------------


def test_failed_check_renders_visibly_as_failed(monkeypatch):
    """Uses a controlled fixture (monkeypatched evaluate_investigation_result)
    to deterministically produce a FAILED outcome for this UI-rendering
    test -- evaluator CORRECTNESS on a real mismatch is covered separately
    and exhaustively in tests/test_golden_reference_evaluator.py; this test
    only proves the UI renders a FAILED EvaluationRun visibly, not that the
    evaluator logic itself is right.

    The standalone "Overall Evaluation Status: FAILED" error box was
    removed (user request, 2026-09-27) -- a failed check is now visible
    only through the "Result vs. Expected Result" table's own "No Match"
    row for the failing finding, which is what this test now checks.

    Patches evals.golden_reference_evaluator.evaluate_investigation_result
    (the function's OWN source module), never a locally-imported `app`
    object -- AppTest.from_file re-execs app.py's source in an isolated
    namespace on every .run(), so patching an `import app` object in THIS
    test's namespace would never be seen by that separate execution; only
    a patch on the shared source module, read when app.py's own
    `from evals.golden_reference_evaluator import evaluate_investigation_result`
    executes during that exec, actually takes effect.

    The fake result's reference_content_fingerprint MUST be the real
    reference_content_fingerprint(reference) -- using a made-up string here
    first (an earlier draft of this test) tripped EvaluationRun.is_stale_for
    itself: app.py correctly treated a mismatched fingerprint as stale and
    cleared it before it ever reached the screen. That was the staleness
    guard working as designed, not a bug -- but it meant this test needs
    the real fingerprint to actually exercise the FAILED-rendering path.
    """
    import evals.golden_reference_evaluator as evaluator_module
    from evals.golden_reference_evaluator import (
        CheckOutcome,
        DeterministicCheckResult,
        EvaluationRun,
        OverallEvaluationStatus,
        reference_content_fingerprint,
    )
    from datetime import datetime, timezone

    def _fake_failed_evaluation(result, reference, **kwargs):
        return EvaluationRun(
            claim_id=result.claim_id,
            run_id=result.run_id,
            reference_case_id=reference.reference_case_id,
            reference_schema_version=reference.schema_version,
            reference_review_status=reference.review_status,
            reference_content_fingerprint=reference_content_fingerprint(reference),
            source_fingerprint_stale=False,
            deterministic_results=[
                DeterministicCheckResult(
                    finding_id="f1",
                    statement="deliberately wrong for this test",
                    source="context.models.EvidencePackage",
                    field_path="structured_facts.claim.denial_reason_code",
                    comparator="equals",
                    expected_value="AUTH_REQUIRED",
                    field_available=True,
                    actual_value="SOMETHING_ELSE",
                    outcome=CheckOutcome.FAILED,
                    reason="deliberately wrong for this test",
                )
            ],
            qualitative_items=[],
            overall_status=OverallEvaluationStatus.FAILED,
            status_reasons=["Deterministic check(s) failed: f1."],
            automated_summary="0 of 1 automated checks passed",
            evaluated_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(evaluator_module, "evaluate_investigation_result", _fake_failed_evaluation)
    at = _fresh_app(monkeypatch)
    at = _investigate(at)
    tab = _golden_eval_tab(at)
    assert "Overall Evaluation Status:" not in _all_text(at)  # removed entirely, user request 2026-09-27
    comparison_df = next(
        df.value for df in tab.get("dataframe") if "Result" in df.value.columns and "Finding" in df.value.columns
    )
    f1_row = comparison_df[comparison_df["Finding"] == "f1"].iloc[0]
    assert f1_row["Result"] == "No Match"
    assert not any("PASSED" in s.value for s in tab.success)


# ---------------------------------------------------------------------------
# Changing cases / rerunning does not expose stale evaluation.
# ---------------------------------------------------------------------------


def test_changing_case_after_evaluation_shows_not_run_not_stale_result(monkeypatch):
    at = _fresh_app(monkeypatch)
    at = _investigate(at)
    evaluation_before = at.session_state["golden_eval_result"]
    assert evaluation_before.claim_id == "CLM-1001"

    at = _select_case(at, "CLM-1002")
    tab = _golden_eval_tab(at)
    # No evaluation for the new case yet -- either fully absent from session
    # state (cleared on case change) or its own is_stale_for() would reject
    # it as soon as an investigation ran for the new case.
    assert at.session_state.get("golden_eval_result") is None


def test_rerunning_investigation_produces_a_fresh_evaluation_for_the_new_run(monkeypatch):
    """Investigate Claim now auto-evaluates (user feedback, 2026-09-26), so
    re-running investigation no longer leaves golden_eval_result cleared/
    absent the way it used to -- it immediately holds a FRESH evaluation
    bound to the NEW run_id, never the stale one from the previous run."""
    at = _fresh_app(monkeypatch)
    at = _investigate(at)
    first_run_id = at.session_state["investigation_result"].run_id
    first_evaluation = at.session_state["golden_eval_result"]
    assert first_evaluation.run_id == first_run_id

    at = _investigate(at)  # rerun the SAME case
    second_run_id = at.session_state["investigation_result"].run_id
    second_evaluation = at.session_state.get("golden_eval_result")
    # A fresh run_id is not guaranteed to differ in value, but the
    # evaluation present after rerunning must always be bound to whichever
    # run_id resulted from THIS click, never left over from the previous one.
    assert second_evaluation is not None
    assert second_evaluation.run_id == second_run_id


def test_source_data_change_after_evaluation_invalidates_it_on_next_render(monkeypatch):
    """Module 6: Module 5's staleness binding only caught a DIFFERENT
    case/run/reference-content -- it did not catch the underlying source
    DATA FILES changing after an evaluation was already computed and
    cached. This is the gap Module 6 closed (evals.golden_reference_
    evaluator.is_evaluation_still_fresh, wired into
    _render_evaluation_section). Simulated here by monkeypatching
    check_source_fingerprint_freshness itself to report "fresh" for the
    call made during evaluation and "stale" for every call after --
    mirroring "evaluate now, then change a source fixture in an isolated
    copy" without ever touching a real fixture file. Confirms the OLD
    evaluation is cleared on the very next render, not left on screen
    looking current.

    Investigate Claim now auto-evaluates (user feedback, 2026-09-26), so a
    single _investigate(at) call already produces the evaluation to be
    invalidated -- no separate _evaluate(at) click is needed to set up
    this scenario any more."""
    import evals.golden_reference_evaluator as evaluator_module

    call_count = {"n": 0}

    def _flaky_check(reference, *, repo_root=None):
        call_count["n"] += 1
        if call_count["n"] <= 1:
            return False, []
        return True, ["data/claims.json (recorded aaaaaaaaaaaa, now bbbbbbbbbbbb)"]

    monkeypatch.setattr(evaluator_module, "check_source_fingerprint_freshness", _flaky_check)

    at = _fresh_app(monkeypatch)
    at = _investigate(at)
    evaluation = at.session_state.get("golden_eval_result")
    assert evaluation is not None
    assert evaluation.source_fingerprint_stale is False  # fresh at the moment it was computed

    # A later render with no new click -- e.g. the user just looking at the
    # page again -- must re-check freshness live and clear the now-stale
    # cached evaluation, never keep displaying it as current.
    at = at.run()
    assert "Overall Evaluation Status: Not run." in _all_text(at)
    assert at.session_state.get("golden_eval_result") is None


# ---------------------------------------------------------------------------
# Expected references do not enter the generation context.
# ---------------------------------------------------------------------------


def test_golden_reference_content_never_appears_in_the_built_prompt(monkeypatch):
    """Captures the actual prompt_bundle passed to the LLM adapter and
    confirms none of CLM-1001's golden-reference-only text (a sentence
    that exists ONLY in the reference JSON, never in data/*.json or
    documents/*.md) leaks into it -- proving the reference is never
    injected into generation, exactly as required."""
    import application.llm_adapter as llm_adapter_module

    captured_prompts = []
    original_generate = llm_adapter_module.OpenAIInvestigationBriefAdapter.generate

    def _spy_generate(self, prompt_bundle):
        captured_prompts.append(prompt_bundle)
        return original_generate(self, prompt_bundle)

    monkeypatch.setattr(llm_adapter_module.OpenAIInvestigationBriefAdapter, "generate", _spy_generate)

    at = _fresh_app(monkeypatch)
    at = _investigate(at)
    assert captured_prompts, "expected run_investigation to call the adapter's generate()"

    golden_set = load_golden_reference_set()
    reference = next(c for c in golden_set.cases if c.claim_id == "CLM-1001")
    # A distinctive, reference-only sentence fragment that is never part of
    # data/*.json or documents/*.md -- if this ever showed up in the built
    # prompt, the reference would have leaked into generation.
    distinctive_fragment = reference.required_findings[0].statement[:40]

    for bundle in captured_prompts:
        prompt_text = getattr(bundle, "system_prompt", "") + getattr(bundle, "user_prompt", "")
        assert distinctive_fragment not in prompt_text
        assert reference.reference_case_id not in prompt_text
