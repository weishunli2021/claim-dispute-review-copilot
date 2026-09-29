> **HISTORICAL — retired scenario.** This document describes the earlier
> AUTHORIZATION-DISPUTE scenario(s) that were replaced by the single
> billing-correction scenario (CLM-BILL-9001). It is kept for historical
> reference only and does not describe the current Dispute Review tab.
> See docs/BILLING_CORRECTION_SCENARIO.md for the active scenario.

# V2 Authorization-Source Evidence — Module 2 Foundation

This document records what Module 2 built: the data and retrieval **foundation** for an
independently-seeded synthetic authorization-source evidence layer. It is a foundation
only — nothing in this module is wired into live brief generation, the evidence gate,
prompts, presets, or the UI. **Module 3** connects this foundation to two demonstration
scenarios and to the existing comparison/workflow/generated-brief/UI pipeline.

All records described here are **entirely synthetic**. No real payer system, real
authorization system, or real member/claim data was contacted, queried, read from, or
used anywhere in this module.

## 1. The three evidence layers and their trust boundaries

| Layer | What it is | Where it lives | Trust boundary |
|---|---|---|---|
| 1. Original recorded claim/decision evidence | The claim as already on file, and its recorded denial/approval | `tools/`, `data/claims.json`, `data/prior_authorizations.json`, surfaced as `dispute_review.models.ClaimSnapshot` | Already on file (`RECORDED` provenance in the existing evidence package) — but "recorded" describes only what this synthetic dataset happens to contain, never a claim of completeness. |
| 2. Provider/patient-submitted information | New authorization information a provider or patient supplies for this dispute | `dispute_review.models.DisputeSubmission` | **Always unverified.** Never authenticated by this codebase, regardless of how it compares to any other layer. |
| 3. Authorization-source evidence (**this module**) | A separately-seeded synthetic fixture standing in for an authorization system of record | `dispute_review/authorization_source_models.py`, `dispute_review/authorization_source_lookup.py`, `data/authorization_source_records.json` | **Independent of the submitted form data in this demo — not a real payer system.** Matching a record here by reference number is a lookup, never proof that the record belongs to a given claim or applies to its billed service date. |

"Independent" is scoped precisely: it means this fixture is a distinct, separately
seeded dataset from both the baseline `prior_authorizations.json` and the submitted-form
data, so a later comparison (Module 3) can reason about three genuinely different
sources rather than two. It does **not** mean any external system was reached, and it
does not upgrade this layer's synthetic records to "verified" status merely because
they live outside the submission.

## 2. New and reused paths

**New in Module 2** (nothing pre-existing was modified):

- `dispute_review/authorization_source_models.py` — the typed
  `AuthorizationSourceRecord`, `AuthorizationSourceLookupStatus`, and
  `AuthorizationSourceLookupResult` models.
- `dispute_review/authorization_source_lookup.py` — the isolated,
  read-only `AuthorizationSourceStore`, its singleton getter
  `get_authorization_source_store()`, and the two lookup entry points
  `lookup_authorization_source_by_reference()` /
  `get_authorization_source_record_by_id()`.
- `data/authorization_source_records.json` — the separately-seeded fixture
  (3 generic demonstration records; the two Module 3 scenario fixtures live in
  isolated test fixtures instead — see §4).
- `tests/test_authorization_source_models.py`,
  `tests/test_authorization_source_lookup.py` — focused tests (§5).
- This document.

**Reused, not duplicated** (styles/abstractions extended, not a parallel framework):

- Pydantic conventions: `ConfigDict(extra="forbid", frozen=True)`, `field_validator`/
  `model_validator` date/identifier normalization — mirrors
  `dispute_review/models.py` exactly.
- Loader/index pattern (`_load_records`, per-record `ValidationError` wrapping,
  duplicate-key rejection) — mirrors `tools/data_store.py`'s `DataStore`, but as its own,
  separate class (`AuthorizationSourceStore`), never `DataStore` itself.
- `tools.validation.require_identifier` — reused directly for the two lookup
  functions' identifier checks, identical convention to
  `tools/prior_auth_tool.py`'s `get_prior_authorization_by_id`.
- Status-enum-per-subsystem convention (`class XStatus(str, Enum)`, never shared across
  subsystems) — matches `EvidenceSourceStatus`, `DisputeJudgeStatus`, `SkillStatus`, etc.
- Isolated-fixture test pattern (`AuthorizationSourceStore(path=...)` writing to
  `tmp_path`) — mirrors `tests/test_data_store.py`'s `DataStore(data_dir=tmp_path)`.

**Explicitly NOT touched** (read for context only, per the task's own inspection list):
`application/dispute_workflow.py`, `dispute_review/ui.py`, `skills/investigate_dispute.py`,
`context/dispute_evidence_retriever.py`, `application/dispute_brief_validator.py`,
`application/dispute_judge.py`, `application/dispute_judge_models.py`. None of these
imports, calls, or is called by anything added in Module 2.

## 3. Field meanings and provenance structure

`AuthorizationSourceRecord` fields, with the meanings the task specifically asked to be
kept distinct:

| Field | Meaning |
|---|---|
| `source_record_id` | Stable, unique id for **this one version**. Never reused, never overwritten. |
| `authorization_reference_number` | The lookup key a provider/patient submission would supply. Shared across every version of one amendment chain by design. |
| `member_id`, `service_code`, `servicing_provider_id` | The identifiers this version's authorization covers. |
| `status` | The authorization **decision** recorded on this version (e.g. `APPROVED`, `DENIED`, `EXPIRED`, `PENDING`) — a plain, documented string, not a closed Enum, matching `PriorAuthorization.status`'s own looser convention. |
| `authorized_start_date` / `authorized_end_date` | The **authorized service period**, if any — `None`/`None` is valid (e.g. a denial authorizes nothing). Never validated against any claim's date of service; a period that excludes a given date is a valid, constructible record (see §6). |
| `source_system` | Fixed label `SYNTHETIC-AUTH-SOURCE-V2`, identifying every record as coming from this isolated fixture. |
| `record_version` | `>= 1`. Version 1 is an original decision; version > 1 is an amendment. |
| `decided_at` | When **this version's** `status` was decided. Optional; never invented when the fixture does not supply it — a missing original-denial timestamp stays missing. |
| `amended_at` | When **this version** was created as an amendment. Always `None` for version 1. |
| `amends_source_record_id` | Links this version to the immediately-prior version's `source_record_id`. **Required** when `record_version > 1`, **forbidden** when `record_version == 1` (enforced by a model validator) — this is what makes version-chain traceability a structural fact, not a convention. |
| `amendment_reason` | Free text describing what changed in this version. |
| `retroactive_effective` | **Tri-state**: `True`/`False` only when the source fixture explicitly asserts retroactive applicability; `None` (unknown/not established) otherwise. **Never computed** from `amended_at` or any other timestamp by this model or by any lookup function — see §6. |
| `notes` | Free text. |

`AuthorizationSourceLookupResult` carries `status` (`FOUND` / `NOT_FOUND` / `AMBIGUOUS` /
`ERROR`), the queried reference number, `record` (the chain's current version, only when
`FOUND`), `version_history` (every version in the chain, oldest first, only when
`FOUND`), `candidates` (every matching record, unselected, only when `AMBIGUOUS`), and a
human-readable `detail`.

## 4. Fixture / version isolation approach

- **File-level isolation**: `data/authorization_source_records.json` is a distinct file
  from `data/prior_authorizations.json`, loaded by a distinct class
  (`AuthorizationSourceStore`) with its own `@lru_cache` singleton
  (`get_authorization_source_store()`) — never `tools.data_store.DataStore` /
  `get_data_store()`. A regression test
  (`test_baseline_prior_authorization_fixtures_are_unaffected_by_this_module_existing`)
  confirms the baseline `PA-1501`/`PA-2001` records and lookups are unaffected by this
  module's mere existence, and that `AUTHSRC-*` ids are not resolvable through the
  baseline lookup.
- **No write path, anywhere**: `AuthorizationSourceStore` has no method that writes back
  to its fixture file or mutates a loaded record. Nothing in
  `dispute_review/authorization_source_lookup.py` accepts a `DisputeSubmission` as
  input at all — a submitted/edited form value has no code path into this store,
  confirmed by `test_editing_a_submission_cannot_mutate_source_records`.
  `AuthorizationSourceRecord` is itself `frozen=True`, so even a caller holding a
  reference to a loaded record cannot mutate it in place.
  Presenting an authorization reference number as a **lookup key only** (never proof of
  applicability) is enforced by returning a full `AuthorizationSourceLookupResult`
  rather than a bare record, and by every docstring in this module saying so explicitly.
- **Version preservation**: `AuthorizationSourceStore.__init__` indexes every version by
  its own unique `source_record_id` and raises `AuthorizationSourceLoadError` on a
  duplicate id — nothing is ever silently overwritten. Amendments are new records with
  new ids, linked backward via `amends_source_record_id`.
- **Real vs. test fixtures**: the real, singleton-backing fixture
  (`data/authorization_source_records.json`) contains 3 small, generic records unrelated
  to `CLM-1001` or to Module 3's two named scenarios — proving the loader, indexing, and
  version-chain machinery work end to end without pre-building Module 3's specific
  demonstration data. The two Module 3 scenarios (amendment correcting a servicing
  provider; approval excluding a service date) are demonstrated exclusively through
  **isolated, `tmp_path`-scoped test fixtures**
  (`test_scenario_a_amendment_corrects_servicing_provider_and_preserves_both_versions`,
  `test_scenario_b_approval_and_service_date_applicability_are_separate_facts`), per the
  task's explicit instruction not to add production demo presets in this module.

## 5. Lookup outcome behavior

`lookup_authorization_source_by_reference(reference_number, *, store=None)`:

- **Blank/`None` reference number** → raises `ValueError` (caller/input bug), identical
  convention to `tools.prior_auth_tool.get_prior_authorization_by_id`.
- **No match anywhere in the fixture** → `NOT_FOUND`.
- **Every match traces back (via `amends_source_record_id`) to exactly one root** → `FOUND`,
  with `record` = the chain's current (un-amended-further) version and
  `version_history` = every version, oldest first.
- **Matches trace back to more than one independent root** → `AMBIGUOUS`. Nothing is
  selected; `candidates` lists every match. This is a genuine data-integrity signal
  (two unrelated authorizations coincidentally sharing a reference number), deliberately
  distinct from a normal multi-version amendment chain, which is `FOUND`, not
  `AMBIGUOUS`.
- **An unexpected failure reading the underlying store** (e.g. a broken store object) →
  `ERROR`, via a broad `except Exception` boundary mirroring
  `context/dispute_evidence_retriever.py`'s own external-subsystem-read pattern — kept
  distinct from `NOT_FOUND`, which means the lookup ran and legitimately found nothing.

`get_authorization_source_record_by_id(source_record_id, *, store=None)` is a direct,
always-unambiguous exact-key lookup (for citing one specific version, e.g. from a
`version_history` entry) — same blank/`None` → `ValueError` convention; returns `None`
when absent, never raises for "not found."

## 6. What remains intentionally unknown

- **Retroactive applicability of an amendment.** `retroactive_effective` defaults to
  `None` and is never inferred from `amended_at`, `decided_at`, or any date comparison —
  confirmed by `test_scenario_a_amendment_corrects_servicing_provider_and_preserves_both_versions`
  and `test_retroactive_effective_defaults_to_unknown_none`. A later amendment timestamp
  is not, on its own, evidence that the amendment applies to an earlier service.
- **Whether an authorized period covers any particular claim's service date.** The model
  has no claim reference and no `applies_to_service_date`-style field or method at all
  (`test_record_is_never_rejected_for_a_period_that_excludes_some_external_date`,
  `test_scenario_b_approval_and_service_date_applicability_are_separate_facts` both assert
  `not hasattr(record, "applies_to_service_date")`). That determination is left entirely
  to Module 3's comparison layer.
- **Whether a matched authorization-source record actually belongs to the claim under
  dispute.** A reference-number match is a lookup key only, exactly like the existing
  `submitted_authorization_lookup` in `context/dispute_evidence_retriever.py` already
  documents for the baseline dataset — this module makes the identical caveat explicit
  for the new, independent source.

## 7. How Module 3 should integrate this foundation

1. Add a new `gather_authorization_source_evidence`-style adapter in
   `context/dispute_evidence_retriever.py` (parallel to the existing
   `gather_structured_evidence`/`gather_policy_evidence`/`gather_graph_evidence`
   adapters), calling `lookup_authorization_source_by_reference` with the submission's
   `authorization_reference_number` and translating the `AuthorizationSourceLookupResult`
   into `EvidenceReference`/`EvidenceSourceOutcome` entries — almost certainly under a
   **new** `EvidenceProvenanceCategory` value (this module deliberately did not add one,
   since that vocabulary belongs to the evidence-package layer Module 3 owns).
2. Surface `AMBIGUOUS` and `ERROR` outcomes as their own, honestly-labeled evidence-source
   outcomes — never silently downgraded to `NOT_FOUND`.
3. Feed the full `version_history` (not just the current version) into evidence so a
   generated brief can, if warranted, discuss both an original decision and its
   amendment — never presenting only the latest version as if no history existed.
4. Leave `retroactive_effective`'s `None` case exactly as `None` in any evidence/prose
   layer — never phrase it as "not retroactive," which would silently convert an unknown
   into a negative fact.
5. Add the two named scenarios (amendment correcting a servicing provider; approval
   excluding the service date) as real entries in `data/authorization_source_records.json`
   (or a Module-3-owned preset mechanism), wire the new adapter into
   `skills/investigate_dispute.py`, and extend `dispute_review/ui.py` and
   `application/dispute_brief_validator.py`/prompts as needed — none of that is done here.

## 8. Validation results (actual, this module)

Run 2026-09-25, in `claim-dispute-review-copilot-v2`'s own newly-created `.venv`
(Python 3.14.5, dependencies installed from the reused `requirements.txt` with no
version changes):

| Check | Command | Result |
|---|---|---|
| Dependency conflicts | `python -m pip check` | `No broken requirements found.` |
| New focused tests | `pytest tests/test_authorization_source_models.py tests/test_authorization_source_lookup.py -q` | **38 passed** (20 model + 18 lookup) |
| New + relevant existing regression tests | `pytest tests/test_authorization_source_models.py tests/test_authorization_source_lookup.py tests/test_tools.py tests/test_data_store.py tests/test_dispute_review_presets.py tests/test_dispute_review_models.py tests/test_dispute_review_comparison.py tests/test_dispute_evidence_retriever.py tests/test_dispute_evidence_models.py tests/test_investigate_dispute_skill.py tests/test_dispute_workflow.py tests/test_synthetic_data_hygiene.py -q` | **199 passed** |
| Full offline suite | `pytest tests/ -q` | **733 passed**, 0 failed |

733 = 695 (historical baseline, carried over from the source project) + 38 new. This
733 count is an **actual run performed in this module**, not a historical claim.

**Historical reference only — NOT rerun in Module 2:**

- 695 passing tests (historical; not rerun in Module 1 or as a standalone figure in
  Module 2 — superseded by the 733 actual full-suite run above, which includes those
  695 plus this module's 38 new tests)
- End-to-end eval: 8/8 (historical; not rerun in Module 2)
- Safety eval: 9/9 (historical; not rerun in Module 2)

No `evals/e2e_eval.py` or `evals/e2e_safety_eval.py` run was performed in this module —
Module 2 added no generation-path or workflow-path change for those evals to exercise,
and the task's own instructions frame the 695/8/9 figures as historical reference, not
something to re-verify here.

## 9. Guardrail statements

- **All authorization-source records in this module are synthetic.** None was derived
  from, or represents, any real member, claim, or authorization.
- **No real payer or authorization system was contacted, queried, or authenticated,**
  anywhere in this module.
- **Matching fields — including a matching authorization reference number — do not
  authenticate an authorization.** A `FOUND` lookup result is a lookup outcome, never a
  verification.
- **Approval status does not, by itself, establish service-date applicability.** A
  record can be `APPROVED` and still not cover a given claim's date of service; nothing
  in this module resolves that comparison.
- **This copilot does not reverse denials, approve payment, or take any consequential
  action.** `AuthorizationSourceStore` is read-only; nothing in this module writes to
  any fixture, claim, or authorization record, and nothing here is wired into any
  generation, validation, or UI path yet.

## 10. Limitations / unresolved issues

- No integration point exists yet for this evidence layer — by design; that is Module
  3's job (§7).
- The real fixture's 3 records are intentionally generic and do not yet include the two
  named Module 3 scenarios; those exist only as isolated test fixtures, per the task's
  explicit "do not add production demo presets" instruction.
- `status` remains an unconstrained string (matching `PriorAuthorization.status`'s own
  convention) rather than a closed Enum — consistent with existing project style, but
  means a typo'd status string would not be caught by this model; no evidence this
  matters given the existing baseline model has the identical property.
