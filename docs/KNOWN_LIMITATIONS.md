# Known Limitations — Claims Investigation Copilot

Prototype limitations, stated plainly. None of these were fixed in H5 — this is a disclosure
document, not a task list to execute. "Production next step" notes are optional roadmap input
only, not commitments, and were **not** implemented here.

1. **Policy Section Recall = 63.33%** (measured, `evals/hybrid_retrieval_eval.py`, an aggregate
   over a small synthetic evaluation set). This demonstrates that retrieved policy evidence is
   not exhaustive and should not be treated as complete — it is not a measured per-request
   probability that a relevant section will be missed. Disclosed to the model itself in its own
   system prompt.
   *Production next step: expand/re-tune the retrieval corpus and embedding strategy, and
   re-measure — not a prompt-engineering fix.*

2. **The deterministic validator (H2) does not prove semantic entailment.** It checks that cited
   evidence references exist, that findings cite something, that the action code is on the
   allowlist, and a narrow authority-language pattern — never whether a statement is actually
   supported in substance by the evidence it cites.
   *Production next step: a separate, explicitly-scoped grounding/entailment check (e.g. a
   claim-level citation-support classifier), evaluated on its own merits, not folded into H2.*

3. **Prompt-injection coverage is structural and limited**, not adversarially proven. One
   evaluation (`evals/e2e_safety_eval.py::S07`) verifies the defensive instruction is present in
   the system prompt and that a compliant-with-injection output would still fail H2's Rule D — it
   does not test whether a real model actually resists a live injection attempt.
   *Production next step: a red-team evaluation suite against the live model, run periodically.*

4. **`AgentStatus.ERROR` currently combines two different situations**: a legitimate "claim not
   found" and an unexpected internal execution/tool failure. Both map to the same status value
   today, distinguishable only by the free-text `.error` message
   (`evals/e2e_safety_eval.py::S09`, documented not fixed).
   *Production next step: split into two `AgentStatus` values (e.g. `NOT_FOUND` vs.
   `EXECUTION_ERROR`) — an evidence-architecture change, deliberately out of scope for H0–H5.*

5. **Case-2 authorization applicability is intentionally left unresolved.** Both PA-1501 (EXPIRED)
   and PA-2001 (APPROVED) are preserved as candidates; no code anywhere matches an authorization
   record to a specific claim by date of service or otherwise.
   *Production next step: a dedicated, explicitly-approved date/service-matching rule — a new
   business-logic decision requiring its own product sign-off, not something to add silently.*

6. **Synthetic-data-only prototype.** All five cases, all policy documents, and the fictional
   payer ("Meridian") are fabricated. No real PHI, member data, or proprietary Humana content
   exists anywhere in this repository.
   *Production next step: not applicable to this prototype's purpose; a real deployment would
   need a full data-governance and PHI-handling review before any real data is introduced.*

7. **Model output can vary across live runs.** H2 does not require or check lexical wording
   match; only structural properties (status values, cited references, action code) are
   guaranteed stable. A live rerun of the same question may phrase its summary differently.
   *Production next step: none needed — this is expected LLM behavior, not a defect; the H2
   validator's job is exactly to keep the *structural* guarantees stable regardless.*

8. **No real payer-system integration of any kind.** `escalate_case` produces a synthetic
   packet only; `external_system_contacted` is always `False`. No ticketing, case-management, or
   notification system is ever contacted.
   *Production next step: a real escalation-execution layer, with its own security/compliance
   review — explicitly out of scope for this prototype.*

9. **Production governance, security, and performance engineering are not implemented.** No
   authentication/authorization on the Streamlit app, no rate limiting, no load testing, no
   observability/monitoring stack, no secrets-management beyond a local `.env`, no incident
   runbook beyond this demo's own recovery steps.
   *Production next step: standard pre-production hardening — none of it was in scope for an
   interview-stage prototype.*
