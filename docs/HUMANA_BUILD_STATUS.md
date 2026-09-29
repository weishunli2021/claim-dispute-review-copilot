# Humana Claims Investigation Copilot — Build Status

Tracks H0–H5 of the finishing plan. Each module records actual findings only —
future modules are NOT_STARTED with no invented results. See
[docs/architecture.md](architecture.md) for the target architecture and
[AGENTS.md](../AGENTS.md) for durable development rules.

---

## H0 — Baseline audit and development setup

**Status: COMPLETE (PASS).** Audit-only; no LLM calls, generation prompts, business rules, or
UI added. See the accompanying chat report for the full 16-section writeup; this entry is the
durable record.

**Purpose:** Verify the reported Modules 1–7 implementation against actual source and execution,
establish an isolated dev environment, run the existing test/eval suites, audit the five
synthetic cases and the human-review/escalation chain, and set up development guidance.

**Changed files:**
- Created `.env.example` (repo root) — was missing; required by
  `tests/test_project_structure.py::test_required_root_files_exist`, which failed before this
  fix. Documents the one real config variable in use, `RAG_EMBEDDING_PROVIDER` (see
  `rag/embeddings.py: get_default_embedding_provider`). No secrets.
- Created `.venv/` (Python 3.14.5, project-local, gitignored) — dependency install target.
- Created `AGENTS.md`, `CLAUDE.md` (`@AGENTS.md` import), this file.
- Ran `git init` at the confirmed project root (no staging, no commits, no remote).
- No other source files modified.

**Commands run:**
```
py -3.14 -m venv .venv
./.venv/Scripts/python.exe -m pip install --upgrade pip
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe -m pip check
./.venv/Scripts/python.exe -m pytest tests/ -q
./.venv/Scripts/python.exe -m evals.skill_eval
./.venv/Scripts/python.exe -m evals.agent_eval
./.venv/Scripts/python.exe -m evals.hybrid_regression_eval
./.venv/Scripts/python.exe -m evals.hybrid_retrieval_eval
./.venv/Scripts/python.exe -m evals.graph_retrieval_eval
./.venv/Scripts/python.exe -m evals.retrieval_eval
git init
```

**Results:**
- `pip check`: no broken requirements.
- `pytest tests/ -q`: **244 passed, 0 failed, 0 skipped, 0 errors** (244 collected via
  `--collect-only`; up from the previously reported 203 — growth consistent with the Skills-layer
  integration stage). 7 warnings: 1 `DeprecationWarning` from chromadb's telemetry module
  (`asyncio.iscoroutinefunction`, third-party, harmless), 6 `sklearn.InconsistentVersionWarning`
  (persisted `rag/index/tfidf/*/vectorizer.joblib` artifacts were fit under scikit-learn 1.9.0;
  this environment installed 1.9.1 per `requirements.txt`'s `>=1.4,<2.0` range). Tests still pass;
  this is a version-skew warning on a prototype pickle, not a defect — flagged as a limitation
  below, not fixed in H0 (would require rebuilding the TF-IDF index, out of minimal-setup scope).
- `evals.skill_eval`: 8/8 cases OK. Skill Completion Accuracy, Required Evidence Recall,
  Missing-Evidence Accuracy, Correct Next-Capability Rate all 100%; Unexpected Dependency/Tool
  Usage Rate 0%.
- `evals.agent_eval`: 9/9 cases OK. Terminal Status Accuracy, Required Action Recall, Human
  Review Recall, Human Review Precision all 100%; Unexpected Action Rate 0%.
- `evals.hybrid_regression_eval`: 8/8 cases OK, no drift. Policy Section Recall, Graph
  Relationship Recall, Structured Evidence Completeness all 100%; 0 cross-case contamination.
- `evals.hybrid_retrieval_eval`: 5 cases, structured/graph/missing-evidence/contamination all
  100%/0%, **Policy Section Recall 63.33%** (4 of 5 cases missed at least one expected section —
  a genuine, reproduced retrieval-quality gap, not a regression).
- `evals.graph_retrieval_eval`: 9 queries at max_hops 1/2/3. Node/Relationship Recall 76.85%/
  68.52% (hops=1) → 96.30%/96.30% (hops=2) → 100%/100% (hops=3). Matches README.
- `evals.retrieval_eval`: 19 queries × 4 (provider × chunking) configurations; Hit@1 57.9–68.4%,
  Hit@5 84.2–94.7%; deliberately-unresolved paraphrase/ambiguous cases reproduced as documented.

**Manual checks:** `git status --short --ignored` confirmed `.venv/` and `rag/index/` are
correctly gitignored; no references to the original project's path or name found in source
(`grep` for `enterprise-ai-case-copilot` and for hardcoded `C:\`/`D:\` paths outside `.venv`
returned none); no git repository existed at or above the project root before this stage.

**Limitations / follow-ups (not fixed in H0, out of minimal-setup scope):**
- No documented/pinned Python version anywhere in the repo (no `pyproject.toml`,
  `python_requires`, or `.python-version`). Only Python 3.14.5 was available on this machine;
  it worked, but the supported range is otherwise undocumented.
- Persisted TF-IDF vectorizer artifacts under `rag/index/tfidf/*/vectorizer.joblib` were built
  under an older scikit-learn point release (see warning above); rebuild via
  `python -m rag.vector_store` / re-running ingestion if the skew warning should be eliminated.
- `app.py` was not smoke-tested in a browser (no UI logic exists yet to verify beyond the
  existing `test_app_py_is_valid_python` syntax check).

**Next step:** H1 (see recommended insertion point in the chat report). Do not start without
separate approval per the task's stage-gating instruction.

> **SUPERSEDED (H1):** the "Recommended H1 insertion point" this entry originally proposed
> (a new `draft_investigation_brief` **skill**, inserted as a new **LangGraph node/edge** in
> `agents/case_agent.py`, reusing `skills/base.py`'s `SkillResult` contract) was **not** what
> was built. The authoritative H1 design is a thin **downstream application service**
> (`application/`) that runs the existing agent workflow once and consumes its result — no new
> skill, no new graph node/edge, no change to `SkillResult`. See the H1 entry below for what was
> actually implemented and why. This note corrects the proposal only; the H0 audit findings and
> test/eval results above are historical and unchanged.

---

## H1 — Context assembler + LLM-generated investigation briefs

**Status: COMPLETE.** Offline implementation verified (see Results below).

## H1 gate recommendation: **LIVE_VERIFIED**

**Live smoke test (4th attempt): LIVE_VERIFIED.** The 3 test-isolation failures from Attempt 3
were fixed (test-only change, see below); the full offline gate then passed at exactly the
expected baseline (**285 passed, 0 failed**); the live configuration pre-flight passed (real,
non-blank `OPENAI_API_KEY`; `LLM_MODEL="gpt-5.6-luna"`, not the `YOUR_VALID_MODEL_ID` placeholder;
`.env` git-ignored); and exactly **one** real OpenAI Responses-API call was made through the
unmodified `application.investigation_service.run_investigation` path, returning a successfully
parsed `InvestigationBrief` that respects every advisory/non-executable boundary. Full record
below, under "Live smoke test — Attempt 4."

**Live smoke test (3rd attempt): BLOCKED — pytest pre-flight gate failed, live call NOT
attempted.** Real `OPENAI_API_KEY`/`LLM_MODEL` are now configured in `.env`, but the mandated
"run pytest first, stop on any failure" gate caught 3 failing tests before any live call was
made — see "Live smoke test" below for the root cause (a test-isolation regression, not a
generation-path defect) and exact failing tests. Per instruction, no fix was applied and no live
call was attempted this round.
**Live smoke test (2nd attempt): LIVE_SMOKE_FAILED — Configuration** (`.env` now exists but
`OPENAI_API_KEY` and `LLM_MODEL` are present as blank values, not real ones; zero network calls
made; clean failure path confirmed working, not a crash). A genuine compatibility defect found
during this attempt — `application/config.py` never actually loaded `.env` — was fixed (see
below); that fix did not resolve the live call because the blank values are a separate,
user-side configuration gap, not a code defect. See "Live smoke test" below for the full record.

**Purpose:** Add a thin downstream application service that runs the existing evidence workflow
exactly once, assembles its `EvidencePackage` into a labeled/budgeted context, and — only when
evidence was already judged sufficient — calls one configured OpenAI adapter to draft a
structured, non-executable `InvestigationBrief`. Preserves every existing contract: `agents/`,
`skills/`, `context/`, `rag/`, `graph/`, `tools/` are unmodified (only `tests/test_project_structure.py`
changed, and only its dependency-boundary assertion — see below).

**New files:**
- `application/__init__.py`, `application/models.py` (`ApplicationResult`, `GenerationStatus`,
  `GenerationMetadata`, `AssembledContext`, `ContextReference`, `InvestigationBrief`, `Finding`,
  `SuggestedNextStep`, `ActionCode`)
- `application/context_assembler.py` (`assemble_context`) — labels an existing `EvidencePackage`
  with deterministic reference ids; never mutates it; never re-retrieves.
- `application/config.py` (`load_llm_config`, `LLMConfigurationError`)
- `application/llm_adapter.py` (`OpenAIInvestigationBriefAdapter`, `FakeInvestigationBriefAdapter`,
  `LLMProviderError`, `LLMOutputError`) — the only module in the repo allowed to import `openai`.
- `application/investigation_service.py` (`run_investigation`) — the orchestration entry point.
- `application/cli.py` — opt-in debug CLI (`python -m application.cli <claim_id> "<query>" [--live]`).
- `prompts/investigation_brief_prompt.py` (`PROMPT_VERSION = "investigation_brief.v1"`,
  `build_prompt`) — the first real file in `prompts/` (previously empty scaffolding).
- `tests/test_application_models.py`, `tests/test_application_context_assembler.py`,
  `tests/test_application_llm_adapter.py`, `tests/test_application_investigation_service.py`,
  `tests/test_application_cli.py`.

**Changed files:**
- `tests/test_project_structure.py`: `EXPECTED_DIRS`/`PACKAGE_DIRS` now include `application`.
  `test_no_llm_synthesis_or_external_graph_dependencies_yet` **renamed and narrowed** to
  `test_no_unapproved_llm_provider_or_external_graph_dependencies` — `openai` removed from its
  disallowed-in-`requirements.txt` list (`anthropic`, `neo4j` remain disallowed). A **new** test,
  `test_openai_sdk_confined_to_generation_adapter`, AST-scans every module under `agents/`,
  `skills/`, `tools/`, `rag/`, `graph/`, `context/` and fails if any imports `openai` — this is
  the actual architectural rule replacing the old blanket ban. No other assertion in this file
  was touched; no skill evidence-only/no-answer-field test anywhere was touched.
- `requirements.txt`: added `openai>=1.50,<2.0` only.
- `.env.example`: added `OPENAI_API_KEY`, `LLM_MODEL`, `LLM_TIMEOUT_SECONDS=30`;
  `RAG_EMBEDDING_PROVIDER` preserved, with a note that `context.hybrid_retriever` hardcodes its
  own policy-provider default independent of that variable (see below).

**How evidence is obtained without a second retrieval:** `agents.state.AgentResult` already
exposes the complete `EvidencePackage` the agent built (`AgentResult.evidence_package`) — no
accessor/wrapper was needed. `application.investigation_service.run_investigation` calls
`agents.case_agent.run_case_agent` exactly once and reads `agent_result.evidence_package`
directly; `application.context_assembler.assemble_context` only reads that object, never calls
`skills.investigate_claim.investigate_claim` or `context.hybrid_retriever.build_evidence_package`
again. Regression-tested: `tests/test_application_investigation_service.py::
test_run_investigation_executes_evidence_workflow_exactly_once` patches
`agents.nodes.investigate_claim` (the name actually bound inside the node, per `agents/nodes.py:29`)
with a call-counting wrapper and asserts `call_count == 1`.

**Original contracts preserved:** `AgentStatus`, `SkillStatus`, `is_evidence_sufficient`,
`EvidencePackage`, `SkillResult` (no answer field added), and the full 6-step agent trace are
all untouched — verified by re-running the complete H0 regression suite and every deterministic
evaluation with zero drift (see Results below). The LLM is only ever consulted **after**
`AgentStatus.EVIDENCE_SUFFICIENT`; `NEEDS_REVIEW`, `ERROR`, and `MAX_STEPS_EXCEEDED` all
short-circuit to a `SKIPPED_*`/error `GenerationStatus` with zero adapter calls.

**Commands run:**
```
./.venv/Scripts/python.exe -m pip install "openai>=1.50,<2.0"
./.venv/Scripts/python.exe -m pytest tests/ -q
./.venv/Scripts/python.exe -m evals.skill_eval
./.venv/Scripts/python.exe -m evals.agent_eval
./.venv/Scripts/python.exe -m evals.hybrid_regression_eval
./.venv/Scripts/python.exe -m evals.hybrid_retrieval_eval
./.venv/Scripts/python.exe -m evals.graph_retrieval_eval
./.venv/Scripts/python.exe -m application.cli CLM-1001 "Why was this claim denied?"
```

**Results (all offline; no live model call):**
- `pytest tests/ -q`: **285 passed, 0 failed, 0 skipped, 0 errors** (244 from H0 + 41 new: 40 in
  the five new `test_application_*` files + 1 net new in `test_project_structure.py`). Same 7
  pre-existing warnings as H0 (chromadb `DeprecationWarning`, sklearn `InconsistentVersionWarning`
  — still open, see H0's limitation entry, not addressed here).
- `evals.skill_eval` / `evals.agent_eval` / `evals.hybrid_regression_eval` /
  `evals.hybrid_retrieval_eval` / `evals.graph_retrieval_eval`: **identical output to H0**,
  including the **63.33% Policy Section Recall** — reproduced, not silently improved, hidden, or
  reframed as fixed. This remains a genuine, open retrieval-quality gap; H1's system prompt
  (`prompts/investigation_brief_prompt.py`) explicitly instructs the model to limit any
  policy-based conclusion not directly supported by a retrieved excerpt rather than assume
  retrieval was exhaustive, but does not and cannot fix the recall gap itself.
- `evals.retrieval_eval` was not re-run in H1 (unaffected by this stage's changes; already
  verified in H0).

**Manual/CLI verification (mocked adapter, no network):** ran
`python -m application.cli <claim_id> "<query>"` for CLM-1001, CLM-1002, CLM-1005, and CLM-9999
and confirmed by hand: Case 1 explains the AUTH_REQUIRED denial with zero prior-authorization
records; Case 2's `assembled_context.references` includes both `auth:PA-1501` (EXPIRED) and
`auth:PA-2001` (APPROVED) as distinct, undedup'd candidates; Case 5 and the unknown claim both
return `generation_status` values in `{SKIPPED_INSUFFICIENT_EVIDENCE, SKIPPED_NOT_FOUND_OR_ERROR}`
with the adapter never invoked; `--live` with `OPENAI_API_KEY`/`LLM_MODEL` unset returns
`CONFIGURATION_ERROR` cleanly instead of crashing.

**Live smoke test — Attempt 1: LIVE_SMOKE_FAILED (configuration).** No `.env` file existed yet;
`OPENAI_API_KEY`/`LLM_MODEL` unset in the shell. `load_llm_config()` raised `LLMConfigurationError`
before any client was constructed → `generation_status="CONFIGURATION_ERROR"`, zero network
requests. Classification: **Configuration** (nothing configured at all).

**Live smoke test — Attempt 2: LIVE_SMOKE_FAILED (configuration), after fixing a genuine
compatibility defect found during this attempt.**
```
./.venv/Scripts/python.exe -m application.cli CLM-1001 \
  "Why was this claim denied, and what evidence is available?" --live
```
A `.env` file now existed at the project root, so this attempt first checked (by variable name
only, via `dotenv_values()` — no value ever read or printed) whether `OPENAI_API_KEY`/`LLM_MODEL`
were populated there, and separately confirmed neither was set in the shell environment.

**Compatibility defect found and fixed:** `application/config.py`'s `load_llm_config()` read only
`os.environ` and never called `python-dotenv`'s `load_dotenv()` — despite `python-dotenv` being a
declared project dependency since the original scaffolding stage and `.env`/`.env.example` having
existed since H0/H1, a local `.env` file was **never actually loaded by any code path**, silently
defeating the documented "configure via `.env`" workflow. Fixed with a single, minimal addition —
one `from dotenv import load_dotenv` import and one `load_dotenv()` call at the top of
`load_llm_config()` (loads a `.env` file into `os.environ` if present; never overrides a variable
already set in the real environment; a no-op if no `.env` exists). No new dependency, no prompt
change, no other application-code change. Re-ran the full regression suite afterward: 284/285
pass; the one pre-existing failure (`test_required_root_files_exist`, `.env.example` missing from
the project root) is unrelated to this fix and to the live smoke test — flagged below as a
separate finding, not fixed here (out of this task's scope).

**Result after the fix:** still **CONFIGURATION_ERROR**. `dotenv_values('.env')` (names only,
values never read/printed) showed `OPENAI_API_KEY` and `LLM_MODEL` present as **blank** entries
(`KEY=` with no value) — the `.env.example` template was evidently copied but never filled in.
`generation_status="CONFIGURATION_ERROR"`, zero network requests, exactly one attempt.
**Failure classification: Configuration** (blank credential/model values in `.env`) — not
authentication, not billing/quota, not model-access, not SDK compatibility, not network/provider,
not timeout, not structured-parsing, not application logic; none of those layers were reached.
No credential value was ever read, printed, or logged. Configured model ID: **none — `LLM_MODEL`
is blank**.

**Verified from the same run, upstream of generation (unaffected by the config failure):**
claim status remains `DENIED` / `denial_reason_code="AUTH_REQUIRED"`; authorization evidence
remains empty (zero `authorization`-kind references; `missing_information=["prior_authorization"]`
unchanged). No `InvestigationBrief` was generated, so there is nothing to check for an improper
"absence proves the denial was valid" claim this round.

**To actually complete a live call:** fill in real values for `OPENAI_API_KEY` and `LLM_MODEL` in
`.env` (a model verified to support the Responses API's structured-output parsing,
`client.responses.parse(text_format=...)` — none is assumed or hard-coded), then re-run the same
command above. This performs exactly one real, billed API call and is never run automatically.

**Separate finding (not fixed, out of scope for this task):** `.env.example` is currently missing
from the project root (`tests/test_project_structure.py::test_required_root_files_exist` fails)
— it appears to have been replaced by `.env` rather than copied. Left as-is pending explicit
instruction, since restoring it isn't required for, or blocking, the live smoke test.

**Live smoke test — Attempt 3: BLOCKED at the pytest pre-flight gate (live call NOT attempted).**
Pre-flight checks all passed: `.env` exists; `OPENAI_API_KEY` confirmed non-blank (164 chars,
value never displayed); `LLM_MODEL` confirmed non-blank, reported value `YOUR_VALID_MODEL_ID`
(flagged below — this reads as an unfilled template placeholder, not a real OpenAI model id, but
it is technically non-blank); `.env` confirmed git-ignored (`git check-ignore -v .env` →
`.gitignore:2:.env`).

Per this task's explicit instruction ("run the complete pytest suite first; if any test fails,
STOP and report the failure; do not weaken tests"), ran `pytest tests/ -q` before any live call:
**3 failed, 282 passed.** Failing tests:
- `tests/test_application_llm_adapter.py::test_load_llm_config_missing_env_raises_configuration_error`
- `tests/test_application_llm_adapter.py::test_openai_adapter_propagates_configuration_error`
- `tests/test_application_investigation_service.py::test_missing_configuration_is_a_clear_application_error`

**Root cause (confirmed, not fixed):** all three tests simulate "no LLM configuration" via
`monkeypatch.delenv("OPENAI_API_KEY", ...)` / `delenv("LLM_MODEL", ...)`, expecting
`load_llm_config()` to still raise `LLMConfigurationError`. That held during Attempt 2 only
because `.env` had *blank* values then, so `load_dotenv()` reloaded empty strings (still falsy).
Now that `.env` holds real, non-blank values, `load_dotenv()` (added as the Attempt-2
compatibility fix) faithfully reloads those real values every time it's called — including right
after a test deletes them — so the "missing configuration" condition these three tests are meant
to simulate no longer occurs in this environment. This is a **test-isolation gap**, not a defect
in the generation path itself (`OpenAIInvestigationBriefAdapter`, `investigation_service`, and
`context_assembler` are not implicated); the correct fix is for those three tests to prevent
`load_dotenv()` from reading the real `.env` (e.g. patch `load_dotenv`, or use `monkeypatch.setenv(..., "")`
instead of `delenv`, or point `load_dotenv` at an isolated path) — deliberately **not applied
here**, since the instruction for this round was to stop and report on any test failure, not
patch tests, and doing so was not pre-approved.

**Consequence:** the live call was **not attempted** this round. No code, prompt, synthetic
data, golden expectations, or test files were modified during this attempt — this was audit-only.

**Test-isolation fix applied (test-only, before Attempt 4):** in the 3 tests named above, replaced
`monkeypatch.delenv("OPENAI_API_KEY"/"LLM_MODEL", raising=False)` with
`monkeypatch.setenv("OPENAI_API_KEY"/"LLM_MODEL", "")`. `load_dotenv()` never overrides a variable
already present in `os.environ` (blank or not), but will repopulate one that was deleted outright
from a real `.env` — so setting to `""` is what actually simulates "unconfigured" once a real
`.env` exists. Files changed: `tests/test_application_llm_adapter.py` (2 tests),
`tests/test_application_investigation_service.py` (1 test). No production code, prompt, synthetic
data, or golden expectation was touched. Re-ran `pytest tests/ -q`: **285 passed, 0 failed** —
back at the expected baseline.

**Live smoke test — Attempt 4: LIVE_VERIFIED.** Configuration pre-flight (values never printed
beyond what's explicitly permitted): `.env` exists; `OPENAI_API_KEY` resolves non-blank;
`LLM_MODEL` resolves non-blank and is **not** the `YOUR_VALID_MODEL_ID` placeholder (configured
value: `gpt-5.6-luna`); `.env` confirmed git-ignored. All gates passed, so exactly one live
request was made via the unmodified `application.investigation_service.run_investigation` path,
using a one-off local call-counting wrapper around `OpenAIInvestigationBriefAdapter.generate` and
`agents.nodes.investigate_claim` (a throwaway script outside the repo, not a new architectural
layer) to prove call counts.

- **Live model call count: 1.** **Evidence-workflow execution count: 1.**
- `agent_status = EVIDENCE_SUFFICIENT`; `generation_status = DRAFTED`; structured-output parsing
  **succeeded** (`response.output_parsed` was a valid `InvestigationBrief`).
- **Case-1 facts, verified from the actual `EvidencePackage` used for this run:**
  `claim_status="DENIED"`, `denial_reason_code="AUTH_REQUIRED"`, authorization-reference count
  **0**. All three match the synthetic fixtures exactly.
- **Semantic-boundary check, read against the generated brief's actual text:** the brief states
  the evidence "does not establish whether an authorization may have existed outside the records
  provided" and that the recorded denial is "consistent with" the authorization rule — **it does
  not claim the missing authorization record conclusively proves the denial was correct or
  valid.** `suggested_next_step.action_code = VERIFY_AUTHORIZATION_INFORMATION` (non-executable,
  advisory). No language anywhere in the brief claims authority to approve, deny, reverse, pay, or
  authorize the claim, or to determine medical necessity. **Passed.**
- **Data integrity:** `evidence_package_mutated = False` (re-serialized before/after generation,
  byte-identical); `synthetic_data_changed = False` (SHA-256 of every `data/*.json` file identical
  before/after the run).
- No prompt, application code, synthetic data, or golden expectation was modified to obtain this
  result — the same `prompts/investigation_brief_prompt.py` (`PROMPT_VERSION =
  "investigation_brief.v1"`) and `application/` code from the original H1 implementation were used
  unmodified.

**Limitations / explicitly out of H1 scope (per task instructions):**
- No reference/action validator against the assembled context — a parsed `InvestigationBrief` is
  a draft, not validated or "grounded" output. That is H2.
- No reviewer UI / accept-or-escalate interaction, and `escalate_case` is still not wired to this
  new layer. That is H3.
- No date-based authorization matching was added (Case 2 still presents both PA-1501 and PA-2001
  as unmatched candidates, per the explicit H1 instruction not to introduce that business rule).
- No index rebuild was performed; the sklearn TF-IDF vectorizer version-skew warning from H0
  remains open, documented, not silently called harmless.
- Prompt-injection and malformed-output handling are implemented structurally (system-prompt
  instruction + `LLMOutputError`/refusal handling) but not adversarially tested beyond the one
  mocked-refusal unit test — not a comprehensive-resistance claim.

---

## H2 — Deterministic validation + explicit failure handling

**Status: COMPLETE (offline).** H1 remains **LIVE_VERIFIED** (unchanged, frozen; H2 made no live
model call). This is a deterministic-validation and status-separation layer only, per the task's
explicit non-goal: "not a universal hallucination detector."

**Purpose:** extend the H1 application service to (1) expose evidence/generation/validation as
three separate, never-collapsed status axes, and (2) deterministically check the objectively-
checkable parts of a DRAFTED `InvestigationBrief` (evidence-reference existence, findings citing
evidence, an advisory-action allowlist, and a narrow prohibited-authority-language pattern check)
with zero additional LLM calls.

**Files changed:**
- `application/models.py` — **consolidated `GenerationStatus`** from 6 values
  (`DRAFTED`/`SKIPPED_INSUFFICIENT_EVIDENCE`/`SKIPPED_NOT_FOUND_OR_ERROR`/`CONFIGURATION_ERROR`/
  `PROVIDER_ERROR`/`OUTPUT_INVALID`) to 3 (`NOT_ATTEMPTED`/`DRAFTED`/`FAILED`) — the two
  `SKIPPED_*` values duplicated what `agent_result.status` (`AgentStatus`) already said; the
  three `*_ERROR` values collapsed into `FAILED`, with which failure kind now on the new
  `GenerationFailureCategory` enum (`CONFIGURATION`/`PROVIDER`/`TIMEOUT`/`STRUCTURED_PARSING`).
  Added `ValidationStatus` (`NOT_RUN`/`PASSED`/`FAILED`), `ValidationIssue`, `ValidationResult`.
  `ApplicationResult` gained `generation_failure_category` and `validation_result` fields; no
  existing field was removed.
- `application/llm_adapter.py` — added `LLMTimeoutError(LLMProviderError)`, raised for
  `openai.APITimeoutError` specifically (caught before the broader `openai.OpenAIError` handler),
  so a timeout is distinguishable from a generic provider/API failure.
- `application/investigation_service.py` — updated to the 3-value `GenerationStatus`, sets
  `generation_failure_category` on every `FAILED` path, and — only immediately after a
  successful `DRAFTED` — calls `application.brief_validator.validate_investigation_brief` once
  (never a second adapter call on any path).
- `application/cli.py` — one stale docstring reference to the old `CONFIGURATION_ERROR` value
  corrected.
- **New:** `application/brief_validator.py` (Rules A–D, see below).
- **New:** `tests/test_application_brief_validator.py` (27 tests, scenarios A–G from the task).
- **Updated:** `tests/test_application_investigation_service.py`,
  `tests/test_application_models.py`, `tests/test_application_llm_adapter.py` — old enum-value
  assertions updated to the consolidated 3-value `GenerationStatus`; every existing behavioral
  assertion (call counts, evidence retention, zero-adapter-call paths) preserved unchanged; new
  assertions added for `validation_result.status` and `generation_failure_category` per case.
- **Not changed:** `agents/`, `skills/`, `context/`, `rag/`, `graph/`, `tools/`, any
  `data/*.json`, any `evals/*.json` golden set, `prompts/investigation_brief_prompt.py` (system
  prompt text untouched — the H2 policy-metadata audit found no change necessary, see below).

**Status model (the three axes, never collapsed):**
1. **EvidenceStatus** — `agent_result.status` (`AgentStatus`: `EVIDENCE_SUFFICIENT` /
   `NEEDS_REVIEW` / `ERROR` / `MAX_STEPS_EXCEEDED`) — unchanged, owned entirely by the existing
   agent workflow.
2. **GenerationStatus** — `NOT_ATTEMPTED` / `DRAFTED` / `FAILED` (+ `generation_failure_category`
   when `FAILED`).
3. **ValidationStatus** — `validation_result.status`: `NOT_RUN` / `PASSED` / `FAILED` (+
   `validation_result.issues`), set only after a `DRAFTED` brief is checked.

Verified against every worked example in the task: Case 1 success →
(`EVIDENCE_SUFFICIENT`, `DRAFTED`, `PASSED`); Case 5 →
(`NEEDS_REVIEW`, `NOT_ATTEMPTED`, `NOT_RUN`); unknown claim →
(`ERROR`, `NOT_ATTEMPTED`, `NOT_RUN`); provider failure after sufficient evidence →
(`EVIDENCE_SUFFICIENT`, `FAILED`, `NOT_RUN`); generated-but-invalid brief →
(`EVIDENCE_SUFFICIENT`, `DRAFTED`, `FAILED`) — all covered by tests.

**Validation rules implemented (`application/brief_validator.py`):**
- **Rule A — evidence-reference existence:** every `evidence_ref` cited anywhere in the brief
  (each `Finding` and `suggested_next_step`) must exactly match a `ref_id` in the
  `AssembledContext` actually rendered into that request's prompt. Exact string match only, no
  fuzzy matching. Every nonexistent ref is individually reported.
- **Rule B — findings require evidence:** every `Finding` must cite at least one `evidence_ref`.
  Scoped to the `findings` collection only, per the task's explicit scope.
- **Rule C — advisory action allowlist:** `suggested_next_step.action_code` must be one of the
  existing 4 `ActionCode` values (`EXPLAIN_RECORDED_STATUS`, `VERIFY_AUTHORIZATION_INFORMATION`,
  `REQUEST_INFORMATION`, `HUMAN_REVIEW`). The `ActionCode` enum itself already made every
  prohibited action (`APPROVE_CLAIM`, `DENY_CLAIM`, `REVERSE_CLAIM`, `PAY_CLAIM`,
  `APPROVE_AUTHORIZATION`, `DENY_AUTHORIZATION`, `DETERMINE_MEDICAL_NECESSITY`) structurally
  unrepresentable in a parsed brief, so this rule is enforced explicitly in the validator as a
  second, independently-auditable layer rather than relying on the schema alone — the enum was
  **not** expanded, per the task's "do not invent a large workflow taxonomy" instruction.
- **Rule D — narrow prohibited-authority language:** a small set of regex patterns over every
  generated narrative field (summary, each finding's statement, each missing/conflicting-evidence
  entry, and the next-step rationale) matching an ACTIVE-VOICE verb (approve/deny/reverse/pay,
  in its inflected forms) immediately governing "the/this claim" or "the/this (prior)
  authorization", plus a "determine(s) ... medical necessity" pattern. Deliberately NOT a keyword
  blacklist: passive-voice historical facts ("The claim was denied.") and plain nouns ("the
  denial reason", "claim status is DENIED") never match, because the object precedes the verb (or
  the word isn't a verb form at all) — verified by 9 must-fail and 8 must-pass parametrized cases
  exactly matching the task's own examples.

**What is intentionally NOT checked (documented, not silently omitted):** whether a finding's
natural-language statement is actually semantically supported by the cited evidence (semantic
entailment); general hallucination detection; comprehensive prompt-injection resistance. Recorded
verbatim in `ValidationResult`'s docstring: *"This deterministic validation layer does not prove
that generated natural language is semantically entailed by the evidence and is not a universal
hallucination detector."*

**Explicit failure-category mapping (`GenerationFailureCategory`, set only when `FAILED`):**

| Adapter exception | Category |
|---|---|
| `LLMConfigurationError` | `CONFIGURATION` |
| `LLMTimeoutError` (new; `openai.APITimeoutError`) | `TIMEOUT` |
| `LLMProviderError` (any other `openai.OpenAIError`) | `PROVIDER` |
| `LLMOutputError` (refused/unparseable structured output) | `STRUCTURED_PARSING` |

No retries anywhere; at most one adapter call per request on every path (regression-tested).

**Policy-provider metadata / label audit (Section 6): PASSED, no code change made.** Reviewed
`prompts/investigation_brief_prompt.py`'s system prompt (rule 7, unchanged) — it already
explicitly states the measured **63% Policy Section Recall**, instructs the model to "never
present retrieval as exhaustive," and to limit any policy-based conclusion not directly supported
by a retrieved excerpt. Reviewed `application/context_assembler.py`'s policy `ContextReference`
labels (`"Policy [{section_id}] {section_title}"`) — neutral citations of the specific retrieved
section, with no language implying completeness or exhaustiveness. No golden relevance label,
retrieval ranking, or synthetic document was touched.

**Test results:**
- `pytest tests/ -q`: **318 passed, 0 failed, 0 skipped, 0 errors** (up from H1's 285: +27 in the
  new `test_application_brief_validator.py`, +6 net new/updated assertions across the three
  updated H1 test files). Same 7 pre-existing warnings as H0/H1 (unrelated, undismissed).
- `evals.skill_eval` / `evals.agent_eval` / `evals.hybrid_regression_eval` /
  `evals.hybrid_retrieval_eval` / `evals.graph_retrieval_eval`: **identical to H0/H1, zero drift**
  — all per-case results and aggregate metrics byte-for-byte the same, including
  **Policy Section Recall = 63.33%**, honestly reproduced, not adjusted.
- `evals.retrieval_eval` not re-run in H2 (unaffected by this stage's changes).

**Regression check:** no `.py` file under `agents/`, `skills/`, `context/`, `rag/`, `graph/`,
`tools/` and no `.json` file under `data/` or `evals/` was modified this stage (confirmed by
filesystem mtime scan in addition to code review). The gitignored, persisted Chroma vector-store
binaries under `rag/index/` show updated mtimes purely from being opened during eval/test runs
(SQLite/HNSW file bookkeeping on read) — not a rebuild, not a data change; confirmed by the
byte-identical eval results above.

**No live OpenAI call was made during H2** — every test uses `FakeInvestigationBriefAdapter` or a
directly-injected fake OpenAI client object, per the task's explicit instruction to keep H2
deterministic and inexpensive.

**Known limitations (H2 scope, by design):**
- No semantic-entailment or hallucination checking — a brief can cite a real reference id next to
  a statement that reference doesn't actually support, and Rule A/B would not catch it. This is
  the task's explicit non-goal, not an oversight.
- Rule C's allowlist is currently unreachable via the type system alone (the schema already
  excludes every prohibited action); it exists as an explicit, independently-auditable second
  layer, not because it can currently fire.
- Rule D is a narrow pattern match tuned to the task's own worked examples, not a general
  safety/injection classifier — a differently-phrased authority claim could still slip through.
- H1's already-known limitations (Policy Section Recall 63.33%, sklearn vectorizer version-skew
  warning, no reviewer UI, `escalate_case` still disconnected) are unchanged by H2.

**Would a final H2 live smoke test add meaningful incremental confidence?** Recommend: **modest
but real value, optional, not urgent.** H2's validator logic is fully covered by the 27 offline
tests plus the two integration tests exercising it end-to-end via `run_investigation` with an
injected fake brief. A live smoke test would additionally confirm that a REAL model's phrasing
(not a hand-constructed test fixture) reliably passes Rules B–D in practice — i.e., that the
system prompt's existing constraints (citation requirement, advisory-only framing) actually
produce validator-passing output from the live model, not just from fixtures built to pass. That
is worth knowing before H3 builds a reviewer UI on top of this, but is not required to consider
H2 itself complete, since H2's own contract is fully exercised offline. **Not run — awaiting your
explicit request**, per the task's instruction.

---

## H3 — Interview Workbench + human review

**Status: COMPLETE.** H0–H2 remain frozen and unchanged. One optional, deliberate live-model
smoke test was performed manually through the UI for CLM-1001 (see below) — not run automatically
by any test.

**Purpose:** expose the existing, already-verified H0–H2 pipeline as a focused, single-screen
Streamlit product experience for the interview demo — a presentation layer only, with zero new
business logic, zero new agents, zero new LLM calls, and zero changes to evidence sufficiency,
retrieval, or the agent's routing.

**Entry point:**
```
./.venv/Scripts/python.exe -m streamlit run app.py --server.headless=true
```
(matches `.claude/launch.json`'s existing `streamlit-app` config, port 8501 — no new launch
config needed.)

**Audit findings (before implementing):**
- `app.py` was a fully unwired placeholder (4 empty tabs, zero business logic, zero tests).
- `application.investigation_service.run_investigation(claim_id, query)` is the correct, sole
  entry point for the UI to call — no adapter override needed (defaults to the real
  `OpenAIInvestigationBriefAdapter` internally).
- Real execution-trace data available: `AgentResult.actions_taken` (the fixed step sequence) plus
  `ApplicationResult.generation_status`/`generation_metadata`/`validation_result`. No dynamic
  skill-selection trace exists or was fabricated — the graph always calls only `investigate_claim`.
- `skills.escalate_case.escalate_case(case_id, reason, missing_information, evidence_summary)` is
  a pure packaging function with no claim re-lookup.
- **`escalate_case` reuse is fully feasible without rerunning the evidence workflow**:
  `AgentResult.evidence_package` and `.missing_information` are already populated for
  `NEEDS_REVIEW` (BUILD_EVIDENCE runs before the sufficiency check), so a thin UI-layer helper
  can call `escalate_case` directly with already-computed data. **No architecture change was
  required; nothing was force-fit.**
- No pre-existing review-packet structure existed.

**New files:**
- `application/workbench.py` — testable view-model/state-helper logic: case catalog + short
  neutral descriptors (derived live from `tools.claim_tool.get_claim`, never hard-coded
  conclusions), per-case default questions, stale-state reset, review-decision recording,
  `get_display_context()` (reuses `application.context_assembler.assemble_context` — a pure,
  stateless relabeling function — for `NEEDS_REVIEW` cases where `investigation_service` itself
  never builds an `AssembledContext`, without re-running retrieval/tools/graph), and
  `is_accepted_draft()` (the single shared gating condition used by both the main branch logic
  and the "Accept" button's availability, so they can never drift apart).
- `application/review_packet.py` — `build_needs_review_packet(result)`: reuses
  `skills.escalate_case.escalate_case` exactly as-is with fields already present on the
  `ApplicationResult`; raises `ValueError` if called outside `NEEDS_REVIEW`. Does not call
  `agents.case_agent.run_case_agent`, does not duplicate `escalate_case`'s packaging logic, and
  is **not** wired into `agents/case_agent.py`'s LangGraph — the main graph still only ever
  invokes `investigate_claim`; this is invoked directly by `app.py` for the human-review path
  only, exactly per the task's required architecture.
- `tests/test_workbench.py` (15 tests, scenarios A–I).

**Changed files:**
- `app.py` — completely rewritten from the 4-tab placeholder into a single-screen flow matching
  the required information hierarchy (Case+question → Recorded Claim Information → Investigate →
  Status → Investigation Result → Human Review → Supporting Evidence expander → Execution Trace
  expander). The unwired Agent Trace/Evaluations/Governance placeholder tabs were removed (they
  had no functional content; Agent Trace's intent is now the real Execution Trace expander on the
  single page). Calls `run_investigation` directly — never the OpenAI adapter directly — exactly
  once per "Investigate Claim" click, with the result cached in `st.session_state` so Streamlit's
  normal rerun-on-interaction behavior (e.g. toggling an expander) never re-triggers a second
  investigation.

**Not changed:** `agents/`, `skills/`, `context/`, `rag/`, `graph/`, `tools/`, any `data/*.json`,
any `evals/*.json` golden set, `application/models.py`, `application/investigation_service.py`,
`application/brief_validator.py`, `application/llm_adapter.py`, `application/context_assembler.py`,
`prompts/investigation_brief_prompt.py` — confirmed by filesystem mtime scan in addition to code
review.

**Case selector:** all 5 cases, with neutral descriptors read live from `tools.claim_tool.get_claim`
(e.g. `CLM-1001 — MRI-KNEE (DENIED / AUTH_REQUIRED)`, `CLM-1002 — MRI-KNEE (PAID)`) — recorded
source facts only, never an investigation conclusion. Selecting a case preloads a demo-convenience
default question (CLM-1001's matches the task's exact specified wording); every question remains
editable.

**Recorded Claim Information:** rendered before any AI content, from `tools.case_context.get_case_context`
directly (the same cheap, deterministic Tool-level call `agents/nodes.py:load_case` already makes),
labeled "RECORDED / SOURCE DATA — not AI-generated," visually separate from the "AI-GENERATED
INVESTIGATION BRIEF" content below it.

**Status display:** three separate metrics — Evidence Status (`agent_result.status`), Generation
Status (`generation_status`), Validation Status (`validation_result.status`) — never collapsed,
exactly per the H2 status model.

**Case-5 human-review path (verified live in the browser):** `NEEDS_REVIEW` /
`NOT_ATTEMPTED` / `NOT_RUN` — UI shows the exact required message "Insufficient evidence for an
AI-generated investigation brief," lists missing/unresolved evidence (`benefit`,
`prior_authorization`, `servicing_provider`), and an expandable "Local Human-Review Packet
(synthetic)" built by `build_needs_review_packet` — confirmed showing
`external_system_contacted: false`, `synthetic: true`, and the same structured facts already
gathered (no re-lookup). Only "Mark for Further Review" is offered (no "Accept," correctly, since
there is no draft). Clicking it recorded a local-only confirmation.

**Generation/validation failure experience:** implemented exactly per the task's specified
messages — "Evidence was assembled successfully, but an investigation draft could not be
generated" for `EVIDENCE_SUFFICIENT`+`FAILED` generation, and "The generated draft did not pass
validation and is not being presented as an accepted investigation brief" for `DRAFTED`+`FAILED`
validation, each with an expandable technical-detail section (failure category / validation
issues, never a raw stack trace or secret).

**Stale-state protection:** `application.workbench.on_case_selected`/`on_question_changed` clear
`investigation_result`/`review_decision` on every case or question change, wired via Streamlit
`on_change` callbacks on both widgets; `app.py` additionally guards at render time
(`result.claim_id == selected_claim_id`) as defense-in-depth. **Verified live in the browser**:
generated a Case-5 result, switched to CLM-1001, confirmed the Case-5 result/status/review section
was completely gone and the question reverted to CLM-1001's default.

**Test results:**
- `pytest tests/ -q`: **333 passed, 0 failed, 0 skipped, 0 errors** (up from H2's 318: +15 in the
  new `tests/test_workbench.py`). Same 7 pre-existing warnings (unrelated, undismissed).
- All 5 deterministic evals (`skill_eval`, `agent_eval`, `hybrid_regression_eval`,
  `hybrid_retrieval_eval`, `graph_retrieval_eval`): **identical to H0/H1/H2, zero drift**,
  including **Policy Section Recall = 63.33%**, honestly reproduced.

**Optional live UI smoke test: PERFORMED (one, manual, deliberate) — SUCCESSFUL.** Ran the actual
Streamlit app (`streamlit run app.py`), selected CLM-1001 (default question), clicked "Investigate
Claim" once. Result: `EVIDENCE_SUFFICIENT` / `DRAFTED` / `PASSED` (0 validation issues), a
well-formed brief citing real evidence refs, correctly hedged language (e.g. "does not by itself
establish that the service was never authorized at any point," "policy retrieval may be
incomplete"), the exact required advisory boundary note, and both review buttons present.
Clicked "Accept Investigation Draft" — recorded a correct local-only confirmation. Execution Trace
showed the real adapter (`openai`) and model (`gpt-5.6-luna`) used. This exercised the one
materially new integration point H3 introduced (Streamlit → `run_investigation` → real adapter)
end to end. No second live call was made. Recommended and performed per the task's explicit
allowance ("only if needed to verify... integration still works") — justified because this was
the first time `app.py` had ever actually been run.

**Incidental finding (informational only, not a defect):** Streamlit's dev-mode file watcher logs
many harmless tracebacks (`ModuleNotFoundError: No module named 'torchvision'`) while probing
`transformers`' optional vision-model submodules for hot-reload support — a known, pre-existing
interaction between `sentence-transformers` (added in H0) and Streamlit's file watcher, unrelated
to any H3 code and with zero functional impact (confirmed by the successful live run above). Not
fixed — installing `torchvision` would be an unnecessary, unrequested dependency addition for a
cosmetic dev-log message.

**Regression check:** confirmed via filesystem mtime scan — zero `.py` changes under `agents/`,
`skills/`, `context/`, `rag/`, `graph/`, `tools/`, zero `.json` changes under `data/` or `evals/`.

**Known limitations:**
- No automated browser/UI test exists (explicitly out of scope — "do not try to unit-test
  Streamlit's rendering pixel by pixel"); UI correctness was verified manually in-browser this
  session, not via CI.
- Human review decisions are session-local only (`st.session_state`) — they do not persist across
  a page reload or a new Streamlit session, by design (no database/file-backed review-decision
  store was requested or built).
- Progress is shown as a single spinner covering the whole blocking call, not a genuine live
  3-stage progress indicator — `investigation_service.run_investigation` exposes no intermediate
  callback hook, and none was added, to avoid touching the frozen H1/H2 orchestration function
  for a purely cosmetic improvement.
- All H1/H2 known limitations (Policy Section Recall 63.33%, sklearn vectorizer version-skew
  warning, no semantic/hallucination validation) are unchanged by H3.

---

## H4 — End-to-end product evaluation + safety/failure tests

**Status: COMPLETE.** H0–H3 remain frozen and unchanged. No live model calls were made this
stage (see "Model usage strategy" below) — H1/H3's live verification stands as-is.

**Purpose:** evaluate the complete existing prototype's behavior end to end, as a distinct
artifact from the component evaluations (skills/agent/retrieval/hybrid/graph), which are
preserved separately and unmodified. See
[docs/EVALUATION_REPORT.md](EVALUATION_REPORT.md) for the full human-readable report; this entry
is the durable status record.

**Audit findings (before implementing):** `evals/` had 5 independent component evaluations
(skill, agent, hybrid regression, hybrid retrieval, graph), each with its own golden set, its own
per-case dataclass result, and its own `print_report`/`_main` — **no shared evaluation-result
model existed**; each eval file is self-contained by established convention. `tests/` has one
thin pytest wrapper per eval (e.g. `test_skill_eval.py`) asserting its results, a pattern H4
followed for its own two new eval files. `application/` (H1–H3) and `app.py` (H3) were unchanged
by this audit — H4 only reads their existing public functions/types.

**New files:**
- `evals/e2e_common.py` — shared `Check`/`ScenarioResult` dataclasses (never collapsed into one
  score), `well_formed_brief()` helper, `print_scenario_report()`, `write_artifact()`. Shared
  between `e2e_eval.py` and `e2e_safety_eval.py` only so the two don't duplicate this shape; they
  never import each other.
- `evals/e2e_eval.py` — the 8 product scenarios (E01–E08). `python -m evals.e2e_eval`.
- `evals/e2e_safety_eval.py` — the 9 safety/failure tests (S01–S09), deliberately kept in a
  separate module/artifact from the 8 product scenarios. `python -m evals.e2e_safety_eval`.
- `tests/test_e2e_eval.py`, `tests/test_e2e_safety_eval.py` — thin pytest wrappers, following the
  existing convention.
- `artifacts/e2e_eval_results.json`, `artifacts/e2e_safety_eval_results.json` — machine-readable,
  per-scenario, per-check detail (new `artifacts/` directory).
- `docs/EVALUATION_REPORT.md` — human-readable report (component eval / E2E / safety / limitations
  / guardrail inventory, kept as 4+1 separate sections, never one score).

**Not changed:** `agents/`, `skills/`, `context/`, `rag/`, `graph/`, `tools/`, any `data/*.json`,
any existing `evals/*.json` golden set or existing `evals/*_eval.py` file, `application/models.py`,
`application/investigation_service.py`, `application/brief_validator.py`,
`application/llm_adapter.py`, `application/context_assembler.py`, `application/workbench.py`,
`application/review_packet.py`, `app.py`, `prompts/investigation_brief_prompt.py` — confirmed by
filesystem mtime scan in addition to code review. **No implementation defect was discovered** that
required a fix (see "Defects discovered" below).

**Model usage strategy:** every E01–E08/S01–S09 scenario uses
`application.llm_adapter.FakeInvestigationBriefAdapter` (mocked, reproducible, zero network calls)
routed through the REAL `application.investigation_service`, the REAL H2
`application.brief_validator`, and (E05) the REAL `application.review_packet` — H2 is never
bypassed to simplify H4. Generation scenarios (E01–E04, E07) call the deterministic evidence
workflow once standalone to discover real, current evidence-reference ids for the mocked brief to
cite (documented as an evaluation-harness-only characteristic in `evals/e2e_eval.py`'s module
docstring — it does not affect or weaken the production single-execution guarantee, independently
regression-tested in `tests/test_application_investigation_service.py`).

**E01–E08 results — 8 of 8 passed.** E06 (isolated Case-2 variation) was achieved via a
**controlled dependency override** at `tools.case_context.get_prior_authorizations` (patched only
for the duration of that one scenario) — `data/prior_authorizations.json` was never read or
modified, and no invasive production-code change was needed, so E06 did **not** need to be
downgraded to a lower-level fixture-only evaluation. Full per-scenario detail in
[docs/EVALUATION_REPORT.md](EVALUATION_REPORT.md) section B.

**S01–S09 results — 9 of 9 passed**, including one deliberately-surfaced, **documented (not
fixed) limitation**: S09 found that "claim not found" and "an unexpected execution/tool failure"
both currently map to the same `AgentStatus.ERROR` value, distinguishable only by the free-text
`.error` message — fixing this would require a new `AgentStatus` value, an evidence-architecture
change explicitly out of scope for H4. Full detail in
[docs/EVALUATION_REPORT.md](EVALUATION_REPORT.md) section C.

**A genuine bug was found and fixed during H4's own construction (not a product defect):** the
first draft of `evals/e2e_common.py`'s `Check` dataclass was called with the raw observed value as
its `passed` field in most call sites (e.g. `Check("x", 0, 0, 0)`), so any truthy-but-wrong
observed value would have silently read as "passed," and some correct zero/False observed values
initially misreported as FAILED. Fixed by adding `Check.equals(name, observed, expected)` (derives
`passed = observed == expected` explicitly) and rewriting every straightforward-equality call site
in `evals/e2e_eval.py` to use it, reserving the raw `Check(...)` constructor for the small number
of checks with a genuinely custom boolean expression. This was caught by running the suite and
inspecting output before trusting a single result — not found via the product/application code,
which was never at fault.

**Test results:**
- `pytest tests/ -q`: **342 passed, 0 failed, 0 skipped, 0 errors** (up from H3's 333: +9 in the
  two new `test_e2e_*.py` files). Same 7 pre-existing warnings (unrelated, undismissed).
- All 5 existing component evals (`skill_eval`, `agent_eval`, `hybrid_regression_eval`,
  `hybrid_retrieval_eval`, `graph_retrieval_eval`): **identical to H0/H1/H2/H3, zero drift**,
  including **Policy Section Recall = 63.33%**, honestly reproduced.
- `evals.e2e_eval`: **8 of 8 scenarios passed.**
- `evals.e2e_safety_eval`: **9 of 9 scenarios passed** (one documents a limitation, per above).

**Live model recommendation:** not run this stage, per instruction. H1 (LIVE_VERIFIED) and H3 (one
live UI smoke test) already established the real path works; H4's mocked E01–E08/S01–S09 suite
adds reproducible product-behavior coverage on top of that, not a reason to re-run live calls. **A
single additional live call would add only marginal confidence** — it would confirm a real
model's phrasing tends to satisfy H2's Rules B–D in practice (already partially observed in H3's
one live run), but would not change any status-model or validator guarantee, all of which are
already deterministic and already covered offline. Not recommended as necessary before H5;
harmless to run if desired, not run here.

**Known limitations:** see [docs/EVALUATION_REPORT.md](EVALUATION_REPORT.md) section D in full;
summary: Policy Section Recall 63.33% (unchanged); H2 validation does not check semantic
entailment (by design); S09's not-found-vs-failure status ambiguity (documented, not fixed); S07
does not test live-model injection resistance (structural checks only). A guardrail inventory
(section E) was captured for H5 to turn into `docs/AI_GUARDRAILS.md` — no such file was created
in H4, and no runtime guardrail engine was built.

---

## H5 — Demo freeze + runbook

**Status: COMPLETE — DEMO_READY.** H0–H4 remain frozen; no features, prompts, agents, retries, or
evaluation scenarios were added. No demo-blocking defect was found, so no production-code fix was
needed or made this stage.

**New files:** `docs/DEMO_RUNBOOK.md`, `docs/IMPLEMENTATION_FACTS.md`,
`docs/KNOWN_LIMITATIONS.md`. **Not changed:** every other file in the repository.

**Final regression (re-run this stage):**
- `pytest tests/ -q`: **342 passed, 0 failed, 0 skipped** — exactly the expected baseline.
- Skill/agent/hybrid-regression/hybrid-retrieval/graph-retrieval evals: **identical to
  H0–H4, zero drift**, including **Policy Section Recall = 63.33%** (unchanged, honestly
  reproduced, not touched).
- `evals.e2e_eval`: **8 of 8** E01–E08 scenarios passed.
- `evals.e2e_safety_eval`: **9 of 9** S01–S09 scenarios passed (S09's documented limitation
  unchanged).

**Demo verification:** launched `streamlit run app.py` fresh; confirmed no startup errors.
Verified live in-browser: Case 1's pre-investigation "Recorded Claim Information" (exact source
data); Case 5's full flow (`NEEDS_REVIEW`/`NOT_ATTEMPTED`/`NOT_RUN`, zero model calls, review
packet, "Mark for Further Review" only — re-confirmed working after all H4 changes); Case 2's
pre-investigation recorded data (`PAID`); stale-state clearing when switching cases (Case 5's
result fully disappeared on switching to CLM-1002). **No live OpenAI call was made this
stage** — confirmed via filesystem scan that `app.py`/`application/*` had not changed since H3's
live verification, so per instruction a new live call was not run "solely for reassurance." Case
1's successful-generation view and Case 2's authorization-candidate display were therefore not
re-verified live in-browser this session; both are already covered by `tests/test_workbench.py`
and `evals/e2e_eval.py` (E01, E02), and by H1/H3's prior live verification.

**Screenshots:** not saved to disk this session — no reliable screenshot-to-disk mechanism was
available without spending significant automation effort, which was explicitly out of scope.
Two views (Case 1 pre-investigation, Case 5 full flow) were visually verified live but not
captured as files; a manual capture list for all four recommended demo screenshots is in
`docs/DEMO_RUNBOOK.md` section F. No screenshot was fabricated.

**Known limitations:** see `docs/KNOWN_LIMITATIONS.md` for the full, disclosure-oriented list
(Policy Section Recall 63.33%; H2 non-semantic validation; structural-only injection coverage;
`AgentStatus.ERROR` conflation; intentionally-unresolved Case-2 ambiguity; synthetic-data-only;
live-output variability; no real payer integration; no production governance/security/performance
work) — none fixed in H5, per its own scope.

**Repository status:** no git commits exist yet (repo initialized in H0, never committed). All
H0–H5 work remains untracked/uncommitted. `.env` confirmed git-ignored and never displayed or
logged at any point in H0–H5. Recommended (not executed): a single local commit covering the full
H0–H5 work, followed by a local tag such as `humana-takehome-demo-v1` — no push, per instruction.

---

## H6 — Experimental semantic evaluation / LLM-as-a-judge (OPTIONAL sidecar)

**Status: OFFLINE-COMPLETE (bug found and fixed post-initial-implementation).** H0–H5 remain
frozen and unchanged; no live judge call has been made — awaiting explicit approval, per
instruction. Full architecture/rubric/limitations in
[docs/H6_SEMANTIC_JUDGE.md](H6_SEMANTIC_JUDGE.md); this entry is the durable status record.

### H6 bugfix: judge result silently never rendered

**Symptom (reported after initial H6 implementation, live in-browser):** Case 1 investigation
succeeded (`EVIDENCE_SUFFICIENT`/`DRAFTED`/`PASSED`), the "Experimental Semantic Evaluation"
expander and "Run AI Semantic Evaluation" button were both visible and clickable, but clicking
produced no visible result and no visible failure message.

**Root cause (found via `streamlit.testing.v1.AppTest`, zero live calls):**
`application/semantic_judge.py: run_semantic_judge` generated a **fresh, unrelated `uuid4()`**
for `SemanticJudgeEnvelope.run_id`, but `app.py`'s staleness guard
(`if envelope is None or envelope.run_id != result.run_id: return`) compares that field against
the **investigation's** `ApplicationResult.run_id`. Two independently-generated UUIDs are never
equal, so the guard fired on every single invocation — for both a successful (`COMPLETED`) and a
failed judge run alike — and the function returned before reaching either the success-rendering
code or the `JudgeStatus.FAILED` error-rendering code. The envelope was computed and stored
correctly; only the render was silently suppressed.

**Confirmed NOT the cause:** acceptance ("Accept Investigation Draft") was never required —
`_render_semantic_judge_section` is called unconditionally inside the
`workbench.is_accepted_draft(result)` branch, which checks only `GenerationStatus`/
`ValidationStatus`, never a review decision. Verified directly: an `AppTest` run with the button
clicked and `"Accept Investigation Draft"` never clicked still reached and correctly rendered the
judge section once the `run_id` fix was applied.

**Diagnostic method:** since a live call was explicitly disallowed for this investigation, the
real `run_semantic_judge`/`OpenAISemanticJudgeAdapter` code path was driven through the real
`app.py` via `streamlit.testing.v1.AppTest`, with only `openai.OpenAI`'s network-transport layer
faked (`unittest.mock.patch("openai.OpenAI", ...)`) — exercising the actual production code,
including the real exception-handling branches, without any network access. This reproduced the
exact symptom (envelope stored correctly in `session_state`, zero UI elements rendered) and
confirmed the fix (`run_id=result.run_id` instead of `run_id=str(uuid4())`) resolves both the
success and failure rendering paths.

**Fix:** one line in `application/semantic_judge.py`: reuse `result.run_id` (already documented
as the intended value in `application/judge_models.py`'s `SemanticJudgeEnvelope` docstring)
instead of generating a new `uuid4()`. Removed the now-unused `uuid4` import. No other file
required a logic change — `app.py`'s guard was already correct; only the value it was being
compared against was wrong.

**New regression coverage:** `run_id == result.run_id` assertions added to the existing A/F/G/H
tests in `tests/test_semantic_judge.py`, plus two new full-stack tests
(`test_full_stack_ui_renders_completed_judge_result_via_real_code_path`,
`test_full_stack_ui_renders_failed_judge_result_via_real_code_path`) that drive the real `app.py`
through `AppTest` with a faked `openai.OpenAI` client — the same technique used to diagnose the
bug — so this exact class of "data computed correctly but silently never rendered" bug cannot
regress undetected at the unit-test level alone.

**Purpose:** an OPTIONAL, advisory-only evaluation sidecar that critiques an already-drafted,
already-H2-validated `InvestigationBrief` on four semantic dimensions (factual grounding,
completeness, uncertainty preservation, authority boundary) via one additional model call,
invoked only on explicit user request. It cannot change `AgentStatus`, `GenerationStatus`,
`ValidationStatus`, the `InvestigationBrief`, claim data, or review state — no code path exists
that would let it.

**New files:** `application/judge_models.py` (`JudgeVerdict`, `SemanticJudgeResult`,
`JudgeStatus`, `JudgeFailureCategory`, `SemanticJudgeMetadata`, `SemanticJudgeEnvelope`),
`application/semantic_judge.py` (`OpenAISemanticJudgeAdapter`, `FakeSemanticJudgeAdapter`,
`run_semantic_judge`), `prompts/investigation_judge_prompt.py` (reuses the existing
`PromptBundle`/`render_user_prompt` from `prompts/investigation_brief_prompt.py` — no duplicated
context-rendering logic), `tests/test_semantic_judge.py` (13 tests, scenarios A–K),
`docs/H6_SEMANTIC_JUDGE.md`.

**Small additive edits (not behavior changes to existing paths):**
- `application/workbench.py`: `reset_investigation_state` now also pops a `"judge_result"`
  session-state key. `investigation_result`/`review_decision` clearing is unchanged.
- `app.py`: one new `_render_semantic_judge_section` function, called only inside the existing
  `workbench.is_accepted_draft(result)` branch; the Investigate-button handler now also clears
  `"judge_result"` alongside the existing `"review_decision"` clear.

**Not changed:** `agents/`, `skills/`, `context/`, `rag/`, `graph/`, `tools/`,
`application/models.py`, `application/investigation_service.py`,
`application/brief_validator.py`, `application/llm_adapter.py`, any golden set, any synthetic
source data — confirmed by filesystem mtime scan in addition to code review.

**Judge status/failure isolation (regression-tested):** `run_semantic_judge` makes at most one
model call (`max_retries=0`, no retry), and a `JudgeStatus.FAILED` result (any of
`CONFIGURATION`/`PROVIDER`/`TIMEOUT`/`STRUCTURED_PARSING`) never mutates or invalidates the
`ApplicationResult` it was computed from — the existing validated brief remains fully usable.
Case 5 (`NEEDS_REVIEW`) and any non-`DRAFTED`+`PASSED` result raise `ValueError` if
`run_semantic_judge` is called on them at all — the UI never calls it in that state, and zero
judge model calls occur.

**Test results (all offline — no live judge call made; includes the bugfix above):**
- `pytest tests/ -q`: **357 passed, 0 failed, 0 skipped** (up from H5's 342: +15 in
  `tests/test_semantic_judge.py` — the original 13 scenarios A–K plus 2 new full-stack
  `AppTest`-driven UI regression tests added with the bugfix). Same 7 pre-existing warnings
  (unrelated, undismissed).
- All 5 existing component evals (skill/agent/hybrid regression/hybrid retrieval/graph
  retrieval): **identical to H0–H5, zero drift**, including **Policy Section Recall = 63.33%**,
  unchanged.
- `evals.e2e_eval` (E01–E08): **8/8**, unchanged. `evals.e2e_safety_eval` (S01–S09): **9/9**,
  unchanged. Neither suite was modified or re-scoped by H6.

**Calibration limitation (see docs/H6_SEMANTIC_JUDGE.md for full detail):** the judge is **not**
calibrated against any human-rated `InvestigationBrief` dataset. It must not be called
"production validated," used as a workflow gate, reported as a system-accuracy percentage, or
claimed to prove semantic correctness. Intended next step (not implemented): a human-rated
golden set, judge-vs-human agreement measurement, then a calibration decision.

**Live-judge recommendation:** offline-complete and ready for one supervised live invocation
(one existing normal Case-1 generation call, if a fresh `ApplicationResult` is needed, plus
exactly one judge call — no retries) **only after explicit approval**, per instruction. Not run
automatically this stage.

---

## H7 — Scenario Lab / synthetic test-case builder

**Status: OFFLINE-COMPLETE.** H0–H6 remain frozen and unchanged (verified against git tag
`humana-takehome-h6-v1`: zero diff in `agents/`, `skills/`, `context/`, `rag/`, `graph/`,
`tools/`, `application/investigation_service.py`, `application/brief_validator.py`,
`application/semantic_judge.py`, `data/`, or any golden set — the tag equals HEAD commit
`49697da`, and the only tracked-file diff against it is `app.py`, restructured into two tabs).
No live generation/judge call has been made for Scenario Lab — awaiting explicit approval, per
instruction. Full architecture/schema/templates/limitations in
[docs/SCENARIO_LAB.md](SCENARIO_LAB.md); this entry is the durable status record.

**Purpose:** let an evaluator compose a temporary, in-memory-only synthetic claim using the
exact existing data schema (`tools/models.py`) and run it through the SAME, unmodified
investigation pipeline Predefined Claims uses — a test/evaluation surface, not a second
investigation engine, not a production claim-entry workflow, and not a persistent data editor.

**New files:** `application/scenario_lab.py` (`ScenarioDraft`, `AuthorizationDraft`,
`MemberMode`, `ProviderMode`, 8 `TEMPLATE_NAMES` + `build_template_draft`, `validate_scenario`
→ `ScenarioValidationResult` (PASS/WARNING/ERROR), `build_scenario_records`,
`_temporary_data_overlay`, `run_scenario_investigation`, Streamlit session-state helpers),
`tests/test_scenario_lab.py` (19 tests, scenarios A–O), `tests/test_app_scenario_lab_ui.py` (7
full-stack `AppTest`-driven UI regression tests), `docs/SCENARIO_LAB.md`.

**Modified files:**
- `app.py` — restructured into `st.tabs(["Predefined Claims", "Scenario Lab"])`. Predefined
  Claims' behavior is unchanged (same widgets, same keys, same branching); its rendering was
  extracted into `_render_predefined_claims_tab()` with no logic change. The
  status/brief/NEEDS_REVIEW/failure branching and the H3 human-review actions / H6 judge
  section were factored into shared, parameterized functions
  (`_render_investigation_result`, `_render_human_review_actions(..., review_key=...)`,
  `_render_semantic_judge_section(..., judge_key=...)`) so Scenario Lab reuses them verbatim
  against its own separately-namespaced session-state keys — no rendering logic is
  duplicated between tabs.
- `application/workbench.py` — `record_review_decision` gained an optional `key: str =
  "review_decision"` keyword parameter (default preserves H3's exact prior behavior and all
  existing call sites/tests) so `app.py` can call the one existing helper for both tabs'
  review-decision recording instead of duplicating its entry-construction logic inline for a
  second, differently-keyed session-state slot.

**Not changed:** `agents/`, `skills/`, `context/`, `rag/`, `graph/`, `tools/`,
`application/investigation_service.py`, `application/brief_validator.py`,
`application/semantic_judge.py`, `application/models.py`, `application/llm_adapter.py`, any
golden set, any synthetic source data file — confirmed by `git diff --stat
humana-takehome-h6-v1` showing zero changes in any of those paths.

### Audit: no duplicated business logic in app.py

Verified by direct code review that Scenario Lab's UI does not re-derive or duplicate any of:
evidence-sufficiency logic (still exclusively `skills/investigate_claim.py`), investigation
routing (still exclusively the LangGraph in `agents/case_agent.py`), authorization-matching
logic (app.py only renders "every candidate is shown," never picks one), generation logic
(app.py never builds a prompt or calls an LLM adapter directly), H2 validation logic (app.py
only reads `result.validation_result.status`/`.issues`), or H6 judge logic (app.py only calls
`run_semantic_judge(result)` and renders its envelope). `application.scenario_lab.
validate_scenario` is a separate, narrower concern — scenario-*construction* quality (required
fields, non-negative amounts, known `plan_id`, date ordering, intentional-conflict detection)
— and never overlaps with or shadows `is_evidence_sufficient` or any other business rule.

### Same-pipeline proof (exact call paths)

- **Predefined Claims:** `app.py` → `application.investigation_service.run_investigation(claim_id, question)`.
- **Scenario Lab:** `app.py` → `application.scenario_lab.run_scenario_investigation(draft)` →
  (inside `_temporary_data_overlay`) → `application.investigation_service.run_investigation(records.claim.claim_id, draft.question, adapter=adapter)`.

Both converge on the identical `run_investigation` function object, which calls the identical
`agents.case_agent.run_case_agent` (LangGraph), `investigate_claim` skill, retrieval/graph/
context layers, H1 generation adapter contract, and H2 `brief_validator`. Scenario Lab changes
only *what data* those layers see, never *how* they decide — so it is accurate to say "the
Scenario Lab changes test data, not business logic."

### Baseline data integrity

`data/*.json` (all 6 files) hashed (SHA-256) before and after running the full H7 test suite
(`tests/test_scenario_lab.py` + `tests/test_app_scenario_lab_ui.py`, 26 tests): **byte-for-byte
identical, confirmed programmatically.** A temporary scenario claim is removed from the shared
`DataStore` singleton in `_temporary_data_overlay`'s `finally` block and the knowledge graph is
rebuilt from baseline-only data immediately afterward; a scenario claim ID is never a member of
`application.workbench.CASE_IDS`, so the Predefined Claims tab's case selector cannot list or
resolve it even in principle. Regression-tested directly (`test_m_scenario_claim_not_accessible_
after_run_completes`, `test_m_scenario_run_does_not_disturb_predefined_claim_lookup`, and the
UI-level `test_scenario_lab_does_not_modify_source_data_files`).

### Network-isolation safeguard

Both H7 UI test files that can reach an OpenAI-calling button (`tests/test_app_scenario_lab_
ui.py`'s `Run Investigation`, and the pre-existing `tests/test_semantic_judge.py`'s `Run AI
Semantic Evaluation`) install a fake `openai.OpenAI` transport (`_install_fake_openai_client`,
via `monkeypatch.setattr`) before any `AppTest` run, so neither test file can reach the real
network regardless of what credentials happen to be present in `.env`. See "Development
incident" below for why this matters concretely, not just in principle.

### Development incident: accidental live-call risk during UI test authoring (fixed)

While first authoring `tests/test_app_scenario_lab_ui.py`, an early version clicked Scenario
Lab's "Run Investigation" button without mocking the OpenAI transport. Because a real
`OPENAI_API_KEY` was present in this project's local `.env`, `run_investigation`'s default
adapter path (`OpenAIInvestigationBriefAdapter`) was reachable, and five test invocations hit
that unmocked path before it was caught (each one timed out at `AppTest`'s widget-completion
wait, consistent with a real network round-trip still in flight when the harness gave up). This
was a test-isolation/development-process defect — a missing mock in a new test file — not a
defect in the shipped application code, and not something the running Streamlit app itself did
autonomously; it was disclosed to the user immediately upon discovery, before any further
implementation work continued. **Fix:** the test file now installs a fake `openai.OpenAI`
transport before every `AppTest` run (see "Network-isolation safeguard" above); all tests were
re-run afterward and confirmed fully offline. No application code changed as a result of this
incident. Recorded here per AGENTS.md rule 9 (report actual results, don't bury a real event).

### H6 compatibility

The H6 "Experimental Semantic Evaluation" expander is reused, logic-unmodified, for Scenario
Lab results via `_render_semantic_judge_section(result, judge_key=...)` — same gating
(`GenerationStatus.DRAFTED` + `ValidationStatus.PASSED`), same click-only invocation, same
`JudgeStatus`/`JudgeFailureCategory` handling. Namespaced to `SCENARIO_JUDGE_RESULT_KEY` so it
can never be confused with Predefined Claims' `judge_result`. All 15 pre-existing
`tests/test_semantic_judge.py` tests re-run unchanged against the restructured `app.py` and
pass, including the two full-stack `AppTest` regression tests added with the H6 `run_id`
bugfix.

**Test results (all offline — no live generation/judge call made):**
- `pytest tests/ -q`: **383 passed, 0 failed, 0 skipped** (up from H6's 357: +19 in
  `tests/test_scenario_lab.py`, +7 in `tests/test_app_scenario_lab_ui.py`). Same 7 pre-existing
  warnings (unrelated, undismissed).
- All 5 existing component evals: **identical to H0–H6, zero drift**, including **Policy
  Section Recall = 63.33%**, unchanged.
- `evals.e2e_eval` (E01–E08): **8/8**, unchanged. `evals.e2e_safety_eval` (S01–S09): **9/9**,
  unchanged. Neither suite was modified or re-scoped by H7.

**Known limitations:** single-process, not concurrency-safe (one shared `DataStore`/graph
singleton); no new evidence domains beyond `tools/models.py`'s existing schema; no
persistence by design; see [docs/SCENARIO_LAB.md](SCENARIO_LAB.md) for the full list including
the Streamlit widget-`key=` design note.

**Live-smoke-test recommendation:** offline-complete and ready for one supervised live
Scenario Lab invocation (one Custom or templated scenario's "Run Investigation," exactly one
generation call, no retries) **only after explicit approval**, per instruction. Not run
automatically this stage.

---

## H8 — Semantic judge 1-5 rubric scores

**Status: OFFLINE-COMPLETE.** H0–H7 remain frozen and unchanged (verified against git tag
`humana-takehome-h7-v1`: zero diff in `agents/`, `skills/`, `context/`, `rag/`, `graph/`,
`tools/`, `data/`, any golden set, `application/investigation_service.py`,
`application/brief_validator.py`, or `application/scenario_lab.py`). No live judge call has been
made — awaiting explicit approval, per instruction. Full architecture/rubric/limitations in
[docs/H6_SEMANTIC_JUDGE.md](H6_SEMANTIC_JUDGE.md); this entry is the durable status record.

**Purpose:** extend H6's experimental semantic judge so each of the four dimensions returns an
integer 1-5 rubric score alongside its existing PASS/FAIL/UNCERTAIN verdict, plus a
deterministically-computed overall score — an evaluation-signal enhancement only, never a
replacement for the categorical verdict system.

**Files changed (exactly 5, confirmed by `git diff --stat humana-takehome-h7-v1`):**
- `application/judge_models.py` — added `DimensionResult` (score 1-5 + verdict + rationale, with
  a `model_validator` rejecting incoherent score/verdict pairs) and `RawJudgeDimensions` (the
  ONLY schema ever handed to the LLM as `text_format` — no `overall_result`/`overall_score`
  field exists on it, so the model cannot invent that arithmetic even in principle).
  `SemanticJudgeResult` restructured to nest the four `DimensionResult` objects and add
  `overall_score: float`; `overall_result`/`overall_score` are now always computed by
  `SemanticJudgeResult.from_dimensions` (a `@classmethod`), the single source of truth used
  identically by the real adapter and by every test fixture.
- `application/semantic_judge.py` — `OpenAISemanticJudgeAdapter.evaluate` now constrains
  `client.responses.parse` to `text_format=RawJudgeDimensions`, catches `pydantic.ValidationError`
  (mapped to `JudgeFailureCategory.STRUCTURED_PARSING`, exactly like any other unparseable
  structured output) alongside the existing timeout/provider/output-error handling, then calls
  `SemanticJudgeResult.from_dimensions(response.output_parsed)`. `run_semantic_judge`'s public
  signature, guard conditions, and failure-isolation contract are unchanged.
- `prompts/investigation_judge_prompt.py` — system prompt extended with the exact 1-5 anchor
  text for all four dimensions (verbatim per spec) and the score/verdict-coherence guidance;
  instruction to compute `overall_result` removed (now computed in code, not by the model).
  `JUDGE_PROMPT_VERSION` bumped `investigation_judge.v1` → `investigation_judge.v2` (a genuine
  schema/prompt change).
- `app.py` — `_render_semantic_judge_section` updated to display each dimension as
  `score / 5 — VERDICT` plus its own rationale caption, an "Overall Semantic Score" tile, an
  "Overall Verdict" tile, and a short score-guide caption. The existing disclaimer caption
  ("Experimental evaluator — not used for workflow decisions...") is unchanged. No score is ever
  labeled or styled as confidence.
- `tests/test_semantic_judge.py` — all H6 fixtures (A–K, plus the 2 full-stack UI tests)
  rebuilt against the nested schema via a shared `_mocked_result`/`_dim` helper (so overall
  values in fixtures are always computed the same deterministic way production code uses, never
  hand-computed separately); 13 new H8-specific tests added (see "Test results" below).

**Not changed:** `agents/`, `skills/`, `context/`, `rag/`, `graph/`, `tools/`, `data/`, any
golden set, `application/investigation_service.py`, `application/brief_validator.py`,
`application/models.py`, `application/llm_adapter.py`, `application/scenario_lab.py`,
`application/workbench.py` — confirmed by `git diff --stat humana-takehome-h7-v1` showing zero
changes in any of those paths.

### 1-5 rubric scoring

Each dimension (factual grounding, completeness, uncertainty preservation, authority boundary)
returns an integer score 1-5 with explicit anchors embedded in the judge prompt (see
[docs/H6_SEMANTIC_JUDGE.md](H6_SEMANTIC_JUDGE.md) for the full anchor text). Scores are never
decimals at the dimension level, never percentages, and never labeled confidence/probability/
accuracy/calibrated-likelihood anywhere in code, prompt, or UI.

### Score / verdict relationship

A score of 4-5 is normally PASS, 1-2 is normally FAIL, and 3 may resolve to any of PASS/FAIL/
UNCERTAIN depending on materiality — scores add nuance, they do not replace the categorical
verdict. `DimensionResult`'s Pydantic `model_validator` rejects the two self-contradictory
combinations explicitly called out in spec (score 4-5 + FAIL, score 1-2 + PASS) at construction
time; a genuinely-incoherent live model response is mapped to
`JudgeFailureCategory.STRUCTURED_PARSING` rather than silently accepted (regression-tested:
`test_h8_h_real_adapter_maps_incoherent_llm_output_to_structured_parsing_failure`).

### Overall score calculation

`overall_score` is the unweighted arithmetic mean of the four dimension scores, calculated
deterministically in `SemanticJudgeResult.compute_overall_score` — **never returned or invented
by the model**, since the model's constrained schema (`RawJudgeDimensions`) structurally excludes
the field. Worked example from spec, regression-tested exactly:
`factual_grounding=5, completeness=4, uncertainty_preservation=5, authority_boundary=5` →
`overall_score == 4.75`. Never converted to or reported as a percentage.

### Overall verdict rule preserved

`overall_result` keeps H6's exact original rule (any FAIL → FAIL; else any UNCERTAIN →
UNCERTAIN; else PASS), now enforced by `SemanticJudgeResult.compute_overall_result` against the
four dimension verdicts in code rather than trusted from the model's own summary — the rule
itself is unchanged, only where it is evaluated moved from prompt-instruction to
code-enforcement, eliminating a class of potential LLM rule-application error.

### Test results (offline — no live judge call made)

- `tests/test_semantic_judge.py` grew from 15 to **28 tests**: the original H6 scenarios A–K and
  2 full-stack UI tests (rebuilt against the new schema, same intent), plus 13 new H8 tests —
  scores 1-5 accepted; score 0 and score 6 rejected; `overall_score`'s deterministic arithmetic
  mean (including the exact spec worked example); FAIL/UNCERTAIN/PASS overall-verdict
  aggregation preserved; incoherent score/verdict combinations rejected both at direct
  construction and via a simulated live-adapter response path; score 3 permits any verdict;
  Scenario Lab compatibility (a Scenario Lab `ApplicationResult` judged identically to a
  Predefined Claims one); and the full-stack `AppTest` UI test extended to assert the score/5,
  per-dimension rationale, overall score, and overall verdict all render, and that "confidence"
  never appears anywhere in the rendered judge section.
- `pytest tests/ -q`: **396 passed, 0 failed** (383 H7 baseline + 13 net new). Same 7
  pre-existing warnings (unrelated, undismissed).
- All 5 existing component evals: **identical to H0–H7, zero drift**, including **Policy
  Section Recall = 63.33%**, unchanged.
- `evals.e2e_eval` (E01–E08): **8/8**, unchanged. `evals.e2e_safety_eval` (S01–S09): **9/9**,
  unchanged. Neither suite was modified or re-scoped by H8.
- `tests/test_app_scenario_lab_ui.py` (all 7 H7 UI tests) and `tests/test_scenario_lab.py` (all
  19 H7 tests) re-run unchanged and pass, confirming Scenario Lab compatibility with the
  restructured judge model without any change to `application/scenario_lab.py`.

### Calibration limitation (unchanged in kind, extended to scores)

The judge — verdicts and now scores alike — remains **not** calibrated against a sufficiently
large human-rated `InvestigationBrief` dataset. The 1-5 scores are evaluation signals, not
calibrated confidence or accuracy probabilities; see
[docs/H6_SEMANTIC_JUDGE.md](H6_SEMANTIC_JUDGE.md)'s "Calibration limitation" section for the
full, unchanged set of dos/don'ts, now extended explicitly to the score.

**Live-judge recommendation:** offline-complete and ready for one supervised live invocation
(one existing normal Case-1 generation call, if a fresh `ApplicationResult` is needed, plus
exactly one judge call — no retries) to confirm the live model reliably returns coherent
`RawJudgeDimensions` JSON under the new v2 prompt/schema, **only after explicit approval**, per
instruction. Not run automatically this stage.

### H8 addendum: leniency-bias fix (prompt v2 → v3)

**Reported by the user** after a live smoke test they ran themselves (outside this session, using
their own `.env` credentials): the judge returned 5/5 on every dimension regardless of the case or
brief evaluated. Diagnosed as a well-documented "LLM-as-judge" ceiling-effect/leniency bias, not a
plumbing defect — `overall_score`/`overall_result` computation was already independently verified
correct offline against mocked/simulated data in H8's own test suite. **Fix:** prompt-text-only
change to `prompts/investigation_judge_prompt.py` (`JUDGE_PROMPT_VERSION` bumped
`investigation_judge.v2` → `investigation_judge.v3`): an explicit calibration warning that 5 must
be rare and requires an active, deliberate search for imperfections before being awarded; each
dimension's 5/4 anchors reframed around that active-search requirement; and a requirement that
every rationale name a concrete phrase/statement/omission rather than a generic affirmation. No
other file changed for this fix — confirmed by diffing against the H8 state above. `pytest tests/
-q` re-run and still **396 passed, 0 failed** (schema/model/rendering code untouched; this is a
prompt-text change only, exercised only by a live call). **Not yet re-verified against a live
call** — recommend one supervised live retest across 2-3 different cases/scenarios to confirm
scores now vary meaningfully before treating this as resolved in practice.

---

## Post-audit remediation — Scenario Lab collision fix, generation parsing gap, Rule D
## precision, E06 cross-source consistency, README/doc synchronization

**Status: COMPLETE, verified offline, zero live OpenAI calls.** Base version:
`claims-copilot-final-v1` (commit `7e824e9d9cd71c995cec2442c21cae413c9e739a`). This stage fixed
four specific defects an independent source-code audit found, added a regression test for each
(that would have failed against `claims-copilot-final-v1`), and synchronized documentation
(including a full README rewrite) against the resulting, actually-current implementation. H0–H8
architecture is otherwise unchanged; the semantic-judge scoring/calibration prompt
(`prompts/investigation_judge_prompt.py`) was explicitly out of scope and was not touched.

### P0 — Scenario Lab must restore baseline state exactly, and define scoped-replacement semantics

**Finding:** `application/scenario_lab.py`'s `_temporary_data_overlay` tracked only "did I add
this record," never "did I overwrite something that was already there." A scenario colliding
with a baseline `(plan_id, service_code)` benefit key (e.g. `PLAN-GOLD` + `MRI-KNEE`, which
baseline data already occupies) had its cleanup unconditionally `pop()` the key — permanently
deleting the baseline benefit for the remainder of the process, not just for the duration of
the scenario. Separately, `benefit_available=False` never touched the benefit key at all, so a
colliding baseline benefit stayed fully visible despite the user declaring "no benefit" for the
scenario. For prior authorizations, only the grouped index
(`prior_authorizations_by_member_service`, read by `tools/prior_auth_tool.py`) was ever
touched — `graph/builder.py` iterates the flat `prior_authorizations` dict directly, so a
scenario reusing an `EXISTING` baseline member+service pair with baseline authorizations already
on file could show a different authorization set in the graph than in structured facts, and a
scenario supplying zero authorization rows for such a pair did not hide the baseline ones at
all (both stores were left completely untouched).

**Fix (`application/scenario_lab.py`, `_temporary_data_overlay`):** every touched key is now
snapshotted (its exact pre-scenario value, or its absence) **before** any mutation, and restored
to that exact snapshot in `finally` — never a blind `pop()`. Scenario Lab's semantics are now
explicit and documented ("scoped-replacement view"): a scenario's benefit *replaces or hides*
whatever occupies `(plan_id, service_code)` for the duration of the run regardless of
`benefit_available`'s value; a scenario's authorization list (zero, one, or many rows) is the
*complete* visible set for `(member_id, service_code)` during the run, with any baseline
authorizations for that exact pair hidden from **both** `prior_authorizations` (the flat dict)
and `prior_authorizations_by_member_service` (the grouped index) — never one store correct and
the other stale. Restoration is unconditional in `finally`, so it survives an exception raised
mid-investigation, not only a successful run.

**Regression tests added** (`tests/test_scenario_lab.py`, "P" group, 6 new tests — each verified
to fail against `claims-copilot-final-v1` before the fix, 5 of 6 directly; see below):
- `test_p_benefit_collision_restores_exact_baseline_value` — PLAN-GOLD/MRI-KNEE benefit replaced
  during the scenario, exact original object (`is` identity) restored after.
- `test_p_benefit_available_false_hides_colliding_baseline_benefit_during_run` —
  `benefit_available=False` on the same colliding key: `structured_facts.benefit is None` during
  the run; baseline restored after.
- `test_p_existing_member_zero_authorizations_hides_baseline_authorizations_everywhere` — M-1002
  + MRI-KNEE (baseline `PA-1501`/`PA-2001`) with `authorizations=[]`: zero authorizations in
  structured facts *and* absent from every graph relationship / assembled reference; baseline
  group and both records restored after.
- `test_p_existing_member_scenario_authorization_replaces_baseline_authorizations` — same pair
  with one scenario authorization: exactly the scenario record visible (structured facts +
  graph), neither baseline record visible; baseline set restored after.
- `test_p_exception_inside_overlay_still_restores_everything` — a forced exception mid-overlay
  still leaves a full in-memory `DataStore` snapshot equal to before, and a normal Predefined
  Claims investigation (CLM-1002) still resolves correctly immediately afterward.
- `test_p_in_memory_datastore_snapshot_equivalent_after_multiple_collision_scenarios` — a
  stronger check than hashing `data/*.json` alone (existing `test_c`): a full in-memory
  `DataStore` snapshot (all six structures) is equal before/after running five scenarios back to
  back, including deliberate collisions.

**Verification against the pre-fix code:** the fixed implementation was temporarily swapped for
the exact `claims-copilot-final-v1` version and the 6 new tests re-run: 5 failed immediately
(the benefit/authorization collision and exception-restoration tests), 1 passed regardless (the
in-memory snapshot test, whose pass/fail against the buggy code is order-dependent within a
shared-process pytest run — the other 5 already directly prove each specific defect
independently). The fix was then restored and the full suite re-confirmed green.

### Scenario Lab concurrency / hosting boundary (not redesigned, documented + surfaced in UI)

Per instruction, Scenario Lab's process-wide mutable singleton was **not** redesigned into a
concurrency-safe architecture. Instead: `app.py`'s Scenario Lab tab now displays a visible
warning ("Scenario Lab is a single-user prototype testing surface. It is not designed for
concurrent multi-user execution. Predefined Claims is the primary demo path."),
`docs/SCENARIO_LAB.md` documents the scoped-replacement semantics and restates the existing
concurrency limitation and production guidance (a per-request `DataStore` instance instead of a
process-wide mutable overlay), and README.md's "Known limitations" section states the same. No
environment/config disable-flag was added — not required by the finding, and the instruction
explicitly said not to over-engineer this point.

### P1 — Generation adapter's structured-parsing exception gap

**Finding:** `application/semantic_judge.py` already caught `pydantic.ValidationError` around
its `client.responses.parse(...)` call and mapped it to `JudgeFailureCategory.STRUCTURED_PARSING`
(from the H8 rubric-scoring work). `application/llm_adapter.py`'s `OpenAIInvestigationBriefAdapter`
did not mirror this for `InvestigationBrief` generation — a response whose JSON the SDK could
parse but that failed `InvestigationBrief`'s own Pydantic validation would have raised a raw,
unhandled `pydantic.ValidationError` out of `generate()`, uncaught by
`application/investigation_service.py`'s `except LLMOutputError` clause.

**Fix (`application/llm_adapter.py`):** added `except ValidationError as exc: raise
LLMOutputError(...) from exc` around the `client.responses.parse(...)` call, positioned before
the existing `except openai.OpenAIError` clause (a `pydantic.ValidationError` is not an
`OpenAIError` subclass, so ordering here doesn't matter for correctness, but mirrors
`application/semantic_judge.py`'s structure for readability).
`application/investigation_service.py` was **not** changed — its existing `except LLMOutputError`
→ `GenerationFailureCategory.STRUCTURED_PARSING` mapping already covers the newly-caught case
correctly; no new failure enum was needed.

**Regression tests added:**
- `tests/test_application_llm_adapter.py::test_openai_adapter_maps_pydantic_validation_error_to_output_error`
  — a fake transport's `responses.parse(...)` itself raises a real `pydantic.ValidationError`
  (by constructing an `InvestigationBrief` with an invalid `action_code` enum value inline, not
  by raising `LLMOutputError` directly), and the adapter must convert it to `LLMOutputError`.
- `tests/test_application_investigation_service.py::test_real_adapter_pydantic_validation_error_maps_to_structured_parsing_end_to_end`
  — the same fake-transport approach driven through the real `OpenAIInvestigationBriefAdapter`
  and `run_investigation`, asserting the full chain: `GenerationStatus.FAILED`,
  `GenerationFailureCategory.STRUCTURED_PARSING`, `ValidationStatus.NOT_RUN`, no fabricated
  brief, evidence work still preserved.

Both tests were confirmed to raise an unhandled `pydantic.ValidationError` (not `LLMOutputError`)
against the pre-fix `application/llm_adapter.py`, then pass cleanly after the fix. No network
call in either test.

### P1 — Authority-language guardrail (Rule D) precision

**Finding:** the original Rule D regex (`verb directly followed by "the/this" + object noun`)
had both false positives and false negatives. False positives: a third-party historical fact
using the same words in active voice ("The payer denied the claim.", "The insurer denied the
claim.", "The recorded system denied the claim.") matched the raw verb+object substring despite
describing something a *different* party already did, not a claim of the system's own
authority; a negated statement ("Do not approve the claim.", "This system does not approve the
claim.", "The copilot cannot approve the claim.") matched despite explicitly *disclaiming*
authority. False negatives: a passive modal recommendation ("The claim should be approved.")
never matched, since the object precedes the verb; a bare object without a determiner ("Approve
claim CLM-1001.") never matched, since the pattern required "the"/"this" immediately before the
noun.

**Fix (`application/brief_validator.py`):** redesigned as three targeted, deterministic pattern
categories rather than a raw verb+object blacklist: (1) imperative — the prohibited verb is the
first word of a sentence; (2) a prohibited verb+object is flagged only when **directly**
governed (no intervening word) by a small, closed set of authority-claiming subjects ("we",
"i", "you", "this/the system", "this/the copilot", "this prototype", "this/the assistant",
"recommend", "suggest", "advise"); (3) an explicit passive-modal pattern ("the/this claim
should be approved/denied/paid/reversed"). The object-noun pattern's determiner ("the"/"this")
was made optional so a bare object ("approve claim CLM-1001") is still caught. Requiring
**strict adjacency** in pattern (2) is what excludes a negated statement without any separate
negation blacklist — the intervening "not"/"cannot"/"does not" breaks the required adjacency —
and what excludes a third-party historical fact, since "payer"/"insurer"/"recorded system" are
not in the closed authority-claiming-subject list. A deliberate, documented remaining gap: an
intervening modal attached to a first-person subject ("We must approve the claim.") is not
caught, since it also breaks the same strict-adjacency requirement; this is accepted and
regression-tested as a known boundary, not silently left undocumented.

**Regression tests added** (`tests/test_application_brief_validator.py`, "H" group, 17 new
tests): 7 parametrized safe/negated cases that must NOT trigger Rule D, 9 parametrized unsafe
cases that MUST trigger it (covering every example above plus the pre-existing "We recommend
approving the claim.", "Pay the claim.", "Deny this authorization.", and the unchanged
medical-necessity pattern), and one test documenting the known "We must approve the claim."
gap explicitly. All 27 pre-existing Rule D tests continue to pass unchanged. Verified against
`claims-copilot-final-v1`'s original implementation: 9 of the 17 new tests failed (the 6 false
positives, the 2 false negatives, and — instructively — the documented-gap test, since the old,
imprecise regex accidentally caught "We must approve the claim." as a side effect of its own
imprecision, a "lucky" true positive traded away in exchange for eliminating several real false
positives).

### P2 — E06 must remove an authorization consistently across every evidence source

**Finding:** `evals/e2e_eval.py`'s E06 scenario isolated "PA-2001 removed" by patching
`tools.case_context.get_prior_authorizations` — a **tool-boundary** patch. `graph/builder.py`
builds the knowledge graph directly from the process-wide `DataStore`'s flat
`prior_authorizations` dict, never through that tool function, so the patch left PA-2001 fully
present in the graph and in every graph-derived reference even though the tool-based structured
facts correctly showed it removed — verified directly (before the fix): structured facts
correctly showed only `PA-1501`, but `authorization:PA-2001` remained present in
`EvidencePackage.graph_relationships`'s node ids and in `AssembledContext.references`.

**Fix (`evals/e2e_eval.py::run_e06`):** replaced the tool-function patch with a temporary,
reversible `DataStore`-boundary override — the same overlay pattern
`application/scenario_lab.py` uses — removing `PA-2001` from both `store.prior_authorizations`
(the flat dict) and its `prior_authorizations_by_member_service` group, clearing the graph
cache, running the real evidence/application path, then restoring both exactly in `finally`
and clearing the graph cache again. `data/prior_authorizations.json` is never read or modified.
The scenario now asserts PA-2001's absence from all four evidence surfaces explicitly:
`EvidencePackage.structured_facts.prior_authorizations`, `EvidencePackage.graph_relationships`,
`AssembledContext.references`, and the rendered generation prompt
(`prompts.investigation_brief_prompt.render_user_prompt(context)`) — plus the existing check
that a brief citing `auth:PA-2001` still fails deterministic validation. `evals/e2e_eval.py`'s
now-unused `from unittest.mock import patch` import was removed.

**Verification:** re-running the old tool-boundary-patch approach standalone (not merely
asserting on it) confirmed `authorization:PA-2001` was indeed present in
`graph_relationships`/assembled references under the old approach — the exact defect the audit
described. `evals.e2e_eval` was also run twice in a row after the fix to confirm the override is
fully idempotent (both runs 8/8, `data/prior_authorizations.json` byte-identical throughout).

### Documentation / README synchronization

`README.md` was substantially rewritten (the prior version described the H0-stage MVP and
explicitly stated "no LLM integration," "no LLM-as-judge," "no guardrails," and "placeholder
Streamlit UI," all long superseded by H1–H8). The new README accurately describes: the
synthetic-data/non-affiliation disclaimer; Predefined Claims as the primary demo path and
Scenario Lab as an experimental, single-user testing surface; every implemented layer (tools,
RAG with the TF-IDF-vs-semantic distinction, Chroma, the NetworkX knowledge graph, GraphRAG
evidence assembly, LangGraph orchestration, reusable skills, the `EvidencePackage`, the
evidence-sufficiency gate and its zero-model-call path, the context assembler, OpenAI
structured generation, the deterministic validator, local human review, the optional semantic
judge, and the guardrails catalog); the three-tier evaluation architecture and the honestly-
reported 63.33% Policy Section Recall; known limitations; setup/run instructions; and an
explicit environment-variable table that corrects a real documentation gap:
**`RAG_EMBEDDING_PROVIDER` does not control the main investigation pipeline's policy retrieval**
— `context/hybrid_retriever.py` hard-codes `provider_name="semantic"` and the `LARGE` chunking
config for its one policy-retrieval call; the env var only affects
`rag/embeddings.py: get_default_embedding_provider`, a lower-level helper not currently invoked
anywhere in the investigation pipeline (confirmed by a repository-wide search finding zero
callers outside `rag/embeddings.py` itself). This was already correctly noted in this document's
own H1 entry but had never been surfaced in the user-facing README until now.

Other documents corrected (current-architecture descriptions only — historical stage records,
including this file's own prior H0–H8 entries and `docs/EVALUATION_REPORT.md`'s H4 narrative,
were left as the historical record they are, with a dated addendum note added where a since-
changed technical detail needed a pointer to the current implementation, per instruction not to
rewrite history):
- `AGENTS.md` — corrected the stale claim that `prompts/`/`guardrails/` are both scaffolding;
  `prompts/` now holds real runtime prompts, and `guardrails/` remains an intentionally-empty
  package (enforcement lives in each rule's owning layer, catalogued in `docs/GUARDRAILS.md`,
  never centralized here).
- `docs/architecture.md` — its "Current implementation status" section (an explicitly *current*,
  not historical, tracker) updated: Prompt/Harness, LLM, Grounding/Guardrails, and Answer/Human
  Escalation moved from PLANNED to IMPLEMENTED with accurate detail; the Context Assembler entry
  corrected from "partially implemented, feeding it to an LLM is planned" to fully implemented;
  new entries added for the application layer, Scenario Lab, and the optional semantic judge;
  Observability and Governance correctly remain PLANNED.
- `docs/GUARDRAILS.md` — guardrail #8 (authority-language validation) rewritten to describe the
  new adjacency-based Rule D design; guardrail #9 (failure handling) updated to mention the new
  `ValidationError` catch in the generation adapter; guardrail #15 (Scenario Lab isolation)
  updated to describe exact-state restoration (not "removes every added record") and reference
  the new "P" test group.
- `docs/EVALUATION_REPORT.md` — H4's original E06 narrative left untouched as the historical
  record it is; a dated addendum note added directly after it pointing to the revised mechanism
  and this entry, without rewriting the H4 section itself.
- `docs/SCENARIO_LAB.md` — already updated as part of the P0 fix above (scoped-replacement
  semantics section, corrected overlay description).
- `docs/H6_SEMANTIC_JUDGE.md` — audited, found to reference none of the fixed items; left
  unchanged, consistent with the explicit instruction not to touch judge calibration.

### Final offline regression (post-remediation)

- `pytest tests/ -q`: **421 passed, 0 failed** (396 H8 baseline + 25 new: 6 Scenario Lab
  collision tests, 1 generation-adapter test, 1 investigation-service end-to-end test, 17 Rule D
  tests). Same 7 pre-existing warnings (unrelated, undismissed).
- All 6 component evals (`retrieval_eval`, `skill_eval`, `agent_eval`, `hybrid_regression_eval`,
  `hybrid_retrieval_eval`, `graph_retrieval_eval`): **identical to H0–H8, zero drift**, including
  **Policy Section Recall = 63.33%**, unchanged.
- `evals.e2e_eval` (E01–E08): **8/8**, including E06's new cross-source consistency checks.
- `evals.e2e_safety_eval` (S01–S09): **9/9**, unchanged (S07's existing "The claim should be
  approved."/"Approve the claim." injection-compliance check continues to correctly fail H2
  validation under the new Rule D design).
- `artifacts/e2e_eval_results.json` / `artifacts/e2e_safety_eval_results.json`: byte-identical
  before/after re-running both suites — no incidental churn to restore.
- Zero live OpenAI calls made at any point during implementation or verification. Every test
  path capable of reaching the OpenAI SDK used `FakeInvestigationBriefAdapter`,
  `FakeSemanticJudgeAdapter`, or a monkeypatched `openai.OpenAI` transport.

**Interview-safe summary:** an independent source-code audit found four real defects —
inexact state restoration in Scenario Lab's temporary-data overlay, a generation-adapter
exception-handling gap, precision issues in the authority-language guardrail, and an
evaluation-only cross-source consistency gap in one E2E scenario — and all four were fixed at
the implementation level (not by weakening a test or changing a golden expectation), each with
a new regression test confirmed to fail against the prior frozen version before the fix and
pass after it. No architectural change, no new feature, no change to the semantic judge's
scoring/calibration prompt.
