"""H6 (OPTIONAL evaluation sidecar): the experimental semantic judge.

    ApplicationResult (already DRAFTED + H2 PASSED)
      -> prompts.investigation_judge_prompt.build_judge_prompt
      -> JudgeAdapter.evaluate()          <- the ONE judge model call
      -> SemanticJudgeEnvelope

This module is completely isolated from the H1/H2 generation path:
- it does NOT import or modify application/llm_adapter.py,
  application/investigation_service.py, or application/brief_validator.py;
- it never calls agents.case_agent.run_case_agent (no evidence re-run);
- it makes AT MOST ONE model call per invocation, with max_retries=0, same
  as the generation adapter;
- a FAILED judge_status is returned as data -- it never raises out of
  run_semantic_judge, and it never mutates the ApplicationResult it was
  given. The InvestigationBrief the caller already has remains fully
  intact and usable regardless of what happens here.

Only call run_semantic_judge() when
`result.generation_status == GenerationStatus.DRAFTED and
result.validation_result.status == ValidationStatus.PASSED`
(application.workbench.is_accepted_draft) -- it raises ValueError
otherwise, the same guard convention as application/review_packet.py's
NEEDS_REVIEW-only check.

H8 adds a 1-5 rubric score per dimension (see application/judge_models.py).
The model is only ever constrained to RawJudgeDimensions (no
overall_result/overall_score field) -- OpenAISemanticJudgeAdapter.evaluate
computes both deterministically via SemanticJudgeResult.from_dimensions
after parsing, so this module still makes AT MOST ONE model call and never
asks the model to invent the overall arithmetic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Protocol

import openai
from pydantic import ValidationError

from application.config import LLMConfigurationError, load_llm_config
from application.judge_models import (
    JudgeFailureCategory,
    JudgeStatus,
    RawJudgeDimensions,
    SemanticJudgeEnvelope,
    SemanticJudgeMetadata,
    SemanticJudgeResult,
)
from application.models import ApplicationResult
from prompts.investigation_judge_prompt import build_judge_prompt
from prompts.investigation_brief_prompt import PromptBundle


class JudgeProviderError(RuntimeError):
    """The judge provider request itself failed (auth, connection, rate
    limit, 5xx, ...). Distinct from JudgeOutputError -- see
    run_semantic_judge's JudgeFailureCategory mapping."""


class JudgeTimeoutError(JudgeProviderError):
    """The judge request exceeded the configured timeout. A subclass of
    JudgeProviderError, raised as its own type so
    JudgeFailureCategory.TIMEOUT can be recorded distinctly from a
    generic provider failure -- mirrors
    application/llm_adapter.py: LLMTimeoutError."""


class JudgeOutputError(RuntimeError):
    """The provider responded, but no valid SemanticJudgeResult could be
    parsed from it (refused, incomplete, or otherwise unparseable)."""


class JudgeAdapter(Protocol):
    name: str
    model_name: Optional[str]

    def evaluate(self, prompt_bundle: PromptBundle) -> SemanticJudgeResult: ...


class OpenAISemanticJudgeAdapter:
    """Real adapter. Reuses application.config.load_llm_config() -- the
    SAME OPENAI_API_KEY/LLM_MODEL/LLM_TIMEOUT_SECONDS configuration the
    generation adapter uses, since H6 introduces no new configuration
    surface. Constructs its own openai.OpenAI client lazily (on first
    evaluate() call) with max_retries=0. Never logs, prints, or returns
    the API key. Does NOT touch application/llm_adapter.py.
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

    def evaluate(self, prompt_bundle: PromptBundle) -> SemanticJudgeResult:
        client = self._ensure_client()
        try:
            # H8: the model is constrained to RawJudgeDimensions -- it has
            # no overall_result/overall_score field at all, so the model
            # structurally cannot invent the overall arithmetic/verdict;
            # both are computed deterministically below via from_dimensions.
            response = client.responses.parse(
                model=self.model_name,
                instructions=prompt_bundle.system_prompt,
                input=prompt_bundle.user_prompt,
                text_format=RawJudgeDimensions,
            )
        except openai.APITimeoutError as exc:
            raise JudgeTimeoutError(f"Judge request timed out: {exc}") from exc
        except ValidationError as exc:
            # A per-dimension score/verdict combination that fails
            # DimensionResult's coherence check (e.g. score=5 + FAIL)
            # surfaces here as a pydantic ValidationError raised while the
            # SDK builds RawJudgeDimensions from the model's JSON -- treat
            # it the same as any other unparseable structured output.
            raise JudgeOutputError(
                f"Judge returned an internally inconsistent structured result "
                f"(e.g. a score/verdict combination that contradicts itself): {exc}"
            ) from exc
        except openai.OpenAIError as exc:
            raise JudgeProviderError(f"Judge request failed: {exc}") from exc

        if response.output_parsed is None:
            raise JudgeOutputError(
                f"Judge did not return a parsable structured result (response status="
                f"{getattr(response, 'status', 'unknown')!r})."
            )
        return SemanticJudgeResult.from_dimensions(response.output_parsed)


class FakeSemanticJudgeAdapter:
    """Test double. Never imports or calls the real SDK. Records every
    prompt_bundle it was called with (`self.calls`) so tests can assert
    on call count (must be exactly 0 or 1, never more), and can be
    configured to return a fixed SemanticJudgeResult or raise a fixed
    exception to exercise every JudgeStatus branch.
    """

    name = "fake"

    def __init__(
        self,
        response: Optional[SemanticJudgeResult] = None,
        raises: Optional[BaseException] = None,
        model_name: str = "fake-judge-adapter-no-live-call",
    ) -> None:
        self._response = response
        self._raises = raises
        self.model_name = model_name
        self.calls: list[PromptBundle] = []

    def evaluate(self, prompt_bundle: PromptBundle) -> SemanticJudgeResult:
        self.calls.append(prompt_bundle)
        if self._raises is not None:
            raise self._raises
        if self._response is not None:
            return self._response
        raise RuntimeError(
            "FakeSemanticJudgeAdapter requires an explicit response= or raises= for tests -- "
            "there is no default canned verdict (unlike FakeInvestigationBriefAdapter's mocked "
            "brief), since a judge result should never be treated as a safe default."
        )


def run_semantic_judge(
    result: ApplicationResult, *, adapter: Optional[JudgeAdapter] = None
) -> SemanticJudgeEnvelope:
    """Run the OPTIONAL H6 semantic judge against an already-drafted,
    already-H2-validated InvestigationBrief.

    Requires `result.investigation_brief is not None` and
    `result.assembled_context is not None` -- raises ValueError otherwise.
    Callers (app.py) must only invoke this when
    application.workbench.is_accepted_draft(result) is True.

    Makes AT MOST ONE model call. Never raises for a provider/timeout/
    config/parsing failure -- those are returned as
    JudgeStatus.FAILED + judge_failure_category, exactly mirroring
    application/investigation_service.py's GenerationStatus.FAILED
    mapping. Never mutates `result`.
    """
    if result.investigation_brief is None or result.assembled_context is None:
        raise ValueError(
            "run_semantic_judge requires an already-drafted InvestigationBrief and its "
            "AssembledContext -- call it only when "
            "application.workbench.is_accepted_draft(result) is True."
        )

    # Reuse the INVESTIGATION's own run_id (not a freshly-generated one) --
    # this is what application/judge_models.py's SemanticJudgeEnvelope
    # docstring already documents ("the ApplicationResult.run_id this
    # judgment was computed against"), and it is what app.py's staleness
    # guard (`envelope.run_id != result.run_id`) actually compares against.
    # A fresh uuid4() here would never equal result.run_id, silently
    # failing that guard on every single invocation -- see
    # docs/HUMANA_BUILD_STATUS.md's H6 bugfix entry for the diagnosis.
    run_id = result.run_id
    prompt_bundle = build_judge_prompt(result.query, result.assembled_context, result.investigation_brief)
    resolved_adapter: JudgeAdapter = adapter if adapter is not None else OpenAISemanticJudgeAdapter()

    try:
        judge_result = resolved_adapter.evaluate(prompt_bundle)
    except LLMConfigurationError as exc:
        return SemanticJudgeEnvelope(
            run_id=run_id,
            claim_id=result.claim_id,
            query=result.query,
            judge_status=JudgeStatus.FAILED,
            judge_failure_category=JudgeFailureCategory.CONFIGURATION,
            error=str(exc),
        )
    except JudgeTimeoutError as exc:
        return SemanticJudgeEnvelope(
            run_id=run_id,
            claim_id=result.claim_id,
            query=result.query,
            judge_status=JudgeStatus.FAILED,
            judge_failure_category=JudgeFailureCategory.TIMEOUT,
            error=str(exc),
        )
    except JudgeProviderError as exc:
        return SemanticJudgeEnvelope(
            run_id=run_id,
            claim_id=result.claim_id,
            query=result.query,
            judge_status=JudgeStatus.FAILED,
            judge_failure_category=JudgeFailureCategory.PROVIDER,
            error=str(exc),
        )
    except JudgeOutputError as exc:
        return SemanticJudgeEnvelope(
            run_id=run_id,
            claim_id=result.claim_id,
            query=result.query,
            judge_status=JudgeStatus.FAILED,
            judge_failure_category=JudgeFailureCategory.STRUCTURED_PARSING,
            error=str(exc),
        )

    return SemanticJudgeEnvelope(
        run_id=run_id,
        claim_id=result.claim_id,
        query=result.query,
        judge_status=JudgeStatus.COMPLETED,
        result=judge_result,
        metadata=SemanticJudgeMetadata(
            model=resolved_adapter.model_name or "unknown",
            prompt_version=prompt_bundle.prompt_version,
            evaluated_at=datetime.now(timezone.utc),
        ),
    )
