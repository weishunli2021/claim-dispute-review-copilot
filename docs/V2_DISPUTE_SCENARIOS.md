> **HISTORICAL — retired scenario.** This document describes the earlier
> AUTHORIZATION-DISPUTE scenario(s) that were replaced by the single
> billing-correction scenario (CLM-BILL-9001). It is kept for historical
> reference only and does not describe the current Dispute Review tab.
> See docs/BILLING_CORRECTION_SCENARIO.md for the active scenario.

# V2 Dispute Scenarios — Module 3 (Integrated Authorization-Source Evidence)

Module 3 wires Module 2's isolated authorization-source foundation into the full
Dispute Review pipeline (preset → comparison → evidence retrieval → gate → brief →
deterministic validation → optional judge → UI) and adds two new, end-to-end-usable
demo scenarios. The displayed app name remains **"Claim Dispute Review Copilot."**

All authorization-source records described here are **entirely synthetic**. No real
payer system was contacted, queried, or authenticated anywhere in this module.

## 1. The two scenarios

### Scenario A — "Provider corrected after denial"

**Preset button label:** *Provider corrected after denial*
**Submission reference:** `DEMO-AUTH-SRC-A`

| Source record | Version | Status | Servicing provider | Authorized period | Notes |
|---|---|---|---|---|---|
| `AUTHSRC-SCEN-A-1` | 1 (original) | APPROVED | `PRV-9998-WRONG-FACILITY` (fictional) | 2026-01-15 → 2026-04-15 | Decided 2026-01-20 (authorization-source decision, not a claim event) |
| `AUTHSRC-SCEN-A-2` | 2 (amends v1) | APPROVED | `PRV-1001` (matches CLM-1001) | 2026-01-15 → 2026-04-15 (unchanged) | Amended/decided 2026-02-20 (authorization-source amendment); `retroactive_effective` explicitly `null` |

**Chronology and evidence provenance.**

*Claim-side timestamp — file, field, meaning, value.* CLM-1001's claim record
(`data/claims.json`) carries only `date_of_service = 2026-02-10` and `status = DENIED`.
`dispute_review.models.ClaimSnapshot` and `tools.models.Claim` both have **no
denial-decision timestamp field of any kind** — confirmed again during this review by
reading both models directly. This is a genuine, structural absence in the baseline
data, not an oversight in retrieval: there is nothing to retrieve.

*What was wrong in an earlier version of this section.* An earlier pass surfaced
`AUTHSRC-SCEN-A-1.decided_at` (2026-01-20T10:00:00Z) and `AUTHSRC-SCEN-A-2.amended_at`
(2026-02-20T09:00:00Z) as evidence and described the amendment as coming "after the
claim's denial." Both timestamps are real and correctly sourced from
`data/authorization_source_records.json`, but **they are AUTHORIZATION-decision
timestamps — when the authorization-source system decided or amended an authorization
record — never claim-denial timestamps.** Describing the amendment as "after the
claim's denial" implicitly treated the authorization's own decision timing as if it
were the claim's denial timing, which is not something the source data supports. This
document is corrected below; the two authorization timestamps remain accurate and are
still surfaced as `AUTHORIZATION_SOURCE`-provenance evidence exactly as before
(`context/dispute_evidence_retriever.py`, confirmed by
`test_gather_scenario_a_evidence_exposes_actual_decided_and_amended_timestamps`) — only
the claim-denial-timing interpretation was wrong.

*The correction: a scenario-only synthetic claim-decision event.* Because no real
claim-denial timestamp exists to compare against, and because a chronology claim like
"after denial" needs an actual timestamp to be checkable rather than asserted in prose,
this scenario now defines exactly one: `CLM-1001`, `2026-02-11T12:00:00Z` (one day after
the claim's real `date_of_service`), in
[`data/scenario_synthetic_claim_decision_events.json`](../data/scenario_synthetic_claim_decision_events.json)
and loaded by
[`dispute_review/scenario_synthetic_claim_decision_events.py`](../dispute_review/scenario_synthetic_claim_decision_events.py).
This is structurally isolated from every baseline claim data path: a separate fixture
file (never `data/claims.json`), a separate Pydantic type (never a field on
`ClaimSnapshot`/`Claim`), and `is_synthetic` is validator-enforced `True` — the type
cannot represent a real event. `gather_authorization_source_evidence` surfaces it as its
own citable evidence reference, `scenario_synthetic_event:CLM-1001:claim_denial_decision`,
under a dedicated provenance category, `SCENARIO_ONLY_SYNTHETIC_EVENT` (never
`AUTHORIZATION_SOURCE`, never `RECORDED`) — its `label` states plainly, every time it is
rendered, that it is invented and not read from `data/claims.json`. The UI renders it as
a `st.warning` alongside the version-history expander (`dispute_review/ui.py`).

*Does the amendment actually follow it?* Yes, checked directly rather than asserted:
`2026-02-11T12:00:00Z` (the synthetic claim-decision event) precedes
`AUTHSRC-SCEN-A-2.amended_at` (`2026-02-20T09:00:00Z`). `gather_authorization_source_evidence`
computes this comparison itself and reports the actual result — it does not just assume
it — and its limitation text states the result while still stating plainly that this
ordering only holds **within the synthetic construction**, never as confirmation of the
claim's real, unrecorded denial timing: *"This demonstration's 'after denial' chronology
is anchored to a SCENARIO-ONLY SYNTHETIC claim-decision timestamp
(2026-02-11 12:00:00+00:00), not to any timestamp recorded in the claim's own actual
recorded data — data/claims.json has no denial-decision timestamp field at all. Within this synthetic
construction, the invented claim-decision timestamp precedes the authorization-source
amendment's amended_at (2026-02-20 09:00:00+00:00); this must never be read as
confirming the claim's actual, real-world denial timing, which remains genuinely
unrecorded and unknown."* For any claim without a defined scenario event, the retriever
falls back to the original, simpler limitation text stating only that the absence makes
the ordering unconfirmable — that fallback path is unchanged and still exercised by
`test_gather_found_scenario_b_no_denial_timing_note_since_no_amendment`-adjacent
coverage (Scenario B has a single version and never reaches this branch at all).

Status is `APPROVED` on **both** versions, by design — this isolates a servicing-provider
correction as the only variable, per the task's own instruction, rather than also
introducing a status change.

**Expected comparisons.**
- Claim vs. **submission**: 4/4 match (the submission's own fields mirror the claim;
  the interesting content lives in the source lookup, not the submission).
- Claim vs. **authorization-source** (current version, `AUTHSRC-SCEN-A-2`): 4/4 match
  (member, service, validity dates, and — now — servicing provider all align).

**Appropriate interpretation.** The CURRENT source version aligns with the claim on
provider, but `retroactive_effective` is `null` — the source never asserts that the
correction applies back to the original service/decision. **This must be read as
"applicability still requires verification," never as "the original denial was
wrong."** Both the prompt (`prompts/dispute_brief_prompt.py` rules 15–18) and the
UI (`dispute_review/ui.py`'s Authorization-Source Lookup section) state the
current/prior distinction and the unknown-retroactive-applicability fact explicitly.

### Scenario B — "Approved authorization — service date outside authorized period"

**Preset button label:** *Approved authorization — service date outside authorized period*
**Submission reference:** `DEMO-AUTH-SRC-B`

| Source record | Version | Status | Servicing provider | Authorized period |
|---|---|---|---|---|
| `AUTHSRC-SCEN-B-1` | 1 (single version) | APPROVED | `PRV-1001` (matches CLM-1001) | 2026-02-15 → 2026-05-15 |

The authorized period begins **five days after** CLM-1001's 2026-02-10 date of
service. Member, service, and servicing provider all align.

**Expected comparisons.**
- Claim vs. submission: 4/4 match.
- Claim vs. authorization-source: **3/4 match; Validity Dates = MISMATCH** — the one
  and only deliberate discrepancy in this scenario.

**Appropriate interpretation.** An APPROVED authorization-source record exists, but its
stated period does not cover this service date. Approval and service-date applicability
are kept as two separate, independently-observable facts everywhere in this codebase
(`dispute_review.authorization_source_models.AuthorizationSourceRecord` has no
"applies" field at all — confirmed by a dedicated test asserting
`not hasattr(record, "applies_to_service_date")`). **Nothing here infers payment
approval, denial reversal, or any final adverse/favorable decision** — the comparison
mismatch is presented as a fact to investigate, never a conclusion.

## 2. Files added or changed

**New:**
- `dispute_review/models.py` — added `DisputeSourceComparisonResult` (+
  `SOURCE_APPLICABILITY_DISCLAIMER`), a SEPARATE result type from
  `DisputeComparisonResult`, never merged.
- `dispute_review/comparison.py` — added `compare_source_record()` and its
  verification-guidance helper, reusing `_compare_exact_identifier` and a newly
  extracted `_compare_date_window` helper (refactored out of the pre-existing
  `_compare_validity_dates`, behavior-preserving — confirmed by the pre-existing
  comparison test suite passing unchanged).
- `dispute_review/presets.py` — added `provider_corrected_after_denial_preset()`,
  `approved_authorization_excludes_service_date_preset()`, extended
  `DisputeDemoPresets`/`build_demo_presets()` from three fields to five.
- `context/dispute_evidence_models.py` — added `EvidenceProvenanceCategory.AUTHORIZATION_SOURCE`
  and `.DETERMINISTIC_SOURCE_COMPARISON`; extended `DisputeEvidencePackage` with
  `authorization_source_lookup_result`, `authorization_source_comparison`,
  `authorization_source_references`.
- `context/dispute_evidence_retriever.py` — added the fourth retrieval adapter,
  `gather_authorization_source_evidence()` (+ `_build_source_comparison_references`),
  wired into `build_dispute_evidence_package`. **Module 4 correction:** each version's
  `decided_at`/`amended_at` timestamps are now included in its evidence `detail` string
  (previously omitted — see §1's chronology discussion).
- `application/dispute_models.py` — added `DisputeGenerationContext.authorization_source_comparison_summary`.
- `application/dispute_context.py` — wired `authorization_source_references` into the
  combined, never-truncated reference list, and set the new summary field.
- `application/dispute_brief_validator.py` — added Rule C2
  (`source_comparison_context_integrity`, the exact structural-regression-guard
  pattern Rule C already uses, applied to the new comparison); extended Rule E's
  `_UNVERIFIED_PROVENANCE` set to include `AUTHORIZATION_SOURCE`.
- `application/dispute_workflow.py` — one call-site change: `validate_brief` now passes
  `evidence_package.authorization_source_comparison` through to
  `validate_dispute_brief` (its new fourth, optional parameter).
- `prompts/dispute_brief_prompt.py` — four new numbered rules (15–18: authorization-
  source evidence handling, the separate source-comparison summary, approval-vs-
  applicability, retroactive-applicability uncertainty); `PROMPT_VERSION` bumped
  `dispute_brief.v2` → `dispute_brief.v3`.
- `prompts/dispute_judge_prompt.py` — extended Dimensions A/C/D to cover the new
  evidence category and its specific authority-boundary concerns; renders the new
  comparison summary; `JUDGE_PROMPT_VERSION` bumped `dispute_judge.v2` → `dispute_judge.v3`.
- `dispute_review/ui.py` — two new preset buttons; refactored `_render_comparison_table`
  to take rows directly (reused for both comparisons); new
  `_render_authorization_source_section()`; new evidence group in the "Evidence used"
  expander. **Module 4 correction:** the version-history expander now also shows each
  version's `decided_at`/`amended_at` when present.
- `data/authorization_source_records.json` — added the two scenarios' three records
  (`AUTHSRC-SCEN-A-1/-2`, `AUTHSRC-SCEN-B-1`) alongside Module 2's three generic
  demonstration records.
- Tests: `tests/test_dispute_authorization_source_integration.py` (new, 25 tests);
  6 new tests appended to `tests/test_dispute_review_ui.py`; 3 new tests appended to
  `tests/test_authorization_source_lookup.py` (the branching-chain gap fix, §3 below);
  1 test corrected in `tests/test_dispute_judge_prompt.py` (expected prompt version
  string bumped from v2 to v3, since the version legitimately changed this module).

**Not modified:** `skills/investigate_dispute.py` (delegates entirely to
`build_dispute_evidence_package`, needed no change), `application/dispute_generator.py`,
`application/dispute_judge.py`, `application/dispute_judge_models.py`, `tools/`,
`data/claims.json`, `data/prior_authorizations.json`, and all five original baseline
claims' behavior.

## 3. Version-selection gap found and fixed (Step 2)

Before integrating the lookup, its version-selection logic was re-audited. A real gap
was found: a **branching chain** (one parent amended by two different children — a
fork) fell through to a silent `max(record_version)` pick instead of being rejected.
Fixed in `dispute_review/authorization_source_lookup.py`:
- `lookup_authorization_source_by_reference` now requires the chain's un-amended "tip"
  count to be exactly 1; any other count (0 or >1) returns `AMBIGUOUS` with every
  version in the chain surfaced as an unresolved candidate — never a silent "latest
  wins."
- `AuthorizationSourceStore.__init__` gained a load-time integrity check: an amended
  record's own `record_version` must be strictly greater than the version it amends,
  which also structurally prevents a 2-record amendment cycle.

Three new regression tests cover this
(`test_lookup_ambiguous_for_a_branching_chain_never_silently_picks_highest_version`,
`test_store_rejects_a_two_cycle_amendment_chain`,
`test_store_rejects_amendment_with_non_increasing_record_version`, in
`tests/test_authorization_source_lookup.py`).

## 4. Gate behavior for source lookup outcomes (Step 5)

`application/dispute_workflow.py`'s `assess_evidence_gate` was **not modified** — it
already treats any `EvidenceSourceOutcome.status == FAILURE` from any secondary source
generically as a `READY_FOR_LIMITED_BRIEF` degradation, never `BLOCKED`. The new
adapter maps outcomes onto that existing, unmodified framework:

| Lookup status | `EvidenceSourceStatus` | Gate impact |
|---|---|---|
| `FOUND` | `SUCCESS_WITH_EVIDENCE` | None — normal case |
| `NOT_FOUND` | `SUCCESS_NO_RESULTS` | None — a legitimate absence, never a retrieval failure |
| `AMBIGUOUS` | `SUCCESS_WITH_EVIDENCE` | None — real records exist (the candidates), just not resolved to one; this is investigation content, not a broken retrieval |
| `ERROR` | `FAILURE` | Degrades to `READY_FOR_LIMITED_BRIEF` (never `BLOCKED`, since this source is supplementary, not core) |

A date mismatch (Scenario B) or an unknown retroactive-applicability value
(Scenario A) is surfaced as a comparison row / limitation — **never** as a
`FAILURE` outcome, and therefore never degrades the gate. This was confirmed directly:
`test_gate_stays_scoped_generation_for_found_scenario_a`/`..._b` both assert
`READY_FOR_SCOPED_GENERATION`, and `test_gate_degrades_to_limited_brief_never_blocked_on_authorization_source_error`
confirms the one path that *does* degrade the gate, and exactly how far.

## 5. What remains unknown (by design, never resolved by this codebase)

- Whether Scenario A's correction applies retroactively to the original service/decision
  (`retroactive_effective = null`, never inferred from `amended_at`).
- Whether the claim's actual denial-decision timestamp precedes or follows the
  amendment with certainty (the claim record has none on file).
- Whether either authorization-source record actually belongs to CLM-1001's real-world
  counterpart, or has been authenticated in any way — a `FOUND` lookup is a fixture
  match, never authentication.
- Whether Scenario B's authorized period was ever intended to cover the 2026-02-10
  service — the mismatch is reported, not explained or excused.

## 6. Tests actually run and results (2026-09-25, this project's own fresh `.venv`)

| Check | Command | Result |
|---|---|---|
| New integration tests | `pytest tests/test_dispute_authorization_source_integration.py -q` | **27 passed** |
| Version-selection gap-fix tests | `pytest tests/test_authorization_source_lookup.py -q` | **21 passed** |
| Full UI suite (incl. 6 new scenario tests) | `pytest tests/test_dispute_review_ui.py -q` | **62 passed** |
| Scenario-only synthetic claim-decision event tests | `pytest tests/test_scenario_synthetic_claim_decision_events.py -q` | **9 passed** |
| Dependency conflicts | `pip check` | `No broken requirements found.` |
| Full offline suite | `pytest tests/ -q` | **798 passed**, 0 failed (733 Module 2 baseline + 25 + 6 + 3 new in Module 3 + 1 Module 4 chronology-evidence regression test + 19 golden-reference-dataset tests + 11 new from this review follow-up: 1 integration + 9 scenario-event + 1 golden-reference) |
| End-to-end eval | `python -m evals.e2e_eval` | **8 of 8** passed |
| Safety eval | `python -m evals.e2e_safety_eval` | **9 of 9** passed |

No paid/live model call was made anywhere in this module or its review follow-up — every
generation/judge call in every test above uses `FakeDisputeBriefAdapter`/`FakeDisputeJudgeAdapter`.

## 7. UI verification performed

**Real browser, no live model call** (this module): started the app from this
project's own `.venv` on an unused local port
(`streamlit run app.py --server.fileWatcherType=none --server.port=8711`), with
`OPENAI_API_KEY`/`LLM_MODEL` explicitly blanked **for that one process's environment
only** (the real `.env` file copied in Module 1 was never read, modified, or exposed) —
this guarantees no paid call could occur while still exercising the full evidence-
retrieval code path. Verified directly in the browser:
- App title/tab label unchanged: "Claim Dispute Review Copilot."
- Both new preset buttons render with the exact requested labels.
- **Scenario A**: loaded the preset, compared (4/4 submission match), clicked
  "Investigate dispute evidence," and confirmed the Authorization-Source Lookup section
  renders: Outcome "Found," Source Record ID `AUTHSRC-SCEN-A-2`, Version 2, Status
  APPROVED, source label `SYNTHETIC-AUTH-SOURCE-V2`, authorized period covering the
  service date, servicing provider `PRV-1001`, retroactive applicability "Unknown / not
  established by the source," a separate 4/4-match source comparison table with its own
  applicability disclaimer, a 2-version history expander (showing `AUTHSRC-SCEN-A-1` as
  PRIOR and `AUTHSRC-SCEN-A-2` as CURRENT), and both scenario-specific limitations
  (retroactive-applicability-unknown, denial-timestamp-absent) rendered under Evidence
  Limitations. Generation correctly failed with `CONFIGURATION` and the honest "no
  credentials are displayed" message — no live call was attempted.
- **Scenario B**: loaded the preset (confirmed the prior result was cleared on preset
  switch), compared, investigated, and confirmed the Authorization-Source Lookup
  section shows Source Record ID `AUTHSRC-SCEN-B-1`, Version 1, APPROVED, authorized
  period `2026-02-15` to `2026-05-15`, and the separate source comparison correctly
  showing "3 of 4 compared fields match; 1 mismatch(es) ... (Validity Dates)" — no
  version-history expander shown (correctly omitted for a single-version record).
- Stopped only the one server process this check started.

**Automated-mocked** (authoritative for exact assertions, including result
invalidation and shared-state isolation): 62 passing Streamlit `AppTest` cases in
`tests/test_dispute_review_ui.py`, including the 6 new tests added this module.

## 8. Demo path

**Scenario A — "Provider corrected after denial" (~30s):**
1. Open the Dispute Review tab (`CLM-1001` is preselected).
2. Click **Provider corrected after denial**, then **Compare submitted information** —
   note the 4/4 submission match.
3. Click **Investigate dispute evidence**.
4. Point at the **Authorization-Source Lookup** section: "Found," version 2, servicing
   provider now `PRV-1001` (matches the claim), retroactive applicability explicitly
   "Unknown / not established." Expand **Version history** to show the original,
   wrong-facility version 1 alongside it.
5. Say: *"The system never concludes the original denial was wrong — it surfaces a
   later correction and flags that whether it applies retroactively is still an open
   question."*

**Scenario B — "Approved authorization — service date outside authorized period" (~25s):**
1. Click **Approved authorization — service date outside authorized period**, then
   **Compare submitted information**.
2. Click **Investigate dispute evidence**.
3. Point at the Authorization-Source Lookup section's separate comparison table:
   Member/Service/Servicing Provider all MATCH, but Validity Dates is MISMATCH — the
   authorized period starts five days after the service date.
4. Say: *"Approval alone never resolves applicability here — the system reports both
   facts side by side and lets a human reconcile them, never inferring payment or a
   final decision."*
