# The Billing-Correction Dispute Review Scenario

**Status: this is the current, active Dispute Review scenario**, replacing all earlier
authorization-dispute scenarios (Matching fields / Different servicing provider / Incomplete
submission / Provider corrected after denial / Approved authorization — service date outside
authorized period). Those are retired; see the `docs/DISPUTE_*`, `docs/V2_DISPUTE_*`, and
`docs/V2_AUTHORIZATION_EVIDENCE.md` files (each now marked historical at the top) for how they
worked, and `dispute_review/authorization_source_lookup.py` /
`dispute_review/authorization_source_models.py` / `dispute_review/scenario_synthetic_claim_decision_events.py`
for the reusable, independently-tested modules kept from that scenario but no longer wired into
the active pipeline.

This document is the single reference for the scenario: the story, the exact seeded data, the
expected findings, the five-section screen layout, and what has and has not been verified.

---

## 1. Scenario story

A provider's billing office submitted a claim for a knee procedure with four billing errors: the
wrong service code, the wrong modifier (laterality), the wrong number of units, and the wrong
servicing-provider identifier. The payer's claims system denied the claim, but its recorded
decision only caught two of the four errors (the service code and the modifier) by comparing the
claim to the operative note on file at the time — it never separately checked units or the
servicing provider.

The billing office later reviews its own documentation, realizes all four fields were wrong, and
submits a proposed correction. This copilot's job is to determine, deterministically, whether each
proposed correction is actually supported by independently recorded service documentation — never
to submit the corrected claim, edit the original claim, reverse the denial, or approve payment. That
remains an analyst's job downstream of this tool.

Every identifier, code, modifier, and provider name in this scenario is **entirely fictional** —
none of it corresponds to a real CPT/HCPCS code, a real provider, or actual Humana policy.

---

## 2. The original claim and recorded decision (seeded exactly, never invented at render time)

Fixture: `data/billing_correction_claim.json`, loaded by `dispute_review/billing_fixtures.py`.
Isolated from `data/claims.json` and the Golden Dataset's `CLM-1001`–`CLM-1005` — never read via
`tools.data_store`/`tools.case_context`.

| Field | Value |
|---|---|
| Claim ID | `CLM-BILL-9001` |
| Claim type | Original submission |
| Submission date | 2026-06-10 |
| Member | `MEM-BILL-01` — Jordan Reyes |
| Plan | `PLN-BILL-01` — Synthetic Choice PPO 500 |
| Billing provider | `PRV-BILL-CLINIC` — Riverside Ortho Clinic |
| Servicing provider (as originally submitted) | `PRV-BILL-SUNRISEHMO200` — Dr. A. Kowalski |
| Service date | 2026-06-01 |
| **Service code (original, wrong)** | `SURG-KNEE-ARTHRO` — "Diagnostic knee arthroscopy" (fictional) |
| **Modifier (original, wrong)** | `MOD-R` — "Right side" (fictional) |
| **Units (original, wrong)** | 2 |
| Place of service | Outpatient surgical center |
| Diagnosis | `DX-KNEE-LIG-01` — "Left knee ligament tear" (fictional) |
| Charge per unit | $1,200.00 |
| Total billed | $2,400.00 (2 × $1,200.00) |
| Authorization reference | Not recorded |

**Recorded decision:**

| Field | Value |
|---|---|
| Status | DENIED |
| Decision date | 2026-06-15 |
| Decision code | `BILLING_DISCREPANCY` (a new, billing-specific reason — never `AUTH_REQUIRED`) |
| Decision reason | "Submitted service code and modifier did not match the documented operative report." |
| **Fields the decision explicitly flagged** | `service_code`, `modifier` — units and servicing provider are **not** mentioned by the decision |
| Allowed amount | $0.00 (explicitly recorded, distinguished from "not recorded") |
| Paid amount | $0.00 (explicitly recorded) |

This split is deliberate: the units and servicing-provider errors are **newly discovered by this
investigation**, not something the original decision ever caught. The UI and the generation prompt
both preserve this distinction rather than attributing every discrepancy to the recorded decision.

---

## 3. Independent supporting records (never touched by form edits)

Fixture: `data/billing_correction_support_records.json`, authored and stored completely separately
from the editable submission — `dispute_review.comparison.compare_billing_correction` reads these,
never anything the provider typed.

| Record ID | Type | Establishes |
|---|---|---|
| `BILLREC-001` | Operative note (2026-06-01, Dr. S. Whitfield) | Service performed = open ligament repair (`SURG-KNEE-REPAIR`), **left** knee (`MOD-L`), performed by `PRV-BILL-SYNTHETICCHOICEPPO500` |
| `BILLREC-002` | Anesthesia/units log (2026-06-01) | Exactly **one** operative session (units = 1), left knee (`MOD-L`) |
| `BILLREC-003` | Provider roster confirmation (2026-06-05) | `PRV-BILL-SYNTHETICCHOICEPPO500` (Dr. S. Whitfield) is the credentialed surgeon of record; `PRV-BILL-SUNRISEHMO200` is a different clinician not involved in this surgery |

---

## 4. Retrieval evidence: real vector search and an isolated graph

Two of the four evidence sources use real retrieval mechanisms rather than a flat load of a fixed
set — both isolated from the shared Golden Dataset & Evaluation infrastructure:

**Synthetic billing policy — real semantic vector search.**
`dispute_review/billing_policy.py`'s `search_billing_policy()` chunks the five `BILL-N` policy
sections (`SMALL` chunking, `rag/chunking.py`), embeds them with the LOCAL "semantic" embedding
provider (`sentence-transformers/all-MiniLM-L6-v2`, `rag/embeddings.py` — no API key, no paid
model call, the same provider `context/hybrid_retriever.py` already uses by default for the
original pipeline), and builds its own **isolated** persistent index at `rag/index_billing/`
(gitignored, never `rag/index/`, never mixed with the six-document shared corpus). A deterministic
query built from the comparison's own CONFLICTS/INSUFFICIENT_EVIDENCE fields (never the
submission's free-text `correction_explanation`) retrieves the top 3 of the resulting ~10 chunks,
ranked by cosine similarity, each carrying a real `score`. `get_billing_policy_sections()` (the
original full, deterministic read) is kept for building this in-memory corpus and remains
available for a "browse the whole policy" use.

**Provider network participation — an isolated knowledge graph.**
`dispute_review/billing_graph.py` builds a small, separate `networkx.MultiDiGraph` from its own
fixture (`data/billing_correction_network.json`: `PRV-BILL-SYNTHETICCHOICEPPO500` → "Synthetic Choice Network";
`PRV-BILL-SUNRISEHMO200` → "Sunrise Alliance Network", the plan's network being "Synthetic Choice
Network") — reusing `graph.builder`'s node/relation constants and `graph.retriever`'s bounded
neighborhood traversal for consistency, but **never** the shared golden-dataset graph (which is
built entirely from `data/providers.json`, a fixture the Golden Dataset & Evaluation tab also
depends on — adding a node there would have meant editing shared state). `gather_network_evidence`
checks both the original and (if different) the proposed servicing-provider id and reports network
participation as its own evidence category (`INDEPENDENT_PROVIDER_NETWORK_RELATIONSHIP`) —
explicitly never a coverage, applicability, or payment fact (enforced by prompt rule 17 and judge
dimension D).

Both sources are treated as OPTIONAL/secondary by the evidence gate (same treatment as independent
support records): missing or empty degrades to `READY_FOR_LIMITED_BRIEF`, never `BLOCKED`.

**Note on network access:** the local sentence-embedding model is downloaded from Hugging Face Hub
on first use if not already cached (a one-time, public model download — no private data leaves the
process, and no LLM/paid API is called); this is pre-existing behavior of the original pipeline's
own default policy provider, not new exposure introduced by this scenario.

---

## 5. The default "Load billing correction example"

`dispute_review/presets.py`'s one function, `billing_correction_example_preset()`, proposes all
four corrections the independent records above actually support:

| Field | Original claim | Proposed correction | Supporting record value | Finding | Evidence reference |
|---|---|---|---|---|---|
| Service Code | `SURG-KNEE-ARTHRO` | `SURG-KNEE-REPAIR` | `SURG-KNEE-REPAIR` | **SUPPORTED** | `support:BILLREC-001` |
| Modifier | `MOD-R` | `MOD-L` | `MOD-L` | **SUPPORTED** | `support:BILLREC-001`, `support:BILLREC-002` |
| Units | 2 | 1 | 1 | **SUPPORTED** | `support:BILLREC-002` |
| Servicing Provider | `PRV-BILL-SUNRISEHMO200` | `PRV-BILL-SYNTHETICCHOICEPPO500` | `PRV-BILL-SYNTHETICCHOICEPPO500` | **SUPPORTED** | `support:BILLREC-001`, `support:BILLREC-003` |

Derived proposed billed amount: 1 unit × $1,200.00/unit = **$1,200.00** — labeled as a derived
recomputation only, never an allowed amount or a payment determination.

Because every field is SUPPORTED, the deterministic recommended next action (computed in
`dispute_review.comparison.build_recommended_next_action`, never model-authored) is exactly:

> "The proposed billing corrections agree with the available service records. Route for analyst
> review and preparation of a corrected claim under the applicable submission requirements.
> Payment is not determined."

## 6. Classification rule (the four-value finding vocabulary)

For each of the four fields, `compare_billing_correction` classifies into exactly one of:

- **SUPPORTED** — a correction was proposed and it agrees with the independent supporting value.
- **CONFLICTS** — either a proposed correction disagrees with the supporting value, or no
  correction was proposed and the *original* claim value itself disagrees with it.
- **CONSISTENT_NO_CORRECTION_NEEDED** — no correction was proposed, and the original value already
  agrees with the supporting value (never labeled an error).
- **INSUFFICIENT_EVIDENCE** — the independent records don't establish a single value (missing, or
  multiple records disagree with each other) — never silently resolved either way.

A changed value is never treated as automatically correct, and an unchanged, already-correct value
is never flagged as an error.

---

## 7. Remaining uncertainty and decision boundaries

- Independent supporting records are treated as **trusted ground truth for comparison**, but an
  analyst should still separately verify they were authored independently and not altered to match
  the requested correction (Section BILL-4 of the synthetic policy).
- If the submission's own `original_claim_reference` or `member_id` no longer match this fixed
  claim's actual values, comparison and evidence retrieval are **blocked** with an explicit reason
  — never silently carried out against a different, ambiguous identity
  (`dispute_review.comparison.check_claim_member_linkage`).
- The synthetic policy (`documents/billing_correction_policy.md`, sections `BILL-1`–`BILL-5`) is
  fictional and is never presented as real Humana or industry guidance.
- Nothing in this scenario submits a corrected claim, edits the original claim, reverses the
  recorded denial, or approves/determines payment — enforced by fixed UI disclaimers, the
  generation prompt's explicit rules, and the deterministic brief validator's authority-language
  check (reused from the original pipeline).

---

## 8. The five-section screen layout (implemented exactly in this order)

1. **Original Claim & Recorded Decision** (read-only) — full claim identification, the four
   investigated fields prominently highlighted, the recorded decision with its explicitly-flagged
   fields, and an "Additional original claim details" expander. Never changes after edits,
   investigation, or judge execution.
2. **Newly Submitted Corrected Claim Details** (editable) — "Provider-submitted proposed
   corrections — unverified," a "Load billing correction example" button, a "Clear / reset"
   button, and every proposed-correction field.
3. **Investigation & Comparison Results** — one combined "Investigate billing correction" button:
   always runs the deterministic comparison first (zero model calls) → the six-column table; then,
   only if the submission validated and no claim/member linkage issue was found, also retrieves
   independent records, semantically-ranked policy passages, and provider-network participation,
   and drafts a cited brief (ONE model call, clearly labeled).
4. **Detailed Evidence, Findings & Next Action** — workflow status, evidence source outcomes,
   limitations/conflicts/missing evidence, the AI-drafted brief, and the deterministic recommended
   next action.
5. **Optional: AI Semantic Evaluation (Judge)** — a separate, optional second model call; advisory
   and uncalibrated; cannot modify the claim or the brief.

---

## 9. Files added and changed

**New fixtures:**
- `data/billing_correction_claim.json`
- `data/billing_correction_support_records.json`
- `data/billing_correction_network.json` — the isolated provider-network fixture.
- `documents/billing_correction_policy.md`

**New modules:**
- `dispute_review/billing_fixtures.py` — isolated fixture loaders (claim/decision, support records).
- `dispute_review/billing_policy.py` — deterministic full-document read, and REAL semantic vector
  search over the scenario's own isolated index (`rag/index_billing/`, gitignored).
- `dispute_review/billing_graph.py` — the isolated provider-network graph and its bounded
  neighborhood traversal, reusing `graph.builder`/`graph.retriever` conventions.

**Rewritten in place (no longer authorization-specific):**
- `dispute_review/models.py` — `BillingCorrectionSubmission`, `BillingClaimSnapshot`,
  `BillingFindingStatus`, `BillingComparisonRow`, `BillingComparisonResult` replace
  `DisputeSubmission`/`ClaimSnapshot`/`ComparisonStatus`/`ComparisonRow`/`DisputeComparisonResult`
  and the retired `DisputeSourceComparisonResult`/`DisputeSubmissionSourceComparisonResult`.
- `dispute_review/comparison.py` — `build_billing_claim_snapshot`, `check_claim_member_linkage`,
  `compare_billing_correction`, `build_recommended_next_action` replace `build_claim_snapshot`,
  `compare_submission`, `compare_source_record`, `compare_submission_to_source_record`.
- `dispute_review/presets.py` — one `billing_correction_example_preset()` replaces
  `build_demo_presets()`'s five presets.
- `context/dispute_evidence_models.py` — provenance categories `INDEPENDENT_SUPPORTING_RECORD`,
  `SYNTHETIC_BILLING_POLICY`, and `INDEPENDENT_PROVIDER_NETWORK_RELATIONSHIP` replace
  `AUTHORIZATION_SOURCE`, `DETERMINISTIC_SOURCE_COMPARISON`,
  `DETERMINISTIC_SUBMISSION_SOURCE_COMPARISON`, `SCENARIO_ONLY_SYNTHETIC_EVENT`; a new
  `network_relationships` field replaces the retired `graph_relationships`/authorization-source
  fields, alongside `support_records`/`linkage_issue`.
- `context/dispute_evidence_retriever.py` — four adapters (`gather_recorded_evidence`,
  `gather_support_record_evidence`, `gather_policy_evidence` — now REAL vector search,
  `gather_network_evidence` — the new isolated-graph adapter) replace the old structured/policy/
  graph/authorization-source adapters.
- `application/dispute_models.py`, `application/dispute_context.py` — drop the two retired
  comparison-summary fields; include `network_relationships` in the assembled context;
  `DISPUTE_PROVENANCE_NOTICE` reworded.
- `application/dispute_brief_validator.py` — Rules C2/C3 (source-comparison integrity) removed;
  Rule E's unverified-provenance set no longer includes the retired `AUTHORIZATION_SOURCE`/
  `RECORDED_VIA_SUBMITTED_LOOKUP` categories.
- `application/dispute_workflow.py` — `_EXPECTED_COMPARISON_FIELDS` updated to the four billing
  fields; the evidence gate adds an explicit BLOCKED path for a claim/member linkage issue, treats
  the provider-network graph as another optional secondary source, and drops the old shared-graph-
  relationship requirement (this scenario never touches the shared golden-dataset graph).
- `prompts/dispute_brief_prompt.py` (`dispute_brief.v8`), `prompts/dispute_judge_prompt.py`
  (`dispute_judge.v6`) — rewritten for the billing scenario; retired rules 15–19 removed; v6 adds a
  rule/dimension update for the new network-participation evidence and updates the policy-evidence
  description now that it is ranked, top-k vector search rather than the full fixed section set;
  v7 strengthens rule 6, and v8 rewrites rule 13, both after a live check (see §13's live-smoke
  findings below).
- `application/dispute_models.py` — `DisputeGenerationContext` gains
  `comparison_verification_guidance` (the deterministic comparison's own verification guidance,
  rendered into the prompt so the model can see, and is told never to duplicate, what the analyst
  has already been shown); `application/dispute_context.py` populates it.
- `skills/investigate_dispute.py` — type import and debug CLI updated.
- `dispute_review/ui.py` — fully rewritten for the five-section billing layout; Missing/Conflicting
  Evidence and Verification Questions moved into a collapsed "Additional review details" expander.
- `dispute_review/__init__.py` — docstring updated.

**Marked historical (kept, not deleted):** `docs/DISPUTE_AI_VALIDATION.md`,
`docs/DISPUTE_AI_WORKFLOW.md`, `docs/DISPUTE_EVIDENCE_RETRIEVAL.md`,
`docs/DISPUTE_REVIEW_BASELINE.md`, `docs/DISPUTE_REVIEW_DEMO.md`, `docs/DISPUTE_REVIEW_LOGIC.md`,
`docs/DISPUTE_REVIEW_UI.md`, `docs/DISPUTE_REVIEW_VALIDATION.md`, `docs/V2_DISPUTE_SCENARIOS.md`,
`docs/V2_DISPUTE_UI_REFINEMENT.md`, `docs/V2_AUTHORIZATION_EVIDENCE.md`.

**Untouched (Golden Dataset & Evaluation, and standalone retired-scenario modules with their own
tests):** `evals/golden_reference_evaluator.py`, `evals/golden_reference_models.py`,
`evals/investigation_golden_reference_set.json`, `docs/V2_GOLDEN_REFERENCE_REVIEW.md`,
`docs/V2_GOLDEN_EVALUATION_UI.md`, `dispute_review/authorization_source_lookup.py`,
`dispute_review/authorization_source_models.py`,
`dispute_review/scenario_synthetic_claim_decision_events.py`.

---

## 10. Old scenarios removed from the active experience

All five authorization-dispute presets (Matching fields, Different servicing provider, Incomplete
submission, Provider corrected after denial, Approved authorization — service date outside
authorized period) and their UI buttons/selectors are removed from `dispute_review/ui.py`. The
claim-vs-authorization-source and submission-vs-authorization-source comparisons, the
authorization-source evidence section, the version-history expander, and the scenario-only
synthetic chronology warning are all removed from the active UI. The underlying
`authorization_source_lookup.py`/`authorization_source_models.py`/
`scenario_synthetic_claim_decision_events.py` modules remain in the codebase, unwired, with their
own passing tests (`tests/test_authorization_source_lookup.py`,
`tests/test_authorization_source_models.py`, `tests/test_scenario_synthetic_claim_decision_events.py`).

---

## 11. Actual tests and offline verification performed

**Retired/replaced tests (with reasons):**
- `tests/test_dispute_authorization_source_integration.py` — **deleted**. Tested the
  authorization-source evidence adapter's wiring into the active pipeline; that wiring no longer
  exists. The underlying `authorization_source_lookup.py` module itself is still covered by its
  own dedicated test file, which is untouched and still passes.
- `evals/dispute_judge_qualitative_examples.py` and
  `tests/test_dispute_judge_qualitative_examples.py` — **deleted**. A hand-authored,
  explicitly-non-calibration illustrative set built entirely around `CLM-1001` and an
  authorization-scenario narrative (a "provider-matching policy" invention, a servicing-provider
  mismatch). The judge PLUMBING it exercised (FakeDisputeJudgeAdapter → DisputeJudgeEnvelope,
  gated by `is_accepted_dispute_draft`) is already covered, against the real billing scenario, by
  `tests/test_dispute_judge.py`.
- `tests/test_dispute_review_models.py`, `test_dispute_review_comparison.py`,
  `test_dispute_review_presets.py`, `test_dispute_evidence_models.py`,
  `test_dispute_evidence_retriever.py`, `test_dispute_context.py`, `test_dispute_brief_validator.py`,
  `test_dispute_workflow.py`, `test_investigate_dispute_skill.py`, `test_dispute_judge_prompt.py`,
  `test_dispute_review_ui.py` — **fully rewritten** against the new billing types/fixtures/UI
  (old content depended on retired types `DisputeSubmission`/`ClaimSnapshot`/`ComparisonStatus`/
  `DisputeComparisonResult`, `build_demo_presets`, and the authorization-source evidence layer).
- `tests/test_dispute_judge.py` — two references to `build_demo_presets("CLM-1001")` updated to
  `billing_correction_example_preset()`/`BILLING_CLAIM_ID`; all other tests (generic judge
  plumbing) unchanged.
- `tests/test_authorization_source_lookup.py` — one test's `DisputeSubmission` construction
  updated to `BillingCorrectionSubmission` (it only needed *some* submission-shaped object to
  prove the authorization-source store has no write path from it); all other tests unchanged.
- `tests/test_dispute_generator.py`, `tests/test_dispute_models.py` — **left unchanged**; their
  use of `"claim:CLM-1001"` is an arbitrary example reference-id string for testing the generic
  `Finding`/`DisputeBrief` schema, not scenario-specific behavior.

**New tests added:** `tests/test_billing_fixtures.py`, `tests/test_billing_policy.py` (real
semantic vector search: ranked top-k, isolated index), `tests/test_billing_graph.py` (isolated
provider-network graph: in-network/out-of-network/unknown-provider distinctions, never resolves
against the shared golden-dataset graph). `tests/test_dispute_evidence_retriever.py` gained
`gather_network_evidence` coverage and updated `gather_policy_evidence` coverage (ranked, scored,
top_k-bounded, instead of "no vector search").

**Commands run and results (2026-09-26, offline, `OPENAI_API_KEY=""` / `LLM_MODEL=""`):**

```
pytest tests/ -q -k "dispute or billing or authorization or scenario_synthetic or graph"
  -> 299 passed
pytest tests/ -q   (full offline suite)
  -> 725 passed, 1 warning, 66.22s
```

Note: the local sentence-embedding model (`sentence-transformers/all-MiniLM-L6-v2`) is downloaded
from Hugging Face Hub on first use if not already cached -- a one-time public model download, not
an LLM/paid API call, and pre-existing behavior of the original pipeline's own default policy
provider. All runs above used an already-warm local cache.

**Offline UI verification:** Streamlit `AppTest` drives the real `app.py` (22 tests in
`tests/test_dispute_review_ui.py`), using a fake OpenAI client (`FakeDisputeBriefAdapter`-shaped
canned responses, never the real SDK) for the "Investigate" and "Run AI semantic evaluation"
buttons. Confirmed: default navigation and Golden Dataset & Evaluation tab (5 cases) untouched; no
retired preset buttons remain; all five sections render in order; the four investigated original
fields render prominently; loading the example and comparing produces four SUPPORTED rows; an
incorrect proposed value produces a CONFLICTS row; editing after compare clears the result; a
member-ID mismatch blocks comparison with an explicit message; the investigate button only calls
the workflow on click; the brief and the exact required "route for analyst review" sentence render;
the judge button appears only after an accepted draft and its scores render; a new investigation
invalidates a prior judge result; the original claim/decision fixture files are byte-identical
before and after a full click-through; two independent `AppTest` sessions do not share state.

This is fake-adapter offline verification, explicitly **not** proof of live-model behavior.

---

## 12. Exact launch command

```bash
cd claim-dispute-review-copilot-v2
.venv/Scripts/python -m streamlit run app.py    # Windows
# .venv/bin/streamlit run app.py                # macOS/Linux
```

---

## 13. Remaining limitations and outstanding live-smoke checklist

- **No live model call has been executed as part of this documented rewrite/test process.**
  Everything reported as "tested" above is offline (fake-adapter) verification, run and recorded by
  this process. Separately, and not run or recorded here, the user informally exercised the
  default example against a real model outside this process and shared one resulting brief for
  review (2026-09-26): the brief correctly reported all four fields SUPPORTED and passed
  deterministic validation, but its wording of rule 6's original-decision-vs-this-investigation
  distinction ("units and servicing provider were not evaluated in that decision") was ambiguous
  enough to read as a claim about this investigation's own coverage rather than the original
  decision's. `prompts/dispute_brief_prompt.py` rule 6 was strengthened in response
  (`dispute_brief.v7`): any "not evaluated [in the original decision]" statement must now be paired,
  in the same or the next sentence, with an explicit statement of what this investigation's own
  comparison found for that field.

  The SAME live run's `verification_questions` output was also reviewed and found to nearly restate
  the synthetic policy's own BILL-4 checklist as four generic questions — duplicating content
  already shown in the deterministic comparison's own "Verification Guidance" (Section 3), and
  asking one question ("confirm no other field was changed") that is structurally guaranteed by the
  submission form and was never a real open question. Rule 13 was rewritten (`dispute_brief.v8`):
  `DisputeGenerationContext` now carries `comparison_verification_guidance` (the same list
  `compare_billing_correction` already computes and the UI already shows), rendered into the prompt
  under its own labeled block so the model can see exactly what the analyst has already been told
  and is instructed never to duplicate it; an EMPTY `verification_questions` list is now explicitly
  the correct output when nothing case-specific remains open, rather than invented filler. The UI
  (`dispute_review/ui.py`) also moved Missing/Conflicting Evidence and Verification Questions into a
  collapsed "Additional review details" expander, so the demo's visual headline stays the summary,
  findings, and the deterministic recommended next action.

  **Neither of these two fixes has itself been re-verified against a live model call** (only
  unit-tested against the prompt's static text and the offline fake-adapter suite) — a live check
  should still confirm, with a real `OPENAI_API_KEY`/`LLM_MODEL`:
  1. `dispute_brief.v8` produces a brief that passes the deterministic validator on the default,
     fully-supported example; that any original-decision-scope statement is paired with an explicit
     statement of this investigation's own finding for that field; and that `verification_questions`
     is empty or genuinely case-specific, never a restatement of the deterministic verification
     guidance or a structurally-guaranteed non-question.
  2. A deliberately-wrong proposed value (e.g. service code) produces a brief that correctly
     reports a CONFLICTS finding rather than treating the change as automatically correct.
  3. The brief correctly treats retrieved policy chunks as a ranked, possibly-partial subset (never
     claiming a point is unsupported by the *whole* policy just because the top-3 chunks didn't
     cover it), and never treats provider network participation as resolving payment/coverage.
  4. `dispute_judge.v6` runs to completion and its four dimension scores/verdicts are sane against
     the same brief (still advisory/uncalibrated — no claim of accuracy is being tested).
  5. The linkage-blocked path (wrong member ID) is never sent to generation at all (already
     enforced deterministically pre-generation, but worth confirming end-to-end with a live run).
- The independent supporting records, the synthetic policy corpus (5 sections / ~10 chunks), and
  the provider-network graph (2 providers, 2 networks) are all small and fixed; this scenario has
  not been extended to a second claim or a larger record/policy/network set. Vector search over
  such a small corpus is a genuine demonstration of the mechanism, not evidence that ranking
  quality has been evaluated at any meaningful scale (no retrieval-quality eval like
  `evals/hybrid_retrieval_eval.py` exists for this scenario's corpus).
- The provider-network graph is deliberately minimal (no `Plan`/`Benefit` nodes, no multi-hop
  traversal beyond `Provider -> Network`) -- it demonstrates the mechanism for one specific
  question (network participation), not a general-purpose graph extension of this scenario.
