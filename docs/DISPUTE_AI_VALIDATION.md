# Dispute Review AI — Final Integrated Validation (Module 6D)

This is the final, integrated validation record for the Dispute Review AI extension
(Modules 6A–6D): tools → `investigate_dispute` skill → evidence package → bounded
`dispute_workflow` → `dispute_generator` → `dispute_brief_validator` → optional
`dispute_judge` → the Dispute Review Streamlit tab. It supersedes nothing in
[docs/DISPUTE_REVIEW_VALIDATION.md](DISPUTE_REVIEW_VALIDATION.md) (Module 5's
deterministic-comparison-only record, still accurate for that layer) and complements
[docs/DISPUTE_AI_WORKFLOW.md](DISPUTE_AI_WORKFLOW.md) and
[docs/DISPUTE_REVIEW_UI.md](DISPUTE_REVIEW_UI.md), which describe the mechanisms this
document validates rather than re-describing them.

## 1. End-to-end integration review

Traced the full path tools → skill → evidence → workflow → generator → validator →
judge → UI and confirmed, by reading the actual code (not by assumption):

- **Zero-model-call comparison** (`dispute_review/comparison.py` +
  `dispute_review/ui.py`'s "Compare submitted information" button) never imports or
  calls into `application/dispute_generator.py`, `application/dispute_judge.py`, or the
  `openai` package — confirmed both by `tests/test_project_structure.py`'s existing
  OpenAI-SDK-confinement test and by direct inspection.
- **Three-source investigation**: `skills/investigate_dispute.py` retrieves from
  structured tools (`tools/prior_auth_tool.py`, `tools/provider_tool.py`, etc.), the
  policy vector store (`rag/`), and the knowledge graph (`graph/retriever.py`) — all
  three source outcomes are recorded on `DisputeEvidencePackage.source_outcomes`
  regardless of individual success/failure, exactly mirroring the original
  `investigate_claim`'s three-source discipline.
- **Generation consumes the NEW submission + comparisons**: `assemble_dispute_context`
  (`application/dispute_context.py`) embeds `comparison:*` references built directly
  from `DisputeComparisonResult.rows` (Rule C in `dispute_brief_validator.py` is a
  structural regression guard on exactly this) and `submitted:*` references built
  directly from the `DisputeSubmission` — the prompt the model sees always includes
  both.
- **Judge is a separate, optional call**: `application/dispute_judge.py` is never
  imported by `application/dispute_workflow.py` (regression-guarded by
  `test_judge_never_runs_automatically_when_workflow_runs`, an AST-based import check,
  not just a behavioral test) and is only ever invoked by the UI after
  `is_accepted_dispute_draft(result)` is true and the analyst explicitly clicks "Run AI
  semantic evaluation."
- **Recorded-vs-unverified distinction**: every submitted-field evidence reference
  carries `EvidenceProvenanceCategory.SUBMITTED_UNVERIFIED` (or
  `RECORDED_VIA_SUBMITTED_LOOKUP` for a resolved-but-unverified lookup); every recorded
  fact carries `RECORDED`/`RECORDED_RELATIONSHIP`; comparison outputs carry
  `DETERMINISTIC_COMPARISON`. Rule E in the validator exists specifically to keep a
  brief from re-describing `SUBMITTED_UNVERIFIED` evidence as authenticated.
- **Optional judge, provider-change policy gap visibility**: `assess_evidence_gate`
  (`application/dispute_workflow.py`) degrades to `READY_FOR_LIMITED_BRIEF` (never
  silently upgrades to full confidence) when the policy or graph source comes back
  empty, and the "Different servicing provider" preset's own caption states plainly
  that a provider-ID match/mismatch says nothing about that facility's ability to
  perform the service — the generation layer never fills that gap with an inference.
- **No authenticity/approval/reversal/coverage/payment implications**: enforced at two
  independent layers — `application/brief_validator.py`'s reused
  `PROHIBITED_AUTHORITY_PATTERNS` (Rule F) at the deterministic layer, and the
  `authority_boundaries` judge dimension at the semantic layer. Module 6D Step 4's
  `false_verification_claim` example (see §4) demonstrates Rule E catching one concrete
  instance of this class before it would ever reach a human.
- **Citation traceability**: every `Finding` and the `suggested_next_step` carry
  `evidence_refs`; Rule A (`evidence_reference_existence`) rejects any reference id not
  present in the exact `DisputeGenerationContext` shown to the model for that request.
- **Failure-path evidence retention**: `run_dispute_workflow` always returns whatever
  `evidence_package`/`comparison_result` the skill produced, on every path including
  `BLOCKED` and every generation/validation failure — confirmed by existing
  `test_dispute_workflow.py` coverage, re-run clean in §3 below.
- **No-stale-brief/judge guarantees**: `dispute_review/ui.py`'s fingerprint-based
  invalidation (`_current_fingerprint`) clears a stale comparison/workflow result on any
  committed field edit; the judge envelope carries its own, stricter
  `_judge_binding_key` — see §2A, the one substantive code change this module made.

**Additive (non-byte-identical) original-file changes carried by this extension, for
the record** — the codebase does **not** claim every original Module 1–7 file is
byte-identical to the pre-extension baseline. Two original files were extended
additively (new function, no existing behavior changed or removed):
- [tools/prior_auth_tool.py](../tools/prior_auth_tool.py) — added
  `get_prior_authorization_by_id(authorization_id)` (Module 6A), a lookup used by the
  dispute-evidence retriever; the pre-existing prior-authorization lookup functions are
  unchanged.
- [graph/retriever.py](../graph/retriever.py) — added
  `get_provider_neighborhood(provider_id, max_hops=2)` (Module 6A), used to retrieve
  graph relationships for a *submitted* provider id; `get_claim_neighborhood` and every
  other pre-existing function are unchanged.

Every other original Module 1–7 file (`agents/`, `application/investigation_service.py`,
`context/hybrid_retriever.py`, etc.) received no changes in Modules 2–6D.

## 2. Four specific gaps closed

### A. Judge binding

**Gap**: the judge envelope's staleness guard previously bound only to
`run_id + brief` (`dispute_review/ui.py`'s `_judge_binding_key`). Because
`DisputeWorkflowResult` is not `frozen=True` (only `extra="forbid"`), a hypothetical
future in-place mutation of `generation_context` (the evidence, deterministic-comparison
summary, and limitations actually shown to the judge) with `run_id` and `brief` left
untouched would not have been detected.

**Fix**: extended the one canonical helper,
[`dispute_review/ui.py`](../dispute_review/ui.py)'s `_judge_binding_key`, to also bind
to `generation_context.model_dump_json()`. No second, competing fingerprint mechanism
was introduced — this is still the single canonical binding key, now covering
`run_id + brief + generation_context` (which itself carries the evidence references,
deterministic comparison findings, and limitations).

**Focused regression test**: `tests/test_dispute_review_ui.py::test_judge_binding_rejects_evidence_change_with_unchanged_run_id_and_brief`
constructs exactly the "evidence changed but run_id+brief unchanged" scenario (a
`model_copy(update=...)` that tampers `generation_context.limitations` while leaving
`run_id` and `brief` identical to what the displayed judge envelope was bound to) and
asserts `get_display_judge_envelope` correctly discards the stale envelope. **PASSED.**

### B. Model-call bounds — call counts AND SDK retry settings

**Gap**: prior modules verified adapter-level call counts (one `generate()`/`evaluate()`
call per workflow run) but had no test asserting the OpenAI SDK's own automatic-retry
setting, so one logical call was not provably one network attempt.

**Finding**: both new adapters (`OpenAIDisputeBriefAdapter`, `OpenAIDisputeJudgeAdapter`
in `application/dispute_generator.py` / `application/dispute_judge.py`) already
construct their `openai.OpenAI(...)` client with `max_retries=0`, identical to the two
original adapters (`application/llm_adapter.py`, `application/semantic_judge.py`) — this
was a verification gap, not a code defect. **The original adapter was not touched.**

**Closed with**: `tests/test_dispute_generator.py::test_openai_adapter_disables_sdk_automatic_retries`
and `tests/test_dispute_judge.py::test_openai_judge_adapter_disables_sdk_automatic_retries`,
each spying on the actual `openai.OpenAI(**kwargs)` constructor call (not reading source)
to assert `kwargs["max_retries"] == 0`. **Both PASSED.**

### C. Shared-state preservation — in-memory objects, not just file hashes

**Gap**: `test_dispute_workflow.py` (workflow-level) and
`test_dispute_review_ui.py` (combined UI-level, investigate+judge together) already
covered shared-state non-mutation, but no test isolated `run_dispute_judge` itself at
the backend level, and none inspected in-memory `DataStore`/graph objects directly
(only source-file-adjacent behavior).

**Closed with**: `tests/test_dispute_judge.py::test_run_dispute_judge_does_not_mutate_shared_state`
— snapshots `get_data_store().claims`, `.providers`, and
`graph.retriever.get_claim_neighborhood("CLM-1001")` before running an accepted draft
through `run_dispute_judge`, then asserts the singleton `DataStore` instance is
unchanged (`store_after is store`) and every snapshotted value is identical afterward.
**PASSED.**

### D. Server-log noise (Streamlit file-watcher / torchvision tracebacks)

**Investigation**: the noise reported during Module 6C's browser check is a
`transformers`/`torchvision` file-watcher traceback
(`ModuleNotFoundError: No module named 'torchvision'`) triggered by Streamlit's
hot-reload watcher walking `transformers`' optional image-processing submodules — the
same pattern was re-inspected here (not dismissed solely for lacking a `dispute_*`
filename, per the explicit constraint) by reading the raw redirected log file directly.
Confirmed: the traceback originates entirely inside `transformers`' own module-discovery
code, triggered by Streamlit's source-file watcher (unrelated to any code in this
extension), and is unrelated to request handling — it did not block the server from
starting or from correctly serving every route exercised.

**Workaround tested**: launching with `--server.fileWatcherType=none` (a Streamlit CLI
flag, no dependency change, no code change) eliminated 100% of the noise. Verified via a
dedicated, isolated server instance on port 8502:
- Startup log was completely clean (0 error/traceback/torchvision lines, vs. roughly
  1,100 in Module 6C's equivalent check without the flag).
- Clicked through the full functional path that originally triggered the noise: loaded
  the "Matching fields" preset, ran "Compare submitted information" (correct 4/4-match
  result), then clicked "Investigate dispute evidence" — which exercises the same
  RAG-embedding-loading code path — and observed the correct evidence-gate,
  missing-evidence, and (absent credentials) generation-failure messaging.
- Re-checked the log after that interaction: still 0 error/traceback lines.

**Conclusion**: `--server.fileWatcherType=none` is a safe, launch-only workaround —
it changes no business logic (it only disables Streamlit's own source-file watcher,
which a demo launch does not need) and produces observably identical application
behavior. Documented in the exact launch command below; no dependency upgrade was made.

## 3. Final offline validation (actual, observed counts)

Run on 2026-09-23, in the project's own `.venv` (Python 3.14.5):

| Check | Command | Result |
|---|---|---|
| Dependency conflicts | `python -m pip check` | `No broken requirements found.` |
| Full unit/integration suite | `python -m pytest tests/ -q` | **672 passed**, 1 warning (pre-existing `chromadb`/`asyncio` deprecation warning, unrelated to this extension), 0 failed |
| End-to-end eval | `python -m evals.e2e_eval` | **8 of 8** defined prototype scenarios passed |
| Safety eval | `python -m evals.e2e_safety_eval` | **9 of 9** defined prototype scenarios passed |

672 = 660 backend/unit tests (Module 6D's Step 2 additions: 1 judge-binding regression
test, 2 SDK-retry spy tests, 1 shared-state backend test) + 12 new
`tests/test_dispute_judge_qualitative_examples.py` tests (Step 4, below). This grows
Module 6C's own last recorded full-suite count of 627 by the sum of every test added in
6D. No test was loosened, skipped, or had its assertion weakened to reach these numbers;
no historical evaluation report (`artifacts/e2e_eval_results.json`,
`artifacts/e2e_safety_eval_results.json`) was altered.

No defect requiring a code fix outside of §2's two intentional changes (judge binding
extension, new tests) was found during this module. No pre-existing issue was
discovered that required separate reporting beyond what prior modules' docs already
record.

## 4. Developer-authored synthetic judge evaluation set (NOT a calibration dataset)

[`evals/dispute_judge_qualitative_examples.py`](../evals/dispute_judge_qualitative_examples.py)
defines four hand-written illustrative examples, each built against **real** CLM-1001
evidence (real reference ids, real deterministic comparison results — no invented data),
explicitly labeled in its own module docstring as **not** a calibration dataset and
**not** a measurement of live judge quality:

| Example | Concern | Deterministic outcome | Reaches judge? |
|---|---|---|---|
| `well_grounded` | none — control case | PASSED | Yes (illustrative judge: all PASS) |
| `invented_policy` | states a specific named "provider-matching policy" that no retrieved evidence actually establishes, citing only a real comparison reference that doesn't say what the brief claims | **PASSED** (Rule A only checks the reference id exists, never that it supports the claim) | Yes — only a judge/human can catch this |
| `omitted_mismatch` | never mentions a real servicing-provider MISMATCH the deterministic comparator found | **PASSED** (no rule inspects what a brief chose not to say) | Yes — only a judge/human can catch this |
| `false_verification_claim` | describes `SUBMITTED_UNVERIFIED` evidence as "confirmed" and claims the denial has already been "reversed" | **FAILED** (Rule E: `unverified_provenance_preserved`) | **No** — correctly rejected before `is_accepted_dispute_draft` gate, mirroring production |

`invented_policy` and `omitted_mismatch` are the deliberately interesting cases: this
evaluation set exists specifically to demonstrate and regression-guard that the
deterministic validator and the LLM judge cover **disjoint** failure classes, not
redundant ones.

[`tests/test_dispute_judge_qualitative_examples.py`](../tests/test_dispute_judge_qualitative_examples.py)
(12 tests, all passing, included in §3's 672) verifies purely offline **plumbing** —
that each example reaches its expected deterministic outcome, and that
`run_dispute_judge` correctly threads a `FakeDisputeJudgeAdapter` response carrying the
developer's hand-picked "illustrative" judge dimensions through to a properly
`run_id`-bound `DisputeJudgeEnvelope` — never a claim about what a real model would
score.

**Incidental finding**: while wording the `well_grounded` example, an initial finding
statement ("has not been independently **verified**") false-positived Rule E's
`unverified_provenance_preserved` check, because that rule is a documented, narrow,
**non-negation-aware** keyword-containment check (see
`application/dispute_brief_validator.py`'s own module docstring and README's "Known
limitations"). This is a known, already-documented limitation, not a new defect — fixed
by rewording the example, not by weakening the validator or the test.

## 5. Live smoke check — COMPLETED (2026-09-23)

**Status: COMPLETED.** A `.env` was subsequently supplied in this project by the user
(user-owned, never copied from the original reference project). Configuration was loaded
exclusively through the real application path
(`application.config.load_llm_config()`, which itself calls `python-dotenv`'s
`load_dotenv()`) — never re-implemented or read ad hoc. **The API key value was never
printed, logged, or inspected** — only its presence and length were checked.

- **Configured model** (from `LLMConfig.model`): `gpt-5.6-luna`. Reported verbatim, as
  configured by the user — this session never substituted a different model. All three
  live generation calls below succeeded in reaching this model and receiving a
  schema-valid structured response, confirming it is a real, callable model on the
  user's account.
- **Embedding provider actually invoked by dispute retrieval**: **`semantic`**
  (`rag.embeddings.SemanticEmbeddingProvider`, a local sentence-embedding model — not an
  OpenAI call), confirmed by **reading the call path**, not by inferring it from
  `RAG_EMBEDDING_PROVIDER`. Trace: `context/dispute_evidence_retriever.py`'s
  `gather_policy_evidence` calls
  `search_policy(query, top_k=top_k, config=DEFAULT_POLICY_CONFIG, provider_name=DEFAULT_POLICY_PROVIDER)`
  where `DEFAULT_POLICY_PROVIDER = "semantic"` is a **hardcoded module constant**
  (deliberately mirroring `context/hybrid_retriever.py`'s identical hardcoded default for
  the original investigation pipeline, per that module's own comment). `search_policy`
  (`rag/retriever.py`) passes `provider_name` straight into `VectorStore(...,
  provider_name=provider_name)`, which calls `get_embedding_provider(provider_name)` — the
  **explicit-name** entry point in `rag/embeddings.py`, never
  `get_default_embedding_provider()` (the **only** function that reads
  `RAG_EMBEDDING_PROVIDER`). **The dispute-review policy retrieval path never reads
  `RAG_EMBEDDING_PROVIDER` at all** — it always uses `"semantic"`, regardless of that
  variable's value. (`RAG_EMBEDDING_PROVIDER` does still govern whichever other code path
  calls `get_default_embedding_provider()` directly — not exercised by this check.)

### Live requests made

**3 of 3 generation requests used** (the maximum authorized) — one per preset:
"Matching fields," "Different servicing provider," "Incomplete submission." **0 of 3
judge requests used** — no live-generated draft passed deterministic validation, so
`is_accepted_dispute_draft` correctly never allowed a judge call; nothing here retried a
generation or judge call. Total live model requests made *deliberately, through the
authorized browser workflow*: **3**. See the "Additional finding" subsection below for
one further, unbudgeted live request this check inadvertently triggered afterward via
an offline test-suite run — total live model requests made in this entire check,
including that one: **4**.

### Results table

| Preset | Generation | Validation | Judge scores/verdicts | Issues |
|---|---|---|---|---|
| Matching fields | DRAFTED | **FAILED** (`evidence_reference_existence`, `unverified_provenance_preserved`) | Not run — draft not accepted | (1) **Real defect**: `suggested_next_step.evidence_refs` cited the bare string `'prior_authorization'`, which is not a reference id that exists anywhere in the evidence shown to the model (real ids look like `submitted:*`, `comparison:*`, `claim:*`, etc.) — a fabricated/malformed citation. (2) **Validator false positive**: a finding correctly stated the authorization "has not been authenticated," but Rule E's non-negation-aware substring check flagged the word "verified" appearing inside earlier hedging language in the same statement. |
| Different servicing provider | DRAFTED | **FAILED** (`unverified_provenance_preserved` only) | Not run — draft not accepted | **Validator false positive only** — no fabricated citation this time; the finding said the submission states information "on an unverified basis," which trips the same Rule E substring check. The servicing-provider MISMATCH and the provider-change policy gap were both correctly surfaced (comparison row + an explicit Evidence Limitation stating no retrieved policy passage addresses a servicing-provider change). |
| Incomplete submission | DRAFTED | **FAILED** (`unverified_provenance_preserved` only) | Not run — draft not accepted | **Validator false positive only** — a finding said submitted details "remain unverified," again tripping Rule E. Both missing fields (servicing provider, authorization end date) were correctly rendered as `UNKNOWN` comparison rows, never guessed as Match or Mismatch. |

### Inspection against actual evidence (scope note)

Because every draft was rejected, the UI — by design, matching production behavior —
never rendered a rejected draft's full brief text to the reviewer (only the evidence
panel and the specific finding text quoted inside each validation-issue message). This
inspection is therefore based on: the full evidence panel shown for each request (every
recorded fact, submitted field, retrieved policy passage, graph relationship, and
comparison finding — all confirmed correct against the actual claim/submission data),
plus the finding-text excerpts quoted inside the validation issues above. It is **not** a
review of each draft's complete summary/findings/verification-questions text, which the
UI intentionally withholds for a rejected draft. Within that scope:
- Deterministic comparisons were described correctly in every case (4/4 match, 3/4 match
  with the Servicing Provider mismatch named, 2/4 match with the two missing fields named
  as Unknown, never guessed).
- The one citation inspected in full (the fabricated `'prior_authorization'` reference on
  preset 1) did **not** support its associated statement — it does not exist. Every other
  reference id visible in the quoted finding text (e.g. `submitted:authorization_reference_number`,
  `submitted:member_id`, etc., on presets 2–3) matched real, present evidence.
- The submitted authorization was described as unverified in every finding inspected —
  correctly, in wording; the *validator's* keyword check does not distinguish that
  correct hedge from an actual overclaim (see below).
- The provider-change policy gap was explicitly acknowledged (preset 2's Evidence
  Limitations).
- Missing submission fields remained visible as `UNKNOWN`, never silently dropped or
  guessed (preset 3).
- No invented authorization, policy requirement, approval, reversal, or payment
  conclusion was observed anywhere inspected.
- Verification questions were not inspected in full for any preset (all three drafts
  were rejected before that field would normally be reviewed); this is an acknowledged
  gap in this smoke check, not a claim that they were checked and found acceptable.

**A validator pass would not have established correctness on its own, and a validator
FAIL does not establish a real content defect either** — as this check found directly:
2 of 3 failures were validator false positives, not actual brief defects.

### Failure classification (per request)

- **Matching fields**: one **real defect** (fabricated `'prior_authorization'` citation)
  **and** one **validator false positive** (negation-unaware Rule E).
- **Different servicing provider**: **validator false positive only** (negation-unaware
  Rule E); no schema/provider/configuration failure, no real content defect identified in
  the inspected portion.
- **Incomplete submission**: **validator false positive only** (negation-unaware Rule E);
  no schema/provider/configuration failure, no real content defect identified in the
  inspected portion.

No shared configuration/authentication/model-access failure occurred at any point — all
three generation calls reached the model and returned a schema-valid, parseable
`DisputeBrief`. No retries were made or needed.

### A systematic pattern, not three isolated false positives

All three live drafts independently chose to hedge unverified submitted information using
the word **"unverified"** (or "on an unverified basis") somewhere in a finding that also
cites `SUBMITTED_UNVERIFIED`/`RECORDED_VIA_SUBMITTED_LOOKUP` evidence — which is exactly
the correct, desired phrasing this project's evidence-provenance model asks for. Rule E's
`_VERIFIED_CLAIM_WORDS` containment check (`"confirmed"`, `"verified"`, `"authenticated"`,
`"validated"`) is not negation-aware (documented, pre-existing limitation — see §7), so
the substring `"verified"` inside `"unverified"` triggers the same rejection meant for an
actual overclaim like "has been verified." With this live model's phrasing habits, this
was **not** a rare edge case: it fired on 3 of 3 requests, meaning in this smoke test **no
live-generated dispute brief could ever reach the optional judge**, regardless of the
brief's actual quality. This is a genuine, live-verified usability/demo risk, not a
per-example edge case (contrast with the single, deliberately-constructed
`false_verification_claim` example in §4, which triggered the *same* rule for the
*correct* reason — an actual overclaim).

### Additional finding: the offline test suite is not hermetic against a real `.env`

After completing the 3 authorized browser requests above, this check re-ran the full
offline `pytest tests/ -q` suite as a routine "confirm nothing broke" step (no live
requests were intended by that step). **That run produced 671 passed, 1 failed**, and
the failure is itself a live-check finding, not a code regression:
`tests/test_dispute_review_ui.py::test_missing_configuration_shows_actionable_message_without_credentials`
asserts that, with no API key configured, generation fails with
`GenerationFailureCategory.CONFIGURATION`. It relies on ambient environment variables
genuinely being absent rather than explicitly blanking them — but
`application/config.py`'s `load_llm_config()` calls `load_dotenv()`, which fills in any
variable **missing from the real environment** from a local `.env` if one exists. With
this project's now-real `.env` present, that assumption silently became false: the test
made a **real, live generation call** (`generation_status` came back `DRAFTED`, not
`FAILED`) instead of exercising the missing-configuration path, and its own assertion
correctly caught the mismatch.

**This consumed one additional, unbudgeted live generation request** — a 4th, beyond
the 3 authorized and reported above — introduced inadvertently by running the full
offline suite after `.env` existed, not by a deliberate extra investigation click. It is
reported here in full rather than omitted. No credential was exposed (the test's own
`assert "sk-" not in error_text` check never even ran, since the failure occurred one
assertion earlier; nothing in this session printed, logged, or displayed the key at any
point). No other test in the suite showed the same failure, and a targeted search found
exactly one other place in the codebase with this exact hermeticity risk
(`tests/test_application_investigation_service.py`'s
`test_missing_configuration_is_a_clear_application_error`) — which **already** guards
against it correctly, with a code comment explaining exactly why:
```python
# Set to "" rather than delenv(): load_llm_config() calls load_dotenv(),
# which repopulates a deleted variable from a real local .env file but
# never overrides one already present (even blank) -- see the matching
# comment in tests/test_application_llm_adapter.py.
monkeypatch.setenv("OPENAI_API_KEY", "")
monkeypatch.setenv("LLM_MODEL", "")
```
The dispute-review UI test (authored in Module 6C, before any real `.env` ever existed
in this project) simply never had this pattern applied to it — a hermeticity gap that
only a real local `.env` could ever surface, which is exactly what this live check did.

**Proposed smallest correction** (not applied in this check): add the same two
`monkeypatch.setenv("OPENAI_API_KEY", "")` / `monkeypatch.setenv("LLM_MODEL", "")` lines
to the top of `test_missing_configuration_shows_actionable_message_without_credentials`
in `tests/test_dispute_review_ui.py`, mirroring the existing, already-correct pattern
verbatim. This is a test-only change (no model, prompt, validator, or source-data
change) and would make the test hermetic against any real local `.env`, exactly as its
sibling test already is.

Because of this, the full offline suite was **not** re-run again in this check (to avoid
triggering the same test a second time while `.env` remains present) — the last verified
clean, fully-offline baseline remains Module 6D's own 672/672 run (§3 above, before
`.env` existed); with `.env` now present, the suite is 671 passed / 1 failed for the
reason above, not because of any change to application code.

## 6. Final UI check

- **Reused, unaffected evidence**: the comparison-only field-entry/Match-Mismatch-Unknown
  rendering was already verified live in Module 6D's own check (§2D) and again in this
  live smoke check below; not separately re-verified a third time.
- **Real-browser-with-live-model** (this check, 2026-09-23): started the app from this
  project's own `.venv` on an unused port (`8511`) with the `--server.fileWatcherType=none`
  launch flag, drove all three live requests in §5 through the actual Dispute Review tab
  UI (never a backend script), and verified directly:
  - The brief-generation workflow status panel (Evidence Gate / Generation / Deterministic
    Validation) rendered correctly for all three live calls, including the correct
    "Draft generated" + "Failed" combination and the "not shown as an accepted brief"
    message.
  - Evidence limitations, missing-evidence entries, and the full evidence panel (recorded
    facts, submitted fields, retrieved policy excerpts, graph relationships, comparison
    findings) rendered correctly and traceably for every request.
  - Since no draft passed validation, the judge UI (scores/verdicts) did not render in
    this check — correctly, since the judge button only appears after an accepted draft
    (unverified independently by the passing `AppTest` coverage below, which does exercise
    that render path with a mocked passing draft).
  - Editing the Member ID field after a completed comparison/workflow run immediately
    cleared the comparison result and the entire AI workflow section — confirmed by
    reading the page before and after the edit; no new live request was triggered by the
    edit itself.
  - The comparison result stayed available and correct while generation was in progress
    and after a validation failure — generation failing never hid or altered the
    deterministic comparison already on screen.
- **Automated-mocked** (still authoritative for the judge-scores/verdicts render path,
  which this live check did not exercise because no live draft passed validation): the
  56-test Streamlit `AppTest` suite (`tests/test_dispute_review_ui.py`, all passing)
  drives the real `app.py`, including
  `test_judge_binding_rejects_evidence_change_with_unchanged_run_id_and_brief` (§2A) —
  clicking presets, comparing, investigating, and running the judge against fake,
  deterministic OpenAI transports.

No UI defect was found in this check. The server (real OS PID, not the shell job id) was
stopped immediately after; no other process was touched.

## 7. Known limitations (dispute-review-AI-specific, in addition to README's list)

- **Rule E (`unverified_provenance_preserved`) is confirmed, by live model output, to
  false-positive systematically on the correct, desired hedge word "unverified."** All
  three live generation calls in §5 independently used "unverified" while correctly
  describing `SUBMITTED_UNVERIFIED` evidence, and all three were rejected for it. This
  is the same pre-existing, documented non-negation-aware substring-check limitation
  (Rule E/Rule F share the pattern; see `application/dispute_brief_validator.py`'s module
  docstring), now confirmed live rather than only illustrated by a hand-written example
  (§4). **Proposed smallest correction** (not applied in this check, per its boundaries —
  "do not change... validators... during this check"):
  in `application/dispute_brief_validator.py`, replace the substring containment check
  ```python
  _VERIFIED_CLAIM_WORDS = ("confirmed", "verified", "authenticated", "validated")
  ...
  matched_word = next((word for word in _VERIFIED_CLAIM_WORDS if word in lowered), None)
  ```
  with a word-boundary-aware regex that excludes an "un-" prefix, e.g.:
  ```python
  _VERIFIED_CLAIM_PATTERN = re.compile(r"(?<!un)\b(confirmed|verified|authenticated|validated)\b", re.IGNORECASE)
  ...
  match = _VERIFIED_CLAIM_PATTERN.search(finding.statement)
  matched_word = match.group(0) if match else None
  ```
  This still catches "has been confirmed"/"was verified"/etc. as an overclaim, while no
  longer matching "unverified"/"unconfirmed". It would need its own test (a finding using
  "unverified" no longer flagged; a finding using a real overclaim word still flagged) and
  is a validator-only change — no prompt, model, or source-data change is implied.
- Rule F (`prohibited_authority_language`) shares the same non-negation-aware class of
  limitation; not observed triggering in this check's live output, but not proven absent
  either — three requests is a smoke test, not exhaustive coverage.
- The developer-authored qualitative examples (§4) are illustrations of the
  deterministic/judge boundary, not a rubric-calibration or live-accuracy dataset. This
  live check (§5) is three smoke-test cases, likewise not a quality benchmark or judge
  calibration dataset — no judge scores were obtained at all, since no draft passed
  validation.
- **Citation quality is not yet proven reliable**: 1 of 3 live drafts cited a
  non-existent reference id (`'prior_authorization'`), correctly caught by Rule A. This
  single data point does not establish a rate, only that the failure mode is real and
  that the deterministic validator catches at least this instance of it.
- The judge itself remains entirely unexercised against a live model — every validation
  failure in §5 prevented it from ever running. Live judge quality is unverified, not
  merely "not yet attempted."

## 8. Readiness conclusion

**Offline-integration readiness: YES, with one now-known caveat.** Every deterministic
and mocked-model code path (comparison, three-source retrieval, evidence gate, generation
plumbing, deterministic validation, judge plumbing, staleness/binding guards, shared-state
isolation, UI wiring) is exercised by 672 passing offline tests when no real `.env` is
present. With a real `.env` present (as during this live check), one test
(`test_missing_configuration_shows_actionable_message_without_credentials`) loses its
isolation and makes a real live call instead of testing the offline path — a known,
now-documented, proposed-but-not-applied fix (§5's "Additional finding"). This does not
affect the 672-count baseline recorded in §3, which was measured before `.env` existed.

**Live-demo readiness: NOT YET — a real, live-verified blocker was found.** The
configuration gap from the prior version of this document is now resolved (a valid
`.env` was supplied and used), and the live model itself is reachable and returns
well-structured output with correct comparisons, correct provenance labeling, and correct
gap acknowledgment. But **every one of the 3 live-generated drafts was rejected by
deterministic validation**, and 2 of those 3 rejections were **validator false
positives** (§7) that would reject a demo-quality, correctly-hedged brief purely because
it uses the word "unverified" — meaning a live demo run today has a high observed chance
of never reaching an accepted brief, and therefore never reaching the judge, regardless
of the underlying model's actual quality. This is a validator defect, not a model quality
problem or a configuration problem, and §7 proposes the smallest fix. Do not read this
document as claiming live-demo readiness — it does not; live judge quality in particular
remains entirely unverified, since no live draft reached it in this check.

---

# Repair and fresh bounded live recheck (2026-09-23, second session)

Everything above (§1–§8) is preserved unchanged as the historical record of the first
live smoke check and its findings. This section adds the repair work those findings
motivated, and a second, independent live recheck. **§12 below is the current readiness
conclusion** — §8 above is superseded for "is this ready today" purposes but is kept
verbatim as the record of what was true before this repair.

## 9. Root causes and exact files changed

### 9A. Test isolation against live model calls

**Root cause** (§5's "Additional finding," now fixed): `application/config.py`'s
`load_llm_config()` calls python-dotenv's `load_dotenv()`, which fills in any
environment variable **absent from the real process environment** from a local `.env`
file. `tests/test_dispute_review_ui.py::test_missing_configuration_shows_actionable_message_without_credentials`
relied on ambient absence rather than explicitly blanking its own config, so a real local
`.env` silently turned it into a live-model test.

**Fixes** (both applied; no environment variable was ever cleared as the sole defense,
per the task's explicit constraint):
1. [tests/test_dispute_review_ui.py](../tests/test_dispute_review_ui.py) — the specific
   test now explicitly does `monkeypatch.setenv("OPENAI_API_KEY", "")` and
   `monkeypatch.setenv("LLM_MODEL", "")` at its start, mirroring the pattern
   `tests/test_application_investigation_service.py` already used correctly. This makes
   `load_llm_config()` itself raise `LLMConfigurationError` before any client is ever
   constructed, regardless of what `.env` contains.
2. [tests/conftest.py](../tests/conftest.py) — **new file**. A reusable, autouse,
   default-deny guard: `openai.OpenAI(...)` construction is blocked and raises
   `UnexpectedLiveProviderCallError` in every test by default. A test that needs a real
   or spy client overrides this by monkeypatching `openai.OpenAI` itself (every existing
   fake-client/spy test in this project already does exactly this, so none needed any
   change). Confirmed by a full-repository search that `openai.OpenAI(...)` is the only
   OpenAI SDK client construction anywhere in `application/` (four call sites, all
   synchronous — no `AsyncOpenAI`, no direct `httpx`/`requests` calls to an OpenAI
   endpoint). This guard does not touch `rag.embeddings.SemanticEmbeddingProvider` (local,
   no network) or anything else.
3. [tests/test_live_provider_guard.py](../tests/test_live_provider_guard.py) — **new
   file**. Proves the guard is active for all four real adapters
   (`OpenAIInvestigationBriefAdapter`, `OpenAISemanticJudgeAdapter`,
   `OpenAIDisputeBriefAdapter`, `OpenAIDisputeJudgeAdapter`) using obviously-fake dummy
   credentials (never the real key) so `load_llm_config()` succeeds and execution
   genuinely reaches the SDK construction boundary — proving the guard fires there,
   before any network request could be sent — plus one test proving an explicit
   fake-client override still works normally. 5 tests, all passing.

Offline evaluation scripts (`evals/e2e_eval.py`, `evals/e2e_safety_eval.py`) run outside
pytest, so the autouse fixture doesn't apply to them — a full search confirmed every
call site in `evals/` already explicitly injects a `Fake*Adapter` and never falls back to
a real adapter's default, so there was no reachable path to guard there; no change was
needed or made to those files.

### 9B. Rule E false positives

**Root cause**: [application/dispute_brief_validator.py](../application/dispute_brief_validator.py)'s
Rule E used plain substring containment (`"verified" in lowered`), which matched inside
"unverified"/"unconfirmed" — the correct, desired hedge — as if it were a positive claim.

**Fix**: two narrow, documented refinements (full rationale in the file's own comments):
1. Word-boundary matching (`\bverified\b`, etc.) instead of substring containment —
   "verified" no longer matches inside "unverified" at all, since there is no word
   boundary between "un" and the root.
2. A small, same-clause, negation-cue exemption: a closed list of negation
   words/contractions (not, never, cannot, doesn't, ...). If one appears anywhere before
   a claim word within the same clause, that occurrence is not flagged. Clauses are split
   on sentence punctuation and a short list of contrastive conjunctions (but, however,
   although, though) — deliberately NOT on "yet" (to avoid breaking "has not yet been
   verified") and deliberately never spanning a "but"/"however" boundary (an earlier
   disclaimer must never exempt a later, separate positive claim).

This is still explicitly documented as narrow keyword/cue matching, not general negation
understanding or semantic entailment — see the module's own expanded comment block.

**Regression tests** ([tests/test_dispute_brief_validator.py](../tests/test_dispute_brief_validator.py),
8 new tests, all passing):
- Safe, not flagged: "The authorization is unverified." / "The authorization has not
  been verified." / "Authenticity has not yet been verified." / "Matching fields do not
  establish verified authorization." (all four are the exact wording patterns observed
  in the first live check).
- Unsafe, still flagged: "The submitted authorization is verified." / "We verified the
  submitted authorization." / "Authenticity has been confirmed."
- Mixed clause, still flagged: "The submission is unverified, but we have verified its
  authenticity." — confirms an earlier disclaimer never exempts a later positive claim.

### 9C. The invented "prior_authorization" reference

**Root cause, found by inspection**: [context/dispute_evidence_retriever.py](../context/dispute_evidence_retriever.py)
formatted each missing-evidence gap as `f"[{item.category}] {item.description}"` — e.g.
`"[prior_authorization] No matching prior authorization record was retrieved..."`. This
visually collided with the EVIDENCE section's real citation format,
`f"[{ref.ref_id}] ({ref.provenance}) {ref.label}: {ref.detail}"` — both used square
brackets around a short identifier-shaped token. The model plausibly pattern-matched the
bracketed category label as if it were a citable reference id and copied it (without
brackets) into `suggested_next_step.evidence_refs`, producing a reference id that never
existed anywhere in the evidence shown to it. There was no real gap/source-outcome
reference available for it to cite instead — the fix does not invent one (that would
misrepresent an absence as a source), it removes the ambiguity and states the correct
instruction explicitly.

**Fixes** (small, targeted, no schema change):
1. `context/dispute_evidence_retriever.py` — the missing-evidence string format changed
   from `f"[{category}] {description}"` to `f"{category}: {description}"` (colon, no
   brackets), removing the visual collision at its source.
2. [prompts/dispute_brief_prompt.py](../prompts/dispute_brief_prompt.py) — `SYSTEM_PROMPT`
   rule 2 split into 2/2a/2b: reference ids must be copied character-for-character from
   the exact bracketed `[ref_id]` tags in the EVIDENCE section only, never constructed
   from a category/field/entity name; the MISSING EVIDENCE/CONFLICTS/LIMITATIONS lists
   contain no reference ids of their own; an absent record has no source reference to
   cite, and none should ever be fabricated to satisfy the citation requirement — prefer
   `missing_or_conflicting_evidence` (which needs no `evidence_refs`) for a pure-absence
   statement. The MISSING EVIDENCE section's own header in `render_user_prompt` now
   states the same thing at the point of use. `PROMPT_VERSION` bumped to
   `dispute_brief.v2` (this project's own convention: bump whenever `SYSTEM_PROMPT`
   wording changes in a way that could affect model behavior).

Rule A itself (`evidence_reference_existence`) and its tests were not touched — it is
still what actually rejects an invalid citation; this fix reduces how often the model
produces one in the first place. No invalid citation is silently corrected after
generation anywhere in this codebase.

**Note**: `prompts/dispute_judge_prompt.py` (the separate judge prompt) was intentionally
**not** touched — the authorized scope for this repair was citation instructions for
generation (Step 3), and the judge prompt is a wholly separate file. §11 below shows this
same root cause independently affecting the judge, discovered during the live recheck —
retained and reported, not fixed in this cycle, per the task's explicit "do not perform
further code edits ... within this budget cycle" instruction.

## 10. Offline verification (post-repair, actual results)

Run on 2026-09-23, in the project's own `.venv`, with a real `.env` present (containing
this project's own configured model, never printed or logged):

| Check | Command | Result |
|---|---|---|
| Dependency conflicts | `python -m pip check` | `No broken requirements found.` |
| Full test suite | `python -m pytest tests/ -q` | **685 passed**, 0 failed, 1 pre-existing unrelated warning |
| End-to-end eval | `python -m evals.e2e_eval` | **8 of 8** passed |
| Safety eval | `python -m evals.e2e_safety_eval` | **9 of 9** passed |

685 = 672 (§3's original baseline) + 5 (`tests/test_live_provider_guard.py`, new) + 8
(new Rule E regression tests). **Zero live requests were made during this entire
offline run** — provable, not merely assumed: the default-deny guard (§9A) was active
for the whole run, and any unintended `openai.OpenAI(...)` construction would have
raised `UnexpectedLiveProviderCallError` and failed that test loudly; the run was
685/685 green. This is the first time this project's full offline suite has been
confirmed to pass cleanly **with a real `.env` present** — previously (§5's "Additional
finding") the same conditions produced 671 passed / 1 failed.

No pre-existing, unrelated test failures were observed in this run.

## 11. Fresh bounded live recheck (2026-09-23, post-repair)

New, independent budget for this recheck only: 3 generation requests (one per preset) +
3 judge requests (one per validated brief) = 6 authorized, 6 used, 0 retries, no model
switching. Configured model: same as §5 (this project's own `.env`, never printed).
Performed entirely through the real browser UI (`--server.fileWatcherType=none`, an
unused local port, this project's `.venv`), never repeated through a backend script. The
server's real OS PID was stopped immediately after; no other process was touched.

### Results table

| Preset | Generation | Validation | Judge scores/verdicts | Semantic issues |
|---|---|---|---|---|
| Matching fields | DRAFTED | **PASSED** | **4.75 / 5, overall PASS** (Evidence Grounding, Coverage, Uncertainty & Provenance, Authority Boundaries all scored) | None material. Judge rationale correctly identified one genuine minor nit: the phrase "provider-supplied" is supported by `submitted:supplied_by` but that ref wasn't listed on that specific finding — an accurate, non-fabricated observation, not a hallucinated concern. |
| Different servicing provider | DRAFTED | **PASSED** | **Judge FAILED (`UNKNOWN_REFERENCE`)** — new defect, see below | Brief itself: no material issues found. Mismatch named with both provider names (Lakeside Imaging Center vs. QuickDraw Labs); provider-change policy gap explicitly preserved ("No retrieved policy passage directly addresses whether a change in servicing provider affects a previously authorized service"); no invented "providers must match" rule. **Judge (separate call) fabricated an evidence_refs entry `'prior_authorization'`**, caught and rejected by `dispute_judge.py`'s own reference check — no fabricated score reached the UI; "The dispute brief above is unaffected and remains fully usable" was shown, correctly. |
| Incomplete submission | DRAFTED | **PASSED** | **Judge FAILED (`UNKNOWN_REFERENCE`)** — same defect | Brief itself: no material issues found. Both missing fields (servicing provider, authorization end date) correctly rendered as UNKNOWN in both the comparison and the brief; correctly hedged throughout ("states, on an unverified basis"). **Judge again fabricated `'prior_authorization'`** in its own evidence_refs — same root cause as §9C, confirmed to affect the judge prompt too (not fixed this cycle, see below). |

**3 of 3 generation requests now pass deterministic validation** (0 of 3 in the first
check) — both repairs (9B, 9C) are confirmed working on live output, not just synthetic
test cases. Citation support was inspected, not just reference existence: every
`evidence_refs` entry in all three accepted briefs was checked against its cited
reference's `detail` text and found to actually support the finding's statement; no
fabricated, unsupported, or misapplied citation was found in any of the three briefs
themselves.

### A new defect found, retained and reported (not fixed this cycle)

**2 of 3 judge calls failed with `DisputeJudgeFailureCategory.UNKNOWN_REFERENCE`**,
both citing the identical fabricated reference id `'prior_authorization'` — the same
root cause as §9C (a model conflating a missing-evidence category label with a citable
reference id), but occurring in the **judge's** own evidence_refs, not the brief's.
`prompts/dispute_judge_prompt.py` was not in this repair's scope and does not yet carry
the strengthened citation instructions added to `prompts/dispute_brief_prompt.py` in
§9C. **This was retained and reported exactly as observed, per instruction — no further
code edit or live attempt was made after it was found**, and the recheck continued to
its planned budget.

Positively: this confirms `application/dispute_judge.py`'s own `UNKNOWN_REFERENCE`
validation (added in Module 6B) is working exactly as designed — a judge output citing a
non-existent reference is rejected outright, never surfaced as a fabricated score, and
the UI correctly states the underlying brief remains unaffected and usable. No
consequential action, no misleading "success," and no credential exposure occurred at
any point.

**Proposed smallest correction for a future task**: apply the same 2/2a/2b-style
citation instructions from §9C to `prompts/dispute_judge_prompt.py`, and re-verify with
a fresh, separately-authorized live budget.

### Browser paths verified this recheck

- Loading each of the three presets and running "Compare submitted information"
  (correct summaries, correct Match/Mismatch/Unknown rows for all three).
- "Investigate dispute evidence" for all three presets, including reading the full
  rendered brief text, citations, and evidence panel for the two that produced usable
  briefs (all three, in fact, since all three passed validation this time).
- "Run AI semantic evaluation" for all three validated briefs, including reading the
  full rendered score breakdown (with per-dimension supporting references and cited
  draft text) for the one that succeeded, and the failure message for the two that
  didn't.
- Editing a field (Member ID) after a completed run and confirming, via direct DOM
  inspection, that the comparison result, workflow status, brief, and judge sections
  were all simultaneously invalidated — performed with zero additional live requests.
- Did **not** re-attempt or retry any of the three judge calls, and made no live request
  beyond the authorized 6.

## 12. Updated readiness conclusion (current, supersedes §8)

**Offline-integration readiness: YES, and the known `.env`-hermeticity caveat from §8 is
now closed.** 685/685 offline tests pass with a real `.env` present, provably with zero
live requests (§9A/§10).

**Rule E and the invented-citation defect that blocked §8's readiness are fixed and
live-confirmed.** All 3 live-generated briefs in this recheck passed deterministic
validation — a full reversal from the first check's 0 of 3.

**Local live-demo readiness for the brief itself: substantially improved, and
demonstrable today** for all three existing presets — generation, retrieval, comparison
description, provenance labeling, and policy-gap acknowledgment all worked correctly and
were validator-accepted on live output, not just mocks.

**The optional judge is NOT yet demo-ready**: 2 of 3 live judge calls failed due to a
newly-confirmed instance of the same citation-fabrication root cause in the separate
judge prompt. It fails safely (no fabricated score, brief unaffected) but currently
succeeds unreliably on live output. This is the blocking item for full live-demo
readiness of the three-action tab as a whole.

**These 3 live cases are a smoke test, not a quality benchmark or judge calibration
dataset** — they establish that the repairs work and reveal one further real defect;
they do not establish a pass rate, a production reliability figure, or judge
calibration against human review.

**Module 7 publishing**: closer than before, but not yet — the judge-prompt citation
fix (proposed above) should be applied and live-rechecked (a new, separately-authorized
budget) before treating the AI-assisted actions as demo-ready end to end. No git
operation, push, or deployment was performed in that session.

---

# Judge-prompt citation fix and third-session live recheck (2026-09-23, third session)

Everything above (§1–§12) is preserved unchanged as the historical record of the first
two sessions. This section verifies §11's hypothesis against the actual judge input,
applies the smallest fix, and reports a third, independently-budgeted live recheck.
**§17 below is the current readiness conclusion.**

## 13. Tracing the actual judge input (hypothesis verified, not assumed)

§11 hypothesized the judge confused a missing-evidence category label with a reference
id. Read `prompts/dispute_judge_prompt.py` and `render_judge_user_prompt`'s actual
output before changing anything, to confirm rather than assume:

- `context.missing_evidence` (already colon-formatted, not bracketed, since §9C's fix)
  is rendered verbatim under a plain `"MISSING EVIDENCE:"` header — no bracket
  collision remains here, confirming §9C's fix on this path.
- **But** the *generated brief itself* independently restates the same
  `"prior_authorization: ..."` gap inside its own `missing_or_conflicting_evidence` list
  (the model's own paraphrase, e.g. "Missing evidence: prior_authorization — No matching
  ..."), and `render_judge_user_prompt` renders that brief-side list too, under
  `"MISSING/CONFLICTING EVIDENCE LISTED BY THE BRIEF: ..."`. So the identifier-shaped
  token `prior_authorization` appeared **twice** in the judge's prompt, in plain prose,
  with no bracket in either place.
- Critically, `JUDGE_SYSTEM_PROMPT` (v1) never told the judge model what counts as a
  valid reference id, never distinguished the EVIDENCE REFERENCES section from prose
  elsewhere in the same prompt, and never said the brief text under review is not an
  authoritative id source. Dimension B (COVERAGE) explicitly directs the judge to check
  whether "missing evidence... explicitly listed" is addressed — drawing its attention
  straight to this token with no boundary telling it the token isn't citable.

**Confirmed root cause**: not a bracket-format collision this time (that part was
already fixed) — the judge is confusing a **missing-evidence category label repeated in
plain prose** for a **citable reference id**, because nothing in `JUDGE_SYSTEM_PROMPT`
ever drew that boundary. Verified by reading the actual rendered prompt, not assumed.
This is the "missing-evidence category labels" branch of the reported hypothesis,
confirmed; "draft text/finding identifiers" is a related contributing factor (the same
token also appears inside the brief's own missing-evidence restatement, part of the
"text being evaluated"); "evidence references" proper (the real, bracketed
`[ref_id]` list) were never the source of confusion.

## 14. Smallest correction

**Files changed**, both in [prompts/dispute_judge_prompt.py](../prompts/dispute_judge_prompt.py)
only — no change to `application/dispute_judge.py`, `application/dispute_judge_models.py`,
`application/dispute_workflow.py`, `application/dispute_generator.py`,
`application/dispute_brief_validator.py`, `dispute_review/`, source data, or the reused
`known_ref_ids` set:

1. **`JUDGE_SYSTEM_PROMPT`** — added an explicit `EVIDENCE_REFS RULE` paragraph
   immediately after the existing `evidence_refs`/`cited_draft_text` field
   descriptions: a valid id is ONLY one of the exact, literal, bracketed ids under
   `EVIDENCE REFERENCES:`; the MISSING EVIDENCE/CONFLICTS/LIMITATIONS lines and the
   brief's own restated missing/conflicting-evidence line are prose, never ids, "even
   when a short label inside them (e.g. 'prior_authorization') looks identifier-shaped"
   (the exact observed case, named explicitly); the brief text under review is content,
   not an authoritative id source; and — the absence-based-criticism instruction — if a
   dimension's critique concerns an absence with no real supporting reference, **leave
   that dimension's `evidence_refs` empty** and explain the absence in `rationale`
   instead of inventing a citation.
2. **`render_judge_user_prompt`** — reused the *same* authoritative reference set
   already rendered (`context.references`, identical to what `known_ref_ids` in
   `application/dispute_judge.py` validates against — no second, competing list was
   created anywhere). Added short inline labels at the point of use, mirroring §9C's
   brief-prompt pattern: `"MISSING EVIDENCE (gap descriptions in prose, NOT reference
   ids...)"`, `"CONFLICTS (prose, not reference ids)"`, `"LIMITATIONS (prose, not
   reference ids)"`, `"MISSING/CONFLICTING EVIDENCE LISTED BY THE BRIEF (prose from the
   brief being reviewed...)"`, and `"EVIDENCE REFERENCES (the ONLY valid evidence_refs
   ids...)"`. Also prefixed the "GENERATED DISPUTE BRIEF TO EVALUATE" block with an
   explicit "this is CONTENT UNDER REVIEW, not an authoritative source of reference ids"
   line.
3. `JUDGE_PROMPT_VERSION` bumped `dispute_judge.v1` → `dispute_judge.v2`, this project's
   own established convention for a `SYSTEM_PROMPT` wording change that could affect
   model behavior.

**Schema check (no change needed)**: `DisputeDimensionResult.evidence_refs: list[str] =
Field(default_factory=list)` in `application/dispute_judge_models.py` already permits an
empty list with no `min_length` constraint — the schema already supports "no fabricated
citation for an absence-based critique"; only the prompt instruction needed
strengthening to actually tell the model to use it.

**Reference validation preserved exactly**: `application/dispute_judge.py`'s
`_validate_judge_references` (the `UNKNOWN_REFERENCE` check against `known_ref_ids`) was
not touched — it still rejects any nonempty invalid reference, unconditionally. This fix
reduces how often the model produces one; it does not weaken what happens if it still
does. No automatic citation correction, no fabricated evidence, and no retry logic was
added anywhere.

## 15. Offline tests (focused, then full suite once)

New tests, [tests/test_dispute_judge_prompt.py](../tests/test_dispute_judge_prompt.py)
(7 tests, new file) + 3 new tests appended to
[tests/test_dispute_judge.py](../tests/test_dispute_judge.py):

- Correctly formatted allowed-reference instructions present in `JUDGE_SYSTEM_PROMPT`
  (`EVIDENCE_REFS RULE`, "NOT reference ids", "never invent a reference id", the exact
  `prior_authorization` example named).
- Category labels (missing evidence/conflicts/limitations, and the brief's own restated
  gap line) rendered without brackets and explicitly labeled "not reference ids" at the
  point of use, using a REAL CLM-1001 context (not a synthetic string) so the real
  `prior_authorization` gap is exercised.
- The real `EVIDENCE REFERENCES` section explicitly marked as the only valid id source,
  with a real ref id confirmed still present in its bracketed form.
- `test_judge_rejects_the_exact_live_observed_prior_authorization_fabrication` — the
  exact live-observed case (`refs=["prior_authorization"]`) still correctly rejected as
  `UNKNOWN_REFERENCE`, confirming the prompt fix did not weaken Rule validation.
- `test_judge_accepts_real_references_from_its_context` (pre-existing, re-verified) —
  valid references still accepted.
- `test_judge_dimension_with_empty_evidence_refs_for_absence_based_critique_is_accepted` —
  an absence-based critique with `evidence_refs=[]` completes normally end-to-end.
- `test_judge_unknown_reference_failure_leaves_the_validated_brief_unchanged` — the exact
  combination the first recheck showed (judge fabricates a ref → `UNKNOWN_REFERENCE` →
  brief/comparison/validation on the `DisputeWorkflowResult` provably untouched).

Run on 2026-09-23, in the project's own `.venv`, with the default-deny provider guard
(`tests/conftest.py`) active throughout and a real `.env` present:

| Check | Command | Result |
|---|---|---|
| Focused judge/context/UI tests | `pytest tests/test_dispute_judge_prompt.py tests/test_dispute_judge.py tests/test_dispute_context.py tests/test_dispute_review_ui.py -q` | **96 passed** |
| Dependency conflicts | `python -m pip check` | `No broken requirements found.` |
| Full test suite (once) | `pytest tests/ -q` | **695 passed**, 0 failed |
| End-to-end eval | `python -m evals.e2e_eval` | **8 of 8** passed |
| Safety eval | `python -m evals.e2e_safety_eval` | **9 of 9** passed |

695 = 685 (§10's post-repair baseline) + 7 (`test_dispute_judge_prompt.py`, new) + 3
(new tests in `test_dispute_judge.py`). **Zero live requests were made during this
entire offline run** — the guard was active throughout and the run was fully green; any
unintended live call would have raised `UnexpectedLiveProviderCallError` and failed
loudly. No pre-existing, unrelated failure was observed.

## 16. Bounded live recheck (2026-09-23, third session, post judge-prompt fix)

**Budget**: up to 3 judge requests (one per preset) + up to 3 fresh generation requests
if prior briefs/contexts were unavailable to reuse, max 6 total, no retries, no model
switching. **Prior state was NOT available to reuse**: this project never persists a
brief, evidence context, or judge envelope anywhere outside one Streamlit session's
in-memory `session_state` — both prior live sessions' server processes were already
stopped, so there was nothing to verify the integrity/binding of and reuse. One fresh
brief was generated per preset before judging it, exactly as the budget's fallback
permits — **no model call was made merely to recover data that was already available
locally; a fresh generation call was the only way to obtain a validated brief to judge
at all.**

**Backend, not browser, and explicitly labeled as such**: this recheck was performed by
directly invoking `run_dispute_workflow` / `run_dispute_judge` with the real
`OpenAIDisputeBriefAdapter`/`OpenAIDisputeJudgeAdapter` adapters in a standalone script
(outside pytest, so the offline guard correctly does not apply to it), not through the
browser. Reason: no browser session state was retained to re-render, and the UI
rendering path itself (brief text, citations, judge scores, edit-invalidation) was
already directly verified live in §11's browser-based recheck — repeating that same
rendering verification was not this recheck's purpose. This recheck's purpose was
specifically whether the *judge's citation behavior* improved, which the backend
functions expose more precisely (exact `evidence_refs` per dimension) than parsing
rendered DOM text would. Exactly 6 live requests were made (tracked explicitly by the
script itself), 0 retries, no model switching, the same configured model from `.env`
throughout (never printed).

### Results table

| Preset | Briefs reused or fresh | Generation | Validation | Judge scores/verdicts | Semantic issues |
|---|---|---|---|---|---|
| Matching fields | Fresh (no prior state available) | DRAFTED | **PASSED** | **5.0 / 5, PASS** (all four dimensions 5/PASS) | None found. All `evidence_refs` on every dimension resolved to real ids; rationales specific and accurate (e.g. correctly notes the brief "does not treat the submitted authorization as authenticated or infer approval from the comparison matches"). |
| Different servicing provider | Fresh | DRAFTED | **PASSED** | **5.0 / 5, PASS** (all four dimensions 5/PASS) | None found. Provider-change policy gap correctly treated: coverage/uncertainty rationale explicitly cites "the lack of a direct provider-change rule"; authority_boundaries rationale explicitly confirms the brief "does not invent a rule that the provider mismatch alone determines claim or authorization validity." Mismatch named with both provider names in the brief itself. |
| Incomplete submission | Fresh | DRAFTED | **PASSED** | **4.5 / 5, PASS** (evidence_grounding 4, coverage 5, uncertainty_and_provenance 4, authority_boundaries 5) | A genuine, non-fabricated minor defect found by the judge: the brief's summary said the submission was "provider- or patient-supplied," while the actual submitted `supplied_by` field is specifically `PATIENT` — a real, checkable imprecision, correctly named in both `evidence_grounding`/`uncertainty_and_provenance` rationale and `unsupported_claims`. Both missing fields (servicing provider, authorization end date) correctly retained as unresolved/UNKNOWN throughout the brief and judge output — uncertainty was not resolved away. |

**0 of 3 judge calls failed this time** (2 of 3 failed with `UNKNOWN_REFERENCE` in §11) —
confirmed fixed, not merely hypothesized fixed. **0 fabricated `evidence_refs` in any of
the 3 briefs or any of the 3 judge outputs**, checked programmatically against the real
reference-id set from each request's own `generation_context`, not just spot-read.

### Judge behavior checked against the requested criteria

- **Judge references exist**: yes, on all four dimensions across all three presets —
  every cited id was cross-checked against `generation_context.references` in code, not
  eyeballed.
- **Rationales are supported by the brief/evidence**: yes — e.g. preset 3's
  `evidence_grounding` critique is independently verifiable (the brief's summary text
  does say "provider- or patient-supplied"; the evidence's `submitted:supplied_by` value
  is literally `PATIENT`) — a real, checkable finding, not a hallucinated one.
- **Provider-change policy gap treated correctly**: yes for preset 2, both in the brief
  (an explicit finding that retrieved policy "do not directly establish how a
  servicing-provider change affects a previously authorized service") and in the judge's
  rationale (quoted above).
- **Incomplete submission retains uncertainty**: yes — both the brief and the judge
  treat the missing servicing-provider ID and authorization end date as unresolved, never
  guessed or silently dropped.
- **Scores follow the rubric without targeting a desired score**: the anti-ceiling-effect
  calibration instruction (`JUDGE_SYSTEM_PROMPT`'s pre-existing "actively try to find at
  least one legitimate critique before assigning 5") appears to be functioning across
  these 3 cases as a set, not merely asserted from one case: 2 of 3 presets scored a
  clean 5/5/5/5 (a plausible outcome for a genuinely well-formed brief, not itself
  evidence of reflexive scoring), and preset 3 shows the judge actually finding and
  scoring down for a real, specific defect (4, 4, 5, 5) rather than defaulting to 5
  regardless of content. Three cases cannot establish a calibration rate — this is an
  observation, not a claim of calibration (see the "not a benchmark" note below).
- **Judge results remain advisory**: yes — no approval/denial/reversal/payment language,
  no invented policy rule, in any rationale across all three presets.
- **Brief/comparison/validation left unchanged by the judge call**: confirmed
  programmatically for all three (`result.brief == brief` before/after was `True` in
  every case).

## 17. Updated readiness conclusion (current, supersedes §12)

**Offline-integration readiness: YES.** 695/695 offline tests pass, with the provider
guard active and zero live requests, on top of §10's already-closed `.env`-hermeticity
fix.

**The judge's invented-citation defect (§11) is fixed and live-confirmed**: 0 of 3 live
judge calls failed in this recheck, versus 2 of 3 in the immediately prior check, with
the identical presets, the identical claim, and the same live model. `UNKNOWN_REFERENCE`
validation itself remains fully intact and enforced (confirmed by a dedicated regression
test using the exact previously-observed fabricated id) — the fix reduced how often an
invalid citation is produced; it did not relax what happens if one still is.

**Local live-demo readiness for all three Dispute Review AI actions (comparison,
brief generation, judge) is now demonstrable together**, for all three existing presets,
using this project's own configured model: every generation passed deterministic
validation, and every judge call completed with an accurate, well-grounded score and
rationale, including one case where the judge correctly caught a real (non-fabricated)
brief imprecision.

**This remains a smoke test (3 presets, 6 requests), not a quality benchmark, a
production reliability figure, or a judge-calibration dataset.** A clean run today does
not guarantee every future run behaves identically — LLM outputs vary — and no claim of
production reliability is made here.

**Remaining limitations, unchanged from §7/§17's predecessor**: Rule E/F remain narrow,
non-negation-aware keyword/cue checks with a small, documented exemption list; the
developer-authored qualitative examples (§4) remain illustrations, not calibration; and
the anti-ceiling-effect calibration behavior observed above is encouraging but not
proven across a larger sample.

**Module 7 publishing**: the specific, live-confirmed blocker from §12 is now resolved.
No other known live-verified blocker remains for the three existing demo presets on
CLM-1001. No git operation, push, or deployment was performed in this session.
operation, push, or deployment was performed in this session.
