# V2 Golden Dataset & Evaluation UI — Module 5

## 1. What this is

Module 5 adds a "Golden Dataset & Evaluation" tab that replaces "Predefined Claims" as the
interview-default surface for the five original claims, pairs it with "Dispute Review" as the
first tab, and hides "Scenario Lab" from the default navigation (implementation and tests
preserved, not deleted). It also adds a small, deterministic evaluator that checks one completed
investigation run against the Module 4 golden reference dataset
([evals/investigation_golden_reference_set.json](../evals/investigation_golden_reference_set.json))
— never a review-approval workflow, never a second generation path.

**All five references remain `reference_origin: AI-drafted`, `review_status: pending_human_review`.
Nothing in this module changed that, and nothing here marks a case reviewed.**

## 2. UI flow

```
Tabs: [Dispute Review]  [Golden Dataset & Evaluation]        (Scenario Lab hidden by default)

Golden Dataset & Evaluation tab:
  1. Case Selection          -- st.selectbox over the 5 claims, labeled with each
                                 reference's scenario_title (application/workbench.py's
                                 CASE_IDS/on_case_selected, reused exactly as Predefined
                                 Claims used them)
  2. Recorded Claim Information -- unchanged from the old Predefined Claims tab
  3. Expected Results (Golden Reference) -- read-only, from the reference JSON only:
       - Required findings (tagged [classification])
       - Missing information & required uncertainty
       - Verification / human-review actions
       - Unsupported / prohibited conclusions
       - Known ambiguities (if any)
       - A visible "reference_origin / review_status" banner
  4. Run Investigation & Evaluation ("Investigate Claim" button) -- calls
       application.investigation_service.run_investigation(claim_id, query) exactly as
       before; renders via the SAME _render_investigation_result shared by Dispute Review.
       The SAME click also automatically runs
       evals.golden_reference_evaluator.evaluate_investigation_result(result, reference)
       AFTER generation/validation have finished -- there is no separate "Evaluate" button
       or click any more (user feedback, 2026-09-26); the comparison table renders directly
       below the investigation result.
```

### Component / evaluator paths

| Concern | File |
|---|---|
| Schema + loader | [evals/golden_reference_models.py](../evals/golden_reference_models.py) (`load_golden_reference_set`) |
| Reference data | [evals/investigation_golden_reference_set.json](../evals/investigation_golden_reference_set.json) (unchanged this module) |
| Evaluator | [evals/golden_reference_evaluator.py](../evals/golden_reference_evaluator.py) |
| UI (tab rendering, evaluation-section rendering) | [app.py](../app.py) (`_render_golden_evaluation_tab`, `_render_expected_results`, `_render_evaluation_section`) |
| Navigation / hiding Scenario Lab | [app.py](../app.py) (`_SHOW_SCENARIO_LAB_TAB`, top-level `st.tabs(...)` block) |

Expected results are read only by `_render_expected_results` (display) and by
`evaluate_investigation_result` (comparison, called only after an explicit "Evaluate" click). They
are never passed to `run_investigation`, `assemble_context`, `build_prompt`, or any adapter —
confirmed by
[`tests/test_app_golden_evaluation_ui.py::test_golden_reference_content_never_appears_in_the_built_prompt`](../tests/test_app_golden_evaluation_ui.py),
which spies on the real `OpenAIInvestigationBriefAdapter.generate` call and asserts a
reference-only sentence fragment never appears in the built prompt.

## 3. Implemented check operators and their scope

`evals/golden_reference_evaluator.py` implements exactly the operators
`evals/golden_reference_models.py`'s `DeterministicCheckSpec.comparator` Literal defines — no
more:

| Comparator | Meaning |
|---|---|
| `equals` | `actual == expected_value` |
| `is_true` / `is_false` | `actual is True` / `actual is False` |
| `is_none` / `is_not_none` | `actual is None` / `actual is not None` |
| `length_equals` | `len(actual) == expected_value` |
| `length_greater_than` | `len(actual) > expected_value` |

Two sources are supported, matching every spec in the current dataset: `context.models.
EvidencePackage` (read from `ApplicationResult.evidence_package`) and `agents.state.AgentResult`
(read from `ApplicationResult.agent_result`). An unrecognized `source` or `field_path`, or a
comparator applied to a non-sized value, reports `NOT_EVALUATED` with an explicit reason string —
never a silent pass, never `eval()`, never keyword/substring matching against generated text.

**Scope statement, exactly as required:** a `PASSED` deterministic check confirms one structured
field on `EvidencePackage`/`AgentResult`. **It does not prove the generated brief expressed that
finding in its own words.** The UI states this explicitly next to the deterministic-checks
expander, and `CLM-1005`'s `f6`/`f7` split makes the distinction concrete: `f6` is a
`DETERMINISTICALLY_CHECKABLE` structural check (`missing_information` is non-empty); `f7` is a
separate `QUALITATIVE_HUMAN_REVIEW` finding for whether that list's *content* actually covers the
two source-supported omissions — never folded into one pass/fail.

### Missing/unavailable vs. present-null

`_resolve_field_path` returns `(value, available)`. `available=False` whenever the root object is
`None`, an intermediate attribute is `None` (so the next `getattr` is impossible), or an attribute
genuinely doesn't exist. `available=True` whenever the path resolves all the way through —
**including when the final value is `None`** (a present null, a real fact, e.g.
`structured_facts.benefit is None` for CLM-1005). A comparator is only ever invoked when
`available=True`, so an unavailable field structurally cannot satisfy `is_none`/`is_not_none` by
accident. The reference's `expected_value` is never used as a fallback `actual_value` — an
unavailable field's `actual_value` is always `None` with `field_available=False`, distinct from a
legitimately-resolved `None`.

## 4. Overall-status rules

| Status | When |
|---|---|
| `NOT_RUN` | UI-only concept — shown before any evaluation exists for the current run/reference. `evaluate_investigation_result` never returns this value. |
| `FAILED` | At least one supported deterministic check actually evaluated and failed. |
| `NEEDS_HUMAN_REVIEW` | No check failed, but at least one of: a check was `NOT_EVALUATED`; any qualitative requirement exists; the reference is `pending_human_review` or `human_reviewed_needs_revision`; the source fingerprint is stale. |
| `PASSED` | Every supported check passed, AND the reference is `human_reviewed_accepted`, AND no qualitative items are pending, AND the source fingerprint is fresh. |

**This module adds no review-approval workflow and never sets `review_status`.** With the current
dataset (every case `pending_human_review`, every case has at least one qualitative finding),
`PASSED` is not reachable — confirmed by
[`tests/test_app_golden_evaluation_ui.py::test_all_green_automated_checks_still_shows_needs_human_review`](../tests/test_app_golden_evaluation_ui.py)
and its pure-logic counterpart in `tests/test_golden_reference_evaluator.py`. The automated-check
summary ("N of M automated checks passed") and the overall status are always shown separately —
an all-green automated summary next to a `NEEDS HUMAN REVIEW` overall status is the expected,
correct combination, not a display bug.

## 5. Reference / source / run binding and invalidation

`EvaluationRun` carries `claim_id`, `run_id` (`ApplicationResult.run_id`), `reference_case_id`,
and `reference_content_fingerprint` (a sha256 of the reference case's own JSON — changes if the
reference is edited, independent of the underlying source files). `EvaluationRun.is_stale_for(result,
reference)` returns `True` if any of these no longer match the current case/run/reference. The UI
checks this on every render of the evaluation section and clears (`st.session_state.pop`) a stale
result rather than displaying it.

Separately, `check_source_fingerprint_freshness` recomputes each source file's sha256 named in the
reference's own `source_fingerprint` field and compares it against the recorded value. A mismatch
sets `EvaluationRun.source_fingerprint_stale = True`, forces `NEEDS_HUMAN_REVIEW` regardless of how
the deterministic checks went, and is displayed as an explicit "needs revalidation" error — **the
fingerprint itself is never silently rewritten, and a stale fingerprint never allows a PASSED
claim.**

Three separate invalidation triggers, each covered by a test:
- Changing the selected case (`_handle_case_change` explicitly pops the evaluation key; the
  staleness check also catches it independently via `claim_id`/`reference_case_id` mismatch).
- Rerunning "Investigate Claim" for the same case (`run_id` changes; the click handler also pops
  the key explicitly).
- Editing the reference's own content (`reference_content_fingerprint` changes).

Reference-review status (an AI-drafted case's own review lifecycle) and human approval of one
particular run's output are kept as separate concepts throughout: `EvaluationRun.
reference_review_status` records the former; nothing in this module records or implies the latter.

## 6. Remaining qualitative review requirements

Unchanged from Module 4 — every case still has at least one `QUALITATIVE_HUMAN_REVIEW` finding
(e.g. CLM-1002's billed-vs-allowed gap staying unexplained, CLM-1005's `f7` substantive-coverage
check) that no automated check in this module resolves. See
[docs/V2_GOLDEN_REFERENCE_REVIEW.md, §5](V2_GOLDEN_REFERENCE_REVIEW.md#5-review-checklist-per-case)
for the full per-case human-review checklist, which this module does not change.

## 7. Tests: what was added, and actual results

New/changed test files:

- [tests/test_golden_reference_evaluator.py](../tests/test_golden_reference_evaluator.py) (28
  tests, pure Python, no Streamlit) — correct/wrong deterministic values, every comparator,
  unavailable-field vs. present-null distinction, reference-value-never-used-as-fallback,
  unsupported source/field/comparator, all-green-but-pending-reference still
  `NEEDS_HUMAN_REVIEW`, qualitative findings force review even with an accepted reference, `PASSED`
  reachability under controlled conditions, `is_stale_for` on claim/run/reference-content changes,
  source-fingerprint drift detection.
- [tests/test_app_navigation.py](../tests/test_app_navigation.py) (7 tests) — default tab
  order/labels, Dispute Review first, `SHOW_SCENARIO_LAB_TAB=1` reveals a third tab, and two
  independent proofs that `_render_scenario_lab_tab`/`application.scenario_lab.get_or_create_draft`/
  `run_scenario_investigation` are never called when hidden (a monkeypatched raise never fires) —
  with a companion test proving the SAME raise DOES fire when the tab is shown, so the silence
  above is real, not a broken patch.
- [tests/test_app_golden_evaluation_ui.py](../tests/test_app_golden_evaluation_ui.py) (9 tests) —
  all five cases display expected results, an investigation and its evaluation render together,
  `NOT RUN` before evaluating, all-green-still-NEEDS-HUMAN-REVIEW, a FAILED example renders
  visibly (via a controlled fixture — see its docstring on why the fixture's fingerprint has to be
  real), case-change and rerun both clear a stale evaluation, and the reference-never-in-prompt
  proof.
- [tests/test_app_scenario_lab_ui.py](../tests/test_app_scenario_lab_ui.py) — updated, not
  deleted: `_fresh_app` now sets `SHOW_SCENARIO_LAB_TAB=1` so its 7 tests keep exercising the exact
  same Scenario Lab code through the exact same `app.py` entrypoint; the stale
  `["Predefined Claims", "Scenario Lab", "Dispute Review"]` label assertion and one `"Service
  code"` widget lookup (which relied on Scenario Lab rendering before Dispute Review — no longer
  true after the reorder) were fixed to be order-independent.
- [tests/test_dispute_review_ui.py](../tests/test_dispute_review_ui.py) — three pre-existing
  hardcoded `at.tabs[2]` lookups (a real latent fragility: they assumed Dispute Review was always
  the *third* tab) were replaced with a label-based lookup; two isolation tests that asserted
  `"scenario_draft" in session_state` now set `SHOW_SCENARIO_LAB_TAB=1` themselves so Scenario Lab
  actually renders for that specific assertion; one test/label updated for the new default
  navigation.

Actual results (2026-09-25, this project's own `.venv`, `OPENAI_API_KEY=""` `LLM_MODEL=""`):

| Check | Command | Result |
|---|---|---|
| Evaluator unit tests | `pytest tests/test_golden_reference_evaluator.py -q` | **28 passed** |
| Navigation tests | `pytest tests/test_app_navigation.py -q` | **7 passed** |
| Golden evaluation UI tests | `pytest tests/test_app_golden_evaluation_ui.py -q` | **9 passed** |
| Scenario Lab UI regression (re-enabled via env var) | `pytest tests/test_app_scenario_lab_ui.py -q` | **7 passed** |
| Dispute Review UI regression | `pytest tests/test_dispute_review_ui.py -q` | **62 passed** |
| Predefined-Claims-logic regression (unchanged, reused as-is) | `pytest tests/test_workbench.py -q` | **15 passed** |
| Focused run (all six files above together) | — | **128 passed** |
| Full offline suite | `pytest tests/ -q` | **842 passed**, 0 failed (798 going into this module + 44 new: 28 evaluator + 7 navigation + 9 golden-evaluation-UI) |
| Dependency conflicts | `pip check` | `No broken requirements found.` |

No paid or live model call was made anywhere in this module — every generation call in every test
above goes through a fake `openai.OpenAI` client (this suite's `tests/conftest.py` autouse fixture
also default-denies any real `openai.OpenAI(...)` construction unless a test explicitly overrides
it).

## 8. UI verification performed, and its limitations

Performed via Streamlit's `AppTest` framework with a fake `openai.OpenAI` client (same pattern as
every other UI test in this project) — **never a live model call**:

- Both tabs render without exception; default order is exactly `["Dispute Review", "Golden
  Dataset & Evaluation"]`.
- Scenario Lab is absent from that list and its rendering function is provably never invoked
  (monkeypatched raise stays silent).
- All five golden cases are selectable and each shows its expected-results sections.
- "Investigate Claim" automatically also runs the golden-reference evaluation (no separate button
  or click), with the real evaluator producing `5/5`, `4/4`, or `6/6` automated checks passed
  (per case) and overall
  `NEEDS HUMAN REVIEW` in every case, through the real (non-mocked) `run_case_agent` /
  `build_evidence_package` pipeline — only the LLM brief-generation call itself is faked.
- A controlled FAILED example renders with visible error styling.
- Changing the selected case, or rerunning the investigation, clears a previously-computed
  evaluation rather than leaving it on screen.

**Limitations:** this is fake-adapter verification of UI wiring and the deterministic evaluator's
integration — it does not verify live model generation, live judge behavior, or anything about
brief *content* quality (that remains the explicitly-scoped-out qualitative-review surface). No
real OpenAI call was made or verified anywhere in this module's testing.

## 9. Short interview demo steps

1. Open the app — **Dispute Review** is the first tab, **Golden Dataset & Evaluation** the second;
   Scenario Lab is not visible.
2. In Golden Dataset & Evaluation, pick a case (e.g. `CLM-1005 — Denial with no recorded reason
   code...`) — point out the **Expected Results** section's "AI-drafted, pending human review"
   banner and its Missing/Uncertainty/Prohibited-conclusions expanders.
3. Click **Investigate Claim** — the existing evidence → generation → validation pipeline runs
   exactly as before, and the golden-reference evaluation now runs automatically in the same
   click — show the automated-check summary (all green for every original case) next to the
   **overall status**, which stays **NEEDS HUMAN REVIEW** — the concrete "green checks ≠
   shippable" point this module is built to demonstrate.
5. Expand **Deterministic checks** to show the per-finding actual-vs-expected reasons, and
   **Qualitative requirements** to show what still needs a human.
6. Switch case (or click Investigate again) and show the evaluation panel resets to **NOT RUN**
   rather than showing a stale result.
