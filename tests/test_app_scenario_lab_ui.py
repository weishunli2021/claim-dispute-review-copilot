"""H7 UI-level regression tests: drive the REAL app.py through Streamlit's
AppTest framework (no live network call anywhere in this file -- Scenario
Lab's pipeline is exercised exactly as it is in tests/test_scenario_lab.py,
just through the actual rendering script this time).

These tests exist to catch the *rendering-layer* bug class the H6 run_id
bug represented: a correctly-computed result that never actually reaches
the screen because of a tab-scoping, session-state key, or staleness-guard
mistake in app.py itself, which a test of application/scenario_lab.py alone
cannot see.
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from application.models import ActionCode, Finding, InvestigationBrief, SuggestedNextStep

APP_PY_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _install_fake_openai_client(monkeypatch) -> None:
    """Fakes ONLY the openai.OpenAI transport (same pattern as
    tests/test_semantic_judge.py's _install_fake_openai_client) so that
    Scenario Lab's "Run Investigation" button -- which calls the real,
    unmocked application.investigation_service.run_investigation with no
    adapter override, exactly as app.py does in production -- can never
    reach the real network in a test, regardless of whether a real
    OPENAI_API_KEY happens to be present in the environment."""
    import openai as openai_module

    fake_brief = InvestigationBrief(
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
            return _FakeParsedResponse(fake_brief)

    class _FakeOpenAIClient:
        def __init__(self, *args, **kwargs):
            self.responses = _FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-ui-test-only")
    monkeypatch.setenv("LLM_MODEL", "fake-model-for-ui-test-only")
    monkeypatch.setattr(openai_module, "OpenAI", _FakeOpenAIClient)


_RUN_TIMEOUT = 30  # local embedding-model loading + graph build can be slow on first use in-process


def _fresh_app(monkeypatch) -> AppTest:
    _install_fake_openai_client(monkeypatch)
    at = AppTest.from_file(str(APP_PY_PATH), default_timeout=_RUN_TIMEOUT)
    at.run()
    assert not at.exception
    return at


def test_both_tabs_present_and_render_without_error(monkeypatch):
    # Module 4 appended a third tab ("Dispute Review") after the two H7
    # tabs this file exercises -- the two original tabs must still be
    # present, in their original order, and this file's own tests below
    # (which only ever click Scenario Lab/Predefined Claims widgets) are
    # otherwise unaffected by that addition.
    at = _fresh_app(monkeypatch)
    tab_labels = [t.proto.label for t in at.tabs]
    assert tab_labels == ["Predefined Claims", "Scenario Lab", "Dispute Review"]


def test_scenario_lab_validate_and_run_produces_a_result(monkeypatch):
    at = _fresh_app(monkeypatch)

    validate_button = next(b for b in at.button if b.label == "Validate Scenario")
    validate_button.click().run()
    assert not at.exception
    assert any("passed structural validation" in s.value for s in at.success)

    run_button = next(b for b in at.button if b.label == "Run Investigation")
    assert not run_button.proto.disabled
    run_button.click().run()
    assert not at.exception

    assert at.session_state["scenario_result"] is not None
    assert at.session_state["scenario_records"] is not None
    metric_labels = {m.label for m in at.metric}
    assert {"Evidence Status", "Generation Status", "Validation Status"} <= metric_labels


def test_scenario_lab_run_investigation_disabled_before_validation(monkeypatch):
    at = _fresh_app(monkeypatch)
    run_button = next(b for b in at.button if b.label == "Run Investigation")
    assert run_button.proto.disabled


def test_scenario_lab_error_level_scenario_blocks_run_investigation(monkeypatch):
    at = _fresh_app(monkeypatch)
    service_code_input = next(t for t in at.text_input if t.label == "Service code")
    service_code_input.set_value("").run()

    validate_button = next(b for b in at.button if b.label == "Validate Scenario")
    validate_button.click().run()
    assert any("ERROR-level issues" in e.value for e in at.error)

    run_button = next(b for b in at.button if b.label == "Run Investigation")
    assert run_button.proto.disabled


def test_reset_scenario_does_not_affect_predefined_claims_tab_state(monkeypatch):
    at = _fresh_app(monkeypatch)

    investigate_button = next(b for b in at.button if b.label == "Investigate Claim")
    investigate_button.click().run()
    assert at.session_state["investigation_result"] is not None
    predefined_result_before = at.session_state["investigation_result"]

    validate_button = next(b for b in at.button if b.label == "Validate Scenario")
    validate_button.click().run()
    run_button = next(b for b in at.button if b.label == "Run Investigation")
    run_button.click().run()
    assert at.session_state["scenario_result"] is not None

    reset_button = next(b for b in at.button if b.label == "Reset Scenario")
    reset_button.click().run()
    assert not at.exception

    # Reset clears the prior result/validation entirely, and the tab's next
    # render immediately repopulates a fresh, default ("Custom") draft --
    # since a form needs a draft object to render -- rather than leaving
    # the OLD (populated/ran) draft sitting in session_state.
    assert "scenario_result" not in at.session_state
    assert "scenario_records" not in at.session_state
    assert "scenario_validation" not in at.session_state
    assert at.session_state["scenario_draft"].template == "Custom"
    assert at.session_state["investigation_result"] is predefined_result_before


def test_scenario_lab_does_not_modify_source_data_files(monkeypatch):
    import hashlib

    data_dir = Path(__file__).resolve().parents[1] / "data"
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(data_dir.glob("*.json"))
    }

    at = _fresh_app(monkeypatch)
    validate_button = next(b for b in at.button if b.label == "Validate Scenario")
    validate_button.click().run()
    run_button = next(b for b in at.button if b.label == "Run Investigation")
    run_button.click().run()
    assert not at.exception

    after = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(data_dir.glob("*.json"))
    }
    assert before == after


def test_scenario_lab_result_shows_temporary_synthetic_scenario_label(monkeypatch):
    at = _fresh_app(monkeypatch)
    validate_button = next(b for b in at.button if b.label == "Validate Scenario")
    validate_button.click().run()
    run_button = next(b for b in at.button if b.label == "Run Investigation")
    run_button.click().run()
    assert not at.exception

    captions = [c.value for c in at.caption]
    assert any("TEMPORARY SYNTHETIC SCENARIO" in c for c in captions)
