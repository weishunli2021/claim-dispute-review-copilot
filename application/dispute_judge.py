"""Module 6B: the OPTIONAL, separate dispute-review judge.

    DisputeWorkflowResult (already DRAFTED + validation PASSED)
      -> prompts.dispute_judge_prompt.build_dispute_judge_prompt
      -> DisputeJudgeAdapter.evaluate()          <- the ONE judge model call
      -> DisputeJudgeEnvelope

Completely isolated from the generation/validation path: does NOT import
or modify application/dispute_workflow.py, application/dispute_generator.py,
or application/dispute_brief_validator.py; never re-runs investigate_dispute
or the workflow; makes AT MOST ONE model call per invocation, with
max_retries=0; a FAILED judge_status is returned as data -- it never
raises out of run_dispute_judge, and it never mutates the
DisputeWorkflowResult it was given. Mirrors application/semantic_judge.py's
exact discipline and structure.

Only call run_dispute_judge() when
`application.dispute_models.is_accepted_dispute_draft(result)` is True --
raises ValueError otherwise, the same guard convention
application/semantic_judge.py already uses (`result.investigation_brief is
None or result.assembled_context is None` -> ValueError).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Protocol

import openai
from pydantic import ValidationError

from application.config import LLMConfigurationError, load_llm_config
from application.dispute_judge_models import (
    DisputeJudgeEnvelope,
    DisputeJudgeFailureCategory,
    DisputeJudgeMetadata,
    DisputeJudgeResult,
    DisputeJudgeStatus,
    RawDisputeJudgeDimensions,
)
from application.dispute_models import DisputeWorkflowResult, is_accepted_dispute_draft
from prompts.dispute_judge_prompt import build_dispute_judge_prompt
from prompts.investigation_brief_prompt import PromptBundle


class DisputeJudgeProviderError(RuntimeError):
    """The judge provider request itself failed (auth, connection, rate
    limit, 5xx, ...). Distinct from DisputeJudgeOutputError."""


class DisputeJudgeTimeoutError(DisputeJudgeProviderError):
    """The judge request exceeded the configured timeout. A subclass of
    DisputeJudgeProviderError, raised as its own type so
    DisputeJudgeFailureCategory.TIMEOUT can be recorded distinctly."""


class DisputeJudgeOutputError(RuntimeError):
    """The provider responded, but no valid DisputeJudgeResult could be
    parsed from it (refused, incomplete, internally inconsistent, or
    otherwise unparseable)."""


class DisputeUnknownReferenceError(RuntimeError):
    """The judge cited an evidence_refs entry that does not exist in the
    context it was actually shown -- Module 6B Step 8's "validate judge
    references against its supplied context" requirement. Mapped to
    DisputeJudgeFailureCategory.UNKNOWN_REFERENCE, a category unique to
    this judge (the original semantic judge performs no equivalent check)."""


class DisputeJudgeAdapter(Protocol):
    name: str
    model_name: Optional[str]

    def evaluate(self, prompt_bundle: PromptBundle) -> DisputeJudgeResult: ...


class OpenAIDisputeJudgeAdapter:
    """Real adapter. Reuses application.config.load_llm_config() -- the
    SAME configuration the generation adapter uses; no new configuration
    surface. Constructs its own openai.OpenAI client lazily, max_retries=0.
    Never logs, prints, or returns the API key."""

    name = "openai"

    def __init__(self) -> None:
        self._client: Optional["openai.OpenAI"] = None
        self.model_name: Optional[str] = None

    def _ensure_client(self) -> "openai.OpenAI":
        if self._client is None:
            config = load_llm_config()
            self._client = openai.OpenAI(
                api_key=config.api_key,
                timeout=config.timeout_seconds,
                max_retries=0,
            )
            self.model_name = config.model
        return self._client

    def evaluate(self, prompt_bundle: PromptBundle) -> DisputeJudgeResult:
        client = self._ensure_client()
        try:
            # The model is constrained to RawDisputeJudgeDimensions -- it
            # has no overall_result/overall_score field at all, so it
            # structurally cannot invent the overall arithmetic; both are
            # computed deterministically below via from_dimensions.
            response = client.responses.parse(
                model=self.model_name,
                instructions=prompt_bundle.system_prompt,
                input=prompt_bundle.user_prompt,
                text_format=RawDisputeJudgeDimensions,
            )
        except openai.APITimeoutError as exc:
            raise DisputeJudgeTimeoutError(f"Judge request timed out: {exc}") from exc
        except ValidationError as exc:
            raise DisputeJudgeOutputError(
                f"Judge returned an internally inconsistent structured result: {exc}"
            ) from exc
        except openai.OpenAIError as exc:
            raise DisputeJudgeProviderError(f"Judge request failed: {exc}") from exc

        if response.output_parsed is None:
            raise DisputeJudgeOutputError(
                f"Judge did not return a parsable structured result (response status="
                f"{getattr(response, 'status', 'unknown')!r})."
            )
        return DisputeJudgeResult.from_dimensions(response.output_parsed)


class FakeDisputeJudgeAdapter:
    """Test double. Never imports or calls the real SDK. Records every
    prompt_bundle it was called with (`self.calls`) so tests can assert on
    call count (must be exactly 0 or 1, never more), and can be configured
    to return a fixed DisputeJudgeResult or raise a fixed exception."""

    name = "fake"

    def __init__(
        self,
        response: Optional[DisputeJudgeResult] = None,
        raises: Optional[BaseException] = None,
        model_name: str = "fake-dispute-judge-no-live-call",
    ) -> None:
        self._response = response
        self._raises = raises
        self.model_name = model_name
        self.calls: list[PromptBundle] = []

    def evaluate(self, prompt_bundle: PromptBundle) -> DisputeJudgeResult:
        self.calls.append(prompt_bundle)
        if self._raises is not None:
            raise self._raises
        if self._response is not None:
            return self._response
        raise RuntimeError(
            "FakeDisputeJudgeAdapter requires an explicit response= or raises= for tests -- there "
            "is no default canned verdict, since a judge result should never be treated as a safe "
            "default."
        )


def _validate_judge_references(judge_result: DisputeJudgeResult, known_ref_ids: set[str]) -> None:
    """Raises DisputeUnknownReferenceError if any dimension cites a
    reference id that does not exist in the context the judge was shown."""
    dimensions = [
        judge_result.evidence_grounding,
        judge_result.coverage,
        judge_result.uncertainty_and_provenance,
        judge_result.authority_boundaries,
    ]
    for dimension in dimensions:
        for ref in dimension.evidence_refs:
            if ref not in known_ref_ids:
                raise DisputeUnknownReferenceError(
                    f"Judge cited evidence_refs entry {ref!r}, which does not exist in the "
                    "context supplied to it."
                )


def run_dispute_judge(
    result: DisputeWorkflowResult, *, adapter: Optional[DisputeJudgeAdapter] = None
) -> DisputeJudgeEnvelope:
    """Run the OPTIONAL dispute-review judge against an already-drafted,
    already-validated DisputeBrief.

    Requires `is_accepted_dispute_draft(result)` -- raises ValueError
    otherwise. Makes AT MOST ONE model call. Never raises for a
    provider/timeout/config/parsing/unknown-reference failure -- those are
    returned as DisputeJudgeStatus.FAILED + judge_failure_category. Never
    mutates `result`: the comparison, evidence, brief, and deterministic
    validation the caller already has remain fully intact regardless of
    what happens here.
    """
    if not is_accepted_dispute_draft(result):
        raise ValueError(
            "run_dispute_judge requires an already-drafted, already-validated DisputeBrief -- "
            "call it only when application.dispute_models.is_accepted_dispute_draft(result) is True."
        )
    assert result.brief is not None and result.generation_context is not None  # narrowed by the guard above

    run_id = result.run_id
    prompt_bundle = build_dispute_judge_prompt(result.generation_context, result.brief)
    resolved_adapter: DisputeJudgeAdapter = adapter if adapter is not None else OpenAIDisputeJudgeAdapter()
    known_ref_ids = {ref.ref_id for ref in result.generation_context.references}

    try:
        judge_result = resolved_adapter.evaluate(prompt_bundle)
        _validate_judge_references(judge_result, known_ref_ids)
    except LLMConfigurationError as exc:
        return DisputeJudgeEnvelope(
            run_id=run_id,
            claim_id=result.claim_id,
            judge_status=DisputeJudgeStatus.FAILED,
            judge_failure_category=DisputeJudgeFailureCategory.CONFIGURATION,
            error=str(exc),
        )
    except DisputeJudgeTimeoutError as exc:
        return DisputeJudgeEnvelope(
            run_id=run_id,
            claim_id=result.claim_id,
            judge_status=DisputeJudgeStatus.FAILED,
            judge_failure_category=DisputeJudgeFailureCategory.TIMEOUT,
            error=str(exc),
        )
    except DisputeJudgeProviderError as exc:
        return DisputeJudgeEnvelope(
            run_id=run_id,
            claim_id=result.claim_id,
            judge_status=DisputeJudgeStatus.FAILED,
            judge_failure_category=DisputeJudgeFailureCategory.PROVIDER,
            error=str(exc),
        )
    except DisputeJudgeOutputError as exc:
        return DisputeJudgeEnvelope(
            run_id=run_id,
            claim_id=result.claim_id,
            judge_status=DisputeJudgeStatus.FAILED,
            judge_failure_category=DisputeJudgeFailureCategory.STRUCTURED_PARSING,
            error=str(exc),
        )
    except DisputeUnknownReferenceError as exc:
        return DisputeJudgeEnvelope(
            run_id=run_id,
            claim_id=result.claim_id,
            judge_status=DisputeJudgeStatus.FAILED,
            judge_failure_category=DisputeJudgeFailureCategory.UNKNOWN_REFERENCE,
            error=str(exc),
        )

    return DisputeJudgeEnvelope(
        run_id=run_id,
        claim_id=result.claim_id,
        judge_status=DisputeJudgeStatus.COMPLETED,
        result=judge_result,
        metadata=DisputeJudgeMetadata(
            model=resolved_adapter.model_name or "unknown",
            prompt_version=prompt_bundle.prompt_version,
            evaluated_at=datetime.now(timezone.utc),
        ),
    )
