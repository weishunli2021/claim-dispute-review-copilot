"""Tests for application/llm_adapter.py and application/config.py.

Fully offline: OpenAIInvestigationBriefAdapter is exercised with a fake
OpenAI client injected in place of the real SDK client (monkeypatched onto
the instance after construction is bypassed), never a real network call.
FakeInvestigationBriefAdapter is exercised directly.
"""

from __future__ import annotations

import types

import openai
import pytest
from pydantic import ValidationError

from application.config import LLMConfigurationError, load_llm_config
from application.llm_adapter import (
    FakeInvestigationBriefAdapter,
    LLMOutputError,
    LLMProviderError,
    LLMTimeoutError,
    OpenAIInvestigationBriefAdapter,
)
from application.models import ActionCode, Finding, InvestigationBrief, SuggestedNextStep
from prompts.investigation_brief_prompt import PromptBundle

SAMPLE_BUNDLE = PromptBundle(system_prompt="system", user_prompt="user", prompt_version="v-test")

SAMPLE_BRIEF = InvestigationBrief(
    summary="Sample.",
    findings=[Finding(statement="A fact.", evidence_refs=["claim:CLM-1001"])],
    missing_or_conflicting_evidence=[],
    suggested_next_step=SuggestedNextStep(
        action_code=ActionCode.EXPLAIN_RECORDED_STATUS, rationale="Because.", evidence_refs=[]
    ),
)


def test_load_llm_config_missing_env_raises_configuration_error(monkeypatch):
    # Set to "" rather than delenv(): load_llm_config() calls load_dotenv(),
    # which does NOT override a variable already present in os.environ (even
    # an empty one) but WILL repopulate a variable that was deleted outright
    # from a real local .env file. Blank-but-present is what actually
    # simulates "unconfigured" in an environment where .env holds real
    # values -- delenv() alone no longer does, once .env is populated.
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    with pytest.raises(LLMConfigurationError) as exc_info:
        load_llm_config()
    assert "OPENAI_API_KEY" in str(exc_info.value)
    assert "LLM_MODEL" in str(exc_info.value)


def test_load_llm_config_invalid_timeout_raises_configuration_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "not-a-number")
    with pytest.raises(LLMConfigurationError):
        load_llm_config()


def test_load_llm_config_success_never_exposed_beyond_fields(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    config = load_llm_config()
    assert config.api_key == "sk-should-not-leak"
    assert config.model == "fake-model"
    assert config.timeout_seconds == 30.0


def test_openai_adapter_builds_no_client_at_construction():
    adapter = OpenAIInvestigationBriefAdapter()
    assert adapter._client is None
    assert adapter.model_name is None


def test_openai_adapter_propagates_configuration_error(monkeypatch):
    # See test_load_llm_config_missing_env_raises_configuration_error's
    # comment: setenv("", ...) rather than delenv(), so a real local .env
    # (loaded via load_dotenv() inside load_llm_config()) cannot silently
    # repopulate these for this test.
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    adapter = OpenAIInvestigationBriefAdapter()
    with pytest.raises(LLMConfigurationError):
        adapter.generate(SAMPLE_BUNDLE)


def test_openai_adapter_maps_sdk_error_to_provider_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")

    adapter = OpenAIInvestigationBriefAdapter()

    class _FakeResponses:
        def parse(self, **kwargs):
            raise openai.APIConnectionError(request=types.SimpleNamespace())

    adapter._client = types.SimpleNamespace(responses=_FakeResponses())
    adapter.model_name = "fake-model"

    with pytest.raises(LLMProviderError) as exc_info:
        adapter.generate(SAMPLE_BUNDLE)
    assert not isinstance(exc_info.value, LLMTimeoutError)


def test_openai_adapter_maps_timeout_to_a_distinct_timeout_error(monkeypatch):
    """LLMTimeoutError must be raised (not the generic LLMProviderError)
    for openai.APITimeoutError specifically, so
    application/investigation_service.py can record
    GenerationFailureCategory.TIMEOUT distinctly from a generic provider
    failure -- see that module's exception-handling order."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")

    adapter = OpenAIInvestigationBriefAdapter()

    class _FakeResponses:
        def parse(self, **kwargs):
            raise openai.APITimeoutError(request=types.SimpleNamespace())

    adapter._client = types.SimpleNamespace(responses=_FakeResponses())
    adapter.model_name = "fake-model"

    with pytest.raises(LLMTimeoutError):
        adapter.generate(SAMPLE_BUNDLE)


def test_openai_adapter_maps_pydantic_validation_error_to_output_error(monkeypatch):
    """Post-audit remediation regression test: application/semantic_judge.py
    already catches pydantic.ValidationError around client.responses.parse(...)
    and maps it to a structured-parsing failure -- application/llm_adapter.py
    did not mirror that behavior for InvestigationBrief generation before
    this fix, so a genuinely malformed-but-schema-adjacent model response
    (one the SDK's own JSON parsing accepts but InvestigationBrief's own
    Pydantic validation rejects) would have raised a raw, unhandled
    ValidationError out of generate() instead of the existing LLMOutputError
    contract. The fake transport raises the REAL pydantic.ValidationError
    itself (not a stand-in LLMOutputError) so this test exercises the real
    adapter's exception-classification boundary, not a fabricated shortcut.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")

    adapter = OpenAIInvestigationBriefAdapter()

    class _FakeResponses:
        def parse(self, **kwargs):
            # Simulate the SDK raising while constructing InvestigationBrief
            # from the model's JSON -- exactly what a real pydantic.ValidationError
            # from client.responses.parse(...) looks like to this adapter.
            InvestigationBrief(
                summary="ok",
                findings=[Finding(statement="ok", evidence_refs=[])],
                missing_or_conflicting_evidence=[],
                suggested_next_step=SuggestedNextStep(
                    action_code="NOT_A_REAL_ACTION_CODE",  # invalid enum value -> ValidationError
                    rationale="ok",
                    evidence_refs=[],
                ),
            )

    adapter._client = types.SimpleNamespace(responses=_FakeResponses())
    adapter.model_name = "fake-model"

    with pytest.raises(LLMOutputError) as exc_info:
        adapter.generate(SAMPLE_BUNDLE)
    assert not isinstance(exc_info.value, (LLMProviderError, LLMTimeoutError))
    assert isinstance(exc_info.value.__cause__, ValidationError)


def test_openai_adapter_maps_refused_output_to_output_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")

    adapter = OpenAIInvestigationBriefAdapter()

    class _FakeResponses:
        def parse(self, **kwargs):
            return types.SimpleNamespace(output_parsed=None, status="incomplete")

    adapter._client = types.SimpleNamespace(responses=_FakeResponses())
    adapter.model_name = "fake-model"

    with pytest.raises(LLMOutputError):
        adapter.generate(SAMPLE_BUNDLE)


def test_openai_adapter_returns_parsed_brief_on_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")

    adapter = OpenAIInvestigationBriefAdapter()
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
    assert captured_kwargs["text_format"] is InvestigationBrief


def test_fake_adapter_default_response_is_clearly_labeled_mocked():
    adapter = FakeInvestigationBriefAdapter()
    brief = adapter.generate(SAMPLE_BUNDLE)
    assert "MOCKED" in brief.summary
    assert adapter.calls == [SAMPLE_BUNDLE]


def test_fake_adapter_can_return_a_fixed_response():
    adapter = FakeInvestigationBriefAdapter(response=SAMPLE_BRIEF)
    assert adapter.generate(SAMPLE_BUNDLE) is SAMPLE_BRIEF


def test_fake_adapter_can_raise_a_fixed_exception():
    adapter = FakeInvestigationBriefAdapter(raises=LLMProviderError("boom"))
    with pytest.raises(LLMProviderError):
        adapter.generate(SAMPLE_BUNDLE)
