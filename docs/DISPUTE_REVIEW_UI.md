# Dispute Review UI (Module 4, extended in Module 6C)

This document describes the Dispute Review tab as implemented — a third tab
appended to `app.py`, focused on `CLM-1001`. Module 4 built the
deterministic comparison layer described below (unchanged since). **Module
6C adds two further optional actions on top of it** — "Investigate dispute
evidence" (the bounded AI workflow, Module 6B) and "Run AI semantic
evaluation" (the optional judge, Module 6B) — described in their own
section near the end of this document. The original Module 4 content below
is left intact rather than rewritten, since the deterministic comparison
behavior it describes is unchanged.

**Three independent actions, three independent model-call boundaries:**
"Compare submitted information" (zero model calls, always available, as
originally built in Module 4) → optional "Investigate dispute evidence"
(one model call, requires `OPENAI_API_KEY`/`LLM_MODEL`) → optional "Run AI
semantic evaluation" (one further, separate model call, same
configuration, only available after a validated brief exists).

## Files added/modified

- **New:** `dispute_review/ui.py` — the only file in `dispute_review/` that
  imports Streamlit. Session-state helpers (`ensure_initialized`,
  `clear_result`, `apply_preset`, `reset_inputs`, `run_comparison`,
  `get_display_result`) are plain functions over a `MutableMapping`
  (`st.session_state` in production, a plain `dict` in tests) — the same
  split `application/workbench.py` already established for Predefined
  Claims. `render_dispute_review_tab(state)` is the single entry point
  `app.py` calls.
- **Modified:** `app.py` — one new import
  (`from dispute_review.ui import render_dispute_review_tab`), the module
  docstring's tab count updated from two to three, and the bottom
  tab-wiring block extended from
  `st.tabs(["Predefined Claims", "Scenario Lab"])` to
  `st.tabs(["Predefined Claims", "Scenario Lab", "Dispute Review"])` plus a
  `with dispute_review_tab: render_dispute_review_tab(st.session_state)`
  block. No other line of `app.py` changed — Predefined Claims' and
  Scenario Lab's own rendering functions are byte-for-byte unchanged.
- **New:** `tests/test_dispute_review_ui.py` (25 tests).
- **Modified:** `tests/test_app_scenario_lab_ui.py` — one assertion updated
  from an exact two-tab list to the new three-tab list (the only change;
  every other test in that file is unchanged and still passes).
- **New:** `docs/DISPUTE_REVIEW_UI.md` (this file).

## UI flow

1. **Recorded Claim Information** — reads `CLM-1001` via the existing
   read-only lookup (`tools.case_context.get_case_context`, the same
   function Predefined Claims already uses) on every render. If no claim
   record is found, the tab shows a clear error and stops — no comparison
   controls are rendered and no replacement claim is invented. Displays
   claim ID, status, denial reason, member ID, service code, date of
   service, and the **servicing** provider (ID + name when resolvable).
   The **ordering** provider is shown separately, explicitly labeled
   "context only — never used as a substitute for the servicing provider
   above," and is never read by the comparison path.
2. **Synthetic Demonstration Presets** — four buttons: *Matching fields*,
   *Different servicing provider*, *Incomplete submission*, *Clear / reset*.
   Each preset button calls straight into Module 3's
   `dispute_review.presets.build_demo_presets` / the individual preset
   functions — no preset value or comparison rule is duplicated in UI
   code. Loading a preset fills the editable fields below; it does **not**
   run the comparison. A caption explains the "different servicing
   provider" preset demonstrates a provider-ID discrepancy only, not the
   alternate facility's actual ability to perform the service.
3. **New Authorization Information (Unverified)** — editable fields:
   Supplied by (Provider/Patient radio), Authorization reference number,
   Member ID, Service code, Authorization start/end date (plain
   `YYYY-MM-DD` text inputs — not `st.date_input`, so an invalid date
   string can be typed and tested directly), Servicing provider ID,
   Dispute explanation. The current live provenance label
   ("Provider-supplied—unverified" / "Patient-supplied—unverified") is
   shown directly under the radio.
4. **Compare submitted information** (one primary button) — builds a
   `dispute_review.models.DisputeSubmission` from the current field values
   and calls `dispute_review.comparison.compare_submission` exactly once;
   no comparison logic is reimplemented in `dispute_review/ui.py`. On a
   `pydantic.ValidationError` (malformed date, impossible date, reversed
   date range), a concise, field-specific error list is shown (e.g.
   `authorization_start_date: Value error, must be a valid ISO YYYY-MM-DD
   date string, got 'not-a-date'`) — never a raw traceback, and the date is
   never silently repaired.
5. **Comparison Result** — a four-row table (Field / Recorded Claim /
   Submitted Information / Status / Explanation) built directly from
   `DisputeComparisonResult.rows`; a missing value renders as `"Not
   provided"`. Below it: the deterministic `summary`, the
   `verification_guidance` list, the fixed `authenticity_disclaimer`
   ("Matching fields do not establish authenticity, authorization
   applicability, coverage, or payment."), and an explicit statement that
   the recorded denial has not changed, the existing AI investigation
   draft does not include this submission, and the result is session-local
   and not saved to any claim system. `dispute_explanation` is rendered
   with `st.text` (plain text, never `unsafe_allow_html`), so it can never
   execute as HTML/script even if it contains markup-like characters.

## Session-state behavior

Every key this tab reads or writes is prefixed `dispute_review_` (see
`dispute_review/ui.py`'s `_PREFIX`) — a namespace wholly disjoint from
Predefined Claims' keys (`selected_claim_id`, `question_text`,
`investigation_result`, `review_decision`, `judge_result`) and Scenario
Lab's `SCENARIO_*_KEY` constants. Nothing in `dispute_review/ui.py` reads,
writes, or clears any key outside that prefix — verified by
`test_dispute_review_activity_does_not_affect_predefined_claims_state` and
`test_dispute_review_activity_does_not_affect_scenario_lab_state`.

**Stale-result protection — two layers:**

1. **Proactive:** every input widget (Supplied by, Authorization
   reference, Member ID, Service code, both dates, Servicing provider ID,
   Dispute explanation) has an `on_change` callback that immediately calls
   `clear_result(state)`, wiping the stored result, its fingerprint, and
   any validation errors. Loading a preset (`apply_preset`) and
   *Clear / reset* (`reset_inputs`) do the same. **Accuracy note (per the
   task's own instruction):** Streamlit text inputs commit their value —
   and therefore fire `on_change` — on Enter or on blur (clicking/tabbing
   away), not per keystroke. An edit mid-typing, before the field is
   committed, does not yet clear a displayed result; this document does
   not claim per-keystroke invalidation, and neither does the in-app
   caption under "New Authorization Information."
2. **Render guard (belt-and-suspenders):** `get_display_result` recomputes
   a fingerprint — the claim snapshot's JSON plus every current widget
   value — and only returns the stored result if it matches the
   fingerprint captured at the moment `compare_submission` was called.
   Even in a hypothetical scenario where an `on_change` callback were
   missed, a stale result still cannot render.

No form is used (a form would batch/retain edits and delay
callbacks/reruns until submit); every widget is an ordinary widget with an
immediate `on_change` callback, per the task's explicit instruction.

No module-level/global variable, `st.cache_data`, or `st.cache_resource` is
used for submissions or results — every value lives only in the
`MutableMapping` passed in, which is real `st.session_state` in production
(already isolated per browser session by Streamlit itself) and a plain
`dict` in tests. `test_two_independent_app_sessions_do_not_share_dispute_review_state`
confirms two separate `AppTest` instances (i.e. two independent script
executions, each with its own `session_state`) never see each other's
dispute-review state.

Widget-state discipline: no code path ever assigns to
`st.session_state[<a dispute-review widget key>]` **after** that key's
widget has already been instantiated in the same script run (Streamlit
disallows this). `ensure_initialized` seeds defaults before any widget
renders; `apply_preset`/`reset_inputs` run inside a button's `on_click`
callback, which Streamlit executes *before* the next script run creates
the widgets. The `st.radio` "Supplied by" widget passes only `key=`, never
`index=` — since its key's value is always already set programmatically
(by `ensure_initialized`/`apply_preset`/`reset_inputs`), also passing
`index` triggered Streamlit's "widget created with a default value but
also had its value set via the Session State API" warning during initial
browser testing; removing `index=` (letting the widget read purely from
`session_state[key]`) fixed it with no behavior change, confirmed by the
same 25 UI tests passing before and after and by a clean
`preview_logs(level="error")` check in the browser afterward.

## Checks performed and results

**Automated (Streamlit `AppTest`, no live network call — `openai.OpenAI` is
monkeypatched to a fake transport in most tests; one test deliberately
does *not* install that fake, to prove the Dispute Review path never even
attempts to construct a real client):**

| Suite | Result |
| --- | --- |
| `tests/test_dispute_review_ui.py` (new, 25 tests) | **25 passed** |
| `tests/test_app_scenario_lab_ui.py` (1 assertion updated for 3 tabs) | **7 passed** |
| `tests/test_dispute_review_models.py` / `comparison.py` / `presets.py` (Module 3, unchanged) | **53 passed** |
| `tests/test_workbench.py` (Predefined Claims logic, unchanged) | **15 passed** |
| **Combined relevant set** | **100 passed, 0 failed** |

Covered by the new suite: all three tabs render with all five claim
choices intact; each of the three presets produces its expected row
outcomes (4/4 Match; exactly one Servicing Provider Mismatch; Validity
Dates + Servicing Provider Unknown) strictly via the real
`compare_submission` call, never a hardcoded expectation independent of
it; a malformed date and a reversed date range each produce a clear error
and no stale result; changing each of the eight input types (reference,
member, service, start date, end date, provider, explanation, supplied-by)
after a comparison clears the result; loading a different preset or
clicking reset clears a previous result; provenance label updates
correctly for both Provider and Patient; two independent `AppTest`
sessions never share dispute-review state; Dispute Review activity leaves
`investigation_result`/`selected_claim_id` (Predefined Claims) and
`scenario_draft` (Scenario Lab) untouched; the Dispute Review path never
constructs an `openai.OpenAI` client and never modifies any `data/*.json`
file (SHA-256 hash comparison before/after exercising all three presets).

**Manual browser smoke check** (this project's own `.venv`, via the
`streamlit-app` launch config on `localhost:8501`, a port confirmed free
before starting; only the server process started for this check was
stopped afterward):

- Opened the app; confirmed the "Dispute Review" tab appears third, after
  "Predefined Claims" and "Scenario Lab".
- Recorded Claim Information rendered correctly: `CLM-1001`, `DENIED` /
  `AUTH_REQUIRED`, `M-1001`, `MRI-KNEE`, `2026-02-10`, servicing provider
  `PRV-1001 — Lakeside Imaging Center`, ordering provider (context-only
  caption) `PRV-4001 — Dr. Ana Kowalski`.
- **Matching fields** preset → Compare → "4 of 4 compared fields match.",
  correct verification guidance and disclaimer text, denial-unchanged /
  not-saved statement present.
- Edited Member ID after that comparison (typed a new value, pressed Tab
  to commit) → the Comparison Result section disappeared immediately.
- **Invalid date**: typed `not-a-date` into the start-date field, clicked
  Compare → clear field-specific error shown
  (`authorization_start_date: Value error, must be a valid ISO YYYY-MM-DD
  date string, got 'not-a-date'`), no stack trace, no result rendered.
- **Different servicing provider** preset → Compare → "3 of 4 ... 1
  mismatch(es) ... (Servicing Provider)."
- **Incomplete submission** preset (clicked directly after the prior
  result, without an intervening Compare) → previous result disappeared
  immediately on preset load, before Compare was even clicked → Compare →
  "2 of 4 ... 2 field(s) could not be compared ... (Validity Dates,
  Servicing Provider)," provenance correctly read
  "Patient-supplied—unverified".
- Confirmed Predefined Claims (case selector, all five claims, claim
  detail fields) and Scenario Lab (form fields, buttons) both still
  render with no exception; did not click "Investigate Claim" or "Run
  Investigation" (live-model actions).
- `preview_logs(level="error")` was checked after every interaction
  throughout this session: clean except for the one `st.radio`
  session-state warning described above, which was fixed and re-verified
  clean.
- No `.env`/`OPENAI_API_KEY` was configured for this manual session, so
  the fact that no live call was attempted is also structurally
  guaranteed by the app's own design (Compare never touches the LLM
  adapter at all), not merely by missing credentials.

## Demo sequence (concise)

1. Open the app → click the **Dispute Review** tab. Note the recorded
   claim: `CLM-1001`, `DENIED / AUTH_REQUIRED`, servicing provider
   `PRV-1001 — Lakeside Imaging Center` (distinct from the ordering
   provider `PRV-4001 — Dr. Ana Kowalski`, shown separately for context).
2. Click **Matching fields** → **Compare submitted information** → all
   four rows show `MATCH`; read the disclaimer and guidance.
3. Click **Different servicing provider** (previous result disappears) →
   **Compare submitted information** → three rows `MATCH`, the Servicing
   Provider row `MISMATCH` against a real alternate provider from the
   synthetic dataset (never the ordering physician).
4. Click **Incomplete submission** (previous result disappears) →
   **Compare submitted information** → Member/Service `MATCH`, Validity
   Dates and Servicing Provider both `UNKNOWN`; note the provenance label
   is now "Patient-supplied—unverified".
5. Edit any field (e.g. Member ID) and press Tab — the result disappears
   immediately, demonstrating stale-result protection.
6. Type `not-a-date` into either date field and click Compare — a
   concise, field-specific validation error appears instead of a result.

## Known limitations and authority boundaries

- Focused on `CLM-1001` only — no multi-claim selector, by design
  (Module 4's own scope).
- No persistence: results exist only in the current browser session's
  `st.session_state` and are discarded on refresh/new session — there is
  no save, export, or submission-tracking mechanism.
- The ORIGINAL LLM investigation pipeline (`application.investigation_service.run_investigation`,
  used by the Predefined Claims and Scenario Lab tabs) does not incorporate
  Dispute Review submissions in any way — a `DisputeComparisonResult` is
  never passed to it, and its AI-drafted investigation brief is generated
  exactly as before, unaware this tab exists. As of Module 6C, a
  completely SEPARATE, NEW AI brief exists (via "Investigate dispute
  evidence") that DOES incorporate the unverified submission — see the
  "Module 6C" section below. The two remain entirely independent pipelines
  with independent session-state, independent model calls, and independent
  typed result contracts.
- No approval, denial, reversal, or payment action exists anywhere in this
  tab, and no such action is implied by an all-Match result — enforced
  both structurally (no such vocabulary appears in
  `dispute_review/comparison.py`'s guidance builder) and by
  `test_dispute_review_result_never_shows_approval_or_payment_language`.
- Matching identifiers are compared **exactly** after trimming — no fuzzy
  matching, case folding, or alias resolution; a submission with a
  differently-cased or abbreviated identifier that a human would recognize
  as "the same" will still show `MISMATCH`.
- The "different servicing provider" preset picks a real, same-type
  provider from the synthetic dataset when one exists, or an explicitly
  fictional demo-only ID (`PRV-DEMO-ALT-FACILITY`, never written to the
  DataStore) otherwise — this demonstrates a provider-ID discrepancy only,
  never a judgment about whether that provider could actually perform the
  service.
- This module's own automated checks are scoped to the Dispute Review path
  plus the directly-adjacent existing UI tests (per the task's explicit
  instruction to reserve the full `pytest`/E2E/safety rerun for Module 5).
  A full-suite regression pass has not been re-run since Module 3's 474/474
  result at the time this section was written; Module 5 (and later
  modules) ran and re-confirmed full-suite results independently — see
  `docs/DISPUTE_REVIEW_VALIDATION.md` (Module 5), `docs/DISPUTE_EVIDENCE_RETRIEVAL.md`
  (Module 6A), and `docs/DISPUTE_AI_WORKFLOW.md` (Module 6B) for those
  later, larger counts. This bullet is left as originally written for
  historical accuracy about what Module 4 itself verified.

## Module 6C: optional AI investigation and optional judge

Everything in this section is NEW relative to Module 4 — the deterministic
comparison above is completely unchanged (same function, same zero-model-
call guarantee, same session-state keys).

### UI flow (appended after a valid comparison)

1. **"Investigate dispute evidence"** — rendered ONLY when a current,
   non-stale comparison result is displayed (i.e. only after a successful
   "Compare submitted information"). Caption: *"Retrieve recorded facts,
   policy passages, and graph relationships, then draft a cited review
   brief."* On click, wrapped in `st.spinner(...)`, calls
   `application.dispute_workflow.run_dispute_workflow(CLAIM_ID, submission)`
   **exactly once** — never `application.investigation_service.run_investigation`
   (the ORIGINAL pipeline). The resulting `DisputeWorkflowResult` is stored
   under `dispute_review_workflow_result`, bound to the same fingerprint
   scheme the comparison result already uses (see "Stale-result
   protection," extended below).
2. **Workflow status** — three `st.metric`s: Evidence Gate, Generation,
   Deterministic Validation, using a fixed presentation label map
   (`_GATE_LABELS`/`_GENERATION_LABELS`/`_VALIDATION_LABELS` in
   `dispute_review/ui.py`) so `EvidenceGateStatus.READY_FOR_SCOPED_GENERATION`
   always renders as **"Ready for scoped generation"** — never
   "Authorization verified," "Complete evidence," or "Approved." Exact
   enum values, `skill_status`, failure category, and the validator's own
   issue list are available in a collapsed "Exact status details
   (technical)" expander, never as the primary view.
3. **Evidence source outcomes** — a table of every
   `EvidenceSourceOutcome` (structured/policy/graph_claim/graph_submitted_provider/
   lookup sources), each status translated to "Evidence retrieved" /
   "Retrieved successfully — no relevant result" / "Retrieval failed" —
   the three-way distinction is never collapsed into a binary
   success/failure.
4. **Evidence limitations** — always rendered when present, **regardless
   of gate status** (Step 3's explicit requirement) — including the
   "no policy passage addresses changing servicing provider" limitation
   for the provider-mismatch preset, even though that preset's gate is
   `READY_FOR_SCOPED_GENERATION`. Conflicts and missing-evidence items are
   shown alongside.
5. **BLOCKED gate** — a clear error, any available evidence still shown
   via the same evidence expander used elsewhere, zero brief rendered, no
   judge offered.
6. **Generation/validation failure** — a clear, failure-category-specific
   message (the `CONFIGURATION` category gets its own message naming the
   two required environment variables and stating "No live model call was
   attempted, and no credentials are displayed here" — reusing the
   backend's own `LLMConfigurationError` message text, which by
   construction never contains a secret value, only variable names); a
   validation `FAILED` result shows the validator's issues but **never
   renders the rejected draft through the normal brief view** and the
   judge button is not offered.
7. **The validated brief** — for `is_accepted_dispute_draft(result)` (i.e.
   `DRAFTED` + validation `PASSED`): summary, findings (each followed by
   its cited reference ids resolved to readable label+detail text via a
   `ref_id -> EvidenceReference` lookup built from
   `generation_context.references` — never a bare, unexplained id),
   missing/conflicting evidence, verification questions, and the suggested
   next step — using `DisputeBrief`'s actual typed fields directly, no
   text regenerated or rewritten in UI code. Below it: the fixed
   `authenticity_disclaimer`, the fixed `provenance_notice`, and an
   updated notice — *"This dispute brief DOES incorporate the unverified
   submission above. The ORIGINAL AI investigation draft (Predefined
   Claims tab) remains a completely separate process and still does not
   include this submission."*
8. **Evidence expander** — grouped exactly into Recorded Structured Facts /
   Unverified Submitted Information / Retrieved Policy Excerpts / Recorded
   Graph Relationships / Comparison Findings (+ Limitations) — each item
   shown as `ref_id — label: detail`, never raw JSON as the primary view.
9. **"Run AI semantic evaluation"** — rendered only inside the accepted-
   draft branch (never offered without a validated brief), inside its own
   expander, with the caption *"An optional second model call assesses the
   draft against its evidence. Scores are advisory and uncalibrated."* On
   click, calls `application.dispute_judge.run_dispute_judge(workflow_result)`
   **exactly once** — never automatically, never as part of step 1.
10. **Judge result** — a 4-row table (Dimension | Score (1–5) | Verdict |
    Rationale) built directly from `DisputeJudgeResult`'s four typed
    dimensions, plus each dimension's `evidence_refs` (resolved the same
    way as brief citations) and `cited_draft_text` shown as captions below
    the table. The overall score/verdict are read directly from the
    backend's `overall_score`/`overall_result` (already Python-computed in
    `DisputeJudgeResult.from_dimensions`) — **never recomputed in UI
    code**. A caption states the average is Python-computed, never
    overrides a FAIL/UNCERTAIN dimension shown above it, and is "an
    advisory, uncalibrated rubric score — never an accuracy percentage, a
    confidence probability, or proof the draft is verified."
11. **Judge failure** — a clear "Evaluation failed" message (with the same
    safe `CONFIGURATION`-category handling as generation failures), no
    fabricated score, and the validated brief above is completely
    unaffected (verified: `test_judge_failure_preserves_the_validated_brief`
    asserts the brief object is unchanged after a forced judge failure).

### Stale-result protection, extended

The comparison result's existing fingerprint mechanism
(`_current_fingerprint`: claim snapshot JSON + every current widget value)
is **reused, not duplicated**, as the binding fingerprint for the AI
workflow result too (`WORKFLOW_FINGERPRINT_KEY`) — `get_display_workflow_result`
follows the exact same render-guard pattern as `get_display_result`.

`clear_result` (the one function every on_change callback, preset button,
and reset button already call) is **extended** to also clear the workflow
result/fingerprint and the judge envelope/binding — so every existing
clearing call site automatically covers the new state with zero changes
to those call sites. This directly satisfies "extend them rather than
creating competing state mechanisms."

**Judge binding is two-layered, per Step 6's explicit instruction that "a
run_id match alone is insufficient if code can replace the brief or
evidence while retaining that run_id":**

1. `application/dispute_judge.py` itself already guarantees
   `DisputeJudgeEnvelope.run_id == DisputeWorkflowResult.run_id`.
2. `dispute_review/ui.py` additionally computes `_judge_binding_key(result)`
   = `run_id + brief.model_dump_json()` at the moment the judge is
   invoked, and `get_display_judge_envelope` recomputes and compares this
   key on every render — so even a hypothetical future code path that
   mutated `result.brief` in place while somehow preserving `run_id` would
   still be rejected. `test_judge_binding_rejects_mismatched_revision_even_with_matching_run_id`
   exercises this directly by constructing a tampered `DisputeWorkflowResult`
   with a modified brief but an unchanged `run_id`, and confirming
   `get_display_judge_envelope` returns `None` for it.

**"A new workflow run invalidates the previous judge result even when the
inputs happen to be identical"** — `run_investigation` (the ui.py
function) calls `clear_judge(state)` **before** invoking the workflow, so
re-clicking "Investigate dispute evidence" with unchanged inputs clears
any prior judge result immediately, even though the render-guard would
have caught it anyway (a fresh `run_dispute_workflow` call always gets a
fresh `uuid4()` `run_id`) — verified directly by
`test_new_investigation_invalidates_old_judge_output`.

### Model-call boundaries (Module 6C)

- **Zero model calls:** rendering the tab, loading any preset, editing any
  field, clicking "Compare submitted information."
- **At most one model call:** clicking "Investigate dispute evidence" (via
  `run_dispute_workflow`, which itself makes at most one generation call
  per Module 6B's own guarantee).
- **At most one further, separate model call:** clicking "Run AI semantic
  evaluation" (via `run_dispute_judge`) — never triggered by anything
  else, including a successful investigation.
- Missing `OPENAI_API_KEY`/`LLM_MODEL` never breaks the tab or the
  comparison — `run_dispute_workflow`/`run_dispute_judge` already return a
  normal, typed `FAILED`/`CONFIGURATION` result rather than raising, and
  `dispute_review/ui.py` renders that as a clear, actionable message with
  no credential-shaped content, verified by
  `test_missing_configuration_shows_actionable_message_without_credentials`
  (asserts `"sk-"` never appears in any rendered error text).

### Checks performed (Module 6C)

**Automated (`tests/test_dispute_review_ui.py`, extended fake-client
helper):** the existing `_install_fake_openai_client` helper (previously
hardcoded to return a canned `InvestigationBrief` for every call) now
branches on the requested `text_format` — `InvestigationBrief` for the
original pipeline, `DisputeBrief` for the new generator, and
`RawDisputeJudgeDimensions` for the new judge — with an `AssertionError`
if a test reaches a `text_format` it did not explicitly configure a
fixture for, so a test can never silently receive a wrong-shaped canned
response. **55 tests pass** in this file (26 preserved from Module 4/5 +
29 new for Module 6C), covering: investigation runs only on click and
never calls the original investigation service; the judge never
auto-invokes; a valid brief renders with citations resolved to readable
content and all five evidence groups present; the provider-mismatch
preset's policy-gap limitation stays visible under a `READY` gate label
that never says "verified" or "approved"; a forced `PROVIDER` generation
failure keeps the comparison visible with no stale brief; missing
configuration shows the actionable, credential-free message; a validation
failure never renders the normal brief view and hides the judge button;
judge scores/verdicts/rationales/references render in a table read
directly from the backend, including a deliberately low `FAIL` dimension
(score 1) staying visible next to a 3.75 average with `overall_result`
still `FAIL`; a forced judge `PROVIDER` failure leaves the validated brief
byte-identical; all 10 input-change kinds plus preset-load and reset clear
both the workflow result and the judge envelope (parametrized); a second
identical-input investigation click gets a new `run_id` and clears the
stale judge; the narrow revision-fingerprint safeguard rejects a
hand-tampered result even when its `run_id` is unchanged; two independent
`AppTest` sessions never share investigation/judge state; Predefined
Claims/Scenario Lab session-state keys are untouched; and `data/*.json`
plus the live `DataStore`/graph singletons are byte-identical before and
after a full investigate+judge cycle.

The original Module 4/5 comparison-only "never calls a model" test was
**renamed** (not deleted) to
`test_comparison_only_path_never_calls_openai_client_or_investigation_service`,
with its docstring updated to state explicitly that it covers the
comparison path only — the tab as a whole now legitimately can call a
model via the two new buttons, so the old, broader name would have become
inaccurate; its original assertions and fail-fast-spy mechanism are
unchanged.

**Manual browser verification** (this project's own `.venv`, via the
`streamlit-app` launch config on `localhost:8501`; distinguished here from
the `AppTest` evidence above, since it exercises the REAL, unmocked
backend — including a genuinely-missing local configuration, not a
scripted fixture):

- Loaded the **Matching fields** preset → Compare → clicked **"Investigate
  dispute evidence"** with no `.env` present in this project (confirmed
  before starting). The tab rendered: Evidence Gate = "Ready for scoped
  generation," Generation = "Generation failed," Deterministic Validation
  = "Not run," the three fixed evidence limitations, the missing-
  prior-authorization gap, and the exact actionable message *"AI
  investigation requires model configuration that is not currently set
  (OPENAI_API_KEY and LLM_MODEL — see .env.example). No live model call
  was attempted, and no credentials are displayed here."* No "Run AI
  semantic evaluation" button appeared (correctly — no accepted draft
  exists). The comparison result above remained fully visible and
  unaffected throughout.
- Confirmed via `preview_logs` that the only tracebacks in the server log
  across this session are a **pre-existing, unrelated** Streamlit
  file-watcher issue (`transformers`' optional, torchvision-dependent
  image-processing submodules raising `ModuleNotFoundError: No module
  named 'torchvision'` when Streamlit's hot-reload watcher walks their
  `__path__` — present before Module 6C and unrelated to any `dispute_*`
  module; grepping the full log for `dispute_review`/`dispute_workflow`/
  `dispute_judge`/etc. returns zero matches).
- Confirmed the Predefined Claims tab still renders correctly (case
  selector, all five claims, claim detail fields, "Investigate Claim"
  button present) without clicking it (a live-model action).
- Stopped only the server process started for this check.
- **This manual check exercises the real backend against a genuinely
  unconfigured environment — it does NOT exercise the real OpenAI API**
  (no credentials exist to do so), and it does not replace or fake a
  production response; the config-missing path IS the real application
  behavior for this environment, not a stand-in for a successful
  generation. A live, successfully-configured generation/judge call has
  **not** been performed in this or any prior module — see "Live model
  behavior is not yet verified" below.

## Live model behavior is not yet verified (as of Module 6C)

Every generation and judge call exercised in this module's automated tests
uses a fake `openai.OpenAI` transport returning a hand-constructed,
already-valid `DisputeBrief`/`RawDisputeJudgeDimensions` object — this
proves the UI correctly renders whatever typed result the backend hands
it, and that the backend's own gating/validation/binding logic (tested
independently and far more thoroughly in `docs/DISPUTE_AI_WORKFLOW.md`)
is wired correctly end-to-end through the UI. **It does not prove a real
model, given the actual prompts, will produce a well-formed, well-cited,
validation-passing brief, or well-calibrated judge scores.** No live
OpenAI call has been made in Module 4, 5, 6A, 6B, or 6C. A live smoke test
against a real, configured model is explicitly out of scope for this
module and remains a prerequisite before any claim of production or demo
quality.

**Update (post-Module 6D, live smoke check):** a live smoke check has since been
performed against a real, configured model — see
[docs/DISPUTE_AI_VALIDATION.md](DISPUTE_AI_VALIDATION.md) §5–§8. Summary: the model is
reachable and returns well-structured, correctly-cited, correctly-provenance-labeled
output through this UI, but all 3 live-generated drafts were rejected by deterministic
validation in that check (most for a validator false-positive reason, not a real content
defect), so the live judge UI path documented above (fake-transport only) still has zero
live verification. This module's own historical record above is left unchanged.
