# Claim Dispute Review Copilot

An evidence-first AI prototype that helps a claims-operations associate investigate why a
member's claim was denied (or approved) — assembling structured facts, relevant policy
language, and knowledge-graph relationships into one evidence package, drafting a typed,
citation-required investigation brief, running it through a deterministic validator, and
routing anything it can't support to local human review.

> **This is an interview/portfolio prototype, not production software.** It is not affiliated
> with, endorsed by, or built from any non-public Humana systems or data, and it does not
> connect to any real payer system. All member, claim, benefit, provider, and policy data in
> this repository are entirely synthetic and fictional — see "Synthetic-data-only policy" below.

> **This project is a separate extension of the existing Claims Investigation Copilot.** It
> starts from the same evidence-first investigation pipeline (Modules 1–7, preserved unchanged)
> and adds a **Dispute Review** capability on top: a third "Dispute Review" tab, focused on
> `CLM-1001`. Dispute Review has three independent actions: a **deterministic comparison** (zero
> model calls, always available — `dispute_review/models.py`, `comparison.py`, `presets.py`), an
> **optional AI investigation** that drafts a cited dispute brief from retrieved evidence
> (`context/dispute_evidence_*.py`, `skills/investigate_dispute.py`,
> `application/dispute_workflow.py` + `dispute_generator.py` + `dispute_brief_validator.py`), and
> an **optional, separate scored judge** that evaluates that brief
> (`application/dispute_judge.py`). The full offline test suite passed 695/695 as of the latest
> full run, and the original 8/8 end-to-end and 9/9 safety evals both remain green on top of the
> extension — see [docs/DISPUTE_REVIEW_LOGIC.md](docs/DISPUTE_REVIEW_LOGIC.md),
> [docs/DISPUTE_EVIDENCE_RETRIEVAL.md](docs/DISPUTE_EVIDENCE_RETRIEVAL.md),
> [docs/DISPUTE_AI_WORKFLOW.md](docs/DISPUTE_AI_WORKFLOW.md),
> [docs/DISPUTE_REVIEW_UI.md](docs/DISPUTE_REVIEW_UI.md), and
> [docs/DISPUTE_AI_VALIDATION.md](docs/DISPUTE_AI_VALIDATION.md) (the complete, unedited
> validation history — original failing live runs, the repairs that followed, and the passing
> recheck below — all preserved) for the full record. **The deterministic comparison needs no
> model configuration.** An initial live smoke check found deterministic validation rejecting
> live-generated drafts (mostly validator false positives, not model-quality problems); those
> validator defects were fixed and a subsequent, independently-budgeted live recheck (3 fresh
> generation requests + 3 judge requests, the full authorized budget for that check) found **all
> 3 demo presets generated a validated brief and a completed judge evaluation**, scoring 5.0/5,
> 5.0/5, and 4.5/5 (all PASS). **These are three smoke-test results, not a production-reliability
> figure or a judge-calibration measurement** — see
> [docs/DISPUTE_AI_VALIDATION.md](docs/DISPUTE_AI_VALIDATION.md) for the complete history,
> including every originally-failing run, kept alongside the fixes. This validation is
> local-demo-only, not a production-readiness claim. Nothing here
> has been deployed, and the two new AI actions never connect to or modify the ORIGINAL AI
> investigation pipeline (Predefined Claims/Scenario Lab) — they are entirely separate pipelines
> with separate session-state and separate model calls.

---

## Hosted demo

[Claims Investigation Copilot](https://claims-investigation-copilot.streamlit.app)

This link is the **original** Claims Investigation Copilot app, not this project. This new
Claim Dispute Review Copilot extension has **not** been deployed anywhere — there is no hosted
instance of it yet.

Access to the original hosted app is **private/access-controlled** (the underlying GitHub
repository is private, and the hosted app is restricted to authorized viewers only) — it is not
a public link. This is a synthetic-data interview/portfolio prototype, not connected to any real
Humana system or data. **Predefined Claims** is the primary demo path; **Scenario Lab** is an
experimental, single-user testing surface (see "Known limitations" below) — it is not designed
for concurrent multi-user use on this shared hosted instance.

---

## What this is

**Target user:** a claims-operations associate investigating a specific member's claim
dispute — not a clinician, not an engineer — who needs a fast, well-supported answer they can
act on or relay to the member, with full traceability back to the underlying records.

**What it does not do:** it never approves, denies, reverses, or pays a claim or prior
authorization, and it never determines medical necessity. It is advisory-only: it drafts an
investigation brief for a human to review, nothing more. See "No autonomous claim
adjudication" below.

**Three tabs, in the Streamlit app (`app.py`):**

1. **Predefined Claims** (the primary demo path) — five curated synthetic cases
   (`CLM-1001`–`CLM-1005`, in `data/claims.json`) covering an auth-required denial, a
   successfully authorized claim, a not-covered service, an out-of-network denial, and a
   deliberately incomplete case that routes to human review.
2. **Scenario Lab** (an experimental testing surface) — lets an evaluator compose a
   *temporary, in-memory-only* synthetic claim and run it through the exact same pipeline, to
   probe edge cases the five curated cases don't cover. **This is a single-user prototype
   testing surface, not designed for concurrent multi-user execution** — see "Known
   limitations."
3. **Dispute Review** (new — this extension) — focused on `CLM-1001`, with three independent
   actions:
   - **Compare submitted information** (always available, zero model calls) — an analyst enters
     new, unverified authorization information (or loads one of three synthetic presets), and it
     is compared against the recorded claim on exactly four deterministic fields (member,
     service, validity dates, servicing provider), producing Match/Mismatch/Unknown results, a
     summary, and verification guidance. Pure, read-only, session-local — never mutates
     `data/*.json`, the shared in-memory claim/provider/authorization store, or the knowledge
     graph, and never calls the OpenAI adapter.
   - **Investigate dispute evidence** (optional, one model call, requires `OPENAI_API_KEY` +
     `LLM_MODEL`) — after a valid comparison, retrieves recorded facts, policy passages, and
     graph relationships (reusing the same structured/vector/graph tools the original pipeline
     uses), then drafts a cited, typed dispute brief through a bounded LangGraph workflow with an
     evidence gate and deterministic, non-LLM validation. This brief DOES incorporate the
     unverified submission — a completely separate artifact from the ORIGINAL AI investigation
     draft in the Predefined Claims tab, which still does not include it.
   - **Run AI semantic evaluation** (optional, one further separate model call, only available
     after a validated brief) — an experimental judge scores the brief on four dimensions
     (evidence grounding, coverage, uncertainty/provenance, authority boundaries) with an
     anchored 1–5 rubric, PASS/FAIL/UNCERTAIN verdicts, and a Python-computed overall
     score/verdict — advisory and uncalibrated, never an accuracy percentage.

   See [docs/DISPUTE_REVIEW_LOGIC.md](docs/DISPUTE_REVIEW_LOGIC.md) (comparison),
   [docs/DISPUTE_EVIDENCE_RETRIEVAL.md](docs/DISPUTE_EVIDENCE_RETRIEVAL.md) (retrieval + skill),
   [docs/DISPUTE_AI_WORKFLOW.md](docs/DISPUTE_AI_WORKFLOW.md) (workflow, generation, validation,
   judge), [docs/DISPUTE_REVIEW_UI.md](docs/DISPUTE_REVIEW_UI.md) (full UI flow for all three
   actions), [docs/DISPUTE_REVIEW_DEMO.md](docs/DISPUTE_REVIEW_DEMO.md) for a walkthrough demo
   script, and [docs/DISPUTE_AI_VALIDATION.md](docs/DISPUTE_AI_VALIDATION.md) for the final
   integrated validation record (offline readiness vs. live readiness, kept explicitly distinct).

---

## Quick start

```bash
py -3.14 -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS/Linux

copy .env.example .env                              # Windows
# cp .env.example .env                               # macOS/Linux
# then fill in OPENAI_API_KEY and LLM_MODEL in .env
```

Run the Streamlit app:

```bash
streamlit run app.py
```

Exact PowerShell command to launch this project's app locally, using its own project-local
`.venv` explicitly (no `.env`/API key required for the steps below to render):

```powershell
D:\AI\claude-code\claim-dispute-review-copilot\.venv\Scripts\python.exe -m streamlit run app.py --server.headless=true
```

**Dispute Review's deterministic comparison needs no `OPENAI_API_KEY`.** "Compare submitted
information" and every preset never import or call any LLM adapter at all — that part of the tab
works fully offline. **Dispute Review's two optional AI actions DO require configuration**,
exactly like the original pipeline's own AI actions: **Predefined Claims**' "Investigate Claim"
button, **Scenario Lab**'s "Run Investigation" button, the optional "Run AI Semantic Evaluation"
expander on those two tabs, **and** Dispute Review's own "Investigate dispute evidence" and "Run
AI semantic evaluation" buttons all require a properly configured `.env` (`OPENAI_API_KEY` +
`LLM_MODEL`) — clicking any of these five without one raises a clear, caught
`LLMConfigurationError` naming the missing variable(s) and shown in the UI without ever exposing
a credential, never a silent failure or a fabricated result. **Live output quality from the
three ORIGINAL-pipeline AI actions (Predefined Claims' "Investigate Claim," Scenario Lab's "Run
Investigation," and their shared "Run AI Semantic Evaluation") remains unverified against a real
model in this project** — only mocked responses have been tested for those, and this extension
never touches that pipeline. **Dispute Review's two AI actions have been live-tested across two
rounds** (see `docs/DISPUTE_AI_VALIDATION.md`, complete history preserved): the first round found
the configured model reachable and returning well-structured, correctly-cited,
correctly-provenance-labeled output, but every live-generated draft was rejected by deterministic
validation (mostly validator false positives); those validator defects were then fixed, and a
second, independently-budgeted live recheck found all 3 demo presets generating a validated brief
**and** a completed judge evaluation (scores 5.0/5, 5.0/5, 4.5/5, all PASS) — three smoke-test
results, not a reliability or calibration measurement.

Run the test suite and evaluations (all fully offline, no API key required):

```bash
pytest tests/ -q
python -m evals.retrieval_eval
python -m evals.skill_eval
python -m evals.agent_eval
python -m evals.hybrid_regression_eval
python -m evals.hybrid_retrieval_eval
python -m evals.graph_retrieval_eval
python -m evals.e2e_eval
python -m evals.e2e_safety_eval
```

An `OPENAI_API_KEY` and `LLM_MODEL` are needed only to click "Investigate Claim" /
"Run Investigation" / "Run AI Semantic Evaluation" in the running app — every automated test
and evaluation above uses a mocked adapter and makes zero live calls.

---

## Environment variables (`.env`)

| Variable | Used by | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | `application/llm_adapter.py`, `application/semantic_judge.py` | Generation and the optional semantic judge share one OpenAI Responses API configuration. |
| `LLM_MODEL` | same as above | The model used for both the brief generator and the judge. |
| `LLM_TIMEOUT_SECONDS` | same as above (default `30`) | Per-request timeout; a request that exceeds it raises a distinct `TIMEOUT` failure category rather than a generic provider error. |
| `RAG_EMBEDDING_PROVIDER` | `rag/embeddings.py: get_default_embedding_provider` only | **Does NOT control the main Claims Copilot evidence path.** The main pipeline (`context/hybrid_retriever.py`) hard-codes `provider_name="semantic"` and the `LARGE` chunking config for its one policy-retrieval call — this variable only affects a lower-level `rag/` helper not currently invoked by the investigation pipeline (useful if you're calling `rag.retriever.search_policy` or `rag.embeddings.get_default_embedding_provider` directly, e.g. from `python -m rag.retriever`). If you're evaluating retrieval quality standalone (`python -m evals.retrieval_eval`, which sweeps all four provider × chunking combinations explicitly) this has no effect either — that script passes `provider_name` explicitly per run. |

No other environment variable is read anywhere in this codebase. Secrets are never logged,
printed, or returned by any code path; `.env` is gitignored and only a placeholder
`.env.example` is versioned.

---

## Architecture

```
Question
   |
   v
Evidence            tools/ (deterministic lookups) + rag/ (policy retrieval)
   |                  + graph/ (knowledge-graph traversal)
   v
Evidence Sufficiency Gate      skills/investigate_claim.py: is_evidence_sufficient
   |
   |  insufficient -> zero-model-call NEEDS_REVIEW path (no LLM call, ever)
   v
Context Assembly       application/context_assembler.py (labeled, budgeted evidence)
   |
   v
LLM                    application/llm_adapter.py (ONE structured-output call)
   |
   v
Typed Brief             application/models.py: InvestigationBrief
   |
   v
Deterministic Validation      application/brief_validator.py (H2, non-LLM)
   |
   v
Human Review             application/review_packet.py, app.py's Accept/Mark actions
   |
   v
Optional Semantic Evaluation      application/semantic_judge.py (advisory only, manual)
```

Layering discipline (see `AGENTS.md` for the durable rules that enforce this):

- **Tool** = one deterministic operation (`tools/`).
- **Skill** = a reusable business capability composed from tools/context (`skills/`).
- **Agent** = orchestrates which skill runs next (`agents/`, a LangGraph `StateGraph`) — it
  never retrieves evidence or judges sufficiency itself.
- **Application layer** = downstream of the agent: context assembly, one LLM generation call,
  deterministic validation, human review, and the optional semantic judge (`application/`).

### Structured Synthetic Data and Deterministic Tools

`data/*.json` (members, plans, claims, benefits, prior authorizations, providers) simulate the
backend systems an associate would otherwise check by hand. `tools/*.py` exposes small, typed,
deterministic lookup functions over that data — same input always produces the same output, no
fuzzy matching, no inference about missing data. An empty prior-authorization list is never
treated as an assumed denial; a `None` benefit is never treated as "not covered." All records
are entirely synthetic — fictional member names, a fictional payer ("Meridian"), fictional
plans/networks/providers.

### Vector RAG (policy evidence retrieval)

`rag/` retrieves evidence only — it never generates an answer. Two embedding providers exist
side by side for comparison: `"tfidf"` (a lexical baseline — matches literal vocabulary
overlap, misses paraphrases) and `"semantic"` (a local `sentence-transformers/all-MiniLM-L6-v2`
model — captures paraphrase/synonym similarity, no API key). **The main investigation pipeline
always uses the semantic provider with the `LARGE` chunking config** (see the environment
variable table above); TF-IDF exists as an explicit comparison baseline, exercised directly by
`evals/retrieval_eval.py`'s four-way sweep, not as an interchangeable production setting.
Chroma (`rag/vector_store.py`) persists each (provider × chunking) combination as its own
isolated local collection under `rag/index/` (gitignored).

### Knowledge Graph

`graph/` builds an in-memory `NetworkX` graph (8 node types, 9 relation types — all structural
facts, e.g. `HAS_AUTHORIZATION`, never a business conclusion like "CAUSED_DENIAL") directly
from the validated `DataStore`. NetworkX is an explicit prototype choice — no server, no
persistence, no concurrent-access story — not a production graph-platform decision (see
`graph/builder.py`'s module docstring). `graph/retriever.py` returns a bounded-depth
neighborhood (default 2 hops) around a claim or member.

### Hybrid GraphRAG (evidence assembly)

`context/hybrid_retriever.py` selectively combines the three evidence sources above (tools =
authoritative structured facts, rag = unstructured policy evidence, graph = relationship
evidence) into one `EvidencePackage` per investigation — never a fourth kind of evidence, never
a final answer. `context/graph_filter.py` walks a fixed set of explicit patterns
(Claim→Member→Plan→Benefit→Service, Member→PriorAuthorization→Service, ...) rather than a
naive "keep this relation type" filter, to avoid leaking unrelated records through a shared hub
node (e.g. two claims for the same service).

### Agent Orchestration (LangGraph)

`agents/case_agent.py` compiles a `StateGraph` that runs a fixed, acyclic sequence — `validate
request → load case → assess case → build evidence → assess evidence →
{complete | needs_review | error | max_steps_exceeded}` — and exposes exactly one public entry
point, `run_case_agent(claim_id, query) -> AgentResult`. No LLM anywhere in `agents/` or
`skills/`: the output is an investigation trace and an `EvidencePackage`, never a generated
answer.

### Reusable Skills

`skills/` sits between Tools and the Agent: four typed business capabilities
(`investigate_claim`, `explain_benefit`, `check_prior_authorization`, `escalate_case`) behind
one shared `SkillResult` contract. `investigate_claim` owns the **evidence-sufficiency rule**
(member, plan, benefit, and servicing-provider records all present, plus at least one policy
chunk retrieved — deliberately not based on any specific policy section or golden-set
expectation). `escalate_case` assembles a synthetic escalation package only —
`evidence["external_system_contacted"]` is always `False`; no ticketing system is ever
contacted.

### Evidence sufficiency gate and the zero-model-call path

When `investigate_claim` judges evidence insufficient, the agent's terminal status becomes
`NEEDS_REVIEW` and `application/investigation_service.py`'s `run_investigation` short-circuits
to `GenerationStatus.NOT_ATTEMPTED` **before the LLM adapter is ever constructed** — zero model
calls, the original `EvidencePackage` preserved unchanged. The same short-circuit applies to an
unknown claim (`ERROR`) or an agent that exceeds its step budget (`MAX_STEPS_EXCEEDED`).

### Context assembler

`application/context_assembler.py` builds a labeled, budgeted `AssembledContext` from the
`EvidencePackage` the agent already produced — it never re-runs retrieval. Every reference id
is built deterministically from a real source field (never an invented id). Policy-excerpt text
is the only field with a length budget (6000 chars); structured facts, authorization
candidates, and missing-information labels are never truncated.

### Generation (OpenAI structured output)

`application/llm_adapter.py` makes exactly one model call via the OpenAI Responses API's
structured-output parsing (`text_format=InvestigationBrief`), constraining the model to a
closed Pydantic schema (summary, cited findings, missing/conflicting evidence, one advisory
next step). Every failure mode — missing configuration, provider/connection error, timeout, or
a response that fails schema validation — is caught and classified into a specific
`GenerationFailureCategory`, never left to raise an unhandled exception or silently produce a
fabricated brief.

### Deterministic validation (H2)

`application/brief_validator.py` is a **non-LLM, deterministic** second check over the
generated brief: every cited evidence reference must actually exist (Rule A), every finding
must cite at least one reference (Rule B), the suggested action must be one of a small advisory
allowlist (Rule C), and a narrow, explicitly-documented set of regex patterns rejects a
generated claim of consequential authority — an imperative, first-person, or self-referential
assertion like "Approve the claim." or "We approve the claim." — while correctly ignoring a
recorded historical fact like "The payer denied the claim." (Rule D). **This is not a
hallucination detector or a general semantic safety classifier** — it checks a small number of
objectively-checkable defects deterministically and cheaply; see the module's own docstring for
the exact, deliberately narrow boundary and documented remaining gaps.

### Local human review

`application/review_packet.py` and `app.py`'s "Accept Investigation Draft" / "Mark for Further
Review" actions record a **local, session-only** reviewer decision — never a claim,
authorization, or payment action, and never contacting any external system.

### Optional experimental semantic judge

`application/semantic_judge.py` is a strictly **advisory, opt-in** second model call, invoked
only by an explicit "Run AI Semantic Evaluation" button click on an already-generated,
already-validated brief. It scores four dimensions (factual grounding, completeness,
uncertainty preservation, authority boundary), each with a categorical PASS/FAIL/UNCERTAIN
verdict *and* a 1–5 rubric score, plus a deterministically-computed overall score (arithmetic
mean) and overall verdict. **No code path lets its result alter `AgentStatus`,
`GenerationStatus`, `ValidationStatus`, the brief, or any review decision.** It is not
calibrated against a human-rated dataset and must never be reported as a workflow gate or an
accuracy figure — see `docs/H6_SEMANTIC_JUDGE.md`.

### Guardrails catalog

`docs/GUARDRAILS.md` is a single-pane-of-glass **documentation** catalog of every guardrail
implemented across the layers above (16 total: the evidence-sufficiency gate, the zero-model-
call path, deterministic validation's four rules, generation/judge failure classification, the
three-way status separation, human review, stale-state protection, the semantic judge's own
advisory boundary, and Scenario Lab's data-isolation guarantees). It aggregates and describes;
it does not relocate or duplicate any enforcement logic. The Streamlit app's "Safety &
Guardrails" expander presents the same static catalog, read-only, with no toggles.

---

## Evaluation architecture

Three genuinely different kinds of evaluation, kept in separate files and never merged into one
score:

1. **Component evals** (`evals/retrieval_eval.py`, `evals/graph_retrieval_eval.py`,
   `evals/hybrid_regression_eval.py`, `evals/hybrid_retrieval_eval.py`, `evals/skill_eval.py`,
   `evals/agent_eval.py`) — each layer scored against its own independently-authored golden set
   (or, for the regression suite, against its own past output, to catch unexpected drift).
2. **Product-scenario evals** (`evals/e2e_eval.py`, E01–E08) — eight end-to-end product
   behaviors through the real, unmodified pipeline with a mocked adapter, each scenario scored
   as an explicit, individually-inspectable checklist.
3. **Safety/failure evals** (`evals/e2e_safety_eval.py`, S01–S09) — adversarial and failure-mode
   scenarios (prompt-injection text treated as data not instructions, generation/judge failure
   isolation, stale-state protection, ambiguous not-found-vs-failure signaling).

**Current honest result, not tuned for a perfect score:** `evals/hybrid_retrieval_eval.py`'s
independent quality golden set reports **Policy Section Recall = 63.33%** — retrieved policy
evidence is demonstrably not exhaustive. This is reported, not hidden, and no guardrail in this
system corrects for it.

---

## Known limitations

- **Retrieval is not exhaustive.** On the prototype's independent retrieval evaluation, Policy
  Section Recall was 63.33%. This demonstrates that retrieved policy evidence is not exhaustive
  and should not be treated as complete — it is not a measured probability that any single
  request will miss a relevant section.
- **Scenario Lab is single-user, not concurrency-safe.** Its temporary-data overlay mutates one
  shared, process-wide `DataStore`/graph singleton. A production or shared-hosted deployment
  should use request-scoped data/context instead of a process-wide mutable overlay — see
  `docs/SCENARIO_LAB.md`.
- **Rule D is a small set of targeted patterns, not a semantic classifier.** It correctly
  distinguishes an imperative/first-person authority claim from a third-party historical fact
  or a negated statement, but an intervening modal it doesn't recognize (e.g. "We must approve
  the claim.") is a documented, deliberate gap — see `application/brief_validator.py`.
- **The deterministic validator is not a hallucination detector.** It checks that a cited
  reference exists and that narrative text avoids specific authority-language shapes — it does
  not check whether the brief's prose is semantically supported by what it cites.
- **The semantic judge is uncalibrated.** Its scores and verdicts are evaluation signals from
  an LLM judge, not calibrated confidence or accuracy probabilities, and it has not been
  measured against a human-rated dataset.
- **No autonomous claim adjudication, anywhere in this system.** Nothing here approves, denies,
  reverses, pays, or modifies a claim or authorization, or determines medical necessity — this
  is enforced structurally (a closed advisory-action allowlist, authority-language validation,
  and a local-only human-review record), never claimed as a guarantee about the correctness of
  the underlying business decision itself.
- **No real Humana integration, of any kind.** This prototype has never connected to, read
  from, or been built from any non-public Humana system or data.
- **Dispute Review is single-claim and exact-match only.** It is focused on `CLM-1001` (no
  multi-claim selector), compares identifiers with exact string equality after trimming (no
  fuzzy matching, case folding, or alias resolution), has no persistence beyond the current
  browser session, and does not authenticate a submitted authorization reference against any
  authoritative source — "unverified" is accurate at every step, by design. See
  `docs/DISPUTE_REVIEW_VALIDATION.md` for the deterministic-comparison validation record.
- **Two live smoke checks (with repairs in between) found and then resolved two validator
  defects; a third live recheck confirms all three demo presets now complete end to end.**
  The original check found all 3 live-generated drafts rejected by deterministic
  validation, most for false positives rather than real content defects — see
  `docs/DISPUTE_AI_VALIDATION.md` for that original run, preserved unedited alongside the
  repairs that followed:
  - **Rule E (`unverified_provenance_preserved`) now uses word-boundary matching plus a
    small, same-clause negation-cue exemption** (e.g. "has not been verified" is no
    longer flagged, while an unhedged "is verified" still is) — still a narrow, documented
    keyword/cue check, not general negation understanding.
  - **Rule F (`prohibited_authority_language`) was not changed and remains a plain
    phrase-shape pattern match with no negation handling of any kind** — it reuses the
    exact same pattern set as the original investigation validator's Rule D described
    above, and shares that same, still-open limitation; unlike Rule E it has not been
    given a negation exemption.
  - A separate defect — the judge citing a missing-evidence category label (e.g.
    `prior_authorization`) as if it were a real reference id — was independently found and
    fixed in `prompts/dispute_judge_prompt.py`.
  A subsequent live recheck (3 fresh generation requests + 3 judge requests — the full
  authorized budget for that check, no retries) found **all 3 briefs passed deterministic
  validation and all 3 judge calls completed**, scoring 5.0/5, 5.0/5, and 4.5/5 (all
  PASS), with citations, comparison descriptions, and provenance labeling all inspected
  and found correct. **These are three smoke-test results — they establish that the
  repairs work on live output, not a production reliability rate or a judge-calibration
  measurement**; a clean run today does not guarantee every future run behaves
  identically. See `docs/DISPUTE_AI_VALIDATION.md` for the complete, unedited history of
  every check performed.

---

## Repository layout

```
agents/        LangGraph orchestration (validate -> load -> assess -> build -> assess -> route)
               -- ORIGINAL pipeline only; no LLM call anywhere in this package, ever
skills/        Reusable business capabilities (investigate_claim, explain_benefit,
               investigate_dispute, ...)
tools/         Deterministic lookups over data/*.json
context/       Hybrid GraphRAG evidence assembly for BOTH pipelines: hybrid_retriever.py
               (original, rag+graph+tools -> EvidencePackage) and dispute_evidence_retriever.py
               + dispute_evidence_models.py (new, for investigate_dispute)
rag/           Vector RAG: chunking, embeddings (tfidf/semantic), Chroma vector store
graph/         In-memory NetworkX knowledge graph + bounded-depth retriever (claim/member/
               provider neighborhoods)
application/   Context assembler, generation adapter, deterministic validator, human review,
               Scenario Lab, semantic judge (ORIGINAL pipeline) + dispute_workflow.py (bounded
               LangGraph workflow), dispute_generator.py, dispute_brief_validator.py,
               dispute_judge.py, dispute_context.py, dispute_models.py, dispute_judge_models.py
               (NEW dispute-review AI pipeline -- a wholly separate set of status/result types,
               never reused from the original pipeline's) -- everything downstream of the agent
prompts/       Versioned system/user prompts for the original generation/judge AND the new
               dispute_brief_prompt.py / dispute_judge_prompt.py
dispute_review/ Pure comparison models/logic/presets (models.py, comparison.py, presets.py,
               zero LLM) + the Dispute Review tab's Streamlit UI (ui.py, the ONLY file here that
               imports Streamlit -- also the only integration point calling into the new AI
               pipeline in application/); see docs/DISPUTE_REVIEW_LOGIC.md,
               DISPUTE_EVIDENCE_RETRIEVAL.md, DISPUTE_AI_WORKFLOW.md, and DISPUTE_REVIEW_UI.md
data/          Synthetic structured data (members, plans, claims, benefits, auths, providers)
documents/     Synthetic policy documents (imaging, prior auth, denial reasons, benefits, ...)
evals/         Component, product-scenario (E01-E08), and safety (S01-S09) evaluation harnesses
tests/         pytest suite -- fully offline, no live API call anywhere
docs/          Architecture, build history, guardrail catalog, known limitations, runbook,
               Dispute Review logic/UI/validation/demo docs
guardrails/    Reserved package scaffold -- see docs/GUARDRAILS.md for where enforcement
               actually lives (it is deliberately NOT centralized in this package)
observability/ Reserved scaffold -- no production tracing/monitoring implemented
governance/    Reserved scaffold -- no model/system-card artifacts implemented yet
app.py         Streamlit UI: Predefined Claims + Scenario Lab + Dispute Review tabs, human
               review, guardrails
```

---

## Synthetic-data-only policy

This project must **never** contain real PHI, real member/patient data, real claims data, or
any proprietary Humana data or systems information. All member, claim, benefit, and provider
data used anywhere in this project — now or in any later stage — must be fabricated/synthetic
and clearly recognizable as such (obviously fake names, member IDs, provider IDs, and a
fictional payer name). This applies to code, fixtures, documentation, and any example output.
Enforced by `tests/test_synthetic_data_hygiene.py` and `tests/test_policy_document_hygiene.py`.
