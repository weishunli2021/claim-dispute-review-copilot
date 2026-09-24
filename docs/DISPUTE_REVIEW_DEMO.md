# Dispute Review Demo Script (3–4 minutes)

A concise walkthrough for an interview demo, covering all three Dispute Review actions:
the deterministic comparison, the optional AI-drafted investigation brief, and the
optional AI judge. Assumes the app is already running locally (see README's "Quick
start" for the exact launch command) and you're on the **Dispute Review** tab.

> **Steps 5 and 6 require `OPENAI_API_KEY` and `LLM_MODEL` set in `.env`.** If you're
> demoing without a configured model (the offline default), narrate steps 5–6 using the
> "Without a configured model" note under each step instead — the app's own
> generation-failure message is itself worth showing: it states plainly that no
> credentials are configured and that no network call was attempted, never a fabricated
> or misleading result. See [docs/DISPUTE_AI_VALIDATION.md](DISPUTE_AI_VALIDATION.md) §5
> for exact setup instructions.

## Sequence

**1. Show CLM-1001's recorded denial** (~15s)

Point at the "Recorded Claim Information" section: `CLM-1001`, status `DENIED`, denial
reason `AUTH_REQUIRED`, member `M-1001`, service `MRI-KNEE`, date of service
`2026-02-10`, servicing provider `PRV-1001 — Lakeside Imaging Center`. Note the ordering
provider (`PRV-4001 — Dr. Ana Kowalski`) is shown separately, explicitly labeled as
context only — never substituted for the servicing provider in any comparison or brief.

**2. Set the scene** (~15s)

"This claim was already denied for a missing authorization. Now imagine a provider's
office calls in and says they *do* have an authorization on file — but nobody has
verified that claim yet. This tab has three tools for that: a deterministic field
comparison, an optional AI-drafted brief that reasons over the full evidence, and an
optional AI judge that scores that brief. All three stay clearly separate from the
original AI investigation on the first tab."

**3. Load "Matching fields" → Compare submitted information** (~25s)

Click **Matching fields**, then **Compare submitted information**. All four rows
(Member, Service, Validity Dates, Servicing Provider) show `MATCH`. Read the summary:
*"4 of 4 compared fields match."* Point at the disclaimer — *"Matching fields do not
establish authenticity, authorization applicability, coverage, or payment"* — and note
this ran with **zero model calls**: purely deterministic, always available, works
offline.

**4. Load "Different servicing provider" → identify the discrepancy** (~20s)

Click the preset (previous result disappears immediately), click **Compare submitted
information**. Point at the Servicing Provider row: `MISMATCH`, with the actual vs.
submitted provider IDs shown side by side. Read the caption above the presets: this
demonstrates a provider-ID discrepancy only — it says nothing about whether that
alternate facility could actually perform an MRI. Mention in passing: a third preset
("Incomplete submission") shows two fields resolving to `UNKNOWN` rather than a guessed
Match/Mismatch when data is simply missing.

**5. Investigate dispute evidence → AI-drafted brief** (~35s)

Re-load **Matching fields** and compare again, then click **Investigate dispute
evidence**. This retrieves recorded facts, relevant policy passages, and graph
relationships — the same three-source retrieval the original investigation pipeline
uses — then drafts a cited, typed brief through a bounded LangGraph workflow. Point at:
the evidence-gate status (ready for scoped generation / limited brief / blocked), the
brief's findings with inline evidence references, the explicit provenance labels
(recorded vs. submitted-unverified), and the verification questions. Emphasize: this
brief incorporates the *new* submission — a separate artifact from the original AI
investigation draft on the Predefined Claims tab, which never sees it.

*Without a configured model*: the same click still runs the full evidence-gate and
retrieval logic — show the "Workflow Status" panel reaching `Generation failed` with the
honest message that `OPENAI_API_KEY`/`LLM_MODEL` are not set and no network call was
attempted. "Notice what it does *not* do: it doesn't fabricate a brief, and it doesn't
pretend the retrieval step didn't happen — the evidence-gate result and source outcomes
above are real and already useful on their own."

**6. Run AI semantic evaluation → judge scoring** (~25s, live model only)

After a validated brief, click **Run AI semantic evaluation**. Point at the four scored
dimensions (evidence grounding, coverage, uncertainty/provenance, authority boundaries),
each an anchored 1–5 rubric score plus a PASS/FAIL/UNCERTAIN verdict — never an accuracy
or confidence percentage, called out explicitly in the UI. Note the overall
score/verdict is always Python-computed from the four dimensions, never invented by the
model, and a single FAIL dimension always stays visible even if the average looks fine.

**7. Show stale-result protection** (~20s)

With results still on screen, click into the Member ID field, change it, and press Tab
(or click elsewhere). The comparison result, the AI brief, and the judge evaluation all
disappear immediately together. "Nothing partially-stale is ever left on screen — any
committed edit to the submission clears every downstream result until you re-run each
step, and the judge has its own extra binding on top of that: it's tied to the exact
evidence and brief revision it actually scored, not just which claim is selected."

## Interview talking points

- **Incremental value, not a rebuild.** This tab doesn't re-investigate the claim from
  scratch — it takes a narrow, high-value slice: when someone hands you new, unverified
  authorization info after a denial, quickly show what lines up, what doesn't, what's
  missing, and (optionally) a cited narrative brief plus an AI second-opinion score —
  so a reviewer knows exactly what to chase down next.
- **Three tools, three different problems, deliberately not blurred together.** The
  four field comparisons are a rules problem — deterministic, fully explainable, zero
  model calls. Drafting a coherent narrative from retrieved evidence is a language
  problem — that's the one call to a generation model, behind an evidence gate, with a
  deterministic validator on the output. Scoring that narrative against a rubric is a
  *judgment* problem — a second, entirely separate, optional model call, never run
  automatically, never mixed into the first two.
- **The deterministic validator and the AI judge catch different things — on purpose.**
  The validator only checks what's mechanically verifiable: does every cited reference
  actually exist, does every finding cite something, is the suggested action on the
  approved advisory list, is unverified evidence never re-described as confirmed. It
  cannot tell you whether a brief invented an unsupported policy rule or silently
  omitted a real mismatch — that's exactly the judge's (or a human's) job. See
  [docs/DISPUTE_AI_VALIDATION.md](DISPUTE_AI_VALIDATION.md) §4 for four concrete,
  hand-authored examples of this boundary.
- **Human authority, unverified provenance, no autonomous action, anywhere in the
  pipeline.** Every comparison result is explicitly labeled "Provider-supplied—unverified"
  or "Patient-supplied—unverified." The AI brief and the judge both run under the same
  closed advisory-action vocabulary and prohibited-authority-language checks as the
  original investigation pipeline — nothing here approves, denies, reverses, or pays a
  claim, and no button exists that could. The original denial record is never touched;
  a human still owns every next step.
- **How it was tested, and what live checks have (and haven't) shown.** Comparison
  logic, the evidence retriever, the workflow, the deterministic validator, and the UI
  are all covered by 695 passing offline tests, including fail-fast spies proving zero
  unintended OpenAI calls and zero shared-state mutation — every generation and judge
  call in that offline suite uses a fake model transport. Separately, two rounds of live
  smoke checks against a real, configured model have been performed: the first found
  validator defects blocking most live drafts from ever reaching the judge; those were
  fixed, and a second, budget-bounded recheck found all 3 demo presets producing a
  validated brief and a completed judge evaluation (scores 5.0/5, 5.0/5, 4.5/5, all
  PASS). **This remains three smoke-test cases, not a production reliability figure or a
  judge-calibration measurement** — don't let either a mocked-test pass rate or three
  live smoke-test cases stand in for a real model-quality claim at scale. See
  [docs/DISPUTE_AI_VALIDATION.md](DISPUTE_AI_VALIDATION.md) for the complete, unedited
  history of every check performed.

## What this demo does not claim

- No measured time savings — this is a capability demo, not an efficiency study.
- Not production-ready — a synthetic-data interview/portfolio prototype.
- Nothing here authenticates a submitted authorization reference against any real
  system — "unverified" is accurate at every step, not a placeholder.
- The existing AI investigation draft (Predefined Claims tab) never includes or reacts
  to a Dispute Review submission — the two capabilities remain fully separate.
- A passing deterministic validation or a high judge score is not proof of correctness —
  both are narrow, automated checks; only human review of the actual generated text
  against actual evidence is real verification.
- Live generation and live judge quality have been checked only against three
  smoke-test cases each, not measured for reliability or calibrated against human
  review — see [docs/DISPUTE_AI_VALIDATION.md](DISPUTE_AI_VALIDATION.md).
