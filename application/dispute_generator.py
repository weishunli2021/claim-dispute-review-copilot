"""Module 6B: exactly one thin LLM adapter for the dispute-review brief,
mirroring application/llm_adapter.py's structure and conventions exactly
(same OpenAI Responses API structured-output call shape, same
max_retries=0, same lazy client construction, same exception taxonomy).

The `openai` import lives only in this module (plus its test double) and
in application/llm_adapter.py / application/semantic_judge.py --
application/ is the one place in this project allowed to import the SDK
(see tests/test_project_structure.py's
test_openai_sdk_confined_to_generation_adapter, which walks agents/,
skills/, tools/, rag/, graph/, context/ and asserts none of them import
openai; application/ is deliberately excluded from that walk, so adding a
second application/ adapter here requires no change to that test's
allowlist).

No live client is constructed and no API call happens at import time --
same lazy-construction guarantee as the original adapter. Reuses
application.config.load_llm_config() as-is: NO new environment variable,
NO new mandatory model name -- the dispute brief is generated with the
exact same OPENAI_API_KEY/LLM_MODEL/LLM_TIMEOUT_SECONDS configuration the
original investigation brief already uses.
"""

from __future__ import annotations

from typing import Optional, Protocol

import openai
from pydantic import ValidationError

from application.config import LLMConfigurationError, load_llm_config
from application.dispute_models import DisputeBrief
from prompts.investigation_brief_prompt import PromptBundle


class DisputeGenerationProviderError(RuntimeError):
    """The provider request itself failed (auth, connection, rate limit,
    5xx, ...). Distinct from a malformed/refused response, which is
    DisputeGenerationOutputError."""


class DisputeGenerationTimeoutError(DisputeGenerationProviderError):
    """The provider request exceeded LLM_TIMEOUT_SECONDS. A subclass of
    DisputeGenerationProviderError, raised as its own type so
    DisputeGenerationFailureCategory.TIMEOUT can be recorded distinctly --
    mirrors application/llm_adapter.py: LLMTimeoutError."""


class DisputeGenerationOutputError(RuntimeError):
    """The provider responded, but no valid DisputeBrief could be parsed
    from it (the model refused, returned an incomplete response, or
    otherwise produced nothing text_format-parseable)."""


class DisputeBriefAdapter(Protocol):
    """The interface application.dispute_workflow depends on. Both
    OpenAIDisputeBriefAdapter (real) and FakeDisputeBriefAdapter (test
    double) implement this."""

    name: str
    model_name: Optional[str]

    def generate(self, prompt_bundle: PromptBundle) -> DisputeBrief: ...


class OpenAIDisputeBriefAdapter:
    """Real adapter. Constructs its openai.OpenAI client lazily (on first
    generate() call) with max_retries=0 (explicit -- no automatic model
    retries anywhere in this workflow) and the configured request timeout.
    Never logs, prints, or returns the API key.
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

    def generate(self, prompt_bundle: PromptBundle) -> DisputeBrief:
        client = self._ensure_client()
        try:
            response = client.responses.parse(
                model=self.model_name,
                instructions=prompt_bundle.system_prompt,
                input=prompt_bundle.user_prompt,
                text_format=DisputeBrief,
            )
        except openai.APITimeoutError as exc:
            raise DisputeGenerationTimeoutError(f"OpenAI request timed out: {exc}") from exc
        except ValidationError as exc:
            raise DisputeGenerationOutputError(
                f"Model returned structured output that failed DisputeBrief validation: {exc}"
            ) from exc
        except openai.OpenAIError as exc:
            raise DisputeGenerationProviderError(f"OpenAI request failed: {exc}") from exc

        if response.output_parsed is None:
            raise DisputeGenerationOutputError(
                f"Model did not return a parsable DisputeBrief (response status="
                f"{getattr(response, 'status', 'unknown')!r}); treating as refused/incomplete output."
            )
        return response.output_parsed


class FakeDisputeBriefAdapter:
    """Test double / non-live default. Never imports or calls the real
    SDK. Records every prompt_bundle it was called with (`self.calls`) so
    tests can assert on call count (must be exactly 0 or 1 per workflow
    run) and on what was actually sent, and can be configured to return a
    fixed brief or raise a fixed exception to exercise every
    DisputeGenerationStatus branch.
    """

    name = "fake"

    def __init__(
        self,
        response: Optional[DisputeBrief] = None,
        raises: Optional[BaseException] = None,
        model_name: str = "fake-dispute-adapter-no-live-call",
    ) -> None:
        self._response = response
        self._raises = raises
        self.model_name = model_name
        self.calls: list[PromptBundle] = []

    def generate(self, prompt_bundle: PromptBundle) -> DisputeBrief:
        self.calls.append(prompt_bundle)
        if self._raises is not None:
            raise self._raises
        if self._response is not None:
            return self._response
        return _default_mocked_brief()


def _default_mocked_brief() -> DisputeBrief:
    """A clearly-labeled canned brief used only when FakeDisputeBriefAdapter
    is given no explicit response. Never presented as a live model result."""
    from application.models import ActionCode, Finding, SuggestedNextStep

    return DisputeBrief(
        summary=(
            "[MOCKED OUTPUT -- no live model call was made] This is a fixed placeholder dispute "
            "brief returned by FakeDisputeBriefAdapter, not a generated result."
        ),
        findings=[
            Finding(
                statement="No live model call was made; this finding is a fixed placeholder.",
                evidence_refs=[],
            )
        ],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.HUMAN_REVIEW,
            rationale="Placeholder output must not be treated as a real recommendation.",
            evidence_refs=[],
        ),
    )
