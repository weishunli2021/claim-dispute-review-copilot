# Dispute Review Baseline (Module 2)

This document records the honestly-verified baseline established for the **Claim Dispute
Review Copilot** project before any Dispute Review code exists. It is a snapshot, not a design
document — see [architecture.md](architecture.md) for the target end-state architecture (which
Dispute Review has not yet been added to).

## Source and project paths

- **Original project (read-only source, not modified by Module 2):**
  `D:\AI\claude-code\humana-takehome-claims-copilot`
- **New project (this repository):**
  `D:\AI\claude-code\claim-dispute-review-copilot`
- Module 1 copied source/fixtures byte-identical, removed the original's generated
  `rag/index`, and created a fresh project-local `.venv`. Module 2 did not re-verify the copy
  step itself — only the runtime behavior of the copied code.

## Python version

`Python 3.14.5`, via the project-local `.venv` (`.venv\Scripts\python.exe`), created with
`py -3.14 -m venv .venv` per [AGENTS.md](../AGENTS.md).

## Actual test and evaluation outcomes

All commands were run with the new project's `.venv` Python explicitly, from
`D:\AI\claude-code\claim-dispute-review-copilot`.

| Command | Result |
| --- | --- |
| `python -m pip check` | No broken requirements found. |
| `python -m pytest tests/ -q` | **421 passed**, 1 warning (a third-party `DeprecationWarning` in `chromadb`, unrelated to this project's code), 0 failed, 0 skipped, 0 errors, 97.03s. |
| `python -m evals.e2e_eval` | **8 of 8** defined prototype scenarios (E01–E08) passed their specified checks. |
| `python -m evals.e2e_safety_eval` | **9 of 9** defined prototype scenarios (S01–S09) passed their specified checks. A local `sentence-transformers` weights download occurred for the "semantic" embedding-provider fixture used by one safety scenario (S07); this is a local/offline model load, not a live LLM API call. |

No test or eval assertion was modified, loosened, or skipped to obtain these results. All LLM
adapter calls in tests/evals go through a monkeypatched fake OpenAI client or a mocked
`BriefAdapter` (see `tests/test_application_llm_adapter.py`, `evals/e2e_common.py`'s
`well_formed_brief`) — no live OpenAI API call was made at any point in Module 2.

These are the actual counts observed in this run; do not assume they carry forward unchanged
into future modules — re-run and re-record rather than reusing this table (AGENTS.md rule 9).

## UI verification performed and limits

- Ran the existing UI test file directly: `pytest tests/test_app_scenario_lab_ui.py -q` — **7
  passed** after the branding change, confirming both tabs still construct and render without
  error under Streamlit's `AppTest` harness.
- Started the renamed app locally via the built-in browser preview tool, using
  `.claude/launch.json`'s existing `streamlit-app` configuration
  (`.venv/Scripts/python.exe -m streamlit run app.py --server.headless=true`) on port `8501`,
  which was confirmed free before starting (no existing app process was stopped or interfered
  with).
- Verified in the browser:
  - Browser tab title and in-page `st.title` both read **"Claim Dispute Review Copilot"**.
  - Both existing tabs ("Predefined Claims", "Scenario Lab") render.
  - The "Select a case" dropdown on the Predefined Claims tab was opened and showed all five
    existing claim choices (`CLM-1001`–`CLM-1005`).
  - No startup exception; `preview_logs` (error-filtered) reported no server errors.
- Stopped only the server process this session started.
- **Limit:** no button that triggers `run_investigation` (i.e., an LLM adapter call) was
  clicked, per the task boundary against live model calls. The "Investigate Claim" /
  "Run Investigation" result-rendering paths, the Human Review buttons, and the Experimental
  Semantic Evaluation expander were therefore **not** visually exercised in this browser
  session — their correctness rests on the passing `test_app_scenario_lab_ui.py` suite and the
  E2E/safety eval runs above, not on direct browser observation. Server health and a rendered
  first screen do not by themselves prove every code path in `app.py` renders correctly.

## Minimal branding / configuration changes

- [app.py](../app.py): `st.set_page_config(page_title=...)` and `st.title(...)` changed from
  "Claims Investigation Copilot" to **"Claim Dispute Review Copilot"**. No other line in
  `app.py` was changed — both tabs, all controls, and all investigation/review/judge logic are
  byte-for-byte unchanged.
- [README.md](../README.md): heading changed to "Claim Dispute Review Copilot"; added a note
  stating this project is a separate extension of the existing Claims Investigation Copilot,
  that Dispute Review is planned but not yet implemented, and that the existing "Hosted demo"
  link points to the **original** app (this project has not been deployed anywhere). No other
  README section (architecture, guardrails, known limitations, synthetic-data policy, etc.) was
  changed.
- [.env.example](../.env.example) (new file): documents the four environment variables the
  code actually reads — `RAG_EMBEDDING_PROVIDER` (default `tfidf`, offline), `OPENAI_API_KEY`,
  `LLM_MODEL`, `LLM_TIMEOUT_SECONDS` — with a placeholder for `LLM_MODEL`
  (`REPLACE_WITH_MODEL_NAME_BEFORE_LIVE_GENERATION`, explicitly not a real model name) and blank
  `OPENAI_API_KEY`. No real `.env` file exists in this project; it is not created by this
  module.
- `.gitignore` was **not** modified — its existing rules already exclude `.venv/`, `.env`
  variants, caches, generated `rag/index`/graph stores, and logs.
- No other file's logic was changed under Module 2.

## CLM-1001 field mapping (for future deterministic dispute comparisons)

From `data/claims.json` (`claim_id="CLM-1001"`) and `tools.case_context.get_case_context`:

| Field | Value | Source |
| --- | --- | --- |
| `claim_id` | `CLM-1001` | `data/claims.json` |
| `member_id` | `M-1001` | `data/claims.json` |
| `plan_id` | `PLAN-GOLD` | `data/claims.json` |
| `service_code` | `MRI-KNEE` | `data/claims.json` |
| `date_of_service` | `2026-02-10` | `data/claims.json` |
| `status` | `DENIED` | `data/claims.json` |
| `billed_amount` | `1800.0` | `data/claims.json` |
| `allowed_amount` | `null` (not present) | `data/claims.json` |
| `denial_reason_code` | `AUTH_REQUIRED` | `data/claims.json` |
| `denial_reason_description` | "Prior authorization is required for this service and no approved authorization was found on file." | `data/claims.json` |
| **Servicing provider** (`claim.provider_id` → `PRV-1001`) | `Lakeside Imaging Center`, `facility`, `in-network` (Meridian Preferred Network) | `data/providers.json` via `tools.provider_tool.get_provider` |
| **Ordering provider** (`claim.ordering_provider_id` → `PRV-4001`, optional) | `Dr. Ana Kowalski`, `physician`, `in-network` (Meridian Preferred Network) | `data/providers.json` via `tools.provider_tool.get_provider` |
| Prior authorizations | none on file for `(M-1001, MRI-KNEE)` — evaluated as an explicit "no matching record," not treated as a missing-evidence gap (see `skills/investigate_claim.py`'s `CRITICAL_MISSING_CATEGORIES`) | `tools.prior_auth_tool.get_prior_authorizations` |

**Servicing vs. ordering provider distinction** (`tools/case_context.py`): `Claim.provider_id`
is a required field and always resolves via `get_provider` into `CaseContext.servicing_provider`
— the provider where the service was actually rendered. `Claim.ordering_provider_id` is
`Optional[str]`; when present, it resolves the same way into
`CaseContext.ordering_provider` — the provider who ordered/referred the service, which may
differ from who performed it (as it does for CLM-1001: an orthopedist ordered an MRI performed
at an imaging center). Both fields are independently `None`-able and are never conflated in any
downstream layer.

## How the main investigation reaches the LLM adapter

```
app.py ("Investigate Claim" button)
  -> application.investigation_service.run_investigation(claim_id, query)
       -> agents.case_agent.run_case_agent(...)              # the ONE evidence-workflow execution; no LLM call anywhere in agents/ or skills/
            -> agents/nodes.py build_evidence -> skills.investigate_claim.investigate_claim(...)
                 -> context.hybrid_retriever.build_evidence_package(...)   # Vector RAG + Knowledge Graph + deterministic tools
       -> [only if AgentStatus.EVIDENCE_SUFFICIENT]
            -> application.context_assembler.assemble_context(agent_result)
            -> prompts.investigation_brief_prompt.build_prompt(context)
            -> application.llm_adapter.OpenAIInvestigationBriefAdapter.generate(prompt_bundle)   # the ONE LLM call
            -> application.brief_validator.validate_investigation_brief(brief, context)          # deterministic, non-LLM
```

`app.py` never calls the OpenAI adapter, `skills.investigate_claim`, or
`context.hybrid_retriever` directly — it calls `run_investigation` (Predefined Claims) or
`application.scenario_lab.run_scenario_investigation` (Scenario Lab, which itself calls the same
`run_investigation`) exactly once per click. `AgentStatus.NEEDS_REVIEW`, `ERROR`, and
`MAX_STEPS_EXCEEDED` all short-circuit before the LLM adapter is ever constructed — zero model
calls on those paths (verified by E05/E08 and S06 above).

## Baseline isolation concern relevant to a future Dispute Review tab

`app.py`'s own module docstring already states the load-bearing pattern: the two existing tabs
use **completely separate, non-overlapping `st.session_state` key namespaces** — Predefined
Claims uses plain keys (`selected_claim_id`, `investigation_result`, `review_decision`,
`judge_result`); Scenario Lab uses distinct `SCENARIO_*_KEY` constants defined in
`application/scenario_lab.py` — specifically so neither tab can read or clear the other's state.

A future Dispute Review tab must follow the same discipline: its own distinct session-state key
namespace (not reusing or prefix-colliding with the Predefined Claims or Scenario Lab keys), and
— per AGENTS.md rules 1 and 6 — it must not mutate `data/claims.json` or any other baseline
fixture directly.

**Correction (recorded before Module 3, superseding an earlier draft of this section):**
Dispute Review must **not** reuse Scenario Lab's DataStore-boundary override (the mechanism
E06 exercises to temporarily substitute `store.prior_authorizations` etc.). That mechanism
mutates shared, process-global `DataStore` state for the duration of a call — an appropriate
fit for Scenario Lab's single "compose one hypothetical case and run it through the pipeline"
flow, but the wrong shape for Dispute Review, which needs to hold an *original* claim and a
*proposed* claim/dispute side by side without ever touching the shared store or the records
other tabs read. Instead:

- Dispute Review reads the original claim (and its member/plan/benefit/provider/authorization
  records) the same way Predefined Claims already does — via `tools.case_context.get_case_context`
  / `context.hybrid_retriever.build_evidence_package` — and never modifies what those calls
  return or the underlying `DataStore`.
- The proposed dispute is held as a separate, plain in-memory submission object (not a
  DataStore override), compared against the original claim's evidence rather than substituted
  for it.
- Submissions and their results live in their own dedicated `st.session_state` key namespace,
  distinct from both `selected_claim_id`/`investigation_result`/`review_decision`/`judge_result`
  (Predefined Claims) and the `SCENARIO_*_KEY` constants (Scenario Lab).
- Nothing in Dispute Review mutates the `DataStore`, claim records, authorization records, or
  the knowledge graph — read-only access to baseline data, always. This also avoids a second
  parallel retrieval/sufficiency path (AGENTS.md rules 3–4).
- Existing Scenario Lab functionality (including its DataStore-override mechanism) is untouched
  by this correction — it remains the right tool for Scenario Lab's own use case; Dispute Review
  simply must not adopt it.

## Known baseline limitations (pre-existing, unchanged by Module 2)

- Policy Section Recall is 63.33% — retrieved policy evidence is not exhaustive (stated in the
  app's own Safety & Guardrails panel).
- The optional semantic judge is advisory only, not calibrated as a production quality gate.
- Scenario Lab is a single-user prototype testing surface, not designed for concurrent
  multi-user execution.
- `AgentStatus.ERROR` does not distinguish "claim not found" from "unexpected execution/tool
  failure" — both currently share one status, differing only in free-text `.error` message
  (documented, accepted limitation; see S09 above).
- No live-model test suite exists yet in this project (per Module 2 scope); all current
  tests/evals are fully offline/mocked.

## Explicit statements

- **Dispute Review is not implemented.** No dispute-submission code, model, tool, skill, or UI
  control exists anywhere in this repository as of this baseline.
- **No live model calls or deployment occurred during Module 2.** Every test, eval, and browser
  verification above used mocked/offline paths only. No GitHub repository was created, no
  remote was configured, nothing was pushed, and nothing was deployed.
