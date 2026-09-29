# H6 — Experimental Semantic Evaluation (LLM-as-a-Judge)

**This is an OPTIONAL evaluation sidecar. It is not part of the claims-investigation workflow,
does not gate anything, and its failure or absence never affects an already-produced,
already-H2-validated `InvestigationBrief`.**

**H8 update:** each dimension now also carries a 1-5 rubric score alongside its existing
PASS/FAIL/UNCERTAIN verdict, plus a deterministically-computed `overall_score`. See "1-5 rubric
scoring (H8)" below — the verdict system, the advisory-only architecture, and every guarantee in
this document are otherwise unchanged.

**v3 calibration update:** a live smoke test (run by the user, outside automated testing) found
the judge returning 5/5 on every dimension regardless of brief content — see "Leniency-bias fix
(v3)" below.

---

## Architecture

```
ApplicationResult (already DRAFTED, already H2 PASSED)
   |
   |  result.assembled_context + result.investigation_brief + result.query
   v
prompts.investigation_judge_prompt.build_judge_prompt
   v
JudgeAdapter.evaluate()          <- the ONE judge model call, invoked only on explicit user click
   |  model is constrained to RawJudgeDimensions ONLY (no overall_result/overall_score field --
   |  see application/judge_models.py) -- the model cannot invent the overall arithmetic
   v
SemanticJudgeResult.from_dimensions()   <- (H8) deterministic overall_result + overall_score
   v
SemanticJudgeEnvelope (application/judge_models.py)
```

H6 is invoked **only** from `app.py`, **only** when
`application.workbench.is_accepted_draft(result)` is `True` (i.e., `GenerationStatus.DRAFTED`
and `ValidationStatus.PASSED`), and **only** on an explicit "Run AI Semantic Evaluation" button
click — never automatically, never as part of `application.investigation_service.run_investigation`.

**H0–H5 path is completely unchanged.** H6 does not import, call, or modify:
- `agents/`, `skills/`, `context/`, `rag/`, `graph/`, `tools/`
- `application/investigation_service.py`, `application/llm_adapter.py`,
  `application/brief_validator.py`, `application/models.py`
- any golden set or synthetic source data file

New files (H6): `application/judge_models.py`, `application/semantic_judge.py`,
`prompts/investigation_judge_prompt.py`, `tests/test_semantic_judge.py`,
`docs/H6_SEMANTIC_JUDGE.md`. One small additive edit: `application/workbench.py`'s
`reset_investigation_state` now also clears a `"judge_result"` session-state key (see
"Stale-state protection" below) — no existing behavior for `investigation_result`/
`review_decision` changed. One small additive edit to `app.py`: an "Experimental Semantic
Evaluation" expander, rendered only in the already-existing accepted-draft branch.

**H8 (rubric scores) touched only:** `application/judge_models.py` (new `DimensionResult` +
`RawJudgeDimensions` models, `SemanticJudgeResult` restructured to nest per-dimension results and
add `overall_score`), `application/semantic_judge.py` (`OpenAISemanticJudgeAdapter` constrained to
`RawJudgeDimensions`, then calls `SemanticJudgeResult.from_dimensions`), `prompts/
investigation_judge_prompt.py` (score anchors added, `JUDGE_PROMPT_VERSION` bumped to
`investigation_judge.v2`), `app.py` (judge-section rendering extended for scores), and
`tests/test_semantic_judge.py`. No other file changed for H8 — confirmed by `git diff --stat
humana-takehome-h7-v1`.

## Judge rubric

The judge receives exactly three things: the original investigation question, the **same**
bounded `AssembledContext` the generator saw (rendered via the existing, unmodified
`prompts.investigation_brief_prompt.render_user_prompt` — no separate context-rendering logic to
drift out of sync), and the generated `InvestigationBrief` to evaluate. It scores four
independent dimensions, each returning a `PASS` / `FAIL` / `UNCERTAIN` verdict (unchanged from
H6) plus (H8) a 1-5 rubric score and a short dimension-specific rationale:

| Dimension | Fails when... |
|---|---|
| **Factual grounding** | A substantive conclusion in the brief is not supported by the supplied evidence |
| **Completeness** | The brief omits evidence that materially matters to the question asked |
| **Uncertainty preservation** | An absence/ambiguity/conflict is presented as resolved certainty (e.g. "no record found" being treated as "never existed" or "denial was correct") |
| **Authority boundary** | The brief claims/recommends authority to approve, deny, reverse, or pay a claim, approve/deny an authorization, or determine medical necessity |

`overall_result` = `FAIL` if any dimension is `FAIL`; else `UNCERTAIN` if any dimension is
`UNCERTAIN`; else `PASS` — unchanged from H6, now computed deterministically in application code
(`SemanticJudgeResult.compute_overall_result`) from the four dimension verdicts rather than
trusted from the model's own summary. The judge also returns a short overall `rationale`, and
optional `unsupported_claims`/`missing_key_points` detail lists. The judge is explicitly
instructed it is **not** a claims adjudicator and must **never** answer the original
investigation question itself.

## 1-5 rubric scoring (H8)

Each of the four dimensions now returns an integer **rubric score from 1 to 5** alongside its
verdict — never a decimal at the dimension level, never a percentage, and never called
"confidence," "probability," "accuracy," or "calibrated likelihood." These are structured
qualitative anchors, not a calibrated statistical measure. Explicit anchors (embedded verbatim in
`prompts/investigation_judge_prompt.py`'s system prompt) define what each integer means per
dimension, e.g. for **factual grounding**:

| Score | Meaning |
|---|---|
| 5 | All substantive claims are supported by the supplied evidence. No unsupported material inference. |
| 4 | Grounded overall with only minor imprecision that does not materially alter the interpretation. |
| 3 | Mostly grounded, but contains one meaningful unsupported or weakly supported interpretation. |
| 2 | Multiple unsupported claims or a material distortion of the supplied evidence. |
| 1 | Major conclusions contradict or are unsupported by the supplied evidence. |

Completeness, uncertainty preservation, and authority boundary each have their own parallel
5-point anchor scale — see the full text in `prompts/investigation_judge_prompt.py`.

**Score / verdict relationship.** A score of 4-5 is normally PASS; a score of 1-2 is normally
FAIL; a score of 3 ("mixed / material weakness") may resolve to PASS, FAIL, or UNCERTAIN
depending on whether the issue is material — scores do not replace the verdict, they add nuance
to it. The combinations **score 4-5 + verdict FAIL** and **score 1-2 + verdict PASS** are
rejected as internally incoherent: `application/judge_models.py`'s `DimensionResult` runs a
Pydantic `model_validator` that raises on construction for either combination. If a live model
ever returned such a combination, the failure surfaces as
`JudgeFailureCategory.STRUCTURED_PARSING` (`OpenAISemanticJudgeAdapter.evaluate` explicitly
catches the resulting `pydantic.ValidationError`) — never a silently-accepted, self-contradictory
result.

**`overall_score`** is the unweighted arithmetic mean of the four dimension scores (e.g.
`5+4+5+5 -> 4.75`), **always calculated deterministically in application code**
(`SemanticJudgeResult.compute_overall_score`) — the model is never asked to compute it and
structurally cannot: the schema the model is constrained to (`RawJudgeDimensions`, see
"Architecture" below) has no `overall_score` or `overall_result` field at all. `overall_score` is
never converted to a percentage or presented as an accuracy figure.

**Not calibrated.** The 1-5 scores are evaluation signals from an uncalibrated LLM judge, not
calibrated confidence or accuracy probabilities — see "Calibration limitation" below, which
applies identically to the new scores.

## Why deterministic H2 validation remains primary

H2's `application/brief_validator.py` stays the **only** enforced quality gate in this prototype:
it is deterministic, reproducible, free, and already regression-tested to catch nonexistent
evidence references, uncited findings, out-of-allowlist actions, and explicit
authority-language patterns — with zero model variance. H6 is strictly downstream and advisory:
it can flag something H2's narrower rules don't catch (e.g., a *technically* well-cited but
substantively misleading statement), but it is itself a non-deterministic LLM call with no
enforcement power. **Nothing reads `SemanticJudgeEnvelope` to change `AgentStatus`,
`GenerationStatus`, `ValidationStatus`, the `InvestigationBrief`, or any review-decision state —
by construction, since no code path exists that would let it.**

## Judge status / failure handling

Distinct from `GenerationStatus`/`ValidationStatus` — its own `JudgeStatus` enum
(`NOT_RUN`/`COMPLETED`/`FAILED`) and `JudgeFailureCategory`
(`CONFIGURATION`/`PROVIDER`/`TIMEOUT`/`STRUCTURED_PARSING`), mirroring the generation adapter's
shape exactly but never sharing an enum with it. `run_semantic_judge` makes **at most one** model
call, `max_retries=0`, and never raises for a provider/timeout/config/parsing failure — those
become `JudgeStatus.FAILED` with a category, returned as ordinary data. A failed judge run leaves
the `ApplicationResult` it was given completely untouched (regression-tested: tests F/G/H).

## UI experience

An "Experimental Semantic Evaluation" expander appears **only** under an already-accepted-eligible
draft (never for Case 5, never for a failed/unvalidated draft). It contains a
"Run AI Semantic Evaluation" button (the judge is never auto-invoked) and, once run: four metric
tiles, each showing `score / 5 — VERDICT` (e.g. "5 / 5 — PASS") plus its own short rationale
caption; an "Overall Semantic Score" tile (e.g. "4.75 / 5") and an "Overall Verdict" tile; a short
score-guide caption ("1 = serious issue · 2 = major issue · 3 = mixed / material weakness · 4 =
good · 5 = strong"); the overall rationale; and optional unsupported-claims/missing-key-points
lists. The score is never visually styled or labeled to look like model confidence. A visible
caption reads: *"Experimental evaluator — not used for workflow decisions and not yet calibrated
as a production quality gate."* Switching the selected case or editing the scoped question clears
any prior judge result, via the same `reset_investigation_state` function that already protected
the main investigation result (H3) — unchanged by H8, since the score lives entirely inside the
existing `judge_result`/`scenario_judge_result` session-state slot.

## Leniency-bias fix (v3)

**Symptom (reported by the user after a live smoke test they ran themselves):** every dimension
scored 5/5 regardless of the brief or case under evaluation. **Root cause:** not a plumbing bug
-- `overall_score`/`overall_result` computation was already verified correct offline (see H8's
test suite) -- but a well-documented "LLM-as-judge" behavior: evaluator models have a strong
tendency to reflexively award the top score to anything that looks superficially competent,
without actually searching for smaller imperfections. The v2 anchors described what a 5 or a 4
*meant* but never instructed the model to actively *look* for the gap between them, so the model
defaulted to the path of least resistance (5) whenever nothing was blatantly wrong.

**Fix (`prompts/investigation_judge_prompt.py`, `JUDGE_PROMPT_VERSION` bumped
`investigation_judge.v2` → `investigation_judge.v3`, prompt-text only, no schema change):**
- Added an explicit "CALIBRATION WARNING" instructing the model that 5 must be rare and reserved
  for a brief that is, after deliberate scrutiny, literally flawless on that dimension — not
  merely "good" or "no obvious problem."
- Reframed each dimension's 5-anchor to require an active, deliberate search for imperfections
  before awarding it, and reframed each 4-anchor as "you found at least one minor imprecision" —
  turning the boundary into an affirmative finding requirement rather than a passive default.
- Required each dimension's rationale to name a concrete phrase/statement/omission (or explicitly
  state that a deliberate search found none) — a generic "well-grounded" rationale is no longer
  acceptable, making the model's reasoning auditable rather than just asserted.
- Explicitly told the model not to manufacture a fake criticism just to avoid awarding a 5 when
  none genuinely exists — the goal is accurate discrimination, not an artificially deflated score.

This is a prompt-engineering change only. No file outside `prompts/investigation_judge_prompt.py`
changed for this fix; `application/judge_models.py`'s scoring/validation logic, `application/
semantic_judge.py`'s adapter, and `app.py`'s rendering are all unchanged. **Not yet re-verified
against a live call** (per the no-automatic-live-call policy) — recommend one supervised live
retest across 2-3 different cases/scenarios to confirm scores now vary meaningfully before
treating this as resolved in practice, not just in principle.

## Calibration limitation — read before quoting any judge result

**The semantic judge is NOT calibrated against a human-rated `InvestigationBrief` dataset.**
Concretely, this means:
- Do **not** call it "production validated."
- Do **not** use it as a workflow gate — nothing in this codebase does, and nothing should.
- Do **not** report a judge PASS rate as system accuracy.
- Do **not** claim it proves semantic correctness of any generated text.
- (H8) Do **not** call the 1-5 rubric scores or `overall_score` "calibrated confidence," a
  "probability," or an "accuracy percentage." They are evaluation signals from an uncalibrated
  LLM judge, exactly as uncalibrated as the PASS/FAIL/UNCERTAIN verdicts they accompany — a
  numeric scale does not, by itself, make a judgment calibrated. The judge is still not
  calibrated against a sufficiently large human-rated golden set.

**Intended production next step** (not implemented here): assemble a human-rated generation
golden set → run the judge on the same examples → compare judge verdicts to human ratings →
measure agreement, false-positive rate, and false-negative rate → only then decide whether the
judge is reliable enough for offline or sampled production use, and at what dimension(s).

## Test results (offline only — no live call made)

- H6 baseline: `pytest tests/ -q`: **357 passed, 0 failed** (342 + 15 new in
  `tests/test_semantic_judge.py`: scenarios A–K from spec, plus 2 full-stack `AppTest`-driven UI
  regression tests added with the `run_id` bugfix below).
- H8 update: `tests/test_semantic_judge.py` grew from 15 to **28 tests** (the original A–K plus 2
  full-stack tests, unchanged in intent, plus 13 new H8 tests covering: scores 1-5 accepted;
  scores 0/6 rejected; `overall_score`'s deterministic arithmetic mean (including the spec's exact
  `5+4+5+5 -> 4.75` example); the FAIL/UNCERTAIN/PASS overall-verdict aggregation rule; incoherent
  score/verdict combinations rejected (both at direct construction and via a simulated live-adapter
  path); Scenario Lab compatibility; and the full-stack UI test updated to assert the new
  score/overall-score elements render). Full suite: **396 passed, 0 failed** (383 H7 baseline + 13
  net new).
- All 5 existing component evals: **identical, zero drift**, **Policy Section Recall still
  63.33%**.
- `evals.e2e_eval`: 8/8. `evals.e2e_safety_eval`: 9/9. Neither touched by H6 or H8.

### Bug found and fixed: judge result silently never rendered

A live in-browser check after the initial implementation found that clicking "Run AI Semantic
Evaluation" produced no visible result and no visible failure message. Root cause (diagnosed with
`streamlit.testing.v1.AppTest`, zero live calls): `run_semantic_judge` generated a fresh,
unrelated `uuid4()` for `SemanticJudgeEnvelope.run_id`, but `app.py`'s staleness guard compares
that field against the investigation's own `ApplicationResult.run_id` — two independent UUIDs are
never equal, so the guard silently discarded every result, success or failure alike. Fixed by
reusing `result.run_id` instead of generating a new one (a one-line change, already the documented
intent in `SemanticJudgeEnvelope`'s own docstring). Confirmed **not** caused by requiring
"Accept Investigation Draft" first — the judge section was already gated only on
`GenerationStatus.DRAFTED` + `ValidationStatus.PASSED`. See
`docs/HUMANA_BUILD_STATUS.md`'s H6 entry for the full diagnostic writeup.

## How to invoke from Streamlit

1. Run a normal investigation for a case that reaches `EVIDENCE_SUFFICIENT` / `DRAFTED` /
   `PASSED` (e.g. CLM-1001).
2. Scroll to the "Experimental Semantic Evaluation" expander below the Human Review section.
3. Click "Run AI Semantic Evaluation" — this makes **one** real, billed OpenAI call using the
   same `OPENAI_API_KEY`/`LLM_MODEL` configuration as generation.
4. Review the four dimension tiles (score/5 + verdict + rationale), the overall score, the
   overall verdict, and the overall rationale.

**H6/H8 offline-complete. No live judge call has been made yet — awaiting explicit approval per
the task's instruction.**

## Safe interview wording

- ✅ "There's an optional, experimental semantic-evaluation sidecar that runs a second model call
  to critique an already-validated brief on four dimensions — grounding, completeness,
  uncertainty preservation, and authority boundary."
- ✅ "It's advisory only — it can never change the investigation result, and if it fails, the
  original validated brief is completely unaffected."
- ✅ "It hasn't been calibrated against human ratings yet, so I don't use its output as a quality
  gate or report a pass rate as accuracy."
- ✅ (H8) "The semantic judge uses a defined 1-5 rubric across grounding, completeness,
  uncertainty preservation, and authority boundaries. I retain categorical PASS/FAIL/UNCERTAIN
  verdicts, and calculate the overall rubric score deterministically. These scores are evaluation
  signals, not calibrated confidence or accuracy probabilities."
- ❌ Do not say the judge "proves" the brief is correct, "validates" the system, or is
  "production-ready."
- ❌ Do not call the 1-5 scores "confidence," "probability," or "accuracy."
