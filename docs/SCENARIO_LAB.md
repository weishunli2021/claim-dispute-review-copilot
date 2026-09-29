# H7 — Scenario Lab

**Scenario Lab is an evaluator/development tool, not a production claim-entry workflow.**
It lets someone compose a *temporary, in-memory-only* synthetic claim using the exact
existing data schema (`tools/models.py`), run it through the SAME, unmodified investigation
pipeline used by the Predefined Claims tab, and observe the result -- without ever creating
a second investigation engine, writing to `data/*.json`, or introducing a new evidence domain.

---

## What it is / is not

| | |
|---|---|
| **Is** | A form that builds real `tools.models.*` instances (`Claim`, `Member`, `Benefit`, `Provider`, `PriorAuthorization`), temporarily makes them visible to the SAME deterministic tools/graph/agent/skills stack, runs ONE investigation, and tears the temporary data down. |
| **Is not** | A second investigation engine, a production claim-entry workflow, a persistent data editor, a new evidence domain, or a way to test anything Predefined Claims' 5 curated cases couldn't already represent in kind. |

## Exact data schema supported

Every Scenario Lab field maps directly onto a real field already defined in `tools/models.py`
-- no parallel schema exists. See `application/scenario_lab.py`'s `ScenarioDraft` dataclass for
the authoritative field list:

- **Claim**: `claim_status` (`DENIED`/`PAID`), `denial_reason_code` (`AUTH_REQUIRED` /
  `SERVICE_NOT_COVERED` / `OUT_OF_NETWORK_PROVIDER` / none), `denial_reason_description`,
  `service_code`, `date_of_service`, `billed_amount`, `allowed_amount`.
- **Member/Plan**: `plan_id` (chosen from the real baseline plans), and either a temporary
  generated member or an existing baseline member.
- **Benefit**: available or absent (absent means no benefit record exists for the
  plan/service -- distinct from "not covered"), `covered`, `requires_prior_auth`,
  `network_requirement`.
- **Servicing/Ordering provider**: existing baseline provider, a temporary generated
  provider (network status + type), an intentionally unresolved `provider_id` (mirrors Case
  5's `PRV-9999` precedent -- never a fabricated record), or (ordering only) none.
- **Prior authorizations**: zero, one, or many `PriorAuthorization` records, each with
  status/effective/expiration/notes.
- **Question**: the scoped investigation question passed to the pipeline unchanged.

## Templates

`TEMPLATE_NAMES` (see `application/scenario_lab.py::build_template_draft`) only prepopulate
field values -- every field stays editable afterward, and none hard-codes an investigation
answer (the same convention the 5 Predefined Claims cases already follow):

1. **Custom** -- blank-slate defaults.
2. **AUTH_REQUIRED — no authorization record** -- denial code set, benefit requires prior
   auth, zero authorization records.
3. **AUTH_REQUIRED — expired authorization** -- one `EXPIRED` authorization whose window
   predates the date of service.
4. **AUTH_REQUIRED — multiple authorization candidates** -- two authorization records (one
   expired, one approved) so the pipeline's "every candidate is shown, none is silently
   picked" behavior can be exercised.
5. **Benefit not covered** -- an available benefit record with `covered=False`.
6. **Out-of-network provider** -- denial code `OUT_OF_NETWORK_PROVIDER`, temporary provider
   with `network_status="out-of-network"` (internally consistent).
7. **Missing provider** -- servicing provider mode `UNRESOLVED` (a `provider_id` that
   resolves to `None`, like Case 5) plus no benefit record -- reaches `NEEDS_REVIEW` through
   the existing pipeline, not a hard-coded shortcut.
8. **Conflicting claim/provider evidence** -- denial code says `OUT_OF_NETWORK_PROVIDER` but
   the provider's own recorded network status is `in-network`. Intentionally inconsistent;
   surfaces as a `WARNING`, not an `ERROR` -- Run Investigation stays available.

## In-memory data architecture

Two process-wide `functools.lru_cache(maxsize=1)` singletons already exist and are read by
every deterministic tool and by the knowledge graph:

- `tools.data_store.get_data_store()` -- a `DataStore` whose `.claims` / `.members` /
  `.benefits` / `.providers` / `.prior_authorizations` /
  `.prior_authorizations_by_member_service` are plain public dicts.
- `graph.retriever._get_graph()` -- a separate cache that builds the knowledge graph once
  from whatever `DataStore` existed at build time.

`application.scenario_lab._temporary_data_overlay(records)` is a context manager that:

1. Writes the scenario's records directly into the shared `DataStore`'s dicts (works
   uniformly across all six `tools/*_tool.py` modules regardless of how each imported
   `get_data_store`, since they all return the same object).
2. Calls `_get_graph.cache_clear()` so the next graph read rebuilds and includes the
   scenario's nodes.
3. Yields control to `application.investigation_service.run_investigation(...)` -- the exact
   same function Predefined Claims calls.
4. In a `finally` block, restores every touched key to its EXACT pre-scenario state -- removing
   a record it added where nothing existed before, or restoring the exact original baseline
   object where one did -- and calls `_get_graph.cache_clear()` again, so any other caller
   (including the Predefined Claims tab, in the same or a later Streamlit rerun) observes only
   baseline-only state afterward.

No file under `data/` is ever read or written by this module. Nothing here persists past the
single `run_investigation()` call it wraps. **Not safe for concurrent multi-user access** (a
single mutable process-wide singleton) -- acceptable for this single-session interactive
prototype; see Known Limitations.

ID generation (`_next_id`) guarantees no collision with baseline records or with other IDs
generated earlier in the *same* scenario build, via one shared `reserved` set threaded
through every ID request in a single `build_scenario_records()` call.

### Scoped-replacement semantics for benefit/authorization collisions

Two scenario fields can legitimately collide with a *different* kind of baseline key than the
freshly-generated ids above: a scenario's `(plan_id, service_code)` benefit key (`plan_id` is
deliberately chosen from real baseline plans) and a scenario's `(member_id, service_code)`
authorization group (when `member_mode=EXISTING` reuses a baseline member). Scenario Lab
defines the scoped structured evidence visible to the scenario claim for the fields it
controls, so collisions are handled as an explicit **replace-or-hide, then restore** operation
rather than a plain insert/delete:

- **Benefit.** If the draft supplies a benefit (`benefit_available=True`), it REPLACES
  whatever occupies `(plan_id, service_code)` for the duration of the run. If the draft
  supplies no benefit (`benefit_available=False`), that key resolves to no benefit at all
  during the run -- even when a baseline benefit already lives there. Either way, the exact
  original value at that key (a benefit object, or the absence of one) is restored afterward,
  never permanently deleted.
- **Prior authorizations.** The scenario's authorization list (zero, one, or many rows) is the
  COMPLETE set visible for `(member_id, service_code)` during the run. Any preexisting
  baseline authorizations for that exact pair are hidden from BOTH `prior_authorizations`
  (the flat dict `graph/builder.py` iterates directly to build every authorization graph node,
  not just the grouped index) and `prior_authorizations_by_member_service` (the grouped index
  `tools/prior_auth_tool.py` reads) for the duration of the run -- so structured evidence and
  the graph can never disagree about which authorizations exist. The baseline group and every
  baseline record it referenced are restored exactly afterward.

This is regression-tested in `tests/test_scenario_lab.py`'s "P" test group (collision
restoration, `benefit_available=False` hiding, existing-member zero/one/many authorization
replacement, exception-safety, and a full in-memory `DataStore` snapshot comparison stronger
than hashing `data/*.json` alone).

## Scenario validation: PASS / WARNING / ERROR

`application.scenario_lab.validate_scenario` checks **scenario construction quality only** --
never a claim adjudication, never business/evidence logic:

- **ERROR** (blocks Run Investigation): empty service code, unsupported claim status,
  negative billed amount, unknown `plan_id`, an `EXISTING` member/provider mode with no id
  selected or an unknown id, or an authorization whose effective date is after its
  expiration date.
- **WARNING** (does not block -- these are valid, useful conflicting-evidence test
  scenarios): `OUT_OF_NETWORK_PROVIDER` denial paired with an in-network provider,
  `AUTH_REQUIRED` denial paired with `requires_prior_auth=False`, or an authorization whose
  validity window excludes the date of service.
- **PASS**: no issues.

`run_scenario_investigation` re-checks for `ERROR` and raises `ScenarioBlockedError` as a
defense-in-depth backstop; the UI's own "Run Investigation" button is `disabled` whenever the
last validation result is `ERROR` (see `app.py`), so this exception path should not normally
be user-reachable.

## Same-pipeline architecture (proof)

- Predefined Claims: `app.py` → `application.investigation_service.run_investigation(claim_id, question)`.
- Scenario Lab: `app.py` → `application.scenario_lab.run_scenario_investigation(draft)` →
  (inside the temporary overlay) → `application.investigation_service.run_investigation(records.claim.claim_id, draft.question, adapter=adapter)`.

Both call paths converge on the exact same `run_investigation` function object, which calls
the exact same `agents.case_agent.run_case_agent` (LangGraph), the exact same
`investigate_claim` skill, the exact same retrieval/graph/context layers, the exact same H1
generation adapter contract, and the exact same H2 `brief_validator`. Scenario Lab changes
*what data those layers see*, never *how they decide*.

## Unsupported evidence domains

Scenario Lab reuses the existing schema exactly as-is. It does **not** add any new evidence
domain, field, or relationship type beyond what `tools/models.py` already defines (e.g. no
new claim-line-item structure, no appeals data, no new policy-document domain). Anything not
expressible in `ScenarioDraft` is out of scope for this tool.

## Baseline-data protection

- `data/*.json` is never opened for writing by any H7 code path -- confirmed by hashing all
  six files before and after the full H7 test suite (`tests/test_scenario_lab.py` +
  `tests/test_app_scenario_lab_ui.py`) and asserting byte-for-byte equality.
- A Scenario Lab claim ID (`CLM-SCN-###`) is never a member of `application.workbench.CASE_IDS`,
  so the Predefined Claims tab's case selector cannot list or resolve it by construction.
- The temporary claim is actively removed from the `DataStore` singleton in the overlay's
  `finally` block, and the knowledge graph is rebuilt from baseline-only data immediately
  afterward -- confirmed by `tests/test_scenario_lab.py`'s
  `test_m_scenario_claim_not_accessible_after_run_completes` (a fresh `get_case_context()`
  call for the scenario's own claim ID returns `claim=None` once the run completes) and
  `test_m_scenario_run_does_not_disturb_predefined_claim_lookup` (a real Predefined Claims
  case still resolves correctly after a Scenario Lab run).

## Stale-state handling

Every scenario-defining field change (including template switches) calls
`application.scenario_lab.reset_scenario_investigation_state`, which clears the prior
scenario result, its records, its validation snapshot, its local review decision, and any H6
judge result -- without touching the draft itself. "Reset Scenario" additionally clears the
draft, causing the tab's next render to repopulate a fresh, default (`Custom`) draft. None of
this ever touches Predefined Claims' own session-state keys
(`selected_claim_id` / `question_text` / `investigation_result` / `review_decision` /
`judge_result`) -- the two tabs use entirely separate, non-overlapping key namespaces (see
`application/scenario_lab.py`'s `SCENARIO_*_KEY` constants vs `application/workbench.py`).

## H6 compatibility

The H6 "Experimental Semantic Evaluation" expander is reused, unmodified in logic, for
Scenario Lab results via the same gating condition (`GenerationStatus.DRAFTED` +
`ValidationStatus.PASSED`) and the same never-automatic, click-only invocation. It is
namespaced to its own session-state key (`SCENARIO_JUDGE_RESULT_KEY`) so a Scenario Lab judge
result can never be confused with, or leak into, a Predefined Claims judge result, and it
clears on every scenario-defining field change exactly like the main scenario result does.

## Known limitations

- **Single-process, not concurrency-safe.** The overlay mutates one shared, process-wide
  `DataStore`/graph singleton. Two simultaneous Scenario Lab runs (or a Scenario Lab run
  racing a Predefined Claims run in a different browser tab/session against the same
  Streamlit process) could observe each other's temporary data mid-flight. Acceptable for a
  single-session interactive prototype; not acceptable for multi-user production use without
  a different data-isolation design (e.g., a per-request DataStore instance).
- **No new evidence domains.** Scenario Lab can only construct what the existing schema
  already models.
- **No persistence.** A Scenario Lab result is lost on page reload / session end by design --
  there is intentionally no "save this scenario" feature.
- **Streamlit widget-state caveat.** Form fields deliberately avoid `key=`-bound
  `value=`/`index=` widgets for the main draft fields (a known Streamlit gotcha: a widget
  with an explicit `key` ignores `value=`/`index=` on reruns after the first), instead
  comparing each widget's return value against the draft each rerun and writing back through
  `draft.<field> = new_value`. Provider-mode/network/type/status sub-selects and
  authorization sub-fields do use `key=` for stable per-row identity, since Streamlit needs a
  key to keep a repeated widget's own click/selection state distinct per row.

## How to use it in Streamlit

1. Open the **Scenario Lab** tab.
2. Pick a template (or leave "Custom") and adjust any field.
3. Click **Validate Scenario** -- review PASS/WARNING/ERROR and the specific issues listed.
4. If not `ERROR`, click **Run Investigation** -- this runs the real pipeline once, including
   one real LLM generation call under the same conditions the Predefined Claims tab uses.
5. Review the result exactly as in Predefined Claims (status, brief or NEEDS_REVIEW packet,
   human review actions, optional H6 judge, supporting evidence, execution trace).
6. Click **Reset Scenario** to start over; switching templates or editing any field also
   automatically clears a stale prior result.

## Interview-safe wording

- "Scenario Lab lets me build a temporary, synthetic test claim on the fly and run it through
  the exact same investigation pipeline the demo uses for its five curated cases -- it's a
  testing/evaluation surface, not a second engine and not a way to create real claims."
- "The temporary data lives only in memory for the duration of one investigation; it's never
  written to the underlying data files, and it disappears the moment the run completes."
- "It reuses the same evidence-sufficiency, generation, and validation logic as the main
  workflow -- it changes what data those layers see, never how they decide."
