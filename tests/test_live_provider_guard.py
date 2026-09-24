"""Proves tests/conftest.py's default-deny guard is actually active, for
all four real OpenAI-backed adapters in this project.

Deliberately does NOT monkeypatch openai.OpenAI in any test here (that
would just re-test the fake-client pattern already covered elsewhere) --
the whole point is to reach the real adapter's construction path and
confirm the autouse guard (not this test) is what stops it, BEFORE any
network request could be sent. Uses obviously-fake, dummy credentials
(never the real key) purely so load_llm_config() succeeds and execution
actually reaches openai.OpenAI(...) -- proving the guard fires at the SDK
boundary itself, not merely because configuration was missing.
"""

from __future__ import annotations

import pytest

from application.dispute_generator import OpenAIDisputeBriefAdapter
from application.dispute_judge import OpenAIDisputeJudgeAdapter
from application.llm_adapter import OpenAIInvestigationBriefAdapter
from application.semantic_judge import OpenAISemanticJudgeAdapter
from prompts.investigation_brief_prompt import PromptBundle
from tests.conftest import UnexpectedLiveProviderCallError

_DUMMY_BUNDLE = PromptBundle(system_prompt="system", user_prompt="user", prompt_version="v-test")


def _set_dummy_config(monkeypatch: pytest.MonkeyPatch) -> None:
    # Obviously-fake dummy values only -- never the real key, never copied
    # from any real .env.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-dummy-guard-proof-not-real")
    monkeypatch.setenv("LLM_MODEL", "dummy-model-guard-proof-not-real")


def test_guard_blocks_original_investigation_adapter(monkeypatch):
    _set_dummy_config(monkeypatch)
    adapter = OpenAIInvestigationBriefAdapter()
    with pytest.raises(UnexpectedLiveProviderCallError):
        adapter.generate(_DUMMY_BUNDLE)


def test_guard_blocks_original_semantic_judge_adapter(monkeypatch):
    _set_dummy_config(monkeypatch)
    adapter = OpenAISemanticJudgeAdapter()
    with pytest.raises(UnexpectedLiveProviderCallError):
        adapter.evaluate(_DUMMY_BUNDLE)


def test_guard_blocks_dispute_brief_adapter(monkeypatch):
    _set_dummy_config(monkeypatch)
    adapter = OpenAIDisputeBriefAdapter()
    with pytest.raises(UnexpectedLiveProviderCallError):
        adapter.generate(_DUMMY_BUNDLE)


def test_guard_blocks_dispute_judge_adapter(monkeypatch):
    _set_dummy_config(monkeypatch)
    adapter = OpenAIDisputeJudgeAdapter()
    with pytest.raises(UnexpectedLiveProviderCallError):
        adapter.evaluate(_DUMMY_BUNDLE)


def test_guard_does_not_interfere_with_an_explicit_fake_client_override(monkeypatch):
    """A test that installs its own fake/spy openai.OpenAI must still work
    -- the autouse guard is a default, not an unconditional block."""
    import openai

    _set_dummy_config(monkeypatch)

    class _FakeResponses:
        def parse(self, **kwargs):
            raise AssertionError("not reached in this test -- construction success is what's checked")

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = _FakeResponses()

    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    adapter = OpenAIDisputeBriefAdapter()
    client = adapter._ensure_client()
    assert isinstance(client, _FakeOpenAI)
