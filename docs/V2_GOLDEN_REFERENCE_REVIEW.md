# V2 Golden Reference Review — Module 4

## 1. What this is, and what it is not

This document, and the dataset it describes
([evals/investigation_golden_reference_set.json](../evals/investigation_golden_reference_set.json)),
are **AI-drafted and pending human review**. They exist to give a future evaluator or reviewer a
precise, source-grounded description of what a correct investigation brief for each of the five
**original predefined claims** (`CLM-1001`–`CLM-1005`, the "Predefined Claims" tab) should
contain — and, just as importantly, what it must never claim.

This is **not**:

- The two dispute-review presets or the two new Module 3 dispute scenarios ("Provider corrected
  after denial", "Approved authorization — service date outside authorized period"). Those are a
  separate feature (`dispute_review/`) with their own separate evidence layer and are entirely
  out of scope for this dataset.
- A replacement for `evals/agent_golden_set.json` or `evals/hybrid_retrieval_golden_set.json`.
  Those already cover, and remain the source of truth for, agent routing/status and
  structured-fact/policy/graph retrieval quality for these same five claims. This dataset builds
  one level higher: what a human-reviewable **brief** for each claim should say, which neither
  existing golden set addresses (both are evidence/routing-only; final-answer generation does not
  consume them).
- Expert-validated. Every field was drafted by inspecting `data/*.json`, `documents/*.md`, and the
  two existing golden sets directly — never by running a generated brief or LLM judge and copying
  its output, and never by treating current application output as correctness. See
  [Section 5](#5-review-checklist-per-case) for what a human reviewer still needs to confirm.

## 2. Schema

Defined in [evals/golden_reference_models.py](../evals/golden_reference_models.py) (pydantic,
`schema_version = "golden_reference.v1"`). Each case has:

| Field | Purpose |
|---|---|
| `reference_case_id`, `claim_id` | Stable identifiers |
| `scenario_title`, `scenario_description` | Human-readable summary |
| `required_findings` | Typed list — see below |
| `missing_information` | What is genuinely absent from the synthetic dataset for this claim |
| `required_uncertainty_or_qualifications` | Hedges a correct brief must include |
| `verification_or_human_review_actions` | What a reviewer/associate should do next |
| `unsupported_or_prohibited_conclusions` | What a brief must **never** say (required, non-empty) |
| `known_ambiguities_or_questions_for_human_review` | Open questions no source resolves |
| `reference_origin` | Always `"AI-drafted"` |
| `review_status` | Always `"pending_human_review"` until a human changes it |
| `source_fingerprint` | `path@sha256:<12-hex>` per cited source file — recomputed and compared by a test (drift detection) |
| `review_notes` | `null` until a reviewer writes something. May legitimately hold an open question or a partial-review observation while `review_status` is still `pending_human_review` — notes and status are independent fields; presence of notes is never itself treated as "reviewed" |

Each `RequiredFinding` carries:

- **`category`** — one of the three kinds the task asked for, kept as a tag rather than three
  separate lists so it survives filtering/export:
  - `SOURCE_DERIVED` (A) — from `data/*.json` facts and `documents/*.md` policy text.
  - `DETERMINISTIC_SYSTEM_BEHAVIOR` (B) — from this codebase's own routing rules
    (`agents/routing.py`, `skills/investigate_claim.py`).
  - `QUALITATIVE_HUMAN_JUDGMENT` (C) — requires a person to read prose and judge it.
- **`classification`** — how (if at all) a future evaluator could check it:
  - `DETERMINISTICALLY_CHECKABLE` — requires a `deterministic_check` naming an exact
    `source`/`field_path`/`comparator`/`expected_value` on a real typed object this codebase
    already produces (`context.models.EvidencePackage` or `agents.state.AgentResult`). The schema
    rejects a `DETERMINISTICALLY_CHECKABLE` finding with no check spec.
  - `QUALITATIVE_HUMAN_REVIEW` — a person reads the brief and judges it; never approximated with a
    keyword/substring match against generated prose.
  - `NOT_CURRENTLY_OBSERVABLE` — not used in this dataset (all five cases turned out to have
    either a real field to check or a genuine human-judgment call available; see
    [Section 6](#6-ambiguities-and-conflicts-found)).
- `supporting_record_ids` / `supporting_policy_section_ids` — every entry is checked by a test
  against the real synthetic dataset / real document headers.

## 3. The five cases, at a glance

| Claim | Denial reason | Central distinction this case tests | Terminal agent status |
|---|---|---|---|
| `CLM-1001` | `AUTH_REQUIRED` | AUTH_REQUIRED = no *matching, currently-effective* authorization on file — never "never authorized at any point" (PA-4) | `EVIDENCE_SUFFICIENT` |
| `CLM-1002` | *(paid)* | Two authorization records (one expired, one applicable) must both be reported; billed-vs-allowed gap is explicitly unexplainable from available data | `EVIDENCE_SUFFICIENT` |
| `CLM-1003` | `SERVICE_NOT_COVERED` | A **present, authoritative** "not covered" benefit record — must never read as "coverage unknown" | `EVIDENCE_SUFFICIENT` |
| `CLM-1004` | `OUT_OF_NETWORK_PROVIDER` | Out-of-network provider is a **real, resolved** record with a negative network fact — never "unidentified" or "unverifiable" | `EVIDENCE_SUFFICIENT` |
| `CLM-1005` | *(none recorded)* | Multiple **genuine absences** (no denial code, no benefit record, no provider record) — must never be filled in with a guessed conclusion in either direction | `NEEDS_REVIEW` |

Full findings, citations, and prohibited conclusions for each case are in
[evals/investigation_golden_reference_set.json](../evals/investigation_golden_reference_set.json).

## 4. How this dataset was drafted

Source material read directly (never generated output):

- `data/claims.json`, `data/members.json`, `data/benefits.json`, `data/providers.json`,
  `data/prior_authorizations.json` — the full synthetic fixture set.
- `documents/claims_denial_guide.md`, `prior_authorization_policy.md`, `benefits_guide.md`,
  `provider_network_policy.md`, `imaging_policy.md`, `appeals_guide.md` — every section header and
  its full text.
- `evals/agent_golden_set.json` — existing, independently-authored terminal-status/routing
  expectations for these same five claims; reused directly for each case's
  `DETERMINISTIC_SYSTEM_BEHAVIOR` finding.
- `evals/hybrid_retrieval_golden_set.json` — existing, independently-authored structured-fact,
  policy-section, and graph-relationship expectations; reused directly for each case's
  `SOURCE_DERIVED` findings and policy citations. No conflict was found between this file, the raw
  `data/*.json`, and the drafted references.
- `application/workbench.py` (`CASE_IDS`, `case_catalog`) and `app.py`
  (`_render_predefined_claims_tab`) — read directly to confirm the five claim IDs are exactly
  `CLM-1001`–`CLM-1005`, matching both `data/claims.json` and both existing golden sets.

Each `DeterministicCheckSpec` was independently verified to actually pass against this codebase's
real, offline `build_evidence_package`/`run_case_agent` output (see
[`tests/test_investigation_golden_reference_set.py::test_deterministic_checks_pass_against_real_offline_output`](../tests/test_investigation_golden_reference_set.py))
— this both catches drafting mistakes and confirms the cited field paths are real, but it is
**not** what makes an expectation correct: correctness comes from the source data and policy
documents, and this check is a consistency guard, not the definition of ground truth.

## 5. Review checklist per case

A human reviewer changing `review_status` away from `pending_human_review` should confirm, per
case:

- [ ] Every `required_findings` statement is actually true of the cited `data/*.json` record(s).
- [ ] Every `supporting_policy_section_ids` entry is the *most* relevant section, not just *a*
      relevant one.
- [ ] `unsupported_or_prohibited_conclusions` covers every wrong conclusion a reviewer has
      actually seen a generated brief make (once a generation layer exists to check against).
- [ ] `known_ambiguities_or_questions_for_human_review` is still accurate — a future schema or
      data change could resolve one of these.
- [ ] For `CLM-1005` specifically: confirm no version of the dataset ever backfills
      `denial_reason_code`, a benefit record, or a `PRV-9999` provider record in a way that would
      silently invalidate this case's entire premise (multiple genuine absences).
- [ ] `source_fingerprint` still matches (a failing
      `test_source_fingerprints_detect_drift_from_current_source_files` run means the underlying
      source changed since this review and the case needs re-reading, not a silent hash bump).

A reviewer partway through this checklist, or one who only wants to flag a question or a partial
observation without finishing the review, may write it into `review_notes` while leaving
`review_status` at `pending_human_review` — the two fields are independent, and notes alone are
never treated as "reviewed." Set `review_status` to `human_reviewed_accepted` or
`human_reviewed_needs_revision` only once the checklist above is actually complete — nothing in
this codebase does that automatically, and no session has done it here.

## 6. Ambiguities and conflicts found

- No conflict was found between `evals/agent_golden_set.json`, `evals/hybrid_retrieval_golden_set.json`,
  and the raw `data/*.json`/`documents/*.md` source for any of the five claims — all three agree.
- `CLM-1002`'s billed-vs-allowed amount ($1,800.00 vs $1,200.00) has no pricing/fee-schedule data
  anywhere in this synthetic dataset to explain the $600.00 difference. This is recorded as
  `missing_information` and an explicit prohibited conclusion ("must not invent a specific
  contractual or fee-schedule reason"), rather than silently omitted or guessed at.
- Every finding in this dataset ended up classified as either `DETERMINISTICALLY_CHECKABLE` (with
  a real field on `EvidencePackage`/`AgentResult`) or `QUALITATIVE_HUMAN_REVIEW`. None needed
  `NOT_CURRENTLY_OBSERVABLE` — every fact a correct brief should surface for these five claims is
  either a resolvable structured/graph fact or a legitimate prose/tone judgment call, not something
  this prototype lacks the capability to observe at all.
- `CLM-1005`'s `AgentResult.missing_information` check is deliberately split into two findings
  rather than one, after review: `f6` is a **structural-only** `DETERMINISTICALLY_CHECKABLE` check
  (`length_greater_than` 0 — the list is non-empty) — this alone says nothing about whether the
  list's entries are about the right things. `f7` is a separate `QUALITATIVE_HUMAN_REVIEW` finding
  listing the actual source-supported omissions this case has (no benefit record for
  `PLAN-BRONZE`+`SPECIALIST-VISIT`; no provider record for `PRV-9999`) that a reviewer must confirm
  the list substantively covers by reading its content — never by a keyword/exact-string match, and
  never folded into `f6`'s pass/fail. Its exact string contents were not independently re-derived
  from source, so asserting an exact string match would have claimed something this drafting
  process didn't verify.
- **Module 6 correction:** `CLM-1001`'s "new PA request before appeal" recommendation was
  originally phrased too categorically ("the next step is submitting a new prior-authorization
  request, not proceeding directly to a formal appeal") relative to the sources it cited — `PA-5`
  says a missing authorization "can **sometimes** be resolved" this way, and `APL-5` says a new
  request "**may** be faster" and frames an appeal-first path as appropriate when the *underlying
  determination* (not just missing paperwork) is what's being disputed. Both sources hedge; the
  original reference didn't. Reworded to match that conditional language, and an explicit
  prohibited-conclusion entry was added against overclaiming this as a guaranteed/required step.
  `review_status` was left at `pending_human_review` throughout — this correction is itself
  AI-drafted and still needs a human reviewer, exactly like the rest of the case; `review_notes`
  on the case documents the change and its reasoning. Confirmed (2026-09-25) that a cached
  evaluation computed against the old wording is correctly detected as stale against the new
  reference content via `EvaluationRun.is_stale_for` — see
  [docs/V2_GOLDEN_EVALUATION_UI.md](V2_GOLDEN_EVALUATION_UI.md) and
  `tests/test_golden_reference_evaluator.py::test_is_stale_for_detects_reference_content_change`
  for the general mechanism this specific edit relies on.

## 7. Module 3 reporting follow-up (carried over from this module's Step 1)

Before drafting this dataset, this module closed a gap identified in Module 3's own dispute-review
work: `decided_at`/`amended_at` timestamps on `AuthorizationSourceRecord` versions were computed
but never surfaced as citable evidence for the "provider corrected after denial" dispute scenario.
This is fully described in
[docs/V2_DISPUTE_SCENARIOS.md, §Chronology and evidence provenance](V2_DISPUTE_SCENARIOS.md), and
is unrelated to this golden reference dataset (that scenario is a dispute-review case, not one of
the five original predefined claims this document covers) — noted here only because the fix's own
re-verification changed the full-suite count referenced by that document from 767 to 768 passed,
and because Module 4's own Step 1 instruction asked for that finding to be reported explicitly
(see the final Module 4 report for the full statement).

**Review follow-up correction:** that first fix was still incomplete. `decided_at`/`amended_at`
are AUTHORIZATION-decision timestamps (when the authorization-source system decided/amended an
authorization record) — they are not, and were never, a CLAIM-denial timestamp. Neither
`dispute_review.models.ClaimSnapshot` nor `tools.models.Claim` (`data/claims.json`) has any
denial-decision timestamp field at all; the claim's only recorded timing fact is
`date_of_service` (2026-02-10 for CLM-1001). Treating the authorization's `decided_at` as if it
established "after denial" would have conflated the two kinds of event. This is now corrected: a
new, explicitly-labeled, isolated fixture —
[dispute_review/scenario_synthetic_claim_decision_events.py](../dispute_review/scenario_synthetic_claim_decision_events.py)
/ [data/scenario_synthetic_claim_decision_events.json](../data/scenario_synthetic_claim_decision_events.json)
— supplies a scenario-only SYNTHETIC claim-decision timestamp for CLM-1001
(`2026-02-11T12:00:00Z`, one day after the real date of service), structurally separate from
`ClaimSnapshot`/`data/claims.json` and impossible to mark non-synthetic (`is_synthetic` is
validator-enforced `True`). `context/dispute_evidence_retriever.py`'s
`gather_authorization_source_evidence` now surfaces this as its own citable
`SCENARIO_ONLY_SYNTHETIC_EVENT` evidence reference and reports, from an actual timestamp
comparison, that it precedes `AUTHSRC-SCEN-A-2`'s `amended_at` (`2026-02-20T09:00:00Z`) — while
its limitation text still states plainly that this is an invented anchor, never the claim's real,
recorded denial timing (which remains genuinely unknown). See
[docs/V2_DISPUTE_SCENARIOS.md](V2_DISPUTE_SCENARIOS.md) for the corresponding scenario-doc update.

## 8. Tests

[tests/test_investigation_golden_reference_set.py](../tests/test_investigation_golden_reference_set.py)
— 19 tests: schema validation (including two deliberate-failure tests confirming the schema
rejects a `pending_human_review` case with notes, and a `DETERMINISTICALLY_CHECKABLE` finding with
no check spec), five-unique-claims, metadata/review-status discipline, non-empty
prohibited-conclusions/findings, record-id and policy-section resolvability against the real
synthetic dataset and real document headers, deterministic-check correctness against real offline
`build_evidence_package`/`run_case_agent` output, and source-fingerprint drift detection. All run
fully offline (`OPENAI_API_KEY=""`/`LLM_MODEL=""`), consistent with this project's Rule 10 (no live
API calls in the default test run).

Actual results, Module 4 drafting (2026-09-25, this project's own `.venv`, `OPENAI_API_KEY=""` `LLM_MODEL=""`):

| Check | Command | Result |
|---|---|---|
| New golden-reference tests | `pytest tests/test_investigation_golden_reference_set.py -q` | **19 passed** |
| Full offline suite | `pytest tests/ -q` | **787 passed**, 0 failed (768 baseline going into this module + 19 new) |
| Dependency conflicts | `pip check` | `No broken requirements found.` |

No paid or live model call was made anywhere in this module.

Actual results, Module 4 review follow-up (2026-09-25, same `.venv`/env, focused tests plus a full
re-run):

| Check | Command | Result |
|---|---|---|
| Golden-reference tests (updated: notes-on-pending-case, CLM-1005 f6/f7 split) | `pytest tests/test_investigation_golden_reference_set.py -q` | **20 passed** |
| Authorization-source integration tests (updated + 1 new) | `pytest tests/test_dispute_authorization_source_integration.py -q` | **27 passed** |
| Scenario-only synthetic claim-decision event tests (new module) | `pytest tests/test_scenario_synthetic_claim_decision_events.py -q` | **9 passed** |
| Dispute Review UI suite (unmodified behavior, re-run for regressions) | `pytest tests/test_dispute_review_ui.py -q` | **62 passed** |
| Dispute evidence retriever suite (re-run for regressions) | `pytest tests/test_dispute_evidence_retriever.py -q` | included in the 135-test focused run below |
| Focused run (all five files above together) | — | **135 passed** |
| Full offline suite | `pytest tests/ -q` | **798 passed**, 0 failed (787 going into this follow-up + 11 new: 1 golden-reference + 1 integration + 9 scenario-event) |

No paid or live model call was made anywhere in this review follow-up either.
