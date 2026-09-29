> **HISTORICAL — retired scenario.** This document describes the earlier
> AUTHORIZATION-DISPUTE scenario(s) that were replaced by the single
> billing-correction scenario (CLM-BILL-9001). It is kept for historical
> reference only and does not describe the current Dispute Review tab.
> See docs/BILLING_CORRECTION_SCENARIO.md for the active scenario.

# V2 Dispute Review UI Refinement

This module reorganizes the Dispute Review screen into one shared, linear layout applied to
every preset, and fixes two concrete defects found while doing so: a wording bug that mislabeled
authorization-source values as "submitted," and a genuine gap where the submitted authorization
period and the current source record's authorized period could silently disagree with each other
even though each one separately covered the claim's date of service.

**No workflow, gate, evidence source, or golden-evaluation logic was rewritten.** The existing
LangGraph workflow (`application/dispute_workflow.py`), the evidence gate, the three original
evidence sources, and the Golden Dataset & Evaluation tab are all unchanged. What changed: the
Dispute Review screen's layout, a new (fourth) deterministic comparison, and a handful of wording
fixes.

## 1. Files changed

**New:**
- Adds `DisputeSubmissionSourceComparisonResult` to
  [dispute_review/models.py](../dispute_review/models.py) and
  `compare_submission_to_source_record` to [dispute_review/comparison.py](../dispute_review/comparison.py)
  — a THIRD comparison (submission vs. current source record directly), plus a fix to
  `_compare_exact_identifier`'s previously-hardcoded "Submitted X" wording.
- `docs/V2_DISPUTE_UI_REFINEMENT.md` (this file).

**Changed:**
- [dispute_review/ui.py](../dispute_review/ui.py) — reorganized into the shared 5-section layout
  (see §2); new wide evidence-comparison table; preset reordering; wording fixes.
- [context/dispute_evidence_models.py](../context/dispute_evidence_models.py) — new
  `DETERMINISTIC_SUBMISSION_SOURCE_COMPARISON` provenance category; new
  `DisputeEvidencePackage.submission_source_comparison` field.
- [context/dispute_evidence_retriever.py](../context/dispute_evidence_retriever.py) — computes the
  new comparison inside `gather_authorization_source_evidence`'s FOUND branch; appends a MISMATCH
  as an explicit conflict (never left implicit); wording fix ("the claim's own actual recorded
  data").
- [application/dispute_models.py](../application/dispute_models.py) — new
  `DisputeGenerationContext.submission_source_comparison_summary` field; wording fix
  (`DISPUTE_PROVENANCE_NOTICE` no longer says "real data").
- [application/dispute_context.py](../application/dispute_context.py) — populates the new summary
  field.
- [application/dispute_brief_validator.py](../application/dispute_brief_validator.py) — new Rule
  C3 (`submission_source_comparison_context_integrity`), mirroring Rule C2.
- [application/dispute_workflow.py](../application/dispute_workflow.py) — `validate_brief` passes
  the new comparison as a 5th argument.
- [prompts/dispute_brief_prompt.py](../prompts/dispute_brief_prompt.py) — new rule 19; renders the
  new comparison summary; fixed the bare "(none)" CONFLICTS wording to state what was actually
  checked; `PROMPT_VERSION` bumped `v3` → `v4`.
- [prompts/dispute_judge_prompt.py](../prompts/dispute_judge_prompt.py) — same CONFLICTS fix;
  dimension A/B/D extended to cover the third comparison; `JUDGE_PROMPT_VERSION` bumped `v3` → `v4`.
- [docs/V2_DISPUTE_SCENARIOS.md](V2_DISPUTE_SCENARIOS.md) — one stale wording quote updated.
- Test files: [tests/test_dispute_review_ui.py](../tests/test_dispute_review_ui.py),
  [tests/test_dispute_authorization_source_integration.py](../tests/test_dispute_authorization_source_integration.py),
  [tests/test_dispute_judge_prompt.py](../tests/test_dispute_judge_prompt.py) — updated for the
  above, plus new focused tests (see §6).

## 2. Final screen order (shared across every preset)

```
1. Original Claim & Recorded Decision   -- claim ID, member, service, date of service,
                                            status, denial reason (code + description),
                                            billed/allowed amounts, servicing vs. ordering
                                            provider (clearly distinguished)
2. Newly Submitted Evidence             -- preset selector (3 primary + 2 additional in an
                                            expander) + submission form, unverified-labeled
3. Investigation Actions                -- Step 1: Compare submitted information (no model call)
                                            Step 2: Investigate dispute evidence (optional,
                                            one model call) -- shows a placeholder before the
                                            first investigation
4. Evidence Comparison                  -- the wide, compact table (see §3), plus a
                                            "Detailed comparison tables & evidence sources
                                            (technical)" expander for the three underlying
                                            4-row comparisons and per-source outcomes
5. Findings & Next Action                -- gate/generation/validation status, generated
                                            brief + citations, optional judge (in its own
                                            expander)
```

Applied identically to all five presets — three PRIMARY demo scenarios (Matching fields;
Provider corrected after denial; Approved authorization — service date outside authorized
period) shown first, with Different servicing provider and Incomplete submission retained as
"Additional examples" in an expander, sharing the exact same pipeline and layout.

## 3. What each primary scenario displays

**A. Matching fields** — all four submission-vs-claim fields MATCH; no authorization-source
record exists for this synthetic reference (NOT_FOUND, a real retrieval outcome, never a
placeholder). The wide table shows "Not available" for every Auth-Source Version column. The
authenticity disclaimer ("matching fields do not establish authenticity...") is always shown.

**B. Provider corrected after denial** — the ORIGINAL authorization-source version
(`AUTHSRC-SCEN-A-1`) shows `PRV-9998-WRONG-FACILITY`; the CURRENT version (`AUTHSRC-SCEN-A-2`)
shows `PRV-1001`, matching the claim. Both versions are `APPROVED`. The wide table's Finding
column and the version-history expander both make the correction visible; a short, always-visible
warning points to a scenario-only SYNTHETIC claim-decision timestamp (never presented as a real
claim field), with the full explanation moved into the Version History expander. Retrospective
applicability is reported as unknown, never inferred. Wording throughout says "the source records
a correction" — never that this tool made or verified one.

**C. Approved authorization with a date conflict** — `AUTHORIZATION STATUS: APPROVED` is shown
as its own fact, separate from the `Authorized Period`/`Validity Dates` MISMATCH rows. The wide
table's "Claim Service Date" row (2026-02-10) sits next to "Authorized Period" showing the source
record's window starting 2026-01-15 — the exclusion is stated plainly, with no implication that a
correction exists or that any payment/coverage decision follows from it.

## 4. Date-discrepancy behavior (the concrete bug this module fixed)

**Found:** the real "Provider corrected after denial" preset submits an authorization period of
`2026-01-11` to `2026-03-12` (derived from a ±30-day window around the claim's date of service),
while the current source record (`AUTHSRC-SCEN-A-2`) is authorized `2026-01-15` to `2026-04-15`.
The claim's date of service (`2026-02-10`) falls inside BOTH periods separately — and nothing
before this module ever compared the two periods against EACH OTHER, only each one against the
claim's date of service.

**Fixed:** `compare_submission_to_source_record` (new) performs exactly that comparison. Values
were preserved exactly as observed — nothing was aligned or silently changed. Verified with the
real preset and real fixture record (not hand-picked values):

```
Submitted period: 2026-01-11 to 2026-03-12
Source period:    2026-01-15 to 2026-04-15
Result: MISMATCH — "the submitted authorization period ... differs from the current source
record's authorized period ... Both periods may separately include the claim's date of service
-- that does not make the two periods themselves the same"
```

This is kept as its own, separate comparison — never merged with either of the two existing
claim-anchored comparisons. Its MISMATCH is:
- Rendered as its own mini comparison table in the Dispute Review screen (§3's "Detailed
  comparison tables" expander) and folded into the wide table's "Authorized Period" row.
- Turned into 4 citable evidence references (`submission_source_comparison:<field>`).
- Appended to `conflicts` explicitly — a caller reading only the conflicts list still learns
  about it (never left implicit in a reference alone).
- Rendered as its own highlighted summary block in the brief AND judge prompts (rule 19 /
  dimension B), so the model is told about it directly, not just shown it buried in the reference
  list.
- Structurally protected by a new validator rule (Rule C3) the same way the other two comparisons
  already were.
- CONFLICTS is never a bare `(none)` when this (or any) discrepancy exists, and states what was
  actually checked even when genuinely empty (e.g. `"none identified across the claim-vs-
  submission comparison"`) — never an unscoped claim that nothing anywhere conflicts.

## 5. Other wording fixes made

- `_compare_exact_identifier` no longer hardcodes "Submitted X matches..." for every comparison —
  a claim-vs-source row now says "The authorization-source record's X matches the claim's
  recorded X," and a submission-vs-source row says "The current source record's X matches the
  submitted X." (Previously, e.g., a claim-vs-source Member row read "Submitted member ID
  matches..." even though nothing was submitted on that side.)
- Every "(Predefined Claims tab)" reference in `dispute_review/ui.py` (2 user-visible `st.info`
  strings + 2 docstrings) now says "(Golden Dataset & Evaluation tab)," matching the Module 5
  rename.
- `DISPUTE_PROVENANCE_NOTICE` no longer says a submitted-identifier lookup match "is real data"
  (ambiguous in a system that constantly disclaims "everything here is synthetic") — now "a
  genuine record already on file in this synthetic dataset -- not fabricated."
- The Scenario A chronology limitation text no longer says "the claim's own real data" — now "the
  claim's own actual recorded data," for the same reason.
- The always-visible chronology warning is now a short pointer; the full "SCENARIO-ONLY SYNTHETIC
  EVENT" explanation moved into the Version History expander (technical detail).

## 6. Actual test results and UI verification

All verification is **fake-adapter, offline** — `tests/conftest.py`'s autouse fixture also
default-denies any real `openai.OpenAI(...)` construction. No live model call was made anywhere in
this module. Live generation for the changed paths will be rechecked separately, as instructed.

New/updated focused tests:

- `tests/test_dispute_review_comparison.py` / `test_dispute_authorization_source_integration.py`
  — wording fix regression test (claim-vs-source never says "submitted"); `compare_submission_to_
  source_record` correctness (all-match, the exact real-data discrepancy case, missing-dates ->
  UNKNOWN never MATCH, neither side mislabeled as "the claim's recorded"); `gather_authorization_
  source_evidence` produces the new comparison + conflict + 4 references; the discrepancy reaches
  the rendered brief prompt with a real evidence id and a properly-scoped, non-bare CONFLICTS
  section; the companion empty-conflicts case is also properly scoped.
- `tests/test_dispute_review_ui.py` — shared layout with all five presets accessible and
  functionally identical; `PRV-9998-WRONG-FACILITY` present in authorization-source evidence but
  absent from the Recorded Claim section's own servicing-provider line; the wide table's
  Original/Current columns reflect the real provider correction; one-version and no-record cases
  render "Not available"/"Not applicable" honestly (never inventing an original version); the
  discrepancy reaches `DisputeGenerationContext` with a real evidence id; the Scenario A UI
  rendering gap closed in the prior module's UI test updated for the new short-warning/expander
  split; the pre-existing "never shows approval language" safety test updated to exclude the
  claim's own newly-displayed, legitimately-quoted recorded denial description (a real recorded
  fact, not a new claim by the tool) while still catching the phrase anywhere else.
- `tests/test_dispute_judge_prompt.py` — `JUDGE_PROMPT_VERSION` bump; CONFLICTS scope-wording
  assertion updated.

Actual results (2026-09-26, this project's own `.venv`, `OPENAI_API_KEY=""` `LLM_MODEL=""`):

| Check | Command | Result |
|---|---|---|
| Non-UI dispute suite | `pytest tests/test_dispute_brief_validator.py tests/test_dispute_context.py tests/test_dispute_evidence_models.py tests/test_dispute_evidence_retriever.py tests/test_dispute_generator.py tests/test_dispute_judge.py tests/test_dispute_judge_prompt.py tests/test_dispute_judge_qualitative_examples.py tests/test_dispute_models.py tests/test_dispute_review_comparison.py tests/test_dispute_review_models.py tests/test_dispute_review_presets.py tests/test_dispute_workflow.py tests/test_investigate_dispute_skill.py -q` | **204 passed** |
| Authorization-source integration (incl. new Module 6 tests) | `pytest tests/test_dispute_authorization_source_integration.py -q` | **35 passed** |
| Dispute Review UI suite (incl. new Module 6 tests) | `pytest tests/test_dispute_review_ui.py -q` | **68 passed** |
| Full offline suite | `pytest tests/ -q` | **859 passed**, 0 failed (846 going in + 13 new: 8 comparison/integration + 5 UI) |

UI verification performed (`AppTest`, fake adapters):
- All five preset buttons present and produce a comparison result through the identical shared
  pipeline.
- `PRV-9998-WRONG-FACILITY` appears in authorization-source evidence, never in the claim's own
  recorded servicing-provider line.
- The wide evidence-comparison table's Original/Current Auth-Source Version columns show the real
  `PRV-9998-WRONG-FACILITY` → `PRV-1001` correction for Scenario A, and "Not available"/"Not
  applicable" honestly for the no-record and single-version cases.
- The submission-vs-source discrepancy reaches `DisputeGenerationContext` with a real,
  citable evidence id.
- Every existing invalidation test (per-field clearing, preset switching, new-investigation
  invalidating the judge) still passes unmodified against the new layout.

## 7. Remaining limitations

- Live-model generation for the reorganized screen and the new third comparison is **not yet
  verified against a real model** — this module made no paid calls, per instruction; live
  generation will be rechecked in a follow-up.
- The wide evidence-comparison table's "Finding" column for identifier fields (Member, Service,
  Servicing Provider) shows a compact `"vs. claim: MATCH; source vs. claim: MATCH; submitted vs.
  source: MATCH"`-style summary rather than full prose — a deliberate compactness trade-off; the
  full explanation for each underlying comparison remains one click away in the "Detailed
  comparison tables" expander.
- No change was made to the Golden Dataset & Evaluation tab, the evidence gate, or the LangGraph
  workflow structure — this module's scope was the Dispute Review screen's presentation layer and
  the one new, genuinely-needed comparison.
