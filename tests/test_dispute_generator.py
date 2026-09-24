"""Tests for application/dispute_generator.py. Fully offline:
OpenAIDisputeBriefAdapter is exercised with a fake OpenAI client injected
in place of the real SDK client, never a real network call.
FakeDisputeBriefAdapter is exercised directly.
"""

from __future__ import annotations

import types

import openai
import pytest
from pydantic import ValidationError

from application.config import LLMConfigurationError
from application.dispute_generator import (
    DisputeGenerationOutputError,
    DisputeGenerationProviderError,
    DisputeGenerationTimeoutError,
    FakeDisputeBriefAdapter,
    OpenAIDisputeBriefAdapter,
)
from application.dispute_models import DisputeBrief
from application.models import ActionCode, Finding, SuggestedNextStep
from prompts.investigation_brief_prompt import PromptBundle

SAMPLE_BUNDLE = PromptBundle(system_prompt="system", user_prompt="user", prompt_version="v-test")

SAMPLE_BRIEF = DisputeBrief(
    summary="Sample.",
    findings=[Finding(statement="A fact.", evidence_refs=["claim:CLM-1001"])],
    missing_or_conflicting_evidence=[],
    verification_questions=[],
    suggested_next_step=SuggestedNextStep(
        action_code=ActionCode.EXPLAIN_RECORDED_STATUS, rationale="Because.", evidence_refs=[]
    ),
)


def test_openai_adapter_builds_no_client_at_construction():
    adapter = OpenAIDisputeBriefAdapter()
    assert adapter._client is None
    assert adapter.model_name is None


def test_openai_adapter_disables_sdk_automatic_retries(monkeypatch):
    """Module 6D Step 2B: one adapter-level generate() call is not
    necessarily one network attempt if the SDK's own retry logic is left
    at its default -- this spies on the ACTUAL openai.OpenAI(...)
    constructor call (not just reading the source) to prove max_retries=0
    is genuinely passed through, honoring the no-automatic-retry design at
    the SDK boundary, not merely at the application call-count boundary.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")

    captured_kwargs = {}

    class _SpyOpenAI:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)
            self.responses = types.SimpleNamespace(parse=lambda **k: types.SimpleNamespace(output_parsed=SAMPLE_BRIEF, status="completed"))

    monkeypatch.setattr(openai, "OpenAI", _SpyOpenAI)

    adapter = OpenAIDisputeBriefAdapter()
    adapter.generate(SAMPLE_BUNDLE)

    assert captured_kwargs.get("max_retries") == 0


def test_openai_adapter_propagates_configuration_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    adapter = OpenAIDisputeBriefAdapter()
    with pytest.raises(LLMConfigurationError):
        adapter.generate(SAMPLE_BUNDLE)


def test_openai_adapter_maps_sdk_error_to_provider_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    adapter = OpenAIDisputeBriefAdapter()

    class _FakeResponses:
        def parse(self, **kwargs):
            raise openai.APIConnectionError(request=types.SimpleNamespace())

    adapter._client = types.SimpleNamespace(responses=_FakeResponses())
    adapter.model_name = "fake-model"

    with pytest.raises(DisputeGenerationProviderError) as exc_info:
        adapter.generate(SAMPLE_BUNDLE)
    assert not isinstance(exc_info.value, DisputeGenerationTimeoutError)


def test_openai_adapter_maps_timeout_to_a_distinct_timeout_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    adapter = OpenAIDisputeBriefAdapter()

    class _FakeResponses:
        def parse(self, **kwargs):
            raise openai.APITimeoutError(request=types.SimpleNamespace())

    adapter._client = types.SimpleNamespace(responses=_FakeResponses())
    adapter.model_name = "fake-model"

    with pytest.raises(DisputeGenerationTimeoutError):
        adapter.generate(SAMPLE_BUNDLE)


def test_openai_adapter_maps_pydantic_validation_error_to_output_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    adapter = OpenAIDisputeBriefAdapter()

    class _FakeResponses:
        def parse(self, **kwargs):
            DisputeBrief(
                summary="ok",
                findings=[Finding(statement="ok", evidence_refs=[])],
                missing_or_conflicting_evidence=[],
                verification_questions=[],
                suggested_next_step=SuggestedNextStep(
                    action_code="NOT_A_REAL_ACTION_CODE", rationale="ok", evidence_refs=[]
                ),
            )

    adapter._client = types.SimpleNamespace(responses=_FakeResponses())
    adapter.model_name = "fake-model"

    with pytest.raises(DisputeGenerationOutputError) as exc_info:
        adapter.generate(SAMPLE_BUNDLE)
    assert not isinstance(exc_info.value, (DisputeGenerationProviderError, DisputeGenerationTimeoutError))
    assert isinstance(exc_info.value.__cause__, ValidationError)


def test_openai_adapter_maps_refused_output_to_output_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    adapter = OpenAIDisputeBriefAdapter()

    class _FakeResponses:
        def parse(self, **kwargs):
            return types.SimpleNamespace(output_parsed=None, status="incomplete")

    adapter._client = types.SimpleNamespace(responses=_FakeResponses())
    adapter.model_name = "fake-model"

    with pytest.raises(DisputeGenerationOutputError):
        adapter.generate(SAMPLE_BUNDLE)


def test_openai_adapter_returns_parsed_brief_on_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    adapter = OpenAIDisputeBriefAdapter()
    captured_kwargs = {}

    class _FakeResponses:
        def parse(self, **kwargs):
            captured_kwargs.update(kwargs)
            return types.SimpleNamespace(output_parsed=SAMPLE_BRIEF, status="completed")

    adapter._client = types.SimpleNamespace(responses=_FakeResponses())
    adapter.model_name = "fake-model"

    result = adapter.generate(SAMPLE_BUNDLE)

    assert result is SAMPLE_BRIEF
    assert captured_kwargs["model"] == "fake-model"
    assert captured_kwargs["instructions"] == "system"
    assert captured_kwargs["input"] == "user"
    assert captured_kwargs["text_format"] is DisputeBrief


def test_fake_adapter_default_response_is_clearly_labeled_mocked():
    adapter = FakeDisputeBriefAdapter()
    brief = adapter.generate(SAMPLE_BUNDLE)
    assert "MOCKED" in brief.summary
    assert adapter.calls == [SAMPLE_BUNDLE]


def test_fake_adapter_can_return_a_fixed_response():
    adapter = FakeDisputeBriefAdapter(response=SAMPLE_BRIEF)
    assert adapter.generate(SAMPLE_BUNDLE) is SAMPLE_BRIEF


def test_fake_adapter_can_raise_a_fixed_exception():
    adapter = FakeDisputeBriefAdapter(raises=DisputeGenerationProviderError("boom"))
    with pytest.raises(DisputeGenerationProviderError):
        adapter.generate(SAMPLE_BUNDLE)
