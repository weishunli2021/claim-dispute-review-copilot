"""Session-wide safety net for this offline test suite.

AGENTS.md rule 10: "Unit tests must not require live API calls.
Deterministic tests run offline." The incident this file closes: with a
real `.env` present in this project, one dispute-review UI test that
assumed configuration was genuinely absent instead made a real,
live `openai.OpenAI(...)` call, because `application/config.py`'s
`load_llm_config()` calls python-dotenv's `load_dotenv()`, which
repopulates any environment variable NOT already set in the real process
environment from a local `.env` file. Clearing/deleting an env var is
therefore not sufficient in isolation -- see
`docs/DISPUTE_AI_VALIDATION.md`'s live smoke check for the full incident,
and `test_missing_configuration_shows_actionable_message_without_credentials`
in `tests/test_dispute_review_ui.py` for the specific test fixed by
explicitly blanking its own config (this file is a second, independent
backstop, not a substitute for that fix).

This is a DEFAULT-DENY guard, autouse for every test in this suite:
constructing `openai.OpenAI(...)` fails immediately with a clear,
distinctive error, UNLESS a test explicitly overrides `openai.OpenAI`
itself. Every legitimate fake-client/spy test already does this via its
own `monkeypatch.setattr(openai, "OpenAI", ...)` call (see e.g.
`tests/test_dispute_generator.py`, `tests/test_dispute_judge.py`,
`tests/test_dispute_review_ui.py`'s `_install_fake_openai_client`) --
that call simply replaces this fixture's patched value for the remainder
of that one test, since pytest's `monkeypatch` fixture is function-scoped
and shared between an autouse fixture and the test body that requests it.

Scope, confirmed by a full-repository search before writing this guard:
`openai.OpenAI(...)` is the ONLY OpenAI SDK client construction anywhere
in `application/` (four call sites -- `application/llm_adapter.py`,
`application/semantic_judge.py`, `application/dispute_generator.py`,
`application/dispute_judge.py` -- all via the same lazy `_ensure_client`
pattern). There is no `openai.AsyncOpenAI` usage, and no direct
`httpx`/`requests` call to an OpenAI endpoint, anywhere in this project.

This guard does NOT touch `rag.embeddings.SemanticEmbeddingProvider` (a
local sentence-transformers model, no network call) or anything else in
the retrieval/graph/tools layers -- it targets exactly and only
`openai.OpenAI` construction. Offline eval scripts (`evals/e2e_eval.py`,
`evals/e2e_safety_eval.py`) are not wired to this pytest fixture because
they run outside pytest (`python -m evals.e2e_eval`) -- but a full search
confirms every call site in `evals/` already explicitly injects a
`Fake*Adapter`, never falling back to a real adapter's default, so there
is no reachable path to a real `openai.OpenAI(...)` construction in those
scripts to guard today.
"""

from __future__ import annotations

import openai
import pytest


class UnexpectedLiveProviderCallError(AssertionError):
    """Raised instead of ever constructing a real openai.OpenAI client
    during this automated test suite. A test that needs an OpenAI(...)
    client must inject a fake or spy client in its own body via
    monkeypatch.setattr(openai, "OpenAI", ...) -- exactly like every
    existing adapter test already does -- which overrides this guard for
    that one test. This guard only fires when no such override is in
    effect, meaning a test unexpectedly reached the real SDK boundary."""


def _deny_openai_construction(*args, **kwargs):
    raise UnexpectedLiveProviderCallError(
        "A test attempted to construct a real openai.OpenAI(...) client. Automated tests "
        "must never reach a live provider -- inject a fake or spy client via "
        "monkeypatch.setattr(openai, 'OpenAI', ...) instead. See tests/conftest.py's "
        "module docstring."
    )


@pytest.fixture(autouse=True)
def _deny_live_provider_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Applied to every test in this suite by default -- see module docstring."""
    monkeypatch.setattr(openai, "OpenAI", _deny_openai_construction)
