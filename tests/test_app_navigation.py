"""Module 5: navigation-focused UI regression tests for app.py, driven
through Streamlit's AppTest framework (no live network call anywhere in
this file).

Covers exactly the things a test of application/workbench.py or
evals/golden_reference_evaluator.py alone cannot see: which tabs actually
render, in what order, and -- critically -- that Scenario Lab's rendering
function is genuinely never CALLED (not just visually hidden) when it is
hidden from the default navigation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PY_PATH = Path(__file__).resolve().parents[1] / "app.py"
_RUN_TIMEOUT = 30  # local embedding-model loading + graph build can be slow on first use in-process


def _fresh_app(monkeypatch, *, show_scenario_lab: bool = False) -> AppTest:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-ui-test-only")
    monkeypatch.setenv("LLM_MODEL", "fake-model-for-ui-test-only")
    if show_scenario_lab:
        monkeypatch.setenv("SHOW_SCENARIO_LAB_TAB", "1")
    else:
        monkeypatch.delenv("SHOW_SCENARIO_LAB_TAB", raising=False)
    at = AppTest.from_file(str(APP_PY_PATH), default_timeout=_RUN_TIMEOUT)
    at.run()
    assert not at.exception
    return at


def test_default_navigation_shows_exactly_dispute_review_then_golden_evaluation(monkeypatch):
    at = _fresh_app(monkeypatch)
    tab_labels = [t.proto.label for t in at.tabs]
    assert tab_labels == ["Dispute Review", "Golden Dataset & Evaluation"]
    assert "Scenario Lab" not in tab_labels


def test_dispute_review_is_the_first_tab(monkeypatch):
    at = _fresh_app(monkeypatch)
    assert at.tabs[0].proto.label == "Dispute Review"


def test_app_title_and_name_unchanged(monkeypatch):
    at = _fresh_app(monkeypatch)
    titles = [t.value for t in at.title]
    assert any("Claim Dispute Review Copilot" in title for title in titles)


def test_show_scenario_lab_env_var_reveals_a_third_tab(monkeypatch):
    at = _fresh_app(monkeypatch, show_scenario_lab=True)
    tab_labels = [t.proto.label for t in at.tabs]
    assert tab_labels == ["Dispute Review", "Golden Dataset & Evaluation", "Scenario Lab"]


def test_scenario_lab_renderer_is_never_invoked_by_default(monkeypatch):
    """The strongest possible proof that hiding is real, not cosmetic:
    make application.scenario_lab.get_or_create_draft (the very first
    scenario_lab function _render_scenario_lab_tab calls) raise if it is
    ever called, then load the default app and confirm no exception --
    if the tab's body ran at all, this would fail loudly instead of
    silently rendering something invisible."""
    import application.scenario_lab as scenario_lab_module

    def _boom(*args, **kwargs):
        raise AssertionError("application.scenario_lab.get_or_create_draft was called even though Scenario Lab is hidden")

    monkeypatch.setattr(scenario_lab_module, "get_or_create_draft", _boom)
    at = _fresh_app(monkeypatch, show_scenario_lab=False)
    assert not at.exception
    tab_labels = [t.proto.label for t in at.tabs]
    assert "Scenario Lab" not in tab_labels


def test_scenario_lab_renderer_is_invoked_when_shown(monkeypatch):
    """Sanity check for the test above: with the env var set, the SAME
    monkeypatched raise DOES fire -- proving the patch target is correct
    and the previous test's silence really does mean "never called", not
    "monkeypatch didn't take"."""
    import application.scenario_lab as scenario_lab_module

    def _boom(*args, **kwargs):
        raise AssertionError("deliberate boom to prove the patch target is live")

    monkeypatch.setattr(scenario_lab_module, "get_or_create_draft", _boom)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-ui-test-only")
    monkeypatch.setenv("LLM_MODEL", "fake-model-for-ui-test-only")
    monkeypatch.setenv("SHOW_SCENARIO_LAB_TAB", "1")
    at = AppTest.from_file(str(APP_PY_PATH), default_timeout=_RUN_TIMEOUT)
    at.run()
    assert at.exception  # the boom above should have fired


def test_golden_evaluation_tab_never_imports_or_calls_run_scenario_investigation(monkeypatch):
    """A second, independent proof: patch the scenario investigation entry
    point itself (not just draft creation) and confirm rendering the
    default app still never touches it."""
    import application.scenario_lab as scenario_lab_module

    def _boom(*args, **kwargs):
        raise AssertionError("run_scenario_investigation was called even though Scenario Lab is hidden")

    monkeypatch.setattr(scenario_lab_module, "run_scenario_investigation", _boom)
    at = _fresh_app(monkeypatch, show_scenario_lab=False)
    assert not at.exception
