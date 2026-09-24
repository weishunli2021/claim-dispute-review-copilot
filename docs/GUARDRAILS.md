# Guardrails Catalog — Claims Investigation Copilot

**This is a documentation artifact only.** It aggregates and describes guardrails that already
exist, each owned and enforced by its existing layer (H0–H8, all frozen). It introduces **no new
enforcement, no new runtime behavior, and no duplicated logic** — every row below links to the
one file that actually implements that rule. If this document and the code ever disagree, the
code is correct; update this document to match it, never the other way around.

---

## Architecture: where each guardrail sits in the pipeline

```
Question
   |
   v
Evidence                      (tools/, rag/, graph/, context/ — deterministic retrieval)
   |
   v
Evidence Sufficiency Gate     (skills/investigate_claim.py — GUARDRAIL 1)
   |
   |  only if sufficient — otherwise: zero-model-call NEEDS_REVIEW path (GUARDRAIL 2)
   v
Context Assembly              (application/context_assembler.py — GUARDRAIL 3, bounded/budgeted)
   |
   v
LLM                           (application/llm_adapter.py — ONE call, structured output)
   |
   v
Typed Brief                   (application/models.py:InvestigationBrief — GUARDRAIL 4)
   |
   v
Deterministic Validation      (application/brief_validator.py — GUARDRAILS 5, 6, 7, 8)
   |
   v
Human Review                  (application/review_packet.py, app.py — GUARDRAIL 11)
   |
   v
Optional Semantic Evaluation  (application/semantic_judge.py — GUARDRAILS 13, 14; advisory only)
```

Cutting across every stage above: failure handling (GUARDRAIL 9), the three-way status
separation that keeps this diagram legible (GUARDRAIL 10), stale-state protection in the UI
(GUARDRAIL 12), and — for the Scenario Lab testing surface specifically — temporary-data
isolation and baseline-data non-mutation (GUARDRAILS 15, 16).

---

## Quick-reference table

| # | Guardrail | Stage | Type | Blocking? |
|---|---|---|---|---|
| 1 | Evidence sufficiency gate | Evidence → Gate | Deterministic | Blocking (routes to NEEDS_REVIEW) |
| 2 | Zero-model-call path for insufficient evidence | Gate → LLM | Deterministic | Blocking (prevents LLM call) |
| 3 | Bounded/budgeted context assembly | Context Assembly | Deterministic | Blocking (truncates, never fabricates) |
| 4 | Structured `InvestigationBrief` output | LLM → Typed Brief | Deterministic (schema) | Blocking (parse fails if non-conforming) |
| 5 | Evidence-reference existence validation | Deterministic Validation | Deterministic | Blocking (`ValidationStatus.FAILED`) |
| 6 | Finding evidence-reference requirement | Deterministic Validation | Deterministic | Blocking |
| 7 | Advisory action allowlist | Deterministic Validation | Deterministic | Blocking |
| 8 | Prohibited authority-language validation | Deterministic Validation | Deterministic | Blocking |
| 9 | Configuration/provider/timeout/parsing failure handling | LLM call boundary | Deterministic (exception mapping) | Blocking (surfaces as FAILED, never crashes) |
| 10 | Separation of EvidenceStatus / GenerationStatus / ValidationStatus | Cross-cutting | Deterministic (type system) | Structural (prevents status conflation) |
| 11 | Human review controls | Human Review | Human | Advisory (record-only, no system action) |
| 12 | Stale-state protection | UI (cross-cutting) | Deterministic | Blocking (clears stale result on state change) |
| 13 | Optional H6/H8 semantic judge | Optional Semantic Evaluation | LLM-based | **Advisory only** — never blocking |
| 14 | Semantic-judge score/verdict coherence validation | Optional Semantic Evaluation | Deterministic | Blocking *within the judge only* (never affects the brief) |
| 15 | Scenario Lab temporary-data isolation | Scenario Lab (H7) | Deterministic | Structural (in-memory only, torn down) |
| 16 | Baseline-data non-mutation | Scenario Lab (H7) | Deterministic + tested | Structural (verified byte-identical) |

---

## 1. Evidence sufficiency gate

- **Pipeline stage:** Evidence → Evidence Sufficiency Gate
- **Type:** Deterministic
- **Risk addressed:** Prevents the LLM from generating an investigation brief when the
  underlying evidence is incomplete (missing member, plan, benefit, or servicing-provider
  record, or zero retrieved policy chunks).
- **Implementation owner:** [`skills/investigate_claim.py`](../skills/investigate_claim.py) —
  `is_evidence_sufficient()`, called from `investigate_claim()`.
- **Trigger:** Every investigation run, immediately after evidence assembly, before any
  generation is attempted.
- **Failure/result behavior:** Returns `SkillStatus.INSUFFICIENT_EVIDENCE` (mapped to
  `AgentStatus.NEEDS_REVIEW` by the agent), with `next_capability="escalate_case"` and the
  specific missing categories listed.
- **Blocking or advisory:** **Blocking** — routes the case to human review instead of
  generation.

## 2. Zero-model-call path for insufficient evidence

- **Pipeline stage:** Gate → LLM boundary
- **Type:** Deterministic
- **Risk addressed:** Guarantees no LLM call — and therefore no fabricated brief — is ever made
  when evidence was already judged insufficient, and equally for an unknown claim (`ERROR`) or
  an agent that exceeded its step budget (`MAX_STEPS_EXCEEDED`).
- **Implementation owner:**
  [`application/investigation_service.py`](../application/investigation_service.py) —
  `run_investigation()`'s `if agent_result.status != AgentStatus.EVIDENCE_SUFFICIENT:`
  short-circuit.
- **Trigger:** Any terminal `AgentStatus` other than `EVIDENCE_SUFFICIENT`.
- **Failure/result behavior:** Returns `GenerationStatus.NOT_ATTEMPTED` with zero adapter calls;
  the original `AgentResult`/`EvidencePackage` (when one exists) is preserved unchanged.
  Regression-tested: `tests/test_application_investigation_service.py:
  test_run_investigation_executes_evidence_workflow_exactly_once` and the E2E safety suite's
  zero-model-call checks (S-series).
- **Blocking or advisory:** **Blocking** — structurally prevents the LLM call.

## 3. Bounded / budgeted context assembly

- **Pipeline stage:** Context Assembly
- **Type:** Deterministic
- **Risk addressed:** Prevents unbounded policy-excerpt text from silently growing the prompt
  without limit, and ensures every fact shown to the model traces back to a real source field
  (never a fabricated id).
- **Implementation owner:**
  [`application/context_assembler.py`](../application/context_assembler.py) —
  `MAX_POLICY_CONTEXT_CHARS` (6000-char budget applied only to policy-excerpt text; structured
  facts, authorization candidates, and missing-information labels are never truncated) and its
  `REF_ID_*` deterministic reference-id construction.
- **Trigger:** Every call to `assemble_context()`, once per investigation with
  `EVIDENCE_SUFFICIENT` status.
- **Failure/result behavior:** Truncates policy excerpts beyond the budget (mechanism exists and
  is tested; does not fire at current defaults per the module's own docstring); never fabricates
  a reference id.
- **Blocking or advisory:** **Blocking** in the sense that truncation is unconditional past the
  budget — but it degrades content, it does not halt the pipeline.

## 4. Structured `InvestigationBrief` output

- **Pipeline stage:** LLM → Typed Brief
- **Type:** Deterministic (schema enforcement)
- **Risk addressed:** Prevents free-form, unparseable, or partially-formed model output from
  ever reaching downstream code — the model is constrained to a closed Pydantic schema via the
  OpenAI Responses API's structured-output parsing.
- **Implementation owner:** [`application/models.py`](../application/models.py) —
  `InvestigationBrief` (and its nested `Finding`, `SuggestedNextStep`, `ActionCode`), consumed by
  [`application/llm_adapter.py`](../application/llm_adapter.py) —
  `OpenAIInvestigationBriefAdapter.generate()`'s `text_format=InvestigationBrief`.
- **Trigger:** Every generation call.
- **Failure/result behavior:** A non-conforming model response either fails to parse (raises
  `LLMOutputError`, see Guardrail 9) or is coerced into the schema — it can never partially
  bypass the type contract.
- **Blocking or advisory:** **Blocking** — a schema violation cannot produce a usable brief.

## 5. Evidence-reference existence validation

- **Pipeline stage:** Deterministic Validation (H2, Rule A)
- **Type:** Deterministic
- **Risk addressed:** Catches a brief citing an evidence reference id that was never actually
  supplied to the model — a specific, mechanically-checkable form of fabrication.
- **Implementation owner:**
  [`application/brief_validator.py`](../application/brief_validator.py) —
  `validate_investigation_brief()`, Rule A (`rule="evidence_reference_existence"`). Exact-string
  match only against `AssembledContext.references` — the same context object actually rendered
  into the prompt.
- **Trigger:** Every `GenerationStatus.DRAFTED` brief, before it is ever shown to a user.
- **Failure/result behavior:** Adds a `ValidationIssue` and sets `ValidationStatus.FAILED`.
- **Blocking or advisory:** **Blocking** — a `FAILED` brief is never presented as an accepted
  investigation result (see `application/workbench.py: is_accepted_draft`).

## 6. Finding evidence-reference requirement

- **Pipeline stage:** Deterministic Validation (H2, Rule B)
- **Type:** Deterministic
- **Risk addressed:** Prevents an uncited assertion — a finding presented with no traceable
  source at all, even a valid one.
- **Implementation owner:**
  [`application/brief_validator.py`](../application/brief_validator.py) — Rule B
  (`rule="finding_requires_evidence_ref"`).
- **Trigger:** Every `GenerationStatus.DRAFTED` brief.
- **Failure/result behavior:** Adds a `ValidationIssue` and sets `ValidationStatus.FAILED` if any
  `Finding.evidence_refs` is empty.
- **Blocking or advisory:** **Blocking**, same mechanism as Guardrail 5.

## 7. Advisory action allowlist

- **Pipeline stage:** Deterministic Validation (H2, Rule C)
- **Type:** Deterministic
- **Risk addressed:** Ensures the brief's suggested next step is always one of a small, closed
  set of non-executable, advisory actions — never an adjudicative or executable action.
- **Implementation owner:**
  [`application/brief_validator.py`](../application/brief_validator.py) —
  `ALLOWED_ACTION_CODES` (`EXPLAIN_RECORDED_STATUS`, `VERIFY_AUTHORIZATION_INFORMATION`,
  `REQUEST_INFORMATION`, `HUMAN_REVIEW`), Rule C (`rule="advisory_action_allowlist"`). Enforced
  twice: once structurally by `ActionCode`'s closed Pydantic enum (a prohibited action name
  cannot even survive structured-output parsing), and again explicitly here so the prohibition
  stays legible and independently verifiable.
- **Trigger:** Every `GenerationStatus.DRAFTED` brief.
- **Failure/result behavior:** Adds a `ValidationIssue` and sets `ValidationStatus.FAILED` if
  `suggested_next_step.action_code` falls outside the allowlist.
- **Blocking or advisory:** **Blocking**.

## 8. Prohibited authority-language validation

- **Pipeline stage:** Deterministic Validation (H2, Rule D)
- **Type:** Deterministic (regex pattern match, not a keyword blacklist)
- **Risk addressed:** Catches the brief asserting that *it itself* exercises consequential
  authority — approving, denying, reversing, or paying a claim; approving or denying a prior
  authorization; or determining medical necessity.
- **Implementation owner:**
  [`application/brief_validator.py`](../application/brief_validator.py) —
  `PROHIBITED_AUTHORITY_PATTERNS` / `_find_prohibited_authority_phrase()`, Rule D
  (`rule="prohibited_authority_language"`). A prohibited verb+object (e.g. "approve the claim")
  is flagged only when it is imperative (sentence-initial: "Approve the claim.") or DIRECTLY
  governed, with no intervening word, by a small closed set of authority-claiming subjects
  ("we", "this system", "recommend", ...: "We approve the claim.", "This system approves the
  claim."). That strict adjacency requirement is what lets a negation or modal in between fall
  through un-flagged without a separate negation blacklist ("This system does not approve the
  claim.", "The copilot cannot approve the claim.") and what correctly excludes a third-party
  historical fact ("The payer denied the claim." — "payer" is not a recognized
  authority-claiming subject) or a passive-voice historical fact ("The claim was denied.") from
  ever being flagged, despite the same words appearing as a literal substring. Two more shapes
  are covered explicitly: a passive modal recommendation ("The claim should be approved.") and
  a bare object with no determiner ("Approve claim CLM-1001."). Post-audit remediation
  (revised from an earlier, less precise verb-then-object-only version that both missed these
  two shapes and false-flagged third-party/negated statements) — see the module's own docstring
  and `tests/test_application_brief_validator.py` for the exact boundary and documented
  remaining gaps (e.g. an intervening modal like "We must approve the claim." is not caught).
- **Trigger:** Scans every narrative field of every `GenerationStatus.DRAFTED` brief (summary,
  each finding's statement, each missing/conflicting-evidence entry, the suggested next step's
  rationale).
- **Failure/result behavior:** Adds a `ValidationIssue` and sets `ValidationStatus.FAILED`.
- **Blocking or advisory:** **Blocking**.

## 9. Configuration/provider/timeout/parsing failure handling

- **Pipeline stage:** LLM call boundary (both generation and, separately, the optional judge)
- **Type:** Deterministic (exception-to-status mapping)
- **Risk addressed:** Guarantees a live-call failure (bad/missing API key, network/provider
  error, timeout, or an unparseable model response) surfaces as clean, typed application data —
  never an unhandled exception, and never a silently-fabricated fallback brief.
- **Implementation owner:**
  [`application/llm_adapter.py`](../application/llm_adapter.py) (`LLMConfigurationError`,
  `LLMTimeoutError`, `LLMProviderError`, `LLMOutputError`) mapped in
  [`application/investigation_service.py`](../application/investigation_service.py)'s
  `run_investigation()` to `GenerationFailureCategory.{CONFIGURATION, TIMEOUT, PROVIDER,
  STRUCTURED_PARSING}`. `LLMOutputError` covers both a refused/incomplete model response and
  (post-audit remediation) a `pydantic.ValidationError` raised while the SDK constructs
  `InvestigationBrief` from the model's JSON -- the adapter explicitly catches and reclassifies
  it rather than letting it escape as a raw, unhandled exception. The judge sidecar mirrors
  this exact pattern independently in
  [`application/semantic_judge.py`](../application/semantic_judge.py) with its own
  `JudgeFailureCategory` (never sharing an enum with generation's) and its own equivalent
  `pydantic.ValidationError` catch (for a per-dimension score/verdict coherence failure -- see
  Guardrail 14).
- **Trigger:** Any exception the adapter raises during its one model call.
- **Failure/result behavior:** `GenerationStatus.FAILED` (or, for the judge, `JudgeStatus.FAILED`)
  with a specific category and the exception message — `max_retries=0`, so no silent retry
  storm either.
- **Blocking or advisory:** **Blocking** for generation (no brief is produced); for the judge,
  a failure is isolated and leaves the already-produced brief completely unaffected (see
  Guardrail 13).

## 10. Separation of EvidenceStatus / GenerationStatus / ValidationStatus

- **Pipeline stage:** Cross-cutting (the type contract every other guardrail is expressed in)
- **Type:** Deterministic (type system / architectural contract)
- **Risk addressed:** Prevents three genuinely different concerns — "was there enough evidence,"
  "did the model produce a brief," and "did the brief pass deterministic checks" — from being
  collapsed into one ambiguous status, which would make it impossible to reason precisely about
  where in the pipeline a given case stopped.
- **Implementation owner:** `AgentStatus` in
  [`agents/state.py`](../agents/state.py) (owned by the agent layer, never re-derived
  downstream), `GenerationStatus` / `GenerationFailureCategory` and `ValidationStatus` in
  [`application/models.py`](../application/models.py).
- **Trigger:** Structural — every `ApplicationResult` carries all three, always.
- **Failure/result behavior:** N/A (this is the contract other guardrails report through, not a
  check with its own pass/fail).
- **Blocking or advisory:** **Structural** — makes the other guardrails' outcomes legible and
  independently inspectable rather than blocking anything itself.

## 11. Human review controls

- **Pipeline stage:** Human Review
- **Type:** Human
- **Risk addressed:** Ensures a NEEDS_REVIEW case, a generation failure, or a validation failure
  is routed to an explicit, local, human-in-the-loop record rather than silently dropped or
  auto-escalated to a real external system.
- **Implementation owner:**
  [`application/review_packet.py`](../application/review_packet.py) —
  `build_needs_review_packet()` (reuses `skills.escalate_case` with evidence already gathered,
  `external_system_contacted=False`); [`application/workbench.py`](../application/workbench.py) —
  `record_review_decision()`; rendered in [`app.py`](../app.py) —
  `_render_human_review_actions()` ("Accept Investigation Draft" / "Mark for Further Review").
- **Trigger:** `AgentStatus.NEEDS_REVIEW`, `GenerationStatus.FAILED`, or
  `ValidationStatus.FAILED` — plus, for an accepted draft, an optional accept/mark decision.
- **Failure/result behavior:** Records a local, session-only decision (`claim_id`, `decision`,
  `recorded_at`). No external ticketing/case-management/communication system is ever contacted.
- **Blocking or advisory:** **Advisory** — a record-keeping action, never itself a claim,
  authorization, or payment action (per `AGENTS.md` rule 6).

## 12. Stale-state protection

- **Pipeline stage:** UI (cross-cutting, Predefined Claims and Scenario Lab alike)
- **Type:** Deterministic
- **Risk addressed:** Prevents a result, review decision, or judge verdict from a *different*
  case, question, or scenario from remaining visible after the user changes what they're
  investigating — a stale result displayed as if current would be actively misleading.
- **Implementation owner:**
  [`application/workbench.py`](../application/workbench.py) — `reset_investigation_state()`
  (clears `investigation_result`/`review_decision`/`judge_result`), called from
  `on_case_selected()` and `on_question_changed()`;
  [`application/scenario_lab.py`](../application/scenario_lab.py) —
  `reset_scenario_investigation_state()` / `apply_template_change()` / `reset_scenario_lab()`,
  using entirely separate, non-overlapping session-state keys from Predefined Claims.
- **Trigger:** Selecting a different case, editing the scoped question, changing any Scenario
  Lab field, switching templates, or clicking "Reset Scenario."
- **Failure/result behavior:** Clears the relevant session-state keys; the UI's own
  `result.claim_id == selected_claim_id` / `envelope.run_id != result.run_id` guards provide a
  second layer of staleness detection at render time.
- **Blocking or advisory:** **Blocking** in the sense that a stale result cannot remain rendered
  — regression-tested extensively (H3, H6, H7 test suites).

## 13. Optional H6/H8 semantic judge

- **Pipeline stage:** Optional Semantic Evaluation (strictly downstream of everything else)
- **Type:** LLM-based
- **Risk addressed:** Offers a second, independent, advisory opinion on grounding, completeness,
  uncertainty preservation, and authority boundary — able to flag something the narrower,
  mechanical Guardrails 5–8 don't catch (e.g. a technically well-cited but substantively
  misleading statement) — while never being trusted as an enforcement layer itself.
- **Implementation owner:**
  [`application/semantic_judge.py`](../application/semantic_judge.py) — `run_semantic_judge()`,
  invoked only from [`app.py`](../app.py) — `_render_semantic_judge_section()`.
- **Trigger:** Manual only — an explicit "Run AI Semantic Evaluation" button click, only ever
  shown when `application.workbench.is_accepted_draft(result)` is `True`. Never automatic, never
  part of `run_investigation()`.
- **Failure/result behavior:** Returns a `SemanticJudgeEnvelope` with a 1-5 rubric score, a
  PASS/FAIL/UNCERTAIN verdict, and a rationale per dimension (see
  [docs/H6_SEMANTIC_JUDGE.md](H6_SEMANTIC_JUDGE.md)). **No code path exists that lets this
  result alter `AgentStatus`, `GenerationStatus`, `ValidationStatus`, the `InvestigationBrief`,
  or any review-decision state** — a judge failure leaves the already-produced brief completely
  untouched.
- **Blocking or advisory:** **Advisory only.** This is the one guardrail in this catalog that
  is explicitly, deliberately never blocking.

## 14. Semantic-judge score/verdict coherence validation

- **Pipeline stage:** Optional Semantic Evaluation, internal to Guardrail 13
- **Type:** Deterministic
- **Risk addressed:** Prevents the judge itself from returning a self-contradictory result (a
  score of 4-5 paired with verdict FAIL, or 1-2 paired with PASS), which would undermine trust
  in the judge's own output.
- **Implementation owner:**
  [`application/judge_models.py`](../application/judge_models.py) — `DimensionResult`'s
  Pydantic `model_validator` (`_check_score_verdict_coherence`); a violation is caught in
  [`application/semantic_judge.py`](../application/semantic_judge.py) —
  `OpenAISemanticJudgeAdapter.evaluate()` and mapped to
  `JudgeFailureCategory.STRUCTURED_PARSING`. `overall_result`/`overall_score` are themselves
  computed deterministically from the four dimensions (`SemanticJudgeResult.from_dimensions`),
  never invented by the model — the model is constrained to a schema
  (`RawJudgeDimensions`) that has no `overall_result`/`overall_score` field at all.
- **Trigger:** Every judge invocation, at the moment the model's structured JSON is parsed into
  typed objects.
- **Failure/result behavior:** An incoherent live response becomes `JudgeStatus.FAILED` /
  `JudgeFailureCategory.STRUCTURED_PARSING` — never a silently-accepted, self-contradictory
  result.
- **Blocking or advisory:** **Blocking within the judge's own output only** — it can cause the
  judge call to report FAILED, but per Guardrail 13, that never touches the investigation brief
  itself.

## 15. Scenario Lab temporary-data isolation

- **Pipeline stage:** Scenario Lab (H7), evidence-assembly boundary
- **Type:** Deterministic
- **Risk addressed:** Lets an evaluator run a synthetic, temporary claim through the real
  pipeline without a second investigation engine and without any risk of that temporary data
  leaking into a concurrent or later Predefined Claims lookup -- including when a scenario
  deliberately reuses a baseline `(plan_id, service_code)` benefit key or a baseline
  `(member_id, service_code)` authorization group, which an earlier version of this overlay
  would have permanently deleted rather than restored (post-audit remediation; see
  `docs/SCENARIO_LAB.md`'s "Scoped-replacement semantics" section).
- **Implementation owner:**
  [`application/scenario_lab.py`](../application/scenario_lab.py) —
  `_temporary_data_overlay()` (mutates the shared `tools.data_store.get_data_store()` singleton
  and clears `graph.retriever._get_graph()`'s cache before yielding to `run_investigation()`,
  then restores every touched key to its EXACT pre-scenario state -- removing a record it
  added where nothing existed before, or restoring the original baseline object where one did
  -- and clears the graph cache again in a `finally` block, so the restoration survives an
  exception raised mid-investigation too).
- **Trigger:** Every "Run Investigation" click in the Scenario Lab tab.
- **Failure/result behavior:** The scenario claim is fully materialized in the `ApplicationResult`
  it returns, then is gone from the `DataStore`/graph the instant the call returns -- verified by
  `tests/test_scenario_lab.py::test_m_scenario_claim_not_accessible_after_run_completes`,
  `test_m_scenario_run_does_not_disturb_predefined_claim_lookup`, and its "P" test group
  (collision restoration, `benefit_available=False` hiding, existing-member authorization
  replacement, exception-safety, and a full in-memory `DataStore` snapshot comparison).
- **Blocking or advisory:** **Structural** — the isolation is unconditional, not a check that
  can pass or fail.

## 16. Baseline-data non-mutation

- **Pipeline stage:** Scenario Lab (H7), data boundary
- **Type:** Deterministic, verified by tests
- **Risk addressed:** Guarantees `data/*.json` (the synthetic source-of-truth files) can never
  be modified by Scenario Lab activity, regardless of what a user constructs and runs.
- **Implementation owner:** Structural — no code path in
  [`application/scenario_lab.py`](../application/scenario_lab.py) ever opens `data/*.json` for
  writing; the overlay operates entirely on the in-memory `DataStore` object. Verified by
  [`tests/test_scenario_lab.py::test_c_scenario_investigation_does_not_modify_source_data`](../tests/test_scenario_lab.py)
  and
  [`tests/test_app_scenario_lab_ui.py::test_scenario_lab_does_not_modify_source_data_files`](../tests/test_app_scenario_lab_ui.py)
  (SHA-256 hash of all 6 data files, before and after running the full Scenario Lab suite —
  byte-for-byte identical).
- **Trigger:** N/A — this is an absence-of-a-code-path guarantee, not a runtime check.
- **Failure/result behavior:** N/A.
- **Blocking or advisory:** **Structural**, continuously regression-tested.

---

## Single pane of glass, not single source of enforcement

This document is a **catalog**, not a **gate**. Every guardrail listed above remains owned and
enforced exactly where it already lived before this document existed:

- Evidence sufficiency stays in `skills/investigate_claim.py` — never re-derived in `agents/`,
  `application/`, or `app.py`.
- Deterministic brief validation stays in `application/brief_validator.py` — never duplicated in
  the semantic judge, Scenario Lab, or the UI.
- The semantic judge's advisory scoring stays in `application/semantic_judge.py` /
  `application/judge_models.py` — it does not gain, and must never gain, any enforcement power.
- Scenario Lab's data isolation stays in `application/scenario_lab.py` — the UI only calls it,
  never reimplements the overlay/teardown logic.

Aggregating these into one readable document makes the *system's* guardrail posture legible for
an interview or demo audience without moving, copying, or re-implementing a single rule. If a
guardrail's behavior ever needs to change, that change happens in exactly the one file this
document points to — never here, and never in a second, competing implementation.

---

## Explicit limitations (read before presenting any guardrail as a guarantee)

- **The semantic judge is advisory only.** It cannot change `AgentStatus`, `GenerationStatus`,
  `ValidationStatus`, the `InvestigationBrief`, or any review-decision state — by construction,
  since no code path exists that would let it (Guardrail 13).
- **The semantic judge is not calibrated as a production quality gate.** Its 1-5 scores and
  PASS/FAIL/UNCERTAIN verdicts are evaluation signals from an uncalibrated LLM judge, not
  calibrated confidence or accuracy probabilities — see
  [docs/H6_SEMANTIC_JUDGE.md](H6_SEMANTIC_JUDGE.md)'s "Calibration limitation."
- **The deterministic validator (`application/brief_validator.py`) is not a universal
  hallucination detector.** It checks whether a cited reference id *exists* and whether narrative
  text *matches* a small set of authority-language patterns — it does not check whether the
  brief's prose is semantically *supported* by the evidence it cites (see the module's own
  docstring, "THIS IS NOT" section).
- **Policy Section Recall remains 63.33%** (measured in `evals/hybrid_retrieval_eval.py`,
  unchanged across H0–H8) — retrieved policy evidence is demonstrably **not exhaustive**; a
  missing policy section is a known, measured retrieval-quality limitation, not something any
  guardrail in this catalog corrects.
- **No autonomous claim adjudication.** Nothing in this system approves, denies, reverses, pays,
  or modifies a claim or authorization, or determines medical necessity — this is enforced
  structurally (a closed advisory-action allowlist, prohibited-authority-language validation,
  and a local-only human-review record), never claimed as a guarantee about the correctness of
  any underlying business decision.
- **No guardrail in this catalog proves a claim denial was correct or incorrect.** Every
  guardrail here checks the *investigation artifact* (is it grounded, cited, advisory, complete
  relative to available evidence) — none of them, individually or together, adjudicate whether
  the original claims decision itself was right.
