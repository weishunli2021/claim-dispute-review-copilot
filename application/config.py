"""Configuration for the H1 LLM adapter only.

Read lazily, at first use -- never at import time (see
application/llm_adapter.py: OpenAIInvestigationBriefAdapter never
constructs a client or reads these until generate() is actually called).
No value read here is ever logged, printed, or returned to a caller.

Compatibility fix (live smoke test, post-H1): python-dotenv has been a
declared project dependency since the original scaffolding stage
("env var management" in requirements.txt) and .env/.env.example have
existed since H0/H1, but nothing in the codebase ever actually called
load_dotenv() -- os.environ.get() only ever sees real process-environment
variables, never a local .env file's contents. That silently defeated the
documented "configure via .env" workflow for every local run. Fixed here,
at the one call site that reads this configuration, by loading .env
(if present) before reading os.environ -- load_dotenv() never overrides a
variable already set in the real environment, and does nothing if no .env
file exists, so this is additive only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class LLMConfigurationError(RuntimeError):
    """Raised when required LLM configuration is missing or malformed.
    Never includes a secret value in its message."""


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str
    timeout_seconds: float


DEFAULT_LLM_TIMEOUT_SECONDS = 30.0


def load_llm_config() -> LLMConfig:
    """Read OPENAI_API_KEY, LLM_MODEL, LLM_TIMEOUT_SECONDS from the
    environment (see .env.example). Raises LLMConfigurationError naming
    which variable(s) are missing/invalid -- never echoing a value back.
    """
    load_dotenv()  # loads a local .env into os.environ if present; never overrides an existing var
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("LLM_MODEL")

    missing = [name for name, value in (("OPENAI_API_KEY", api_key), ("LLM_MODEL", model)) if not value]
    if missing:
        raise LLMConfigurationError(
            "Missing required LLM configuration: " + ", ".join(missing) + ". "
            "Set these in a local .env file (see .env.example) before requesting "
            "investigation-brief generation. No model was called."
        )

    timeout_raw = os.environ.get("LLM_TIMEOUT_SECONDS", str(DEFAULT_LLM_TIMEOUT_SECONDS))
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError as exc:
        raise LLMConfigurationError(
            f"LLM_TIMEOUT_SECONDS must be a number, got {timeout_raw!r}."
        ) from exc
    if timeout_seconds <= 0:
        raise LLMConfigurationError(
            f"LLM_TIMEOUT_SECONDS must be a positive number, got {timeout_seconds!r}."
        )

    assert api_key is not None and model is not None  # narrowed by the `missing` check above
    return LLMConfig(api_key=api_key, model=model, timeout_seconds=timeout_seconds)
