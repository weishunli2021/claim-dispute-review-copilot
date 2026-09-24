# Dispute Review Logic (Module 3)

A small, independently testable, PURE business-logic layer that compares new,
unverified authorization information against an existing claim. This document
describes what exists after Module 3; it is not a design proposal.

**Not implemented in this module:** any UI, any `st.session_state` storage,
any change to `app.py`, any change to how the existing LLM investigation
pipeline runs. See "What Module 4 still needs to add" below.

## Files and public function signatures

All new code lives in one new top-level package, `dispute_review/`, plus
three new test files under `tests/`. No existing file's logic was changed.

### `dispute_review/models.py`

Pydantic contracts (frozen, `extra="forbid"`, matching the convention already
used by `application/judge_models.py`).

- `SubmissionSource(str, Enum)` — `PROVIDER` / `PATIENT`.
- `DisputeSubmission(BaseModel)` — the incoming, unverified submission.
  Fields: `authorization_reference_number`, `member_id`, `service_code`,
  `authorization_start_date`, `authorization_end_date`,
  `servicing_provider_id`, `dispute_explanation`, `supplied_by` (defaults to
  `SubmissionSource.PROVIDER`). All business fields are `Optional`.
- `ClaimSnapshot(BaseModel)` — a read-only view of exactly the claim fields
  needed for comparison: `claim_id`, `member_id`, `service_code`,
  `date_of_service`, `servicing_provider_id`. **No ordering-provider field
  exists on this type at all** — see "Servicing vs. ordering provider" below.
- `ComparisonStatus(str, Enum)` — `MATCH` / `MISMATCH` / `UNKNOWN`.
- `ComparisonRow(BaseModel)` — `field`, `claim_value`, `submitted_value`,
  `status`, `explanation`.
- `DISPUTE_AUTHENTICITY_DISCLAIMER` — the fixed disclaimer string (see
  "Unverified provenance and authority boundaries" below).
- `DisputeComparisonResult(BaseModel)` — `claim_id`, `submission_provenance`,
  `rows` (exactly 4, enforced by `Field(min_length=4, max_length=4)`),
  `summary`, `authenticity_disclaimer` (defaults to the constant above),
  `verification_guidance`, `authorization_reference_number`,
  `dispute_explanation`.

### `dispute_review/comparison.py`

```python
def build_claim_snapshot(case_context: tools.case_context.CaseContext) -> ClaimSnapshot
def compare_submission(claim: ClaimSnapshot, submission: DisputeSubmission) -> DisputeComparisonResult
```

`compare_submission` is a pure function: no I/O, no mutation of either
argument (both are frozen), no Streamlit/session-state dependency, no LLM
call. `build_claim_snapshot` performs one read (`case_context.claim`'s
already-resolved fields) and raises `ValueError` if `case_context.claim` is
`None`.

### `dispute_review/presets.py`

```python
def pick_alternate_servicing_provider_id(case_context: tools.case_context.CaseContext) -> str
def matching_preset(claim: ClaimSnapshot) -> DisputeSubmission
def different_servicing_provider_preset(claim: ClaimSnapshot, alternate_provider_id: str) -> DisputeSubmission
def incomplete_submission_preset(claim: ClaimSnapshot) -> DisputeSubmission
def build_demo_presets(case_context: tools.case_context.CaseContext) -> DisputeDemoPresets

@dataclass(frozen=True)
class DisputeDemoPresets:
    claim: ClaimSnapshot
    matching: DisputeSubmission
    different_servicing_provider: DisputeSubmission
    incomplete: DisputeSubmission

FICTIONAL_DEMO_PROVIDER_ID: str  # "PRV-DEMO-ALT-FACILITY" -- never written to the DataStore
```

`build_demo_presets` is the single entry point Module 4 needs: given an
already-resolved `CaseContext` (e.g. from `tools.case_context.get_case_context`),
it returns a `ClaimSnapshot` and all three demo `DisputeSubmission`s, ready to
run through `compare_submission`.

## Actual claim-field mapping

Verified directly against `data/claims.json` and `tools/models.py` (not
assumed from the Module 2 report):

| `ClaimSnapshot` field | Source | CLM-1001 actual value |
| --- | --- | --- |
| `claim_id` | `Claim.claim_id` | `CLM-1001` |
| `member_id` | `Claim.member_id` | `M-1001` |
| `service_code` | `Claim.service_code` | `MRI-KNEE` |
| `date_of_service` | `Claim.date_of_service` | `2026-02-10` |
| `servicing_provider_id` | `Claim.provider_id` | `PRV-1001` (Lakeside Imaging Center, facility, in-network) |

`Claim.ordering_provider_id` (`PRV-4001`, Dr. Ana Kowalski, physician,
in-network) is **never read by `build_claim_snapshot`**, and `ClaimSnapshot`
has no field to hold it even if it were. This is a structural guarantee, not
just a code-review convention: `dispute_review.comparison` cannot
accidentally compare a submission's `servicing_provider_id` against the
ordering provider, because the ordering provider's id is simply not present
anywhere in the type the comparator reads from. Verified by
`tests/test_dispute_review_comparison.py::test_ordering_provider_never_substitutes_for_servicing_provider`,
which submits `PRV-4001` (the real ordering provider) as the servicing
provider and asserts `MISMATCH`, not `MATCH`.

## Comparison rules

Exactly four rows, always in this order:

1. **Member** — `claim.member_id` vs. `submission.member_id`.
2. **Service** — `claim.service_code` vs. `submission.service_code`.
3. **Validity Dates** — is `claim.date_of_service` within
   `[submission.authorization_start_date, submission.authorization_end_date]`,
   inclusive? A missing claim date or either missing boundary is `UNKNOWN`,
   never treated as an open-ended authorization (e.g. a missing end date does
   NOT mean "valid indefinitely").
4. **Servicing Provider** — `claim.servicing_provider_id` vs.
   `submission.servicing_provider_id`. Never the ordering provider (see above).

For every row: either side missing → `UNKNOWN`; both present and unequal (or
service date outside the validity window) → `MISMATCH`; both present and
equal (or service date within the window, inclusive of both boundaries) →
`MATCH`. No fifth row is ever added for the authorization reference number
itself — this prototype has no authoritative record to check a brand-new
submission's reference number against, and the absence of a matching
authorization in the existing synthetic dataset is never treated as proof
that no authorization exists elsewhere.

## Normalization and date rules

- Every string business field on `DisputeSubmission` is trimmed of
  surrounding whitespace; a blank or whitespace-only string normalizes to
  `None` (missing), via a `field_validator(mode="before")`.
- Identifier comparison is **exact string equality only**, after trimming —
  no case folding, fuzzy matching, alias mapping, or inferred equivalence, in
  either the Member, Service, or Servicing Provider row.
- `authorization_start_date`/`authorization_end_date` accept a real `date`
  object or a valid ISO `YYYY-MM-DD` string; a blank/whitespace-only string
  becomes `None` (missing) the same way string fields do.
- A malformed date string (e.g. `"not-a-date"`) or an impossible calendar
  date (e.g. `"2026-02-30"`) raises a `pydantic.ValidationError` at
  construction time — `date.fromisoformat`'s own error is re-raised with a
  clearer message, never silently repaired or reinterpreted.
- `authorization_start_date > authorization_end_date` raises a
  `ValidationError` via a `model_validator(mode="after")` — the two dates are
  never silently swapped into a valid order.
- `dispute_explanation` is **inert text everywhere in this package**:
  `compare_submission` never parses, searches, or branches on its contents.
  It cannot override an identifier comparison, a date-validity result, a
  Match/Mismatch/Unknown verdict, or the verification guidance — it is
  carried through to `DisputeComparisonResult.dispute_explanation` purely as
  human-readable context. Verified by
  `test_explanation_text_cannot_alter_comparison_result`, which sets
  explanation text that explicitly asserts a match and confirms the actual
  result still reports `MISMATCH`.

## Unverified provenance and authority boundaries

- `submission_provenance` is always exactly `"Provider-supplied—unverified"`
  or `"Patient-supplied—unverified"`, derived only from
  `DisputeSubmission.supplied_by` — never from any inference about which
  fields matched.
- `authenticity_disclaimer` is **always present** on every
  `DisputeComparisonResult`, with fixed text:
  `"Matching fields do not establish authenticity, authorization
  applicability, coverage, or payment."` It defaults from a module-level
  constant (`DISPUTE_AUTHENTICITY_DISCLAIMER`), so no code path can produce a
  result missing it.
- `authorization_reference_number` and `dispute_explanation` are carried
  through as **context only** — their presence never implies either has been
  authenticated or independently verified.
- `verification_guidance` is a deterministic list of concise, conditional
  next steps built purely from the four rows' outcomes and whether a
  reference number was supplied: verify the reference/status with an
  authoritative source, confirm scope/applicability, reconcile any
  mismatched fields by name, obtain any missing fields by name, and route
  for human review. **No result ever says a claim/authorization should be
  approved, paid, reversed, or denied, and none implies the original denial
  record has changed** — enforced structurally (no such vocabulary exists in
  `_build_verification_guidance`) and checked by
  `test_result_never_suggests_approval_denial_or_payment`.
- No confidence score exists anywhere in this layer.

## How demo presets are derived

All three presets are built from `build_demo_presets(case_context)`, which
first calls `build_claim_snapshot` and `pick_alternate_servicing_provider_id`
against the **real, already-resolved** `CaseContext` — nothing is hardcoded
by `claim_id`, and the same functions work for any claim, not just CLM-1001.

1. **`matching_preset`** — `member_id`/`service_code`/`servicing_provider_id`
   copied verbatim from the claim snapshot; a validity window of
   `date_of_service ± 30 days`; a synthetic authorization reference
   (`"DEMO-AUTH-0001"`).
2. **`different_servicing_provider_preset`** — identical to the matching
   preset except `servicing_provider_id` is replaced by a **different, real**
   provider id chosen by `pick_alternate_servicing_provider_id`: the same
   `provider_type` as the claim's actual servicing provider, excluding both
   the claim's own servicing provider id **and its ordering provider id**
   (read directly from `case_context.ordering_provider`, even though
   `ClaimSnapshot` itself never carries it) — so the ordering physician is
   never chosen as a "different servicing facility" merely because its id
   differs. For CLM-1001 this currently resolves to `PRV-1002` (QuickDraw
   Labs, facility). If no real alternative of the same type exists, it falls
   back to the explicitly fictional `FICTIONAL_DEMO_PROVIDER_ID`
   (`"PRV-DEMO-ALT-FACILITY"`), which is **never** written to the DataStore.
3. **`incomplete_submission_preset`** — keeps `member_id`/`service_code`
   matching; omits `servicing_provider_id` and `authorization_end_date`, so
   those two rows resolve to `UNKNOWN` when run through `compare_submission`.

Every preset's `dispute_explanation` states it is a synthetic demo
submission, not a real dispute. **No preset is ever inserted into
`data/*.json`, the shared `tools.data_store.DataStore` singleton, or the
knowledge graph** — `pick_alternate_servicing_provider_id` only *reads*
`tools.data_store.get_data_store().providers`; nothing in `presets.py` writes
to it. Preset outcomes (Match/Mismatch/Unknown) are never hardcoded anywhere
— every test that asserts an outcome does so by actually calling
`compare_submission` on the preset's output.

### Why this does NOT reuse Scenario Lab's DataStore-boundary override

`application/scenario_lab.py`'s `_temporary_data_overlay` mutates the
process-wide `DataStore`/graph singletons for the duration of one call — the
right shape for Scenario Lab's "compose one hypothetical case and run it
through the real investigation pipeline" flow. Dispute review needs a
different shape: hold an **original** claim and a **proposed** submission
side by side, read-only, without ever touching shared state that other
tabs/callers depend on. Accordingly:

- `build_claim_snapshot` only *reads* already-resolved `CaseContext` fields.
- `DisputeSubmission` is a wholly separate, in-memory object — never a
  substitute written into the `DataStore`.
- `compare_submission` never fetches anything external and never mutates
  either argument (both frozen).
- `pick_alternate_servicing_provider_id` only reads
  `get_data_store().providers` — it has no write path.

`tests/test_dispute_review_presets.py::test_integration_clm_1001_lookup_leaves_original_data_unchanged`
reads CLM-1001 via `tools.case_context.get_case_context`, runs all three
presets through the real comparator, and asserts the claim, its prior
authorizations, and the full provider/claim id sets in the `DataStore` are
byte-for-byte unchanged afterward — without importing
`application.scenario_lab` anywhere in that test.

## Test results

Using the project's `.venv` Python (`.venv\Scripts\python.exe`), no live
OpenAI calls:

| Command | Result |
| --- | --- |
| `pytest tests/test_dispute_review_models.py tests/test_dispute_review_comparison.py tests/test_dispute_review_presets.py -v` | **53 passed**, 0 failed |
| `pytest tests/ -q` (full suite) | **474 passed**, 1 warning (pre-existing, unrelated third-party `chromadb` `DeprecationWarning`), 0 failed, 0 skipped, 0 errors, ~71s |

474 = the Module 2 baseline's 421 + this module's 53 new tests. No existing
test was modified, loosened, or skipped to obtain this result.

## What is implemented now vs. deferred to Module 4

**Implemented in Module 3:**

- `dispute_review.models` — `DisputeSubmission`, `ClaimSnapshot`,
  `ComparisonRow`, `DisputeComparisonResult` and their validation rules.
- `dispute_review.comparison` — `build_claim_snapshot`, `compare_submission`
  (pure, four-row comparison, deterministic summary/guidance/disclaimer).
- `dispute_review.presets` — the three synthetic CLM-1001-focused (but not
  CLM-1001-special-cased) demo presets, and `pick_alternate_servicing_provider_id`.
- Focused unit tests plus one integration test against the real,
  already-resolved CLM-1001 `CaseContext`.

**Deliberately NOT implemented — deferred to Module 4:**

- **No UI.** `app.py` was not touched; there is no Dispute Review tab, form,
  or button anywhere yet.
- **No session-state storage.** Nothing in `dispute_review/` reads or writes
  `st.session_state`, and no Streamlit import exists anywhere in the package.
  Module 4 must give Dispute Review submissions/results their **own
  dedicated `st.session_state` key namespace** — distinct from both
  Predefined Claims' keys (`selected_claim_id`, `investigation_result`,
  `review_decision`, `judge_result`, defined in `application/workbench.py`)
  and Scenario Lab's `SCENARIO_*_KEY` constants (`application/scenario_lab.py`)
  — following the same non-overlapping-namespace discipline `app.py`'s own
  module docstring already establishes for those two tabs.
- **No stale-result invalidation.** Because there is no session-state layer
  yet, there is also no logic to clear a previously-displayed
  `DisputeComparisonResult` when its inputs change. Module 4 must invalidate
  any displayed dispute-review result whenever any submission input field
  **or the selected preset** changes — mirroring
  `application/workbench.py`'s and `application/scenario_lab.py`'s existing
  stale-state protection for the two current tabs.
- **The existing LLM investigation pipeline is untouched and does not
  incorporate these submissions.** `application.investigation_service.run_investigation`,
  `agents.case_agent.run_case_agent`, and `skills.investigate_claim` were not
  modified; a `DisputeComparisonResult` is not evidence input to, and is
  never read by, the investigation brief generation path. If a future module
  wants the two to interact, that is a new, explicit integration decision —
  not something Module 3 or 4 does implicitly.
- No PDF/OCR, database, persistence, external verification, or action of any
  kind — this remains a pure, in-memory comparison layer.
