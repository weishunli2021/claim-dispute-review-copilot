# Implementation Fact Sheet — Claims Investigation Copilot

For interview accuracy. Every number here comes from an actual run recorded in
[docs/HUMANA_BUILD_STATUS.md](HUMANA_BUILD_STATUS.md); nothing is estimated or invented. See
[docs/KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for what to disclose proactively if asked.

---

## A. What is actually implemented

- **Synthetic structured claim data** — `data/*.json` (members, plans, claims, benefits, prior
  authorizations, providers), validated by Pydantic models. Five hand-authored scenarios.
- **Deterministic tools** — `tools/*_tool.py`: narrow, pure lookups; "not found" (`None`/`[]`) is
  always distinct from a malformed-input error (`ValueError`).
- **Vector retrieval** — `rag/`: two selectable embedding providers (TF-IDF lexical baseline,
  local `sentence-transformers` semantic), Chroma-backed, evaluated on Hit@k/SectionRecall@k/
  Precision@k across both providers and two chunking configs.
- **Knowledge graph / GraphRAG evidence** — `graph/` (NetworkX, in-process) +
  `context/hybrid_retriever.py`, combining structured facts, policy chunks, and filtered graph
  relationships into one `EvidencePackage` per claim.
- **LangGraph orchestration** — `agents/case_agent.py`: a fixed `StateGraph` (`validate_request →
  load_case → assess_case → build_evidence → assess_evidence → {complete | needs_review | error |
  max_steps_exceeded}`). No dynamic routing beyond this fixed graph.
- **Reusable skills** — `skills/`: `investigate_claim`, `explain_benefit`,
  `check_prior_authorization`, `escalate_case`, behind one shared `SkillResult` contract and a
  deterministic registry.
- **`EvidencePackage`** — `context/models.py`: structured facts + policy evidence + graph
  relationships + explicit missing-evidence list + provenance, no answer field.
- **Evidence-sufficiency gate** — `skills/investigate_claim.py: is_evidence_sufficient`: member,
  plan, benefit, and servicing-provider presence, plus ≥1 policy chunk retrieved.
- **Downstream LLM generation** — `application/investigation_service.py` +
  `application/llm_adapter.py`: exactly one OpenAI Responses-API call, only after
  `EVIDENCE_SUFFICIENT`, never inside the agent graph itself.
- **Typed `InvestigationBrief`** — `application/models.py`: summary, findings (with evidence
  refs), missing/conflicting evidence, one advisory next step. No case-identity field, no
  confidence score, no chain-of-thought field.
- **Deterministic validation** — `application/brief_validator.py`: evidence-reference existence,
  findings-require-evidence, advisory-action allowlist, narrow prohibited-authority-language
  pattern check. No semantic entailment checking.
- **Streamlit workbench** — `app.py` + `application/workbench.py`: case selector, Recorded Claim
  Information, status display, brief rendering, Supporting Evidence / Execution Trace expanders.
- **Human-review actions** — "Accept Investigation Draft" / "Mark for Further Review", local
  session-state only.
- **Component + E2E + safety evaluation** — 5 independent component evals (skill/agent/hybrid
  regression/hybrid retrieval/graph), 8 end-to-end product scenarios, 9 safety/failure tests, kept
  as separate, never-combined artifacts.

## B. What is NOT implemented

- No connection to any Humana production system, of any kind.
- No real member, claim, or provider data anywhere — five synthetic scenarios only.
- No autonomous claim adjudication (approve/deny) anywhere in the system.
- No payment or claim-reversal action, anywhere.
- No autonomous prior-authorization determination — prior-authorization candidates are shown, never matched or resolved.
- No medical-necessity decision of any kind.
- No dynamically autonomous selection across the four reusable skills — the agent graph always
  and only invokes `investigate_claim`; `explain_benefit`, `check_prior_authorization`, and
  `escalate_case` are independently callable but never chosen by the agent at runtime.
- No universal hallucination detector — H2's validator checks reference existence and pattern
  matches, not semantic truth.
- No exhaustive policy retrieval — measured Policy Section Recall is 63.33%, disclosed to the
  model in its own system prompt.
- No production-grade prompt-injection guarantee — one structural/mocked evaluation (S07) exists;
  no live adversarial testing was performed.
- No fine-tuning of any model.
- No production workflow integration (ticketing, case management, notification systems).

## C. Important architecture facts

- The main LangGraph always invokes `investigate_claim`; it never dynamically selects a skill.
- Evidence sufficiency is owned by exactly one function:
  `skills/investigate_claim.py: is_evidence_sufficient`. Nothing else recomputes it.
- The downstream application invokes the LLM only after `AgentStatus.EVIDENCE_SUFFICIENT` —
  verified structurally and by test (`tests/test_application_investigation_service.py`,
  `evals/e2e_safety_eval.py::S06`).
- Case 5 (`CLM-1005`, insufficient evidence) makes **zero** model calls — regression-tested.
- An unknown claim ID makes **zero** model calls — regression-tested. It is currently
  indistinguishable from an internal execution failure at the `AgentStatus` level (both are
  `ERROR`) — see known limitations.
- The H2 validator does not perform semantic entailment checking — it checks that cited
  references exist, that findings cite something, that the action code is on the allowlist, and a
  narrow authority-language pattern. It does not check whether a statement is actually true given
  the evidence.
- `escalate_case` reuse (Case 5 / NEEDS_REVIEW human-review path): `application/review_packet.py`
  calls `skills.escalate_case.escalate_case()` directly with data already produced by the
  evidence workflow — it does **not** re-run evidence gathering and is **not** wired into the
  LangGraph. `evidence["external_system_contacted"]` is always `False`.
- Human review actions (Accept / Mark for Further Review) are local, session-state-only prototype
  actions — they never mutate any source data file, and nothing is sent to any external system.

## D. Verified metrics (exact, as of the last full run this session)

| Metric | Result |
|---|---|
| `pytest tests/ -q` | **342 passed, 0 failed, 0 skipped** |
| Skill eval | Completion Accuracy 100%, Evidence Recall 100%, Missing-Evidence Accuracy 100%, Next-Capability 100%, Unexpected Dependency Usage 0% (8 cases) |
| Agent eval | Terminal Status Accuracy 100%, Required Action Recall 100%, Unexpected Action Rate 0%, Human Review Recall 100%, Human Review Precision 100% (9 cases) |
| Hybrid regression eval | Policy Section Recall 100%, Graph Relationship Recall 100%, Structured Evidence Completeness 100%, 0 cross-case contamination (8 cases; drift-detection only) |
| Hybrid retrieval eval (independent) | Structured Evidence Accuracy/Completeness 100%, **Policy Section Recall 63.33%**, Graph Relationship Recall 100%, Missing Evidence Accuracy 100%, Contamination 0% (5 cases) |
| Graph retrieval eval | Node/Relationship Recall 76.85%/68.52% (hops=1) → 96.30%/96.30% (hops=2) → 100%/100% (hops=3) (9 queries) |
| E2E product scenarios (E01–E08) | **8 of 8 passed** |
| Safety/failure tests (S01–S09) | **9 of 9 passed** (S09 documents a limitation rather than a clean guarantee) |
| H1 live smoke test | **LIVE_VERIFIED** — one real OpenAI call for CLM-1001 via `application.investigation_service.run_investigation`, produced a correctly-structured, correctly-hedged, H2-passing `InvestigationBrief` |
| H3 live UI smoke test | **Performed, successful** — one live call through the actual Streamlit app for CLM-1001, including a successful "Accept Investigation Draft" click |

No metric above has been rounded favorably, estimated, or invented. If a number can't be found in
`docs/HUMANA_BUILD_STATUS.md`, it should not be quoted as verified.
