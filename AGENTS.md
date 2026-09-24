# AGENTS.md — Development Guidance for the Claims Investigation Copilot

This file is the authoritative, durable instruction set for anyone (human or AI agent) working
on this repository. `CLAUDE.md` imports it. Keep it concise; put status/history in
[docs/HUMANA_BUILD_STATUS.md](docs/HUMANA_BUILD_STATUS.md), not here.

## What this project is

A synthetic-data, advisory-only prototype: **Claims Investigation Copilot**. It helps a payer
claims-operations specialist investigate an existing claim inquiry. It is not connected to
Humana production systems, and it is not authorized to approve, deny, reverse, pay, or modify
claims or authorizations, or to make medical-necessity decisions. "Accept investigation draft"
must never mean "Approve claim." Human escalation is a local, simulated review workflow, not a
real external ticket, communication, or production-system update.

See [docs/architecture.md](docs/architecture.md) for the target end-state architecture and
current implementation status, and [README.md](README.md) for what each implemented layer does.

## Rules for every change in this repository

1. **Preserve Modules 1–7** (Structured Synthetic Data, Deterministic Tools, Vector RAG,
   Knowledge Graph, Hybrid GraphRAG/EvidencePackage, LangGraph Agent Orchestration, Reusable
   Skills). Extend by adding new layers downstream of them, not by rewriting their contracts.
2. **Skills stay evidence-oriented.** No skill in `skills/` gains a final natural-language
   answer field. Any new generation capability is a separate, later layer that *consumes*
   evidence — it does not get folded into an existing skill's contract.
3. **New generation stays downstream of evidence assembly.** A future LLM/generation step
   consumes an already-built `EvidencePackage` (or a `SkillResult` wrapping one); it never
   triggers its own parallel retrieval, and it never runs when evidence was already judged
   insufficient (`SkillStatus.INSUFFICIENT_EVIDENCE` / `AgentStatus.NEEDS_REVIEW`).
4. **Do not duplicate sufficiency, retrieval, or business rules.** `is_evidence_sufficient`
   lives in exactly one place: `skills/investigate_claim.py`. Retrieval lives in `rag/`,
   `graph/`, and `context/`. Do not re-derive or shadow these elsewhere (e.g. in `agents/` or a
   new generation layer).
5. **Synthetic data only.** No real PHI, real member/patient data, real claims data, or
   proprietary Humana data or systems information, anywhere — code, fixtures, docs, or example
   output. See `tests/test_synthetic_data_hygiene.py` and `tests/test_policy_document_hygiene.py`.
6. **No consequential claim/authorization/payment actions.** Nothing in this codebase approves,
   denies, reverses, pays, or modifies a claim or authorization, or changes benefits.
7. **Human review is local and simulated.** `escalate_case` (and any future reviewer-facing UI)
   assembles a synthetic package and records a local reviewer decision only — no external
   ticketing/case-management/communication system is ever contacted.
8. **No hard-coded final answers by case ID.** Any generation or explanation logic must be
   derived from the actual `EvidencePackage`/`SkillResult` passed in, never branch on a specific
   `claim_id`/`case_id` to special-case its output.
9. **No fake metrics, traces, or test results.** Every reported number (test count, eval score,
   coverage claim) must come from an actual run. Do not estimate, round favorably, or reuse a
   stale number without re-verifying it.
10. **Unit tests must not require live API calls.** Deterministic tests run offline. A future
    live-model test (if added) is a separate, explicitly-labeled suite, never mixed into the
    default `pytest` run.
11. **Preserve source provenance and uncertainty.** Every fact surfaced to a reviewer traces back
    to a specific tool/retrieval/graph source (see `context.models.EvidenceProvenance`); absence
    of a record is stated as absence, never silently converted into a conclusion (e.g. an empty
    prior-authorization list is not "denied"; `benefit=None` is not "not covered").
12. **Never print or commit secrets.** Do not open credential files to inspect their contents.
    `.env` is gitignored; only a sanitized `.env.example` is versioned.
13. **Do not weaken controls/tests to obtain passing results.** If a test or guardrail fails,
    fix the underlying code or report the failure — do not loosen an assertion, delete a check,
    or edit a golden-set expectation just to make a run go green.
14. **Implement only the explicitly requested module/stage.** Do not use a module's ticket as
    license to refactor unrelated working code, rename things, or expand scope.

## Practical notes

- Python: this environment uses Python 3.14.5 via a project-local `.venv` (`py -3.14 -m venv
  .venv`). See [docs/HUMANA_BUILD_STATUS.md](docs/HUMANA_BUILD_STATUS.md) for the exact install/
  test/eval commands actually run and their results.
- Layering discipline: `Tool` (one deterministic op, `tools/`) → `Skill` (a business capability,
  `skills/`) → `Agent` (orchestrates skills, `agents/`). `context/` (GraphRAG/hybrid retrieval)
  is used *by* `investigate_claim`, not called directly by the agent. Do not blur these layers.
- `prompts/` now holds real, versioned runtime prompts (`investigation_brief_prompt.py`,
  `investigation_judge_prompt.py`) — no longer scaffolding. `guardrails/` remains a placeholder
  Python package (no logic) *by design*: the actual guardrails it would have held are
  implemented and enforced in their owning layers (`skills/investigate_claim.py`,
  `application/brief_validator.py`, `application/investigation_service.py`,
  `application/semantic_judge.py`, `application/scenario_lab.py`) rather than centralized here,
  per rule 4 above — see [docs/GUARDRAILS.md](docs/GUARDRAILS.md) for the single-pane-of-glass
  catalog of what's implemented and exactly which file owns each rule. `observability/` and
  `governance/` remain genuine placeholders (no logic, no artifacts) — see
  [docs/HUMANA_BUILD_STATUS.md](docs/HUMANA_BUILD_STATUS.md) for the current classification of
  each before building into them.
