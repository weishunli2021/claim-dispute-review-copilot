"""Exactly one thin LLM adapter: the OpenAI Python SDK's Responses API,
using its structured-output parsing helper (`client.responses.parse`) with
InvestigationBrief as the Pydantic `text_format`. No provider failover, no
model routing, no tool-calling, no plugin infrastructure -- one provider,
one call shape.

The `openai` import lives only in this module (plus its test doubles) --
see tests/test_project_structure.py's
test_openai_sdk_confined_to_generation_adapter for the enforced boundary:
agents/, skills/, tools/, context/, rag/, graph/ must never import it.

No live client is constructed and no API call happens at import time.
OpenAIInvestigationBriefAdapter reads configuration and builds its client
lazily, on the first generate() call, so importing this module (or the
rest of application/) never requires OPENAI_API_KEY to be set.
"""

from __future__ import annotations

from typing import Optional, Protocol

import openai
from pydantic import ValidationError

from application.config import LLMConfigurationError, load_llm_config
from application.models import InvestigationBrief
from prompts.investigation_brief_prompt import PromptBundle


class LLMProviderError(RuntimeError):
    """The provider request itself failed (auth, connection, rate limit,
    5xx, ...). Distinct from a malformed/refused response, which is
    LLMOutputError -- the caller needs to tell these apart (see
    application/investigation_service.py's GenerationStatus mapping)."""


class LLMTimeoutError(LLMProviderError):
    """The provider request exceeded LLM_TIMEOUT_SECONDS. A subclass of
    LLMProviderError (an `except LLMProviderError` still catches it), but
    raised as its own type so application/investigation_service.py can
    record GenerationFailureCategory.TIMEOUT distinctly from a generic
    provider/API failure -- per H2's "at minimum distinguish ... timeout"
    requirement."""


class LLMOutputError(RuntimeError):
    """The provider responded, but no valid InvestigationBrief could be
    parsed from it (the model refused, returned an incomplete response, or
    otherwise produced nothing `text_format`-parseable)."""


class BriefAdapter(Protocol):
    """The interface application.investigation_service depends on. Both
    OpenAIInvestigationBriefAdapter (real) and FakeInvestigationBriefAdapter
    (offline tests / the CLI's default, non---live mode) implement this."""

    name: str
    model_name: Optional[str]

    def generate(self, prompt_bundle: PromptBundle) -> InvestigationBrief: ...


class OpenAIInvestigationBriefAdapter:
    """Real adapter. Constructs its openai.OpenAI client lazily (on first
    generate() call) with max_retries=0 (explicit, for the demo -- see
    AGENTS.md/H1 scope) and the configured request timeout. Never logs,
    prints, or returns the API key.
    """

    name = "openai"

    def __init__(self) -> None:
        self._client: Optional["openai.OpenAI"] = None
        self.model_name: Optional[str] = None

    def _ensure_client(self) -> "openai.OpenAI":
        if self._client is None:
            config = load_llm_config()  # raises LLMConfigurationError if unset -- propagates as-is
            self._client = openai.OpenAI(
                api_key=config.api_key,
                timeout=config.timeout_seconds,
                max_retries=0,
            )
            self.model_name = config.model
        return self._client

    def generate(self, prompt_bundle: PromptBundle) -> InvestigationBrief:
        client = self._ensure_client()
        try:
            response = client.responses.parse(
                model=self.model_name,
                instructions=prompt_bundle.system_prompt,
                input=prompt_bundle.user_prompt,
                text_format=InvestigationBrief,
            )
        except openai.APITimeoutError as exc:
            raise LLMTimeoutError(f"OpenAI request timed out: {exc}") from exc
        except ValidationError as exc:
            # The SDK constructs InvestigationBrief from the model's raw
            # JSON as part of client.responses.parse(...) -- if that JSON
            # fails InvestigationBrief's own Pydantic validation (mirrors
            # application/semantic_judge.py's identical boundary for the
            # judge's RawJudgeDimensions), treat it the same as any other
            # unparseable structured output rather than letting a raw
            # ValidationError escape run_investigation.
            raise LLMOutputError(
                f"Model returned structured output that failed InvestigationBrief validation: {exc}"
            ) from exc
        except openai.OpenAIError as exc:
            raise LLMProviderError(f"OpenAI request failed: {exc}") from exc

        if response.output_parsed is None:
            raise LLMOutputError(
                f"Model did not return a parsable InvestigationBrief (response status="
                f"{getattr(response, 'status', 'unknown')!r}); treating as refused/incomplete output."
            )
        return response.output_parsed


class FakeInvestigationBriefAdapter:
    """Test double / CLI default (non---live mode). Never imports or calls
    the real SDK. Records every prompt_bundle it was called with
    (`self.calls`) so tests can assert on what the service actually sent
    it, and can be configured to return a fixed brief or raise a fixed
    exception to exercise every GenerationStatus branch.
    """

    name = "fake"

    def __init__(
        self,
        response: Optional[InvestigationBrief] = None,
        raises: Optional[BaseException] = None,
        model_name: str = "fake-adapter-no-live-call",
    ) -> None:
        self._response = response
        self._raises = raises
        self.model_name = model_name
        self.calls: list[PromptBundle] = []

    def generate(self, prompt_bundle: PromptBundle) -> InvestigationBrief:
        self.calls.append(prompt_bundle)
        if self._raises is not None:
            raise self._raises
        if self._response is not None:
            return self._response
        return _default_mocked_brief()


def _default_mocked_brief() -> InvestigationBrief:
    """A clearly-labeled canned brief used only when FakeInvestigationBriefAdapter
    is given no explicit response -- e.g. the CLI's default (non---live) mode.
    Never presented as a live model result (see application/cli.py, which
    labels every mocked run in its printed output)."""
    from application.models import ActionCode, Finding, SuggestedNextStep

    return InvestigationBrief(
        summary=(
            "[MOCKED OUTPUT -- no live model call was made] This is a fixed placeholder brief "
            "returned by FakeInvestigationBriefAdapter, not a generated result."
        ),
        findings=[
            Finding(
                statement="No live model call was made; this finding is a fixed placeholder.",
                evidence_refs=[],
            )
        ],
        missing_or_conflicting_evidence=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.HUMAN_REVIEW,
            rationale="Placeholder output must not be treated as a real recommendation.",
            evidence_refs=[],
        ),
    )
