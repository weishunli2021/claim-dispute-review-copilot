> **HISTORICAL — retired scenario snapshot.** This Module 6 readiness snapshot describes the
> earlier AUTHORIZATION-DISPUTE scenario(s), test counts, and live-check history that predate the
> billing-correction scenario rewrite. Kept for historical reference only. See
> docs/BILLING_CORRECTION_SCENARIO.md for the current scenario and its own verification record.

# V2 Demo Readiness — Module 6

## 1. Features completed

- **Navigation:** Dispute Review first, Golden Dataset & Evaluation second, Scenario Lab hidden
  by default (implementation and tests preserved — `SHOW_SCENARIO_LAB_TAB=1` re-enables it for
  dev/test use only; confirmed absent from `.env`/`.env.example` and every launch path).
- **Dispute Review:** three original deterministic presets (Matching fields, Different servicing
  provider, Incomplete submission) plus two source-backed scenarios (Provider corrected after
  denial; Approved authorization — service date outside authorized period), each with a
  clearly-labeled, isolated synthetic-source evidence layer kept separate from the submission
  comparison. Scenario A's chronology is anchored to an explicitly-labeled, invented (never real)
  claim-decision timestamp, shown alongside both authorization-source versions. Optional AI
  investigation and optional judge remain separate, opt-in, one-model-call-at-most actions.
- **Golden Dataset & Evaluation:** all five original claims, each with an AI-drafted,
  pending-human-review reference; a small deterministic evaluator
  (`evals/golden_reference_evaluator.py`) that checks completed runs against that reference,
  reports PASSED/FAILED/NOT_EVALUATED per check, and an overall status that is never PASSED while
  the reference is pending or qualitative findings remain unreviewed.
- **Module 6 fix:** a cached evaluation's source-fingerprint freshness is now re-checked on every
  render, not just frozen at the moment it was computed — closes a real gap where source data
  changing after an evaluation was cached could otherwise keep looking "fresh" (see §3).
- **Module 6 correction:** CLM-1001's "new PA request before appeal" reference action was
  reworded from a categorical claim to the source's own hedged language (PA-5/APL-5 both say
  "can sometimes"/"may", never "will"); documented in the case's own `review_notes`, review status
  left `pending_human_review`.

## 2. Actual offline checks and their coverage

| Command | Result | Scope |
|---|---|---|
| `pytest tests/ -q` | **846 passed**, 0 failed | Everything: original pipeline, dispute review, golden dataset/evaluator, navigation |
| `python -m evals.e2e_eval` | **8 of 8** passed | **Original investigation pipeline ONLY** (CLM-1001–1005, CLM-9999) — does NOT exercise Dispute Review, the two new source scenarios, or Golden Dataset & Evaluation |
| `python -m evals.e2e_safety_eval` | **9 of 9** passed | Same scope as above — original pipeline only. `S08` (stale-UI-state) exercises `application/workbench.py`'s reset logic, which the Golden Dataset & Evaluation tab reuses, but does not exercise Dispute Review or the golden evaluator's own (separately, exhaustively pytest-tested) staleness logic. |
| `pip check` | not re-run this module | No dependency/environment changes were made |

**No dedicated offline E2E/safety eval script exists for Dispute Review or Golden Dataset &
Evaluation** — a full repository search (`ls evals/*.py`) confirms only `e2e_eval.py`/
`e2e_safety_eval.py` exist, both scoped to the original pipeline as shown above. Dispute Review and
Golden Dataset & Evaluation coverage lives entirely in `tests/` (pytest), not in `evals/`. **The
8/8 and 9/9 above are not evidence of dispute-scenario or golden-evaluation correctness** — that
evidence is the pytest suite counts in §7.

## 3. UI verification method

**Fake-adapter (offline) verification only, via Streamlit's `AppTest` framework — no live model
call was made anywhere in this module's testing.** Every generation call goes through a fake
`openai.OpenAI` client (`tests/conftest.py`'s autouse fixture also default-denies any real
`openai.OpenAI(...)` construction unless a test explicitly overrides it).

Verified this module (citations to existing tests where already sufficient; new tests only where a
real gap was found):

- Dispute Review opens first, Golden Dataset & Evaluation second, Scenario Lab absent and provably
  never invoked — `tests/test_app_navigation.py` (all 7 tests).
- Three original presets work — `tests/test_dispute_review_ui.py::test_matching_preset_...`,
  `test_different_servicing_provider_preset_...`, `test_incomplete_preset_...` (pre-existing).
- Both source-backed scenarios work, including gate/generation status —
  `test_scenario_a_investigation_renders_authorization_source_section`,
  `test_scenario_b_investigation_shows_validity_dates_mismatch` (pre-existing).
- **New:** Scenario A's UI actually shows both authorization-source versions AND the explicit
  synthetic-chronology warning text (not just present in the underlying evidence package) —
  `test_scenario_a_ui_shows_both_versions_and_explicit_synthetic_chronology` (added this module;
  this was a real coverage gap — the feature worked, but nothing previously proved it reached the
  rendered page).
- Source comparison stays separate from submission comparison — structurally enforced
  (`DisputeComparisonResult` vs. `DisputeSourceComparisonResult`, distinct reference-id prefixes
  and provenance categories), exercised throughout `tests/test_dispute_review_ui.py`.
- Golden Dataset & Evaluation supports all five claims, with expected-results sections rendering
  for each — `tests/test_app_golden_evaluation_ui.py::test_all_five_cases_selectable_...`
  (pre-existing).
- Expected references never enter the generation prompt —
  `test_golden_reference_content_never_appears_in_the_built_prompt` (pre-existing, spies on the
  real adapter call).
- Investigation / deterministic evaluation / optional judge stay separate — structurally three
  distinct types and three distinct session-state keys (`investigation_result`, `golden_eval_result`,
  `judge_result` on Golden Dataset & Evaluation; the fully separate `dispute_review_*`-prefixed
  keys, including `dispute_review_judge_envelope`, on Dispute Review). No code path merges them.
- Changing inputs clears stale downstream results — exhaustively tested per-field for Dispute
  Review (`tests/test_dispute_review_ui.py`, ~10 dedicated tests) and for case-change/rerun on
  Golden Dataset & Evaluation (`tests/test_app_golden_evaluation_ui.py`, pre-existing).
- **New (Module 6, the source-staleness gap — see §4):** a cached evaluation is invalidated on the
  very next render if the underlying source data changed, even with no new click —
  `test_source_data_change_after_evaluation_invalidates_it_on_next_render`
  (`tests/test_app_golden_evaluation_ui.py`) plus the pure-logic pair
  `test_is_evaluation_still_fresh_true_when_nothing_changed` /
  `test_is_evaluation_still_fresh_false_after_source_file_changes_post_evaluation`
  (`tests/test_golden_reference_evaluator.py`).

## 4. The source-staleness gap: what was found and fixed

**Found:** `EvaluationRun.source_fingerprint_stale` was computed once, at the moment
`evaluate_investigation_result` ran, and frozen into the stored object. `EvaluationRun.is_stale_for`
(the existing invalidation check) only detects a *different* case/run/reference-content — it does
not detect the underlying source *data files* changing after an evaluation was already cached. A
user could evaluate a case (source fresh), then — in a long-running session — have the underlying
source data edited, and the previously-cached evaluation would keep displaying its
now-stale-but-frozen "fresh" state until the user happened to click Evaluate again.

**Fixed with the smallest change that closes it:** a new function,
`evals.golden_reference_evaluator.is_evaluation_still_fresh(evaluation, reference)`, re-derives
current source-fingerprint freshness live and compares it against what the cached `EvaluationRun`
recorded. `app.py`'s `_render_evaluation_section` now calls this on every render of an
*already-cached* evaluation (skipped only on the render that just computed it, since that result is
guaranteed fresh at that instant) and clears the cache if the live check disagrees.

**Verified without ever mutating a real fixture file:** every test above uses either a `tmp_path`
throwaway copy of a data file, or a monkeypatched `check_source_fingerprint_freshness` — real
`data/*.json` files were never touched.

## 5. Reference and qualitative-review status

All five references remain `reference_origin: "AI-drafted"`, `review_status:
"pending_human_review"` — confirmed by direct inspection after every edit this module made.
**23 deterministic checks pass** across the five claims (5+4+4+4+6); **8 qualitative findings
remain pending** (1+2+1+2+2) — both counts independently recomputed this module and unchanged from
Module 5's report (the CLM-1001 wording correction changed only prose in `verification_or_
human_review_actions`/`unsupported_or_prohibited_conclusions`, not any finding's classification or
count). A stale prior evaluation computed against CLM-1001's pre-correction wording is confirmed
detected as stale against the corrected reference (direct check, 2026-09-25 — see
`docs/V2_GOLDEN_REFERENCE_REVIEW.md`, "Module 6 correction").

## 6. Live-model checks still outstanding

**No live/paid model call was made in this module, or in Module 5, for any v2 dispute or golden-
evaluation path.** Dispute Review's original two AI actions were live-tested in earlier
(pre-v2/v1-era) work per `docs/DISPUTE_AI_VALIDATION.md` — but that record predates the two new
source-backed scenarios (Module 3), the scenario-only synthetic chronology event (Module 4 review
follow-up), and the entire Golden Dataset & Evaluation tab (Module 5). **None of those newer v2
paths have ever been exercised against a real model.** This is the single largest remaining gap
before calling this prototype demo-ready end to end.

### Manual live-smoke steps (perform these yourself — do not delegate to an automated run)

1. **Configure credentials.** Copy `.env.example` to `.env` if you haven't already, and set a real
   `OPENAI_API_KEY` and `LLM_MODEL` — use this project's existing convention
   (`application/config.py` reads these; never paste a key into chat, a commit, or this
   documentation).
2. **Launch using this project's own v2 virtual environment** (see §8 for the exact command).
3. **Generate one brief for each new dispute scenario:**
   - Dispute Review tab → **Provider corrected after denial** → **Compare submitted information**
     → **Investigate dispute evidence**.
   - Repeat with **Approved authorization — service date outside authorized period**.
4. **Inspect, for each:** the brief's citations (do they reference real evidence ids?), the
   chronology/version-history section (does the synthetic-event label still read correctly with
   real model output layered on top?), any stated uncertainty/qualification language, and the
   deterministic **Validation** result (Passed/Failed, and which rule if failed).
5. **Run the optional judge** on one of the two generated briefs — click **Run AI semantic
   evaluation** — and record its four dimension scores/verdicts and overall result.
6. **Run one golden investigation with the real model:** Golden Dataset & Evaluation tab → pick
   any claim → **Investigate Claim** (this now calls the real, configured model) → **Evaluate Run
   Against Golden Reference**. Confirm the deterministic checks still pass against real (not fake)
   generation/evidence output, and that the overall status still correctly shows **NEEDS HUMAN
   REVIEW**.

### What to record for each run above

- Scenario/claim and which action was clicked (e.g. "Scenario A, Investigate dispute evidence").
- A wall-clock timestamp as the practical run identifier — `run_id` (a UUID) is generated
  internally per run (`ApplicationResult.run_id` / `DisputeWorkflowResult.run_id`) but is **not**
  currently displayed anywhere in the UI; a timestamp plus the scenario name is the practical
  substitute unless you inspect `st.session_state` directly.
- Generation status, Validation status, and (if run) Judge status/verdict, exactly as shown in the
  UI.
- The specific findings/citations the brief produced, and whether they look evidence-grounded.
- Any error message or any conclusion the brief stated that looks unsupported or overclaimed —
  record it verbatim; do not paraphrase away a real problem.

**Until these steps are actually performed and their results recorded, live-model behavior for
every v2-specific dispute/golden-evaluation path remains explicitly unverified — this status must
not be silently upgraded by a future session without actually running the steps above.**

## 7. Actual verification commands and results (this module)

Timestamps and counts, 2026-09-25, this project's own `.venv`, `OPENAI_API_KEY=""` `LLM_MODEL=""`:

| Check | Command | Result |
|---|---|---|
| New/updated golden-reference & evaluator tests | `pytest tests/test_golden_reference_evaluator.py tests/test_investigation_golden_reference_set.py -q` | **50 passed** |
| Golden evaluation UI tests | `pytest tests/test_app_golden_evaluation_ui.py -q` | **10 passed** |
| Navigation tests | `pytest tests/test_app_navigation.py -q` | **7 passed** |
| Dispute Review UI tests (incl. new Scenario A UI test) | `pytest tests/test_dispute_review_ui.py -q` | **63 passed** |
| Full offline suite | `pytest tests/ -q` | **846 passed**, 0 failed (842 going into this module + 4 new: 1 Scenario A UI test + 2 evaluator staleness tests + 1 golden-eval-UI staleness test) |
| Original-pipeline E2E eval | `python -m evals.e2e_eval` | **8 of 8** (original pipeline only — see §2) |
| Original-pipeline safety eval | `python -m evals.e2e_safety_eval` | **9 of 9** (original pipeline only — see §2) |

No paid or live model call was made anywhere in this module.

## 8. Exact local launch command

Using this project's own v2 `.venv` explicitly, on a port unlikely to collide with anything else
running locally:

```powershell
D:\AI\claude-code\claim-dispute-review-copilot-v2\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8502 --server.headless=true
```

Then open `http://localhost:8502` in a browser. Omit `--server.headless=true` if you want
Streamlit to open the browser tab for you. `--server.port 8502` is arbitrary but avoids the
default `8501` in case another Streamlit instance (e.g. the original v1 project) is already
running on it locally — use any free port.

No `OPENAI_API_KEY`/`LLM_MODEL` is required for Dispute Review's deterministic comparison, or for
browsing Golden Dataset & Evaluation's expected-results sections — only for the "Investigate..."
and judge buttons.

## 9. Known limitations / blockers

Carried over, still accurate, from `README.md`'s existing "Known limitations" section (retrieval
not exhaustive at 63.33% policy-section recall, Scenario Lab single-user, Rule D/F pattern-only,
validator not a hallucination detector, judge uncalibrated, Dispute Review single-claim/
exact-match) — none of that changed this module. New/updated for v2:

- **Live-model behavior for every v2-specific path (two new dispute scenarios, the synthetic
  chronology event, all of Golden Dataset & Evaluation) is unverified** — see §6. This is the
  primary outstanding blocker before this prototype can be called demo-ready against a real model.
- **`run_id` is not shown anywhere in the UI** — a minor usability gap for live-smoke record-
  keeping (see §6), not fixed here since doing so would be a UI feature addition outside this
  module's explicit no-new-features scope.
- Golden Dataset & Evaluation's deterministic evaluator checks structured fields only — it
  provides no signal at all about generated-brief prose quality; this is by design, not a gap, but
  worth restating to avoid the "23/23 passing" number being misread as brief accuracy.

## 10. Files changed in Module 6

- [app.py](../app.py) — source-staleness re-check wired into `_render_evaluation_section`.
- [evals/golden_reference_evaluator.py](../evals/golden_reference_evaluator.py) — added
  `is_evaluation_still_fresh`.
- [evals/investigation_golden_reference_set.json](../evals/investigation_golden_reference_set.json)
  — CLM-1001 wording correction + `review_notes` (content only; `review_status` unchanged).
- [tests/test_golden_reference_evaluator.py](../tests/test_golden_reference_evaluator.py) — 2 new
  tests for the staleness fix.
- [tests/test_app_golden_evaluation_ui.py](../tests/test_app_golden_evaluation_ui.py) — 1 new test
  for the staleness fix at the UI level.
- [tests/test_investigation_golden_reference_set.py](../tests/test_investigation_golden_reference_set.py)
  — updated one test to reflect CLM-1001's new `review_notes` (was asserting all-empty).
- [tests/test_dispute_review_ui.py](../tests/test_dispute_review_ui.py) — 1 new test closing the
  Scenario A UI-rendering coverage gap.
- [docs/V2_GOLDEN_REFERENCE_REVIEW.md](V2_GOLDEN_REFERENCE_REVIEW.md) — documented the CLM-1001
  correction.
- [docs/V2_INTERVIEW_DEMO.md](V2_INTERVIEW_DEMO.md) — new.
- [docs/V2_DEMO_READINESS.md](V2_DEMO_READINESS.md) — new (this file).
- [README.md](../README.md) — updated to describe the current two-tab default navigation, the two
  new dispute scenarios, and Golden Dataset & Evaluation (see README's own changelog note at the
  top of the relevant section for exactly what changed).

No remote configured, nothing pushed, nothing deployed, no commit made — v2 remains an
uncommitted, local-only working copy exactly as it has been since Module 1.
