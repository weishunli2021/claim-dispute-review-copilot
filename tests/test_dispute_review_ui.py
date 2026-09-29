"""UI-level regression tests: drive the REAL app.py through Streamlit's
AppTest framework (no live network call anywhere in this file) for the
ONE active Dispute Review scenario -- the billing-correction review of
CLM-BILL-9001.

These tests exercise the Dispute Review tab exactly as a user would:
clicking buttons and editing text inputs -- never by calling
dispute_review internals directly.
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

APP_PY_PATH = Path(__file__).resolve().parents[1] / "app.py"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_RUN_TIMEOUT = 30

# A well-formed DisputeBrief citing "claim:CLM-BILL-9001" -- always present
# in the recorded_facts of every submission against this fixed claim.
FAKE_DISPUTE_BRIEF = DisputeBrief(
    summary="Fake offline billing-correction brief for UI testing only.",
    findings=[Finding(statement="SOURCE FACT: fake finding for UI test.", evidence_refs=["claim:CLM-BILL-9001"])],
    missing_or_conflicting_evidence=["Fake missing-evidence item for UI test."],
    verification_questions=["Fake verification question for UI test."],
    suggested_next_step=SuggestedNextStep(
        action_code=ActionCode.HUMAN_REVIEW, rationale="fake", evidence_refs=["claim:CLM-BILL-9001"]
    ),
)

FAKE_JUDGE_RAW = RawDisputeJudgeDimensions(
    evidence_grounding=DisputeDimensionResult(
        score=5, verdict=JudgeVerdict.PASS, rationale="fake", evidence_refs=["claim:CLM-BILL-9001"]
    ),
    coverage=DisputeDimensionResult(score=5, verdict=JudgeVerdict.PASS, rationale="fake"),
    uncertainty_and_provenance=DisputeDimensionResult(score=4, verdict=JudgeVerdict.PASS, rationale="fake"),
    authority_boundaries=DisputeDimensionResult(
        score=1, verdict=JudgeVerdict.FAIL, rationale="fake -- deliberately low to test visibility despite a high average"
    ),
    rationale="Overall fake rationale for UI test.",
)


def _install_fake_openai_client(monkeypatch, *, dispute_brief=None, judge_raw=None) -> None:
    import openai as openai_module

    fake_investigation_brief = InvestigationBrief(
        summary="Fake offline brief for UI testing only.",
        findings=[Finding(statement="SOURCE FACT: fake finding for UI test.", evidence_refs=["claim:FAKE"])],
        missing_or_conflicting_evidence=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.EXPLAIN_RECORDED_STATUS, rationale="fake", evidence_refs=["claim:FAKE"]
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
    return next(tab for tab in at.tabs if tab.proto.label == "Dispute Review")


def _click(at: AppTest, label: str) -> AppTest:
    button = next(b for b in _dispute_tab(at).button if b.label == label)
    return button.click().run()


def _text_input(at: AppTest, label: str):
    return next(t for t in _dispute_tab(at).text_input if t.label == label)


def _all_text(at: AppTest) -> str:
    tab = _dispute_tab(at)
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


# --- 1. navigation + Golden Dataset & Evaluation untouched ----------------------------------


def test_default_navigation_renders_and_golden_dataset_untouched(monkeypatch):
    at = _fresh_app(monkeypatch)
    tab_labels = [t.proto.label for t in at.tabs]
    assert tab_labels == ["Dispute Review", "Golden Dataset & Evaluation"]

    case_select = next(s for s in at.selectbox if s.label == "Select a case")
    assert len(case_select.options) == 5
    assert any(opt.startswith("CLM-1001") for opt in case_select.options)
    assert any(opt.startswith("CLM-1005") for opt in case_select.options)


def test_dispute_review_shows_the_isolated_billing_claim_id(monkeypatch):
    at = _fresh_app(monkeypatch)
    text = _all_text(at)
    assert "CLM-BILL-9001" in text
    assert "CLM-1001" not in text  # never mixes in a golden-dataset claim


# --- 2. exactly one active scenario: no retired preset controls -----------------------------


def test_no_retired_authorization_preset_buttons_remain(monkeypatch):
    at = _fresh_app(monkeypatch)
    labels = {b.label for b in _dispute_tab(at).button}
    for retired in (
        "Matching fields",
        "Provider corrected after denial",
        "Approved authorization — service date outside authorized period",
        "Different servicing provider",
        "Incomplete submission",
    ):
        assert retired not in labels
    assert "Load billing correction example" in labels
    assert "Investigate billing correction" in labels


# --- 3. Section 1: original claim & decision, all five sections in order --------------------


def test_section_headers_appear_in_required_order(monkeypatch):
    at = _fresh_app(monkeypatch)
    headers = [s.value for s in _dispute_tab(at).subheader]
    assert headers[0].startswith("1. Original Claim")
    assert headers[1].startswith("2. Newly Submitted Corrected Claim")
    assert headers[2].startswith("3. Investigation & Comparison Results")


def test_original_claim_shows_prominent_four_investigated_fields(monkeypatch):
    at = _fresh_app(monkeypatch)
    text = _all_text(at)
    for value in ("SURG-KNEE-ARTHRO", "MOD-R", "PRV-BILL-WRONG"):
        assert value in text


def test_original_claim_shows_recorded_decision_and_flagged_fields(monkeypatch):
    at = _fresh_app(monkeypatch)
    text = _all_text(at)
    assert "BILLING_DISCREPANCY" in text
    assert "service_code" in text and "modifier" in text  # decision-flagged fields


# --- 4. Section 2: example load, reset, editable submission ---------------------------------


def test_load_example_populates_fields(monkeypatch):
    at = _fresh_app(monkeypatch)
    at = _click(at, "Load billing correction example")
    assert _text_input(at, "Service code").value == "SURG-KNEE-REPAIR"
    assert _text_input(at, "Modifier").value == "MOD-L"
    assert _text_input(at, "Units").value == "1"
    assert _text_input(at, "Servicing provider ID").value == "PRV-BILL-ACTUAL"


def test_reset_clears_fields_and_result(monkeypatch):
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    at = _click(at, "Load billing correction example")
    at = _click(at, "Investigate billing correction")
    assert at.session_state["dispute_review_result"] is not None
    at = _click(at, "Clear / reset")
    assert _text_input(at, "Service code").value == ""
    assert at.session_state["dispute_review_result"] is None


# --- 5. Section 3: comparison table ----------------------------------------------------------


def test_compare_default_example_yields_four_supported_rows(monkeypatch):
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    at = _click(at, "Load billing correction example")
    at = _click(at, "Investigate billing correction")
    result = at.session_state["dispute_review_result"]
    assert result is not None
    assert len(result.rows) == 4
    assert all(row.status.value == "SUPPORTED" for row in result.rows)


def test_compare_wrong_proposed_values_yields_conflicts(monkeypatch):
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    _text_input(at, "Service code").set_value("SURG-BOGUS")
    at.run()
    at = _click(at, "Investigate billing correction")
    result = at.session_state["dispute_review_result"]
    assert result is not None
    row = next(r for r in result.rows if r.field == "Service Code")
    assert row.status.value == "CONFLICTS"


def test_editing_after_compare_clears_the_result(monkeypatch):
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    at = _click(at, "Load billing correction example")
    at = _click(at, "Investigate billing correction")
    assert at.session_state["dispute_review_result"] is not None
    _text_input(at, "Modifier").set_value("MOD-X")
    at.run()
    assert at.session_state["dispute_review_result"] is None


def test_linkage_mismatch_blocks_comparison(monkeypatch):
    at = _fresh_app(monkeypatch)
    _text_input(at, "Member ID").set_value("MEM-SOMEONE-ELSE")
    at.run()
    at = _click(at, "Investigate billing correction")
    assert at.session_state["dispute_review_result"] is None
    assert at.session_state["dispute_review_linkage_issue"] is not None
    text = _all_text(at)
    assert "does not match" in text.lower()


# --- 6. Section 3/4: AI investigation (fake adapter only) ------------------------------------


def test_investigate_button_runs_comparison_and_workflow_together(monkeypatch):
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    at = _click(at, "Load billing correction example")
    assert at.session_state["dispute_review_workflow_result"] is None
    at = _click(at, "Investigate billing correction")
    assert at.session_state["dispute_review_result"] is not None
    assert at.session_state["dispute_review_workflow_result"] is not None


def test_investigation_renders_brief_as_continuation_of_section_three(monkeypatch):
    """UI decluttering (user request, 2026-09-27): the former "4. Detailed
    Evidence, Findings & Next Action" section header was removed entirely
    -- the evidence/findings/next-action output now renders as a direct,
    unbroken continuation of Section 3, and "Evals: LLM-as-a-Judge" is
    renumbered down to "4." to close the resulting gap."""
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    at = _click(at, "Load billing correction example")
    at = _click(at, "Investigate billing correction")
    headers = [s.value for s in _dispute_tab(at).subheader]
    assert not any(h.startswith("4. Detailed Evidence") for h in headers)
    assert any(h.startswith("4. Evals: LLM-as-a-Judge") for h in headers)
    assert "### Billing-Correction Review Brief" not in _all_text(at)
    text = _all_text(at)
    assert "Fake offline billing-correction brief for UI testing only." in text


def test_summary_is_combined_into_one_block(monkeypatch):
    """UI decluttering (user request, 2026-09-27): the deterministic
    comparison's own summary and the AI-drafted brief's own summary used to
    render as two separately-headed "Summary" blocks; they now render as
    ONE combined "Summary" block containing both texts."""
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    at = _click(at, "Load billing correction example")
    at = _click(at, "Investigate billing correction")
    tab = _dispute_tab(at)
    summary_headings = [m.value for m in tab.markdown if m.value.strip() == "**Summary**"]
    assert len(summary_headings) == 1
    text = _all_text(at)
    assert "4 of 4 proposed correction(s) supported by independent records" in text
    assert "Fake offline billing-correction brief for UI testing only." in text


def test_suggested_next_step_combined_into_the_same_green_summary_box(monkeypatch):
    """UI decluttering (user request, 2026-09-27): the AI-drafted brief's
    "Suggested Next Step" used to render in its own separate green box,
    below Findings; it now renders INSIDE the one combined Summary box
    (no separate "**Suggested Next Step**" heading), so the whole tab has
    exactly one st.success call for this output."""
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    at = _click(at, "Load billing correction example")
    at = _click(at, "Investigate billing correction")
    tab = _dispute_tab(at)
    assert not any(m.value.strip() == "**Suggested Next Step**" for m in tab.markdown)
    success_texts = [s.value for s in tab.success]
    assert len(success_texts) == 1
    combined = success_texts[0]
    assert "4 of 4 proposed correction(s) supported by independent records" in combined
    assert "Fake offline billing-correction brief for UI testing only." in combined
    assert "Suggested Next Step: HUMAN_REVIEW" in combined


def test_findings_and_next_step_evidence_are_collapsed_by_default(monkeypatch):
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    at = _click(at, "Load billing correction example")
    at = _click(at, "Investigate billing correction")
    labels = [e.label for e in _dispute_tab(at).expander]
    assert "Findings" in labels
    assert "Evidence for this recommendation" in labels
    assert "Workflow Status" in labels  # user request 2026-09-27: collapsed, not shown inline


def test_workflow_status_is_a_plain_table_not_large_metrics(monkeypatch):
    """Regression guard: "Draft generated" under a large st.metric read as
    confusing next to "Deterministic Validation: Passed" (user feedback,
    2026-09-26) -- Workflow Status is now a plain, normal-font-size table,
    and the generation outcome reads as a plain Succeeded/Failed."""
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    at = _click(at, "Load billing correction example")
    at = _click(at, "Investigate billing correction")
    assert not _dispute_tab(at).metric  # no more large-metric widgets for workflow status
    text = _all_text(at)
    assert "Succeeded" in text
    assert "Model Call" in text


def test_deterministic_recommended_action_and_fixed_disclaimers_removed_from_brief_display(monkeypatch):
    """UI decluttering (user request, 2026-09-26): the "Deterministic
    recommended next action" box, the authenticity disclaimer, the
    provenance notice, and the closing "This brief does not..." block were
    all deliberately removed from the visible brief to reduce repeated
    boilerplate. This is a REGRESSION GUARD for that intentional removal,
    not a claim that the underlying computations/data no longer exist --
    dispute_review.comparison.build_recommended_next_action and
    DisputeWorkflowResult.authenticity_disclaimer/provenance_notice are
    still fully computed and unit-tested (see test_dispute_review_comparison.py);
    only their display in this brief was removed. No other guardrail
    (validator, gate, advisory-action allowlist) is affected."""
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    at = _click(at, "Load billing correction example")
    at = _click(at, "Investigate billing correction")
    text = _all_text(at)
    assert "Deterministic recommended next action" not in text
    assert "Route for analyst review and preparation of a corrected claim" not in text
    assert "does not submit a corrected claim" not in text
    assert "This brief distinguishes RECORDED facts" not in text


# --- 7. Section 5: judge -----------------------------------------------------------------


def test_judge_button_present_only_after_accepted_draft(monkeypatch):
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    assert "Run LLM-as-a-Judge evaluation" not in {b.label for b in _dispute_tab(at).button}
    at = _click(at, "Load billing correction example")
    at = _click(at, "Investigate billing correction")
    assert "Run LLM-as-a-Judge evaluation" in {b.label for b in _dispute_tab(at).button}


def test_judge_scores_render(monkeypatch):
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF, judge_raw=FAKE_JUDGE_RAW)
    at = _click(at, "Load billing correction example")
    at = _click(at, "Investigate billing correction")
    at = _click(at, "Run LLM-as-a-Judge evaluation")
    text = _all_text(at)
    assert "Overall Advisory Score" in text or any(
        m.label == "Overall Advisory Score" for m in _dispute_tab(at).get("metric")
    )


def test_new_investigation_invalidates_old_judge_output(monkeypatch):
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF, judge_raw=FAKE_JUDGE_RAW)
    at = _click(at, "Load billing correction example")
    at = _click(at, "Investigate billing correction")
    at = _click(at, "Run LLM-as-a-Judge evaluation")
    assert at.session_state["dispute_review_judge_envelope"] is not None
    at = _click(at, "Investigate billing correction")
    assert at.session_state["dispute_review_judge_envelope"] is None


# --- 8. source-of-truth safety: nothing here mutates the isolated fixtures ------------------


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_full_flow_does_not_modify_source_fixture_files(monkeypatch):
    claim_path = DATA_DIR / "billing_correction_claim.json"
    support_path = DATA_DIR / "billing_correction_support_records.json"
    before = (_hash(claim_path), _hash(support_path))

    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF, judge_raw=FAKE_JUDGE_RAW)
    at = _click(at, "Load billing correction example")
    at = _click(at, "Investigate billing correction")
    at = _click(at, "Run LLM-as-a-Judge evaluation")

    after = (_hash(claim_path), _hash(support_path))
    assert before == after


def test_original_claim_snapshot_unchanged_after_edits_and_investigation(monkeypatch):
    at = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    before = _all_text(at)
    before_original_block = "SURG-KNEE-ARTHRO" in before and "MOD-R" in before

    at = _click(at, "Load billing correction example")
    at = _click(at, "Investigate billing correction")

    after = _all_text(at)
    assert before_original_block
    assert "SURG-KNEE-ARTHRO" in after  # original claim value still shown, never replaced by the proposed value
    assert "MOD-R" in after


def test_two_independent_sessions_do_not_share_state(monkeypatch):
    at1 = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    at1 = _click(at1, "Load billing correction example")
    at1 = _click(at1, "Investigate billing correction")

    at2 = _fresh_app(monkeypatch, dispute_brief=FAKE_DISPUTE_BRIEF)
    assert at2.session_state["dispute_review_result"] is None
    assert at1.session_state["dispute_review_result"] is not None
