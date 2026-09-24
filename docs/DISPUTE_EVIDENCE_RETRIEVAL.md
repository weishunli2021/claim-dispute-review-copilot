# Dispute Evidence Retrieval (Module 6A)

Tools, three-source retrieval, and the `investigate_dispute` skill: a typed
evidence layer that combines the existing deterministic comparator
(Modules 3–5) with structured, vector-policy, and graph evidence, for one
dispute-review submission against one existing claim.

**Status: evidence assembly only.** No AI generation, no LLM judge, and no
UI change exist yet — this module produces a typed `DisputeEvidencePackage`
and nothing else. See "Module 6B contract requirements" below for what
consumes this package next, and [docs/DISPUTE_REVIEW_UI.md](DISPUTE_REVIEW_UI.md)
for the Dispute Review tab, which is **unmodified** and does not yet call
anything built in this module.

## Actual reused tools and new adapters

Reused exactly as-is (no wrapper, no reimplementation):

- `tools.case_context.get_case_context` — the same claim/member/plan/
  benefit/authorization/provider lookup Predefined Claims and the original
  investigation pipeline already use.
- `tools.provider_tool.get_provider` — looks up the SUBMITTED servicing
  provider id, when it differs from the claim's recorded one.
- `dispute_review.comparison.build_claim_snapshot` /
  `compare_submission` — the exact Module 3 comparator; never re-derived.
- `rag.retriever.search_policy` — the same semantic/vector retriever the
  original pipeline uses (`provider_name="semantic"`, `config=LARGE`,
  matching `context/hybrid_retriever.py`'s own defaults).
- `graph.retriever.get_claim_neighborhood` +
  `context.graph_filter.filter_claim_graph_context` — the claim's own
  graph relationships, filtered by the EXISTING claim-relevance filter,
  completely unmodified.
- `context.hybrid_retriever._identify_missing_evidence` — reused directly
  (not re-derived) for the structured missing-evidence list, per AGENTS.md
  rule 4 ("do not duplicate... retrieval... rules elsewhere").

Two small, minimal additions where an existing contract genuinely didn't
exist yet (not cosmetic wrappers — each adds a capability nothing else
provided):

- **`tools.prior_auth_tool.get_prior_authorization_by_id(authorization_id)`**
  — exact-id lookup against the full dataset (the existing
  `get_prior_authorizations(member_id, service_code)` is scoped to a
  member+service pair; a submitted authorization reference is arbitrary
  free text with no known member/service to scope by). Same conventions
  as every other tool: `require_identifier` validation, `None` for
  "not found," never fuzzy matching.
- **`graph.retriever.get_provider_neighborhood(provider_id, max_hops)`**
  — mirrors `get_claim_neighborhood`/`get_member_neighborhood` exactly (a
  third root-type wrapper around the same private `_neighborhood`
  helper). Needed because the submitted servicing provider may be a node
  the claim itself has no edge to.

New (Module 6A):

- `context/dispute_evidence_models.py` — `DisputeEvidencePackage`,
  `EvidenceReference`, `EvidenceSourceOutcome`, `EvidenceSourceStatus`,
  `EvidenceProvenanceCategory`.
- `context/dispute_evidence_retriever.py` — the three evidence adapters
  (`gather_structured_evidence`, `gather_policy_evidence`,
  `gather_graph_evidence`) and `build_dispute_evidence_package`, the
  dispute-evidence analogue of `context/hybrid_retriever.py`'s
  `build_evidence_package`.
- `skills/investigate_dispute.py` — the skill wrapping the above into
  `SkillResult`, the dispute-evidence analogue of `skills/investigate_claim.py`.

## `investigate_dispute` signature and return contract

```python
def investigate_dispute(claim_id: str, submission: DisputeSubmission) -> SkillResult
```

- **Input:** a claim id and an already-validated `DisputeSubmission`
  (Module 3). This function does not construct or re-validate a
  submission from raw input.
- **Output:** `skills.base.SkillResult` — `status` is one of:
  - `SkillStatus.NOT_FOUND` — no matching claim (`error` names the
    claim_id).
  - `SkillStatus.ERROR` — an unrecoverable exception in evidence assembly
    (the one broad exception boundary, mirroring `investigate_claim`).
  - `SkillStatus.COMPLETED` — a `DisputeEvidencePackage` was assembled,
    **however thin**. `evidence={"dispute_evidence_package": <dict>}`
    (same envelope pattern as `investigate_claim`'s
    `evidence={"evidence_package": ...}`). `missing_information` carries
    the package's own `missing_evidence` list.
- **No `SkillStatus.INSUFFICIENT_EVIDENCE` is ever returned by this
  skill** — see "No blanket sufficiency rule" below.
- Not registered in `skills.build_default_registry()` — consumed by direct
  import (`from skills.investigate_dispute import investigate_dispute`),
  the same way `agents/nodes.py` consumes `investigate_claim` directly
  rather than through the registry. Module 6B's workflow is expected to do
  the same.

### No blanket sufficiency rule

Per the task's explicit instruction, this skill does **not** implement "all
three sources returned something, therefore evidence is sufficient." It
returns `SkillStatus.COMPLETED` whenever a package was assembled at all —
including the `incomplete` preset, where two of four comparison rows are
`UNKNOWN`. Sufficiency judgment (what's needed to actually draft a brief)
is explicitly deferred to Module 6B's workflow, which reads the package's
`source_outcomes`, `missing_evidence`, `conflicts`, and `limitations`
itself.

## Evidence / reference / provenance design

Six sections on `DisputeEvidencePackage`, mapping directly to the task's
Step 2 list:

| # | Field | Contents |
| --- | --- | --- |
| 1 | `recorded_facts` | Structured facts already on file, PLUS any real record found by looking up a submitted identifier (kept visibly distinct — see below) |
| 2 | `submitted_fields` | Every non-blank `DisputeSubmission` field, verbatim, unverified |
| 3 | `comparison_result` + `comparison_findings` | The full `DisputeComparisonResult` (Module 3) plus one citable reference per row |
| 4 | `policy_passages` | Retrieved policy chunks |
| 5 | `graph_relationships` | Recorded graph relationships (claim's own + submitted provider's own, distinguished) |
| 6 | `source_outcomes` / `missing_evidence` / `conflicts` / `limitations` | Explicit availability, gaps, and caveats |

**Reference namespaces** (every `EvidenceReference.ref_id`, built only
from real fields — never fabricated, mirroring
`application/context_assembler.py`'s own `REF_ID` convention):

| Prefix | Category | Example |
| --- | --- | --- |
| `claim:` `member:` `plan:` `benefit:` `auth:` `provider:servicing:` `provider:ordering:` | Recorded facts | `provider:servicing:PRV-1001` |
| `submitted_provider_lookup:` | A REAL provider record found via the submitted id — never labeled `provider:servicing:` | `submitted_provider_lookup:PRV-1002` |
| `submitted_authorization_lookup:` | A REAL authorization record found via the submitted reference — never labeled `auth:` | `submitted_authorization_lookup:PA-1501` |
| `submitted:` | Raw submission fields, verbatim | `submitted:servicing_provider_id` |
| `policy:` | Retrieved policy chunk | `policy:imaging_policy::IMG-2::large::00` |
| `graph:` | The claim's own recorded relationship | `graph:claim:CLM-1001--SERVICED_BY-->provider:PRV-1001` |
| `graph_submitted_provider:` | The SUBMITTED provider's own relationship — never the claim's | `graph_submitted_provider:provider:PRV-1002--PARTICIPATES_IN-->network:meridian-silver-network` |
| `comparison:` | One comparator row | `comparison:servicing_provider` |

**Submission references never resemble verified authorization records** —
enforced structurally: a record found via a submitted identifier always
gets a `submitted_provider_lookup:`/`submitted_authorization_lookup:`
prefix and `provenance=RECORDED_VIA_SUBMITTED_LOOKUP`, never the
`provider:servicing:`/`auth:` prefix and `RECORDED` provenance used for
the claim's own already-established facts — even when the underlying
record is genuine. Tested by
`test_recorded_versus_submitted_provider_separation`-equivalent assertions
in `tests/test_dispute_evidence_retriever.py`.

**Six closed provenance categories**
(`context.dispute_evidence_models.EvidenceProvenanceCategory`): `RECORDED`,
`SUBMITTED_UNVERIFIED`, `RECORDED_VIA_SUBMITTED_LOOKUP`, `RETRIEVED_POLICY`,
`RECORDED_RELATIONSHIP`, `DETERMINISTIC_COMPARISON` — every reference uses
exactly one, never invented ad hoc.

**Per-source outcome tracking**
(`EvidenceSourceStatus`: `SUCCESS_WITH_EVIDENCE` / `SUCCESS_NO_RESULTS` /
`FAILURE`) — a legitimate empty result (no policy chunk crossed the
relevance cutoff, a submitted id not present in the dataset) is never
conflated with an actual retrieval failure (a malformed identifier, an
unexpected exception). `detail` on a `FAILURE` outcome is always a short,
clean message — never a raw exception string. Verified with controlled
fixtures in `tests/test_dispute_evidence_retriever.py` (forcing
`search_policy`/`get_claim_neighborhood` to raise vs. return empty).

**Retrieval scores are metadata, never confidence:** `EvidenceReference.score`
carries a policy passage's own similarity score (0.42–0.57 range observed
below) — present only on `policy:` references, and never described as an
accuracy or confidence probability anywhere in this module.

**Graph and structured facts share one source, stated explicitly:** every
`DisputeEvidencePackage.limitations` list includes *"Graph relationships
and structured facts are drawn from the same underlying synthetic dataset
and should not be treated as independent confirmation of one another."*

## Retrieval scope and limits

- **Structured:** one `get_case_context` call; at most one submitted-provider
  lookup and one submitted-authorization-reference lookup, each performed
  **only when the submitted value differs from what's already recorded**
  (avoids redundant evidence — e.g. the `matching` preset triggers neither).
- **Policy:** exactly one `search_policy` call, `top_k=3` (documented
  constant `DEFAULT_DISPUTE_POLICY_TOP_K`), config `LARGE`, provider
  `"semantic"`. The query is built deterministically from the recorded
  service code, recorded denial reason, a fixed per-discrepancy-field hint
  for each comparator row that isn't a `MATCH`, and the submitted
  servicing-provider id — **`dispute_explanation` is never included**, so
  free-text submission narrative can never expand or redirect retrieval
  scope. No iterative retrieval loop — one query, one call.
- **Graph:** one `get_claim_neighborhood` call (`max_hops=2`, matching the
  original pipeline's own default) through the existing claim filter;
  conditionally one `get_provider_neighborhood` call for the submitted
  provider (`max_hops=1`, deliberately small), **filtered to forward-only
  edges** (`source_id == the provider's own node`) — this is what prevents
  another claim that merely shares the same provider from leaking in,
  regardless of hop count. Never creates a node or edge for the submitted
  provider or authorization; only reads what already exists.

## Policy and graph coverage limitations

- The synthetic policy corpus (`documents/*.md`) contains **no passage
  about changing or substituting a servicing provider on an already-
  authorized service** — confirmed by reading all five documents directly.
  `gather_policy_evidence` detects this deterministically (a small,
  documented keyword check — `"change"`, `"transfer"`, `"substitut"`, …)
  and appends an explicit limitation whenever the comparator found a
  Servicing Provider mismatch and no retrieved passage mentions it. This
  fired correctly for the `different_servicing_provider` preset (see
  below) and did not fire for `matching`/`incomplete` (no such
  discrepancy).
- Graph relationship coverage is exactly what `context/graph_filter.py`
  already scopes for claim investigation (member/plan/benefit/service/
  provider/network/authorization edges for THIS claim only) plus, when
  applicable, the submitted provider's own `PARTICIPATES_IN` edge. It does
  not model dispute-specific relationships (there is no "DISPUTED_BY" edge
  or similar) — graph evidence here answers "what is recorded," not "is
  this dispute valid."

## Test results

Using `D:\AI\claude-code\claim-dispute-review-copilot\.venv\Scripts\python.exe`, no live OpenAI calls:

| Suite | Result |
| --- | --- |
| New focused (`test_dispute_evidence_models.py`, `test_dispute_evidence_retriever.py`, `test_investigate_dispute_skill.py`) | **33 passed** |
| Tool/graph additions (`test_tools.py` +4, `test_graph_retriever.py` +3) | included below |
| `test_skills_no_duplication.py` (extended to cover `investigate_dispute`) | **5 passed** |
| Existing dispute-review suite (Modules 3–5, unmodified) | **79 passed** |
| Full `pytest tests/ -q` | **542 passed**, 0 failed, 0 skipped, 0 errors |
| `pytest tests/test_e2e_eval.py tests/test_e2e_safety_eval.py` (pytest-wrapped E01–E08/S01–S09) | **9 passed** — original investigation behavior fully unaffected |

542 = the prior 500-test baseline + 33 new dispute-evidence tests + 4 new
`prior_auth_tool` tests + 3 new `graph_retriever` tests + 1 new
`investigate_dispute`-specific reuse-discipline test + 1 net addition to
`SKILL_MODULES`'s generic checks (no new test function, existing checks
now also cover the new skill). No existing test was weakened; retrieval
benchmarks (`retrieval_eval`, `graph_retrieval_eval`, `hybrid_*_eval`,
`agent_eval`, `skill_eval`) were not rerun, per the task's explicit
instruction.

## Per-preset evidence report (real components, CLM-1001)

### Matching preset

- **Structured facts found:** claim, member, plan, benefit, servicing
  provider (`PRV-1001`), ordering provider (`PRV-4001`) — 6 recorded
  facts. No prior-authorization candidates on file (flagged in
  `missing_evidence`, not treated as a denial).
- **Policy references retrieved (3):** `prior_authorization_policy::PA-2`
  (score 0.572), `imaging_policy::IMG-2` (0.564), `benefits_guide::BEN-2`
  (0.522).
- **Graph relationships (10):** the claim's full filtered neighborhood —
  BELONGS_TO/FOR_SERVICE/SERVICED_BY/ORDERED_BY from the claim,
  ENROLLED_IN/HAS_BENEFIT/USES_NETWORK/PARTICIPATES_IN chains.
- **Unverified submission info:** all 8 fields present, `Provider`-supplied.
- **Gaps/errors:** `submitted_authorization_lookup` → `SUCCESS_NO_RESULTS`
  (the synthetic reference isn't a real id, as expected); no failures.

### Different servicing provider preset

- **Structured facts found:** the same 6, **plus**
  `submitted_provider_lookup:PRV-1002` (`RECORDED_VIA_SUBMITTED_LOOKUP`) —
  visibly separate from `provider:servicing:PRV-1001` (`RECORDED`).
- **Policy references retrieved (3):** `prior_authorization_policy::PA-2`
  (0.532), `claims_denial_guide::CLM-2` (0.517), `imaging_policy::IMG-2`
  (0.497).
- **Graph relationships (11):** the claim's 10 + 1 —
  `graph_submitted_provider:provider:PRV-1002--PARTICIPATES_IN-->network:meridian-silver-network`
  — PRV-1002's own relationship only; no other claim that also references
  a shared provider ever appears (verified directly against real data
  where PRV-1001 is shared by CLM-1001 and CLM-1002 — see
  `test_submitted_provider_graph_evidence_excludes_unrelated_claims`).
- **Unverified submission info:** all 8 fields present, submitted provider
  `PRV-1002` (a real, different, same-type facility).
- **Gaps/errors:** the "no policy passage addresses changing servicing
  provider" limitation fired correctly; no failures.

### Incomplete preset

- **Structured facts found:** the same 6 (no submitted-provider lookup —
  the field is blank).
- **Policy references retrieved (3):** `imaging_policy::IMG-2` (0.566),
  `prior_authorization_policy::PA-4` (0.560), `claims_denial_guide::CLM-2`
  (0.530).
- **Graph relationships (10):** identical to `matching`'s claim-side set
  (no submitted-provider query — nothing to look up).
- **Unverified submission info:** 6 of 8 fields present (`servicing_provider_id`
  and `authorization_end_date` omitted, matching the preset's design),
  `Patient`-supplied.
- **Gaps/errors:** no failures; the two omitted fields surface as
  `UNKNOWN` comparison rows (unchanged Module 3 behavior), not as a
  retrieval error.

Every result above's `limitations` list also always includes the two
fixed, dataset-wide caveats (record absence ≠ nonexistence elsewhere;
graph and structured facts share one source).

## Module 6B contract requirements (recorded, NOT implemented)

Per the task's Step 7 — documented here for the next module, nothing below exists in code yet.

**A. Bounded workflow** — uses the existing LangGraph dependency (already
in `requirements.txt`, used by `agents/case_agent.py`); invokes
`investigate_dispute`; keeps retrieval status, generation status,
validation status, and judge status as four separate fields (mirroring
`ApplicationResult`'s existing three-way separation, extended by one);
explicit failure routes for NOT_FOUND/ERROR; no autonomous/looping
retrieval — one pass through evidence, one generation call, one
validation pass, per run.

**B. LLM brief** — receives the submission, comparison findings, and
retrieved evidence (i.e., the `DisputeEvidencePackage` this module
produces); structured output with evidence references (citing this
module's `ref_id`s); must distinguish recorded facts from unverified
submission content (the `provenance` field this module already attaches to
every reference is exactly what makes that distinction checkable);
explains discrepancies/uncertainties/verification questions; cannot
approve, reverse, deny, or pay a claim (same advisory-action constraint as
the existing `ActionCode` allowlist); evidence gating scoped to what the
brief can actually support — a thin package (e.g. the `incomplete` preset)
must produce limitations or a restricted review package, never a
fabricated conclusion.

**C. Deterministic validation** — citation/reference existence against
this package's actual `ref_id`s; required citations per finding; consistency
with `comparison_result`'s own Match/Mismatch/Unknown verdicts (a brief
claiming a match where the comparator found a mismatch must fail
validation); unverified-provenance preservation (the brief must not upgrade
a `SUBMITTED_UNVERIFIED`/`RECORDED_VIA_SUBMITTED_LOOKUP` reference into an
authenticated fact); advisory action constraints; a validation failure must
never remove or hide the deterministic `comparison_result` still sitting on
the package.

**D. Optional LLM judge** — a separate, explicit action after a valid
brief exists; receives this same evidence package, `comparison_result`,
and the generated brief; four dimensions (evidence grounding; coverage of
material findings in the supplied evidence; uncertainty/provenance;
authority boundaries), each with an anchored 1–5 rubric, PASS/FAIL/
UNCERTAIN, rationale, and supporting references (mirroring
`application/judge_models.py`'s existing `DimensionResult` shape exactly);
a Python-computed average, never LLM-invented; a failed dimension stays
visible regardless of the average; judge failure → a failed evaluation
status, never invented scores; judge output can never alter the
comparison, brief, validation, or any human decision; any change to
input/evidence/brief invalidates a prior judge result (the same
`run_id`-based staleness guard `application/judge_models.py`'s
`SemanticJudgeEnvelope` already uses); calibration claims require
human-rated examples not yet collected.

## Files added/modified

**Added:**
- [context/dispute_evidence_models.py](../context/dispute_evidence_models.py)
- [context/dispute_evidence_retriever.py](../context/dispute_evidence_retriever.py)
- [skills/investigate_dispute.py](../skills/investigate_dispute.py)
- [tests/test_dispute_evidence_models.py](../tests/test_dispute_evidence_models.py)
- [tests/test_dispute_evidence_retriever.py](../tests/test_dispute_evidence_retriever.py)
- [tests/test_investigate_dispute_skill.py](../tests/test_investigate_dispute_skill.py)
- [docs/DISPUTE_EVIDENCE_RETRIEVAL.md](DISPUTE_EVIDENCE_RETRIEVAL.md) (this file)

**Modified:**
- [tools/prior_auth_tool.py](../tools/prior_auth_tool.py) — added `get_prior_authorization_by_id`.
- [graph/retriever.py](../graph/retriever.py) — added `get_provider_neighborhood`.
- [tests/test_tools.py](../tests/test_tools.py) — 4 new tests for the new tool function.
- [tests/test_graph_retriever.py](../tests/test_graph_retriever.py) — 3 new tests for the new graph function.
- [tests/test_skills_no_duplication.py](../tests/test_skills_no_duplication.py) — `investigate_dispute` added to `SKILL_MODULES`, plus one new dedicated test.

No change to `app.py`, `dispute_review/ui.py`, `dispute_review/models.py`,
`dispute_review/comparison.py`, `dispute_review/presets.py`,
`application/*`, `agents/*`, `data/*.json`, or any policy document.

## Readiness for 6B

Ready. `investigate_dispute` is a stable, tested, directly-importable
entry point returning a complete, typed `DisputeEvidencePackage` — exactly
what a bounded LangGraph workflow needs to gate generation on. No UI or
generation code was touched, so Module 6B can build the workflow/generator/
validator/judge layer without any risk of having silently changed what
this module already validated.
