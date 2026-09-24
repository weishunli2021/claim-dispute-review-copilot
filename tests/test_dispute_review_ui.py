"""H4/Module 4 UI-level regression tests: drive the REAL app.py through
Streamlit's AppTest framework (no live network call anywhere in this file
-- same pattern as tests/test_app_scenario_lab_ui.py's
_install_fake_openai_client, duplicated here so this file has no import
dependency on another test module).

These tests exercise the Dispute Review tab exactly as a user would:
clicking preset buttons, editing text inputs, and clicking "Compare
submitted information" -- never by calling dispute_review internals
directly (behavior assertions over source-string checks, per Module 4's
own instructions).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from application.dispute_judge_models import DisputeDimensionResult, RawDisputeJudgeDimensions
from application.dispute_models import DisputeBrief
from application.judge_models import JudgeVerdict
from application.models import ActionCode, Finding, InvestigationBrief, SuggestedNextStep
from dispute_review.models import ComparisonStatus

APP_PY_PATH = Path(__file__).resolve().parents[1] / "app.py"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_RUN_TIMEOUT = 30  # local embedding-model loading + graph build can be slow on first use in-process

# Module 6C fixtures: a well-formed DisputeBrief and a well-formed judge
# result, each citing "claim:CLM-1001" -- a reference guaranteed present in
# every preset's generation context (the claim record itself), so these
# fixtures work regardless of which preset a given test loads first.
FAKE_DISPUTE_BRIEF = DisputeBrief(
    summary="Fake offline dispute brief for UI testing only.",
    findings=[Finding(statement="SOURCE FACT: fake finding for UI test.", evidence_refs=["claim:CLM-1001"])],
    missing_or_conflicting_evidence=["Fake missing-evidence item for UI test."],
    verification_questions=["Fake verification question for UI test."],
    suggested_next_step=SuggestedNextStep(
        action_code=ActionCode.VERIFY_AUTHORIZATION_INFORMATION, rationale="fake", evidence_refs=["claim:CLM-1001"]
    ),
)

# Deliberately mixed: three PASS dimensions (one at 5, two at 4-5) plus one
# FAIL at score 1 -- gives a moderately high average (3.75) while
# overall_result must still be FAIL and the failing dimension must still be
# visibly rendered (Step 7 test #8).
FAKE_JUDGE_RAW = RawDisputeJudgeDimensions(
    evidence_grounding=DisputeDimensionResult(
        score=5, verdict=JudgeVerdict.PASS, rationale="fake", evidence_refs=["claim:CLM-1001"]
    ),
    coverage=DisputeDimensionResult(score=5, verdict=JudgeVerdict.PASS, rationale="fake"),
    uncertainty_and_provenance=DisputeDimensionResult(score=4, verdict=JudgeVerdict.PASS, rationale="fake"),
    authority_boundaries=DisputeDimensionResult(
        score=1, verdict=JudgeVerdict.FAIL, rationale="fake -- deliberately low to test visibility despite a high average"
    ),
    rationale="Overall fake rationale for UI test.",
)


def _install_fake_openai_client(monkeypatch, *, dispute_brief=None, judge_raw=None) -> None:
    """Fakes ONLY the openai.OpenAI transport (same pattern as
    tests/test_app_scenario_lab_ui.py), extended (not replaced) for Module
    6C: the fake `.responses.parse()` now branches on the requested
    `text_format` so ONE fake client correctly serves the original
    investigation pipeline (`InvestigationBrief`), the new dispute
    generator (`DisputeBrief`), and the new dispute judge
    (`RawDisputeJudgeDimensions`). A DisputeBrief/judge call is only
    allowed when the corresponding fixture was explicitly passed in --
    otherwise it raises loudly, so a test can never silently get a
    wrong-shaped canned response for a code path it didn't intend to
    exercise.
    """
    import openai as openai_module

    fake_investigation_brief = InvestigationBrief(
        summary="Fake offline brief for UI testing only.",
        findings=[Finding(statement="SOURCE FACT: fake finding for UI test.", evidence_refs=["claim:FAKE"])],
        missing_or_conflicting_evidence=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.EXPLAIN_RECORDED_STATUS,
            rationale="fake",
            evidence_refs=["claim:FAKE"],
        ),
    )

    class _FakeParsedResponse:
        status = "completed"

        def __init__(self, parsed):
            self.output_parsed = parsed

    class _FakeResponses:
        def parse(self, **kwargs):
            text_format = kwargs.get("text_format")
            if text_format is InvestigationBrief:
                return _FakeParsedResponse(fake_investigation_brief)
            if text_format is DisputeBrief:
                if dispute_brief is None:
                    raise AssertionError("Unexpected DisputeBrief generation call -- no dispute_brief fixture configured.")
                return _FakeParsedResponse(dispute_brief)
            if text_format is RawDisputeJudgeDimensions:
                if judge_raw is None:
                    raise AssertionError("Unexpected judge call -- no judge_raw fixture configured.")
                return _FakeParsedResponse(judge_raw)
            raise AssertionError(f"Unexpected text_format requested: {text_format!r}")

    class _FakeOpenAIClient:
        def __init__(self, *args, **kwargs):
            self.responses = _FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-ui-test-only")
    monkeypatch.setenv("LLM_MODEL", "fake-model-for-ui-test-only")
    monkeypatch.setattr(openai_module, "OpenAI", _FakeOpenAIClient)


def _fresh_app(monkeypatch, *, dispute_brief=None, judge_raw=None) -> AppTest:
    _install_fake_openai_client(monkeypatch, dispute_brief=dispute_brief, judge_raw=judge_raw)
    at = AppTest.from_file(str(APP_PY_PATH), default_timeout=_RUN_TIMEOUT)
    at.run()
    assert not at.exception
    return at


def _dispute_tab(at: AppTest):
    # Scoped to the Dispute Review tab specifically: Scenario Lab has its
    # own, identically-labeled "Service code" text_input, so an app-wide
    # (unscoped) lookup by label can silently match the wrong tab's widget.
    return at.tabs[2]


def _click(at: AppTest, label: str) -> AppTest:
    button = next(b for b in _dispute_tab(at).button if b.label == label)
    return button.click().run()


def _text_input(at: AppTest, label_prefix: str):
    return next(t for t in _dispute_tab(at).text_input if t.label.startswith(label_prefix))


def _comparison_result(at: AppTest):
    return at.session_state["dispute_review_result"]


# --- 1. all three tabs render; five claim choices remain ---------------------------------


def test_all_three_tabs_render_and_five_claim_choices_remain(monkeypatch):
    at = _fresh_app(monkeypatch)
    tab_labels = [t.proto.label for t in at.tabs]
    assert tab_labels == ["Predefined Claims", "Scenario Lab", "Dispute Review"]

    case_select = next(s for s in at.selectbox if s.label == "Select a case")
    assert len(case_select.options) == 5
    assert any(opt.startswith("CLM-1001") for opt in case_select.options)
    assert any(opt.startswith("CLM-1005") for opt in case_select.options)


def test_dispute_review_shows_recorded_claim_id(monkeypatch):
    at = _fresh_app(monkeypatch)
    dispute_tab = at.tabs[2]
    markdown_text = " ".join(m.value for m in dispute_tab.markdown)
    assert "CLM-1001" in markdown_text


# --- 2-4. preset -> compare -> expected row outcomes (via the real comparator) -----------


def test_matching_preset_produces_four_match_rows_after_compare(monkeypatch):
    at = _fresh_app(monkeypatch)
    _click(at, "Matching fields")
    _click(at, "Compare submitted information")

    result = _comparison_result(at)
    assert result is not None
    assert [row.status for row in result.rows] == [ComparisonStatus.MATCH] * 4


def test_different_servicing_provider_preset_produces_provider_mismatch(monkeypatch):
    at = _fresh_app(monkeypatch)
    _click(at, "Different servicing provider")
    _click(at, "Compare submitted information")

    result = _comparison_result(at)
    by_field = {row.field: row for row in result.rows}
    assert by_field["Servicing Provider"].status == ComparisonStatus.MISMATCH
    assert by_field["Member"].status == ComparisonStatus.MATCH
    assert by_field["Service"].status == ComparisonStatus.MATCH


def test_incomplete_preset_produces_date_and_provider_unknown(monkeypatch):
    at = _fresh_app(monkeypatch)
    _click(at, "Incomplete submission")
    _click(at, "Compare submitted information")

    result = _comparison_result(at)
    by_field = {row.field: row for row in result.rows}
    assert by_field["Validity Dates"].status == ComparisonStatus.UNKNOWN
    assert by_field["Servicing Provider"].status == ComparisonStatus.UNKNOWN


# --- 5. invalid dates -> clear error, no stale result -------------------------------------


def test_invalid_date_shows_clear_error_and_no_stale_result(monkeypatch):
    at = _fresh_app(monkeypatch)
    start_date_input = _text_input(at, "Authorization start date")
    start_date_input.set_value("not-a-date").run()
    _click(at, "Compare submitted information")

    assert at.session_state["dispute_review_result"] is None
    errors = at.session_state["dispute_review_validation_errors"]
    assert errors
    assert any("authorization_start_date" in e for e in errors)
    # no raw traceback text on the page
    assert not at.exception
    assert any("could not be validated" in e.value for e in at.error)


def test_reversed_date_range_via_ui_shows_clear_error(monkeypatch):
    at = _fresh_app(monkeypatch)
    start_date_input = _text_input(at, "Authorization start date")
    end_date_input = _text_input(at, "Authorization end date")
    start_date_input.set_value("2026-06-01").run()
    end_date_input.set_value("2026-01-01").run()
    _click(at, "Compare submitted information")

    assert at.session_state["dispute_review_result"] is None
    errors = at.session_state["dispute_review_validation_errors"]
    assert errors
    assert not any("Traceback" in e for e in errors)


# --- 6. after a comparison, changing each input type clears the result -------------------


def _fresh_compared_app(monkeypatch) -> AppTest:
    at = _fresh_app(monkeypatch)
    _click(at, "Matching fields")
    _click(at, "Compare submitted information")
    assert at.session_state["dispute_review_result"] is not None
    return at


def test_changing_authorization_reference_clears_result(monkeypatch):
    at = _fresh_compared_app(monkeypatch)
    _text_input(at, "Authorization reference number").set_value("DIFFERENT-REF").run()
    assert at.session_state["dispute_review_result"] is None


def test_changing_member_id_clears_result(monkeypatch):
    at = _fresh_compared_app(monkeypatch)
    _text_input(at, "Member ID").set_value("M-OTHER").run()
    assert at.session_state["dispute_review_result"] is None


def test_changing_service_code_clears_result(monkeypatch):
    at = _fresh_compared_app(monkeypatch)
    _text_input(at, "Service code").set_value("OTHER-SERVICE").run()
    assert at.session_state["dispute_review_result"] is None


def test_changing_start_date_clears_result(monkeypatch):
    at = _fresh_compared_app(monkeypatch)
    _text_input(at, "Authorization start date").set_value("2026-01-01").run()
    assert at.session_state["dispute_review_result"] is None


def test_changing_end_date_clears_result(monkeypatch):
    at = _fresh_compared_app(monkeypatch)
    _text_input(at, "Authorization end date").set_value("2026-12-31").run()
    assert at.session_state["dispute_review_result"] is None


def test_changing_servicing_provider_clears_result(monkeypatch):
    at = _fresh_compared_app(monkeypatch)
    _text_input(at, "Servicing provider ID").set_value("PRV-OTHER").run()
    assert at.session_state["dispute_review_result"] is None


def test_changing_dispute_explanation_clears_result(monkeypatch):
    at = _fresh_compared_app(monkeypatch)
    explanation_widget = next(t for t in _dispute_tab(at).text_area if t.label == "Dispute explanation")
    explanation_widget.set_value("A new explanation.").run()
    assert at.session_state["dispute_review_result"] is None


def test_changing_supplied_by_clears_result(monkeypatch):
    at = _fresh_compared_app(monkeypatch)
    supplied_by_widget = next(r for r in _dispute_tab(at).radio if r.label == "Supplied by")
    supplied_by_widget.set_value("Patient").run()
    assert at.session_state["dispute_review_result"] is None


# --- 7. preset changes / reset clear previous results --------------------------------------


def test_loading_a_different_preset_clears_previous_result(monkeypatch):
    at = _fresh_compared_app(monkeypatch)
    _click(at, "Incomplete submission")
    assert at.session_state["dispute_review_result"] is None


def test_reset_clears_previous_result_and_fields(monkeypatch):
    at = _fresh_compared_app(monkeypatch)
    _click(at, "Clear / reset")
    assert at.session_state["dispute_review_result"] is None
    assert at.session_state["dispute_review_member_id"] == ""
    assert at.session_state["dispute_review_auth_reference"] == ""


# --- 8. patient/provider provenance updates correctly ---------------------------------------


def test_provenance_label_updates_for_provider_and_patient(monkeypatch):
    at = _fresh_app(monkeypatch)
    _click(at, "Matching fields")  # provider-supplied by default
    _click(at, "Compare submitted information")
    result = _comparison_result(at)
    assert result.submission_provenance == "Provider-supplied—unverified"

    supplied_by_widget = next(r for r in _dispute_tab(at).radio if r.label == "Supplied by")
    supplied_by_widget.set_value("Patient").run()
    _click(at, "Compare submitted information")
    result = _comparison_result(at)
    assert result.submission_provenance == "Patient-supplied—unverified"


# --- 9. two independent sessions do not share submissions/results --------------------------


def test_two_independent_app_sessions_do_not_share_dispute_review_state(monkeypatch):
    at_a = _fresh_app(monkeypatch)
    _click(at_a, "Matching fields")
    _click(at_a, "Compare submitted information")
    assert at_a.session_state["dispute_review_result"] is not None

    at_b = _fresh_app(monkeypatch)
    assert at_b.session_state["dispute_review_result"] is None
    assert at_b.session_state["dispute_review_member_id"] == ""


# --- 10. new state does not overwrite existing tab state ------------------------------------


def test_dispute_review_activity_does_not_affect_predefined_claims_state(monkeypatch):
    at = _fresh_app(monkeypatch)
    investigate_button = next(b for b in at.button if b.label == "Investigate Claim")
    investigate_button.click().run()
    assert at.session_state["investigation_result"] is not None
    predefined_result_before = at.session_state["investigation_result"]

    _click(at, "Matching fields")
    _click(at, "Compare submitted information")
    _text_input(at, "Member ID").set_value("SOMETHING-ELSE").run()

    assert at.session_state["investigation_result"] is predefined_result_before
    assert "selected_claim_id" in at.session_state
    assert at.session_state["selected_claim_id"] == "CLM-1001"


def test_dispute_review_activity_does_not_affect_scenario_lab_state(monkeypatch):
    at = _fresh_app(monkeypatch)
    assert "scenario_draft" in at.session_state
    draft_before = at.session_state["scenario_draft"]

    _click(at, "Matching fields")
    _click(at, "Compare submitted information")

    assert at.session_state["scenario_draft"] is draft_before
    assert "scenario_result" not in at.session_state


# --- 11. no LLM call, no source-data mutation ------------------------------------------------


def test_comparison_only_path_never_calls_openai_client_or_investigation_service(monkeypatch):
    """Fail-fast spies at the two boundaries that actually matter (Module 5
    correction): merely NOT installing a fake OpenAI client, or not
    setting an API key, does not prove no client construction or model
    call occurred -- it only proves that IF one occurred, it would either
    fail (missing credentials) or silently no-op, neither of which this
    test would necessarily catch. An active spy that raises the instant
    either boundary is touched is the only thing that actually proves it.

    SCOPE (Module 6C correction): this test covers ONLY the deterministic
    comparison path (rendering the tab, loading a preset, clicking
    "Compare submitted information") -- it does NOT claim the entire
    Dispute Review tab can never call a model. As of Module 6C, the tab
    also offers "Investigate dispute evidence" and "Run AI semantic
    evaluation", which DO legitimately call the OpenAI SDK when clicked
    (see test_investigation_button_invokes_dispute_workflow_only_on_click
    and the judge tests below for that boundary's own, separate coverage).
    This test's original guarantee -- that merely comparing submitted
    information never touches a model -- is preserved exactly, not
    deleted, by scoping its rename to what it actually tests.

    Patches both boundaries the comparison-only path could reach if it
    (incorrectly) tried to generate an AI investigation brief:
      - openai.OpenAI construction (application/llm_adapter.py's
        OpenAIInvestigationBriefAdapter._ensure_client is the only
        ORIGINAL call site -- see tests/test_project_structure.py's
        test_openai_sdk_confined_to_generation_adapter).
      - application.investigation_service.run_investigation (the single
        existing entry point into the ORIGINAL evidence + generation
        pipeline; app.py's own Predefined Claims/Scenario Lab tabs call it
        only from their own button handlers, never merely on render).

    Exercises rendering the tab plus all three presets' full
    load-preset-then-compare flow -- if either boundary were reached at
    any point, the spy raises immediately and at.exception is non-empty.
    """
    import openai as openai_module
    import application.investigation_service as investigation_service_module

    def _fail_openai_construction(*args, **kwargs):
        raise AssertionError("openai.OpenAI must never be constructed by the Dispute Review path")

    def _fail_run_investigation(*args, **kwargs):
        raise AssertionError(
            "application.investigation_service.run_investigation must never be called by "
            "the Dispute Review path"
        )

    monkeypatch.setattr(openai_module, "OpenAI", _fail_openai_construction)
    monkeypatch.setattr(investigation_service_module, "run_investigation", _fail_run_investigation)

    at = AppTest.from_file(str(APP_PY_PATH), default_timeout=_RUN_TIMEOUT)
    at.run()
    assert not at.exception

    for label in ("Matching fields", "Different servicing provider", "Incomplete submission"):
        _click(at, label)
        _click(at, "Compare submitted information")
        assert not at.exception

    assert at.session_state["dispute_review_result"] is not None


def test_dispute_review_does_not_modify_source_data_files(monkeypatch):
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DATA_DIR.glob("*.json"))}

    at = _fresh_app(monkeypatch)
    for label in ("Matching fields", "Different servicing provider", "Incomplete submission"):
        _click(at, label)
        _click(at, "Compare submitted information")
    assert not at.exception

    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DATA_DIR.glob("*.json"))}
    assert before == after


def test_dispute_review_leaves_shared_in_memory_state_unchanged(monkeypatch):
    """File-hash comparisons (see test_dispute_review_does_not_modify_source_data_files
    above) only prove data/*.json was never written to -- they say nothing
    about the process-wide, already-loaded in-memory singletons every tab
    actually reads from (tools.data_store.get_data_store() and
    graph.retriever's cached graph). AppTest runs the real app.py in this
    same process, so this test snapshots those two singletons' full
    contents (not just key sets) and the CLM-1001 graph neighborhood
    before driving the real UI through all three presets + Compare, then
    asserts nothing changed. Deliberately does not import or exercise
    application.scenario_lab's DataStore-boundary override anywhere.
    """
    from graph.retriever import get_claim_neighborhood
    from tools.data_store import get_data_store

    store = get_data_store()
    claims_before = {k: v.model_dump() for k, v in store.claims.items()}
    members_before = {k: v.model_dump() for k, v in store.members.items()}
    providers_before = {k: v.model_dump() for k, v in store.providers.items()}
    benefits_before = {k: v.model_dump() for k, v in store.benefits.items()}
    prior_auths_before = {k: v.model_dump() for k, v in store.prior_authorizations.items()}
    grouped_auths_before = {
        k: [a.model_dump() for a in v] for k, v in store.prior_authorizations_by_member_service.items()
    }
    graph_neighborhood_before = get_claim_neighborhood("CLM-1001").model_dump()

    at = _fresh_app(monkeypatch)
    for label in ("Matching fields", "Different servicing provider", "Incomplete submission"):
        _click(at, label)
        _click(at, "Compare submitted information")
    assert not at.exception

    # Same process-wide singletons (get_data_store/_get_graph are
    # lru_cache(maxsize=1)) -- re-fetching returns the SAME objects, so
    # this is a genuine before/after comparison of shared in-memory state,
    # not a fresh reload from disk.
    store_after = get_data_store()
    assert store_after is store
    assert {k: v.model_dump() for k, v in store_after.claims.items()} == claims_before
    assert {k: v.model_dump() for k, v in store_after.members.items()} == members_before
    assert {k: v.model_dump() for k, v in store_after.providers.items()} == providers_before
    assert {k: v.model_dump() for k, v in store_after.benefits.items()} == benefits_before
    assert {k: v.model_dump() for k, v in store_after.prior_authorizations.items()} == prior_auths_before
    assert {
        k: [a.model_dump() for a in v] for k, v in store_after.prior_authorizations_by_member_service.items()
    } == grouped_auths_before
    assert get_claim_neighborhood("CLM-1001").model_dump() == graph_neighborhood_before


def test_dispute_review_result_states_denial_unchanged_and_not_saved(monkeypatch):
    at = _fresh_compared_app(monkeypatch)
    dispute_tab = at.tabs[2]
    info_text = " ".join(i.value for i in dispute_tab.info)
    assert "recorded denial" in info_text.lower()
    assert "does not include this submission" in info_text.lower()
    assert "session-local" in info_text.lower()


def test_dispute_review_result_never_shows_approval_or_payment_language(monkeypatch):
    at = _fresh_compared_app(monkeypatch)
    dispute_tab = _dispute_tab(at)
    # st.write(...) calls render as "markdown" elements under AppTest, so
    # dispute_tab.markdown already captures them -- there is no separate
    # .write collection on a Tab/Block.
    all_text = " ".join(
        [w.value for w in dispute_tab.markdown] + [w.value for w in dispute_tab.caption]
    ).lower()
    for forbidden in ("approved", "verified authorization", "eligible for payment", "denial reversed"):
        assert forbidden not in all_text


# =====================================================================================
# Module 6C: optional AI investigation + optional judge
# =====================================================================================


def _fresh_investigated_app(monkeypatch, *, preset="Matching fields", dispute_brief=FAKE_DISPUTE_BRIEF) -> AppTest:
    at = _fresh_app(monkeypatch, dispute_brief=dispute_brief)
    _click(at, preset)
    _click(at, "Compare submitted information")
    assert at.session_state["dispute_review_result"] is not None
    _click(at, "Investigate dispute evidence")
    assert not at.exception
    return at


def _fresh_judged_app(monkeypatch, *, dispute_brief=FAKE_DISPUTE_BRIEF, judge_raw=FAKE_JUDGE_RAW) -> AppTest:
    at = _fresh_app(monkeypatch, dispute_brief=dispute_brief, judge_raw=judge_raw)
    _click(at, "Matching fields")
    _click(at, "Compare submitted information")
    _click(at, "Investigate dispute evidence")
    workflow_result = at.session_state["dispute_review_workflow_result"]
    assert workflow_result.generation_status.value == "DRAFTED"
    assert workflow_result.validation_result.status.value == "PASSED"
    _click(at, "Run AI semantic evaluation")
    assert not at.exception
    return at


# --- 1/2. investigation/judge only run on explicit click, never automatically -------------


def test_investigation_button_invokes_dispute_workflow_only_on_click(monkeypatch):
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    _click(at, "Matching fields")
    _click(at, "Compare submitted information")
    # merely comparing never populates a workflow result
    assert at.session_state.get("dispute_review_workflow_result") is None

    _click(at, "Investigate dispute evidence")
    assert not at.exception
    result = at.session_state["dispute_review_workflow_result"]
    assert result is not None
    assert result.claim_id == "CLM-1001"


def test_investigation_button_does_not_call_original_investigation_service(monkeypatch):
    import application.investigation_service as investigation_service_module

    def _fail(*args, **kwargs):
        raise AssertionError("The ORIGINAL run_investigation must never be called by the dispute workflow button")

    _install_fake_openai_client(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    monkeypatch.setattr(investigation_service_module, "run_investigation", _fail)

    at = AppTest.from_file(str(APP_PY_PATH), default_timeout=_RUN_TIMEOUT)
    at.run()
    _click(at, "Matching fields")
    _click(at, "Compare submitted information")
    _click(at, "Investigate dispute evidence")
    assert not at.exception
    assert at.session_state["dispute_review_workflow_result"].generation_status.value == "DRAFTED"


def test_judge_is_never_auto_invoked_after_investigation(monkeypatch):
    at = _fresh_investigated_app(monkeypatch)
    assert at.session_state.get("dispute_review_judge_envelope") is None


# --- 3. valid generated brief renders with citations and evidence -------------------------


def test_valid_brief_renders_with_summary_findings_and_citations(monkeypatch):
    at = _fresh_investigated_app(monkeypatch)
    dispute_tab = _dispute_tab(at)
    all_text = " ".join(w.value for w in dispute_tab.markdown)
    assert "AI Dispute Review Brief" in all_text
    all_write_text = " ".join(w.value for w in dispute_tab.markdown)
    assert FAKE_DISPUTE_BRIEF.summary in all_write_text or any(
        FAKE_DISPUTE_BRIEF.summary == w.value for w in dispute_tab.markdown
    )
    captions = " ".join(c.value for c in dispute_tab.caption)
    assert "claim:CLM-1001" in captions  # the cited reference id is traceable, not raw JSON only


def test_valid_brief_shows_missing_evidence_and_verification_questions(monkeypatch):
    at = _fresh_investigated_app(monkeypatch)
    dispute_tab = _dispute_tab(at)
    all_text = " ".join(w.value for w in dispute_tab.markdown)
    assert "Missing / Conflicting Evidence" in all_text
    assert "Verification Questions" in all_text


def test_evidence_expander_groups_are_present(monkeypatch):
    at = _fresh_investigated_app(monkeypatch)
    dispute_tab = _dispute_tab(at)
    all_text = " ".join(w.value for w in dispute_tab.markdown)
    for group in (
        "Recorded Structured Facts",
        "Unverified Submitted Information",
        "Retrieved Policy Excerpts",
        "Recorded Graph Relationships",
        "Comparison Findings",
    ):
        assert group in all_text


# --- 4. provider-change policy gap remains visible with a READY gate -----------------------


def test_provider_change_policy_gap_visible_with_ready_gate(monkeypatch):
    at = _fresh_investigated_app(monkeypatch, preset="Different servicing provider")
    result = at.session_state["dispute_review_workflow_result"]
    assert result.gate_result.status.value == "READY_FOR_SCOPED_GENERATION"

    dispute_tab = _dispute_tab(at)
    all_text = " ".join(w.value for w in dispute_tab.warning)
    assert "change in servicing provider" in all_text

    gate_metrics = {m.label: m.value for m in dispute_tab.metric}
    assert gate_metrics.get("Evidence Gate") == "Ready for scoped generation"
    assert gate_metrics["Evidence Gate"] not in ("Authorization verified", "Complete evidence", "Approved")


# --- 5. LIMITED/BLOCKED, retrieval failure, generation failure, validation failure ---------


def test_generation_failure_keeps_comparison_visible_and_shows_no_stale_brief(monkeypatch):
    at = _fresh_app(monkeypatch)  # no dispute_brief fixture configured -> DisputeBrief call would raise
    _click(at, "Matching fields")
    _click(at, "Compare submitted information")
    assert at.session_state["dispute_review_result"] is not None

    # Force a clean, documented generation failure instead of letting the
    # unconfigured-fixture AssertionError propagate as an app exception --
    # this exercises the real PROVIDER failure path deterministically.
    import application.dispute_generator as generator_module

    class _AlwaysFailsAdapter:
        name = "always-fails"
        model_name = "fake"

        def generate(self, prompt_bundle):
            raise generator_module.DisputeGenerationProviderError("simulated outage")

    import application.dispute_workflow as workflow_module

    monkeypatch.setattr(workflow_module, "OpenAIDisputeBriefAdapter", _AlwaysFailsAdapter)

    _click(at, "Investigate dispute evidence")
    assert not at.exception

    result = at.session_state["dispute_review_workflow_result"]
    assert result.generation_status.value == "FAILED"
    assert result.generation_failure_category.value == "PROVIDER"
    assert result.brief is None

    # comparison result is still visible/unaffected
    assert at.session_state["dispute_review_result"] is not None

    dispute_tab = _dispute_tab(at)
    error_text = " ".join(e.value for e in dispute_tab.error)
    assert "could not generate a brief" in error_text.lower()


def test_missing_configuration_shows_actionable_message_without_credentials(monkeypatch):
    # Deliberately do NOT install the fake client -- simulates a genuinely
    # unconfigured environment. Explicitly blank (not delenv) both
    # variables: application.config.load_llm_config() calls python-dotenv's
    # load_dotenv(), which repopulates a DELETED variable from a real local
    # .env file (if one exists in this project) but never overrides one
    # already present, even blank -- the same hermeticity pattern already
    # used correctly by tests/test_application_investigation_service.py's
    # test_missing_configuration_is_a_clear_application_error and
    # tests/test_application_llm_adapter.py. Without this, a real local
    # .env silently turns this into a live-model test (see
    # docs/DISPUTE_AI_VALIDATION.md's live smoke check for the incident
    # this fixes) -- the session-wide deny-guard in tests/conftest.py is a
    # second, independent backstop, but blanking config here is what keeps
    # this test's actual intent (the missing-configuration UI path) correct
    # regardless of ambient environment.
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    at = AppTest.from_file(str(APP_PY_PATH), default_timeout=_RUN_TIMEOUT)
    at.run()
    _click(at, "Matching fields")
    _click(at, "Compare submitted information")
    _click(at, "Investigate dispute evidence")
    assert not at.exception  # missing config must not break the tab

    result = at.session_state["dispute_review_workflow_result"]
    assert result.generation_status.value == "FAILED"
    assert result.generation_failure_category.value == "CONFIGURATION"

    dispute_tab = _dispute_tab(at)
    error_text = " ".join(e.value for e in dispute_tab.error).lower()
    assert "requires model configuration" in error_text
    assert "sk-" not in error_text  # no credential-shaped content ever shown
    # comparison remains available regardless of missing configuration
    assert at.session_state["dispute_review_result"] is not None


def test_validation_failure_does_not_render_normal_brief_view_and_disables_judge(monkeypatch):
    defective_brief = DisputeBrief(
        summary="Defective.",
        findings=[Finding(statement="Uncited claim.", evidence_refs=[])],  # fails Rule B
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(action_code=ActionCode.HUMAN_REVIEW, rationale="x", evidence_refs=[]),
    )
    at = _fresh_investigated_app(monkeypatch, dispute_brief=defective_brief)
    result = at.session_state["dispute_review_workflow_result"]
    assert result.generation_status.value == "DRAFTED"
    assert result.validation_result.status.value == "FAILED"

    dispute_tab = _dispute_tab(at)
    all_text = " ".join(w.value for w in dispute_tab.markdown)
    assert "AI Dispute Review Brief" not in all_text  # the rejected draft is not rendered via the normal view

    error_text = " ".join(e.value for e in dispute_tab.error)
    assert "did not pass deterministic validation" in error_text

    # judge action is not offered at all when there is no accepted draft
    judge_buttons = [b for b in dispute_tab.button if b.label == "Run AI semantic evaluation"]
    assert judge_buttons == []


# --- 6/7. judge available only for a current validated brief; scores/verdicts/refs render ---


def test_judge_button_present_only_after_accepted_draft(monkeypatch):
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    _click(at, "Matching fields")
    _click(at, "Compare submitted information")
    dispute_tab = _dispute_tab(at)
    assert not any(b.label == "Run AI semantic evaluation" for b in dispute_tab.button)

    _click(at, "Investigate dispute evidence")
    dispute_tab = _dispute_tab(at)
    assert any(b.label == "Run AI semantic evaluation" for b in dispute_tab.button)


def test_judge_scores_verdicts_rationales_and_references_render(monkeypatch):
    at = _fresh_judged_app(monkeypatch)
    dispute_tab = _dispute_tab(at)

    dataframes = list(dispute_tab.dataframe)
    assert dataframes  # the dimension table rendered
    judge_df = dataframes[-1].value  # the last dataframe rendered is the judge table
    dimension_names = set(judge_df["Dimension"])
    assert dimension_names == {"Evidence Grounding", "Coverage", "Uncertainty & Provenance", "Authority Boundaries"}
    assert set(judge_df["Score (1-5)"]) == {5, 5, 4, 1}
    assert set(judge_df["Verdict"]) == {"PASS", "FAIL"}

    captions = " ".join(c.value for c in dispute_tab.caption)
    assert "claim:CLM-1001" in captions  # evidence_grounding's evidence_refs is traceable


def test_low_failed_dimension_remains_visible_despite_high_average(monkeypatch):
    at = _fresh_judged_app(monkeypatch)
    dispute_tab = _dispute_tab(at)

    metrics = {m.label: m.value for m in dispute_tab.metric}
    assert "Overall Advisory Score" in metrics
    overall_score = float(metrics["Overall Advisory Score"].split("/")[0].strip())
    assert overall_score >= 3.5  # a moderately high average
    assert metrics["Overall Verdict"] == "FAIL"  # but FAIL still wins, per DisputeJudgeResult's own rule

    dataframes = list(dispute_tab.dataframe)
    judge_df = dataframes[-1].value
    assert "FAIL" in set(judge_df["Verdict"])  # the failing dimension row is still rendered

    # The UI's own disclaimer legitimately uses the words "accuracy" and
    # "confidence probability" -- to explicitly say the score is NEITHER
    # of those, per the task's own instruction ("do not convert scores
    # into accuracy percentages or confidence probabilities"). Assert that
    # disclaimer is actually present, rather than banning the words
    # outright (which would also forbid stating the correct caveat).
    all_text = " ".join(w.value for w in dispute_tab.markdown) + " " + " ".join(c.value for c in dispute_tab.caption)
    lowered = all_text.lower()
    assert "never an accuracy percentage" in lowered
    assert "confidence probability" in lowered and "never" in lowered


# --- 9. judge failure preserves the validated brief -----------------------------------------


def test_judge_failure_preserves_the_validated_brief(monkeypatch):
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)  # no judge_raw -> judge call raises AssertionError
    _click(at, "Matching fields")
    _click(at, "Compare submitted information")
    _click(at, "Investigate dispute evidence")
    brief_before = at.session_state["dispute_review_workflow_result"].brief

    import application.dispute_judge as judge_module

    class _AlwaysFailsJudgeAdapter:
        name = "always-fails"
        model_name = "fake"

        def evaluate(self, prompt_bundle):
            raise judge_module.DisputeJudgeProviderError("simulated judge outage")

    monkeypatch.setattr(judge_module, "OpenAIDisputeJudgeAdapter", _AlwaysFailsJudgeAdapter)

    _click(at, "Run AI semantic evaluation")
    assert not at.exception

    envelope = at.session_state["dispute_review_judge_envelope"]
    assert envelope.judge_status.value == "FAILED"
    assert envelope.result is None

    # the validated brief is completely unaffected
    assert at.session_state["dispute_review_workflow_result"].brief == brief_before
    assert at.session_state["dispute_review_workflow_result"].validation_result.status.value == "PASSED"

    dispute_tab = _dispute_tab(at)
    error_text = " ".join(e.value for e in dispute_tab.error).lower()
    assert "evaluation failed" in error_text
    all_text = " ".join(w.value for w in dispute_tab.markdown)
    assert "AI Dispute Review Brief" in all_text  # the brief is still shown


# --- 10. every input/preset/reset change clears AI and judge state -------------------------


@pytest.mark.parametrize(
    "action",
    [
        "member_id",
        "service_code",
        "auth_reference",
        "start_date",
        "end_date",
        "servicing_provider",
        "explanation",
        "supplied_by",
        "preset",
        "reset",
    ],
)
def test_every_input_type_clears_ai_and_judge_state(monkeypatch, action):
    at = _fresh_judged_app(monkeypatch)
    assert at.session_state["dispute_review_workflow_result"] is not None
    assert at.session_state["dispute_review_judge_envelope"] is not None

    if action == "member_id":
        _text_input(at, "Member ID").set_value("SOMETHING-ELSE").run()
    elif action == "service_code":
        _text_input(at, "Service code").set_value("OTHER-SERVICE").run()
    elif action == "auth_reference":
        _text_input(at, "Authorization reference number").set_value("DIFFERENT-REF").run()
    elif action == "start_date":
        _text_input(at, "Authorization start date").set_value("2026-01-01").run()
    elif action == "end_date":
        _text_input(at, "Authorization end date").set_value("2026-12-31").run()
    elif action == "servicing_provider":
        _text_input(at, "Servicing provider ID").set_value("PRV-OTHER").run()
    elif action == "explanation":
        explanation_widget = next(t for t in _dispute_tab(at).text_area if t.label == "Dispute explanation")
        explanation_widget.set_value("A new explanation.").run()
    elif action == "supplied_by":
        supplied_by_widget = next(r for r in _dispute_tab(at).radio if r.label == "Supplied by")
        supplied_by_widget.set_value("Patient").run()
    elif action == "preset":
        _click(at, "Incomplete submission")
    elif action == "reset":
        _click(at, "Clear / reset")

    assert at.session_state["dispute_review_result"] is None
    assert at.session_state["dispute_review_workflow_result"] is None
    assert at.session_state["dispute_review_judge_envelope"] is None


# --- 11/12. new investigation invalidates old judge output; stale binding never displayed ---


def test_new_investigation_invalidates_old_judge_output(monkeypatch):
    at = _fresh_judged_app(monkeypatch)
    old_run_id = at.session_state["dispute_review_workflow_result"].run_id
    assert at.session_state["dispute_review_judge_envelope"] is not None

    # Re-run investigation with IDENTICAL inputs -- a fresh run_id is
    # still generated, and the old judge result must be invalidated
    # immediately (Step 6: "even when the inputs happen to be identical").
    _click(at, "Investigate dispute evidence")
    assert not at.exception

    new_result = at.session_state["dispute_review_workflow_result"]
    assert new_result.run_id != old_run_id
    assert at.session_state["dispute_review_judge_envelope"] is None


def test_judge_binding_rejects_mismatched_revision_even_with_matching_run_id(monkeypatch):
    # Narrow fingerprint safeguard test: simulate code replacing the brief
    # while (hypothetically) retaining the same run_id -- the binding
    # fingerprint must still reject it, since a run_id match alone is
    # documented as insufficient.
    at = _fresh_judged_app(monkeypatch)
    workflow_result = at.session_state["dispute_review_workflow_result"]
    envelope = at.session_state["dispute_review_judge_envelope"]
    assert envelope.run_id == workflow_result.run_id  # currently matches

    tampered_brief = workflow_result.brief.model_copy(update={"summary": "TAMPERED -- different content"})
    tampered_result = workflow_result.model_copy(update={"brief": tampered_brief})
    at.session_state["dispute_review_workflow_result"] = tampered_result
    # keep the SAME fingerprint key so get_display_workflow_result still
    # accepts it as "current" (only the run_id/brief content changed)
    import dispute_review.ui as ui_module

    displayed = ui_module.get_display_judge_envelope(at.session_state, tampered_result)
    assert displayed is None  # rejected despite envelope.run_id == tampered_result.run_id


def test_judge_binding_rejects_evidence_change_with_unchanged_run_id_and_brief(monkeypatch):
    # Module 6D Step 2A's specific gap: a run_id-only (or run_id+brief-only)
    # binding would NOT catch a hypothetical future bug that replaced the
    # EVIDENCE/CONTEXT a judge result was actually computed against, while
    # leaving run_id and the brief itself untouched. The binding must also
    # track the generation context (comparison summary, comparison
    # findings, and limitations all live on it).
    at = _fresh_judged_app(monkeypatch)
    workflow_result = at.session_state["dispute_review_workflow_result"]
    envelope = at.session_state["dispute_review_judge_envelope"]
    assert envelope.run_id == workflow_result.run_id

    # Tamper with the generation context's limitations (part of "material
    # limitations" the judge was shown) -- run_id and brief both unchanged.
    tampered_context = workflow_result.generation_context.model_copy(
        update={"limitations": ["TAMPERED -- a limitation the judge never actually saw"]}
    )
    tampered_result = workflow_result.model_copy(update={"generation_context": tampered_context})
    assert tampered_result.run_id == envelope.run_id
    assert tampered_result.brief == workflow_result.brief  # brief itself is unchanged

    import dispute_review.ui as ui_module

    displayed = ui_module.get_display_judge_envelope(at.session_state, tampered_result)
    assert displayed is None  # rejected despite unchanged run_id AND unchanged brief


# --- 13. two independent sessions do not share investigation/judge state -------------------


def test_two_sessions_do_not_share_investigation_or_judge_state(monkeypatch):
    at_a = _fresh_judged_app(monkeypatch)
    assert at_a.session_state["dispute_review_workflow_result"] is not None
    assert at_a.session_state["dispute_review_judge_envelope"] is not None

    at_b = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    assert at_b.session_state.get("dispute_review_workflow_result") is None
    assert at_b.session_state.get("dispute_review_judge_envelope") is None


# --- 14. existing tab state unaffected ------------------------------------------------------


def test_investigation_and_judge_do_not_affect_predefined_claims_or_scenario_lab_state(monkeypatch):
    at = _fresh_judged_app(monkeypatch)
    assert "selected_claim_id" in at.session_state
    assert at.session_state["selected_claim_id"] == "CLM-1001"
    assert "scenario_draft" in at.session_state
    assert "scenario_result" not in at.session_state
    assert "investigation_result" not in at.session_state or at.session_state["investigation_result"] is None


# --- 15. source data / shared state unchanged ------------------------------------------------


def test_investigation_and_judge_do_not_modify_source_data_or_shared_state(monkeypatch):
    from graph.retriever import get_claim_neighborhood
    from tools.data_store import get_data_store

    store = get_data_store()
    claims_before = {k: v.model_dump() for k, v in store.claims.items()}
    graph_before = get_claim_neighborhood("CLM-1001").model_dump()
    files_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DATA_DIR.glob("*.json"))}

    _fresh_judged_app(monkeypatch)

    store_after = get_data_store()
    assert store_after is store
    assert {k: v.model_dump() for k, v in store_after.claims.items()} == claims_before
    assert get_claim_neighborhood("CLM-1001").model_dump() == graph_before
    files_after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DATA_DIR.glob("*.json"))}
    assert files_after == files_before
