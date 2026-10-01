# V2 Interview Demo Script

This is a rehearsal script for the local v2 prototype, not a marketing document. Every claim in
it is grounded in code that exists today (see §D for exact paths). See
[docs/V2_DEMO_READINESS.md](V2_DEMO_READINESS.md) for the full readiness/limitations record this
script assumes.

> **This script was rewritten for the current billing-correction scenario.** The earlier
> authorization-dispute demo steps (matching fields, provider-corrected-after-denial, approved-
> authorization-with-a-date-mismatch) are retired along with that scenario; see
> [docs/BILLING_CORRECTION_SCENARIO.md](BILLING_CORRECTION_SCENARIO.md) for the full current
> scenario and the `docs/DISPUTE_*`/`docs/V2_DISPUTE_*` files (marked historical) for how the old
> one worked.

## A. 30-second introduction

> This is a claims-operations copilot for one specific, common workflow: a provider disputes a
> denial by submitting corrected billing information — the right service code, modifier, unit
> count, and servicing provider. The tool reconciles three things side by side: what's already
> **recorded** on the original claim and its denial, what was just **submitted** and is still
> **unverified**, and what independently-authored service documentation actually supports. It
> never edits the original claim, submits a corrected claim, reverses the denial, or approves
> payment. An analyst still reads the comparison, the evidence, and the AI-drafted brief, and owns
> the actual decision.

## B. 6–8 minute main demo

Launch first (see §readiness doc for the exact command). Confirm the app opens with **Dispute
Review** as the first tab and **Golden Dataset & Evaluation** as the second — no third tab.

### Step 1 — Original claim: two of four errors were caught, two were not

- **UI action:** Dispute Review tab → read Section 1 (Original Claim & Recorded Decision) as
  rendered, with no clicks yet.
- **Expected visible result:** the four investigated fields are prominently shown
  (`SURG-KNEE-ARTHRO`, `MOD-R`, 2 units, `PRV-BILL-SUNRISEHMO200`), and the recorded decision states it
  only flagged `service_code` and `modifier` — units and servicing provider are not mentioned.
- **Say:** "The claims system only caught two of the four billing errors on this claim when it
  denied it — it never separately checked units or the servicing provider. That distinction
  matters: whatever this investigation finds beyond those two fields is a NEW finding, not
  something the original decision already knew."

### Step 2 — Load the example and investigate: comparison + cited brief in one click

- **UI action:** Section 2 → click **Load billing correction example** → Section 3 → click the one
  combined **Investigate billing correction** button.
- **Expected visible result:** first, a six-column table (Field | Original claim | Proposed
  correction | Supporting record value | Finding | Evidence reference) with all four rows
  **SUPPORTED**, plus the disclaimer that this does not submit a corrected claim, change the
  original claim, reverse the denial, or determine payment; then, right below it, Section 4 shows
  workflow status (gate/generation/validation), the independent supporting records and synthetic
  policy sections retrieved, and the AI-drafted brief with cited findings.
- **Say:** "One click here does two things in sequence: it first checks every proposed correction
  against independently-authored documentation — an operative note, a units log, a provider
  roster — never against itself, with zero model calls; then, only if that validates cleanly, it
  goes on to retrieve policy and network evidence and draft a cited brief with one model call. All
  four fields happen to be supported here, but a changed value is never treated as automatically
  correct; the tool actually checks before it ever calls a model."
- **Optional:** edit the Service code field back to a wrong value and click **Investigate billing
  correction** again — show the row flips to **CONFLICTS**, and the brief below it updates to
  reflect that — a wrong proposed value is never silently accepted.
- **If generation/validation fails:** the comparison and evidence sections above render
  regardless — only the AI-drafted brief itself is affected. Point out the comparison/evidence is
  still fully usable, then fall back to §C. If it fails before even the comparison table renders,
  that is a real bug (the comparison step is zero-model-call and deterministic), not a live-model
  flake.

### Step 3 — Golden dataset: expected findings → actual run → structured checks → qualitative review

- **UI action:** Golden Dataset & Evaluation tab → pick any of the five cases (e.g. `CLM-1005`) →
  expand **Required findings** / **Missing information & required uncertainty** — point out the
  **"AI-drafted, pending human review"** banner. Click **Investigate Claim**, then **Evaluate Run
  Against Golden Reference**.
- **Expected visible result:** an automated-check summary (e.g. "6 of 6 automated checks passed")
  right next to an **Overall Evaluation Status** that still reads **NEEDS HUMAN REVIEW** — never
  PASSED, because the reference itself is pending review and qualitative findings remain.
- **Say:** "This is the point I most want to land: every structured check can pass and the
  headline status still correctly says 'needs human review.' Green checkmarks on a few structured
  fields are not the same as a reviewed, trustworthy answer — the tool is built to say so, not to
  paper over the gap."
- **If generation/validation fails:** the evaluation button and its result are independent of
  brief generation succeeding — the deterministic checks run against `EvidencePackage`/
  `AgentResult`, which exist regardless of whether the brief itself validated. Evaluate anyway and
  make the same point.

## C. 3-minute fallback path

If time is short or something breaks mid-demo:

1. Dispute Review → **Load billing correction example** → **Investigate billing correction** (Step
   2 above); the comparison table alone (its zero-model-call first half) cannot fail on
   generation even if the brief that follows it does.
2. Golden Dataset & Evaluation → any case → point at the **Expected Results** section without
   even clicking Investigate — the "AI-drafted, pending human review" banner and the
   findings/citations are the headline point and need no model call to show.
3. One line: "Every automated check that can pass, does — and the tool still won't call anything
   'passed' overall while a human hasn't signed off. That's the core design decision here."

## D. Exact source paths to open if asked

| Concern | Path |
|---|---|
| LangGraph workflow (original pipeline) | [agents/case_agent.py](../agents/case_agent.py) |
| LangGraph workflow (dispute pipeline) | [application/dispute_workflow.py](../application/dispute_workflow.py) |
| `investigate_dispute` skill | [skills/investigate_dispute.py](../skills/investigate_dispute.py) |
| Evidence retrieval (dispute, 3 sources) | [context/dispute_evidence_retriever.py](../context/dispute_evidence_retriever.py) |
| Deterministic comparator | [dispute_review/comparison.py](../dispute_review/comparison.py) |
| Isolated billing fixtures | [dispute_review/billing_fixtures.py](../dispute_review/billing_fixtures.py) |
| Synthetic billing policy loader | [dispute_review/billing_policy.py](../dispute_review/billing_policy.py) |
| Validator (dispute brief) | [application/dispute_brief_validator.py](../application/dispute_brief_validator.py) |
| Validator (original brief) | [application/brief_validator.py](../application/brief_validator.py) |
| Golden reference schema/data | [evals/golden_reference_models.py](../evals/golden_reference_models.py), [evals/investigation_golden_reference_set.json](../evals/investigation_golden_reference_set.json) |
| Golden evaluator | [evals/golden_reference_evaluator.py](../evals/golden_reference_evaluator.py) |
| Full scenario reference | [docs/BILLING_CORRECTION_SCENARIO.md](BILLING_CORRECTION_SCENARIO.md) |

## E. Concise, grounded answers to likely questions

**"What's the incremental value beyond just auto-adjudicating the correction?"**
Nothing here decides anything — deliberately. The value is reconciliation and traceability: the
recorded claim/decision, the provider's submitted-unverified correction, and independently-
authored supporting records are laid out side-by-side with explicit provenance, a structured brief
that must cite real evidence references or fail validation, and a deterministic recommended action
computed straight from the comparison, never from the model's own phrasing. It shortens the
"gather everything and figure out what's actually supported" step, without pretending to replace
the decision.

**"Why LangGraph for a fixed sequence — isn't that overkill?"**
The graph is intentionally not agentic — `validate → load → assess → build evidence → assess
evidence → {complete | needs_review | error}` is a fixed, acyclic sequence with one model call at
most (`max_retries=0`, no tool-selection loop — see `agents/routing.py`, `application/
dispute_workflow.py`). LangGraph is used here for its typed state machine and explicit routing,
not for autonomous planning — it's a deliberate, auditable pipeline, not an agent deciding what to
do next.

**"What does the golden dataset verify, and what does it not verify?"**
It verifies that specific structured fields on `EvidencePackage`/`AgentResult` — e.g. a claim's
`denial_reason_code`, a benefit's `covered` flag, the agent's terminal status — match an
independently-drafted expectation, for the five original claims. It does **not** verify that a
generated brief's prose correctly expresses those findings, and it does not verify live-model
output at all (every offline check uses a fake adapter). 23 structured checks currently pass
across the five claims; 8 findings remain explicitly qualitative and unautomated by design.

**"Why isn't the judge calibrated?"**
The semantic judge (`application/semantic_judge.py`, `application/dispute_judge.py`) is a second,
independent LLM call that scores a brief on a 1–5 rubric across four dimensions. It has never been
checked against a human-rated reference set — no calibration study exists. It's presented as an
advisory signal only, never a gate, and the UI and docs say so explicitly rather than implying a
confidence figure it hasn't earned.

---

*No capability, measured business outcome, or user-research finding is claimed here beyond what
the code above actually implements and what this project's own tests/evals actually ran.*
