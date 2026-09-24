# Demo Runbook — Claims Investigation Copilot

Practical, print-and-use runbook for the interview demo. See
[docs/IMPLEMENTATION_FACTS.md](IMPLEMENTATION_FACTS.md) for what's actually implemented and
[docs/KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for limitations. This file assumes H0–H4 are
frozen and verified (see [docs/HUMANA_BUILD_STATUS.md](HUMANA_BUILD_STATUS.md)).

---

## A. Pre-demo checklist

Run through this **before** the call, not during it.

- [ ] Terminal is in the project root: `D:\AI\claude-code\humana-takehome-claims-copilot`
- [ ] `.venv\Scripts\python.exe` exists (no manual "activation" needed — commands below call it
      directly, but `.\.venv\Scripts\Activate.ps1` also works if you prefer an activated shell)
- [ ] `.env` exists at the project root (`Test-Path .env` → `True`)
- [ ] `OPENAI_API_KEY` is set in `.env` — **confirm presence only, never print or display it**
- [ ] `LLM_MODEL` is set in `.env` to a real, verified model id (not the `YOUR_VALID_MODEL_ID`
      placeholder) — this is safe to display, it is not a secret
- [ ] `.env` is git-ignored (`git check-ignore -v .env` should print a match, not nothing)
- [ ] Launch command ready (below), browser tab ready at `http://localhost:8501`
- [ ] You know, without looking anything up: Case 1 → DENIED/AUTH_REQUIRED/0 authorization
      records/EVIDENCE_SUFFICIENT+DRAFTED+PASSED; Case 5 → NEEDS_REVIEW+NOT_ATTEMPTED+NOT_RUN,
      zero model calls

**Launch command:**
```
.\.venv\Scripts\python.exe -m streamlit run app.py --server.headless=true
```
**Expected local URL:** `http://localhost:8501`

---

## B. Primary demo sequence (~3–4 minutes)

1. Open **Claims Investigation Copilot** in the browser.
2. One sentence: "Recorded claim data is shown as-is; everything AI-generated is clearly
   separated and labeled below it."
3. Select **CLM-1001** from the case dropdown.
4. Point at **2. Recorded Claim Information** — DENIED, AUTH_REQUIRED, all from source data, no
   model involved yet.
5. Note the scoped question is pre-filled: *"Why was this claim denied, and what evidence is
   available?"* — editable, but leave it as-is for the default flow.
6. Click **Investigate Claim**.
7. Point at **4. Status**: `EVIDENCE_SUFFICIENT` / `DRAFTED` / `PASSED`.
8. Walk through the **Summary** and 1–2 **Findings** — note the SOURCE FACT vs. INTERPRETATION
   labeling.
9. Expand **Supporting Evidence** — show it's grouped (Claim/Benefit/Provider/Policy).
10. Point out: **0 authorization records**, and that the brief says the absence "does not
    conclusively prove the denial was correct" — not an adjudication.
11. Briefly expand **Execution Trace** — the fixed step sequence, plus the real adapter/model used.
12. Point at **Human Review**: "Accept Investigation Draft" / "Mark for Further Review" — explain
    neither approves, denies, reverses, or pays anything; both are local-only records.
13. Switch the case selector to **CLM-1005**.
14. Point at **4. Status**: `NEEDS_REVIEW` / `NOT_ATTEMPTED` / `NOT_RUN` — the prior result is
    gone (stale-state protection).
15. Close with: *"When required evidence is insufficient, I don't ask the model to fill in the
    gaps — generation simply doesn't run. Zero model calls happen on this path."*

---

## C. Optional Case-2 deep dive (if asked about ambiguity)

- Select **CLM-1002**. Recorded status is **PAID**.
- Click **Investigate Claim**, expand **Supporting Evidence** → **Authorization**.
- Point out **both** PA-1501 (EXPIRED) and PA-2001 (APPROVED) are shown.
- Say explicitly: *"The system preserves both candidates and does not claim to know which one, if
  either, applied to this specific claim — there's no date-matching logic here, by design."*

---

## D. Recovery / backup path

**Guiding rule: never fabricate a live result. If a live call fails or isn't available, say so
plainly and fall back to a previously-verified, clearly-labeled description.**

| Situation | What to do |
|---|---|
| **OpenAI call fails / errors** | The UI will show: *"Evidence was assembled successfully, but an investigation draft could not be generated."* with a technical-detail expander (failure category, no stack trace). Say: "This is the app's own defined failure path — evidence is never lost, and no draft is fabricated." Then continue the demo on **Case 5** (zero model calls, always works) or **Case 1's Recorded Claim Information / Supporting Evidence** (no live call needed). |
| **Timeout** | Same UI path as above, `generation_failure_category = TIMEOUT`. Same recovery: narrate the failure state honestly, pivot to Case 5. |
| **Network unavailable** | Everything except the live OpenAI call still works offline: case selection, Recorded Claim Information, Case 5's full NEEDS_REVIEW flow (zero model calls), Supporting Evidence, Execution Trace. Lead with those; explain the live-generation step conceptually using the verified H1/H3 facts below. |
| **Streamlit needs restart** | `Ctrl+C` in the terminal, then re-run the launch command above. Reselecting a case and re-clicking Investigate reproduces state (nothing persists across a restart by design — session-local only). |
| **Live output differs slightly from a rehearsed run** | Expected and fine — wording is not fixed (H2 does not require lexical matching). Point at the **structural** guarantees instead: status values, cited evidence references, the advisory-only action code. Never claim the wording will be identical every time. |
| **Live model produces a validation failure** | The UI will show: *"The generated draft did not pass validation and is not being presented as an accepted investigation brief."* with a validation-issues expander. This is a **good** demo moment, not a failure to hide: say "This is the deterministic H2 guardrail catching a real defect before a human ever sees it as an accepted draft." |

**If you need backup evidence of a prior successful run without a live call right now**, cite
these previously-verified, truthful facts (do not present them as a live result — say they were
previously verified):
- H1 `LIVE_VERIFIED`: one real OpenAI call for CLM-1001 produced a correctly-structured,
  correctly-hedged brief that passed H2 validation (documented in
  [docs/HUMANA_BUILD_STATUS.md](HUMANA_BUILD_STATUS.md)'s H1 entry).
- H3: one live UI smoke test reproduced the same result end-to-end through Streamlit, including
  a successful "Accept Investigation Draft" click (documented in the H3 entry).
- No screenshot files were saved for either run — see
  [docs/IMPLEMENTATION_FACTS.md](IMPLEMENTATION_FACTS.md) and the manual screenshot list below if
  you want your own backup images before the interview.

---

## E. Facts safe to say in the interview

- 342 automated tests pass.
- 8 of 8 predefined end-to-end scenarios pass their specified checks.
- 9 of 9 predefined safety/failure tests pass their specified checks (one of them documents a
  real, un-fixed limitation rather than hiding it).
- Real model integration was smoke-tested successfully, twice — once via a direct API call (H1),
  once through the full Streamlit UI (H3).
- Evidence insufficiency prevents model generation entirely — not a prompt instruction, a
  code-level gate before the adapter is ever called.
- Every evidence reference a generated brief cites is deterministically checked for existence
  against the evidence actually supplied — not a semantic check, an existence check.
- Policy retrieval has a documented, measured 63.33% Section Recall limitation — this is
  disclosed to the model in its own system prompt, not concealed.

## Claims NOT to make

- "100% accurate"
- "Hallucination-free"
- "Production-ready"
- "A fully autonomous agent"
- "Dynamically selects across all four skills" (the graph always calls `investigate_claim`; the
  other three skills are independently callable but not dynamically chosen by the agent)
- "Proves the claim denial was correct" (or incorrect)
- "Exhaustive policy understanding" (63.33% recall is real and disclosed)
- "Production-grade prompt-injection resistance" (structural protections only, not live-tested
  against adversarial input)

---

## F. Manual screenshot capture list (not auto-captured this session)

Two zero-cost screenshots were viewed and verified live this session but not saved as files (no
reliable screenshot-to-disk mechanism was available without spending significant time on
automation, which was explicitly out of scope for H5). If you want image backups before the
interview, capture these manually:

1. **Case 1, pre-investigation**: select CLM-1001, screenshot the "2. Recorded Claim Information"
   section (no live call needed).
2. **Case 5, full NEEDS_REVIEW flow**: select CLM-1005, click Investigate Claim, screenshot
   "4. Status" through the "Local Human-Review Packet" expander (zero model calls — free to
   re-run as many times as you like).
3. **Case 1, successful investigation** (requires one real, billed API call): select CLM-1001,
   click Investigate Claim, screenshot "4. Status" (`EVIDENCE_SUFFICIENT`/`DRAFTED`/`PASSED`)
   through the Findings and Human Review buttons.
4. **Case 2, authorization ambiguity** (requires one real, billed API call): select CLM-1002,
   click Investigate Claim, expand Supporting Evidence → Authorization, screenshot both PA-1501
   and PA-2001 shown together.

Items 3–4 were intentionally not run this session to avoid an unnecessary live API call — H1 and
H3 already verified this exact path works.
