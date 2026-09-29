# Conceptual Architecture

This document describes the **intended end-state architecture** for the Enterprise AI Case
Resolution Copilot prototype. It is a target design, not a description of what is currently
implemented — see the repository root [README.md](../README.md) for what actually exists at
this stage.

## Flow

```
User
  -> Agent Orchestrator
  -> Reusable Skills
  -> Deterministic Tools
  -> Structured Synthetic Data

  (in parallel)
  -> Vector RAG
  -> Knowledge Graph
  -> GraphRAG / Hybrid Retrieval

  (all of the above feed)
  -> Context Assembler
  -> Prompt / AI Harness
  -> LLM
  -> Grounding / Guardrails
  -> Answer OR Human Escalation
  -> Observability
  -> Evaluation
```

## Component notes

- **User** — a member-service associate investigating a specific member case (e.g. a denied
  outpatient MRI claim).
- **Agent Orchestrator** (`agents/`) *(IMPLEMENTED — orchestration only, no answer generation)*
  — a LangGraph `StateGraph` (`case_agent.py`) that coordinates the deterministic investigation
  workflow: `validate_request → load_case → assess_case → build_evidence → assess_evidence →
  {complete | needs_review | error | max_steps_exceeded}`. It decides which capability runs
  next (`routing.py`) and never retrieves anything or judges evidence sufficiency itself — both
  are delegated to the `investigate_claim` Skill (`build_evidence` calls it; routing reads its
  resulting status). `load_case` still calls `tools.case_context.get_case_context` directly for
  its own narrow existence check — a plain Tool-level capability, not the fuller
  claim-investigation business capability the Skill owns. No LLM call anywhere in `agents/`;
  the output is an investigation trace (`AgentResult.actions_taken`) and the same
  `EvidencePackage` the Skill produces — never a natural-language answer or an autonomous
  claims decision.
- **Reusable Skills** (`skills/`) *(IMPLEMENTED — business capabilities only, no answer
  generation)* — typed capabilities that coordinate multiple tools/context sources, behind one
  shared contract (`base.py`: `SkillMetadata`, `SkillInput`, `SkillResult`) and a deterministic
  `SkillRegistry` (`registry.py`; no LLM-based skill selection). Four Skills at version
  `1.0.0`:
  - `investigate_claim` (`investigate_claim.py`) — wraps `context.hybrid_retriever` and now
    owns the evidence-sufficiency rule (moved here from `agents/routing.py`).
  - `explain_benefit` (`explain_benefit.py`) — member/plan/service benefit evidence, without a
    full claim investigation; never infers coverage when no benefit record exists.
  - `check_prior_authorization` (`check_prior_authorization.py`) — every matching authorization
    record for a member/service, preserved as-is; an empty result is never an assumed denial.
  - `escalate_case` (`escalate_case.py`) — a **synthetic** escalation package only; never
    contacts an external system, never sends anything.
  
  Every `SkillResult` has **no final-answer field** — evidence, status, missing information,
  and (where relevant) a suggested `next_capability`, nothing more. Golden-set evaluation
  (`evals/skill_golden_set.json`, `evals/skill_eval.py`) scores Skill Completion Accuracy,
  Required Evidence Recall, Missing-Evidence Accuracy, Correct Next-Capability Rate, and
  Unexpected Dependency/Tool Usage Rate.
- **Deterministic Tools** (`tools/`) *(IMPLEMENTED)* — narrow, predictable functions with no
  model reasoning inside them: `get_member`, `get_claim`, `get_plan`, `get_benefits`,
  `get_prior_authorizations`, `get_provider`, plus `get_case_context` to assemble the full fact
  set for a claim. Each is a pure lookup over the DataStore — no fuzzy matching, no inference.
  A well-formed but unknown identifier returns `None`/`[]`; a blank or malformed identifier
  raises `ValueError`.
- **Structured Synthetic Data** (`data/`) *(IMPLEMENTED)* — fabricated
  member/plan/claim/benefit/prior-authorization/provider JSON records, loaded and validated
  against Pydantic models (`tools/models.py`) by `tools/data_store.py`. Covers five scenarios,
  including a straightforward auth-required denial, a successful authorized claim, a
  not-covered service, an out-of-network denial, and a deliberately incomplete case.
- **Vector RAG** (`rag/`) *(IMPLEMENTED — evidence retrieval only, no answer generation)* —
  document ingestion (`document_loader.py`), chunking (`chunking.py`, `SMALL`/`LARGE`
  configurations), a persisted Chroma vector store (`vector_store.py`), and retrieval
  (`retriever.py`: `search_policy`) over the synthetic policy corpus (`documents/*.md`). Returns
  ranked `RetrievedChunk` evidence with document/section provenance and a similarity score.
  Does not explain, summarize, or generate an answer from what it retrieves — no LLM call in
  this layer.

  **Embeddings are two distinct, selectable providers** (`embeddings.py`), never hard-wired:
  - `"tfidf"` — a **lexical baseline** (`TfidfLexicalEmbeddingProvider`), matching on literal
    word overlap. Not semantic retrieval, despite going through the same vector pipeline.
  - `"semantic"` — a local sentence-embedding model (`SemanticEmbeddingProvider`, default
    `sentence-transformers/all-MiniLM-L6-v2`), which captures paraphrase/synonym similarity the
    lexical baseline misses. A prototype-appropriate choice, not a production model selection.

  Indexes are isolated by **both** provider and chunking config
  (`rag/index/<tfidf|semantic>/<small|large>/`) — vectors from different embedding providers
  are never mixed in one Chroma collection.
- **Knowledge Graph** (`graph/`) *(IMPLEMENTED — structured relationship evidence only, no
  answer generation)* — an in-memory NetworkX graph built directly from validated
  `tools.data_store.DataStore` records (`builder.py`): 8 node types (`Member`, `Plan`, `Claim`,
  `Benefit`, `Service`, `Provider`, `Network`, `PriorAuthorization`) and 9 relation types
  (`ENROLLED_IN`, `HAS_BENEFIT`, `FOR_SERVICE`, `BELONGS_TO`, `SERVICED_BY`, `ORDERED_BY`,
  `PARTICIPATES_IN`, `USES_NETWORK`, `HAS_AUTHORIZATION`), all structural facts — no business
  conclusions are ever encoded as an edge. `retriever.py`'s `get_claim_neighborhood` /
  `get_member_neighborhood` return a bounded-depth (`max_hops`) neighborhood as typed
  `GraphContext`/`GraphNode`/`GraphRelationship` objects (`models.py`), independent of NetworkX.
  Where Vector RAG answers "what does policy say," the Knowledge Graph answers "how are these
  specific records connected" — a member's plan, a claim's servicing/ordering providers, a
  member's prior-authorization history. **NetworkX, not a production graph platform**: in-memory,
  no server, no persistence, no query language — appropriate for a prototype this size, not for
  production graph workloads. See `graph/builder.py`'s module docstring for the full reasoning,
  including the referential-integrity policy that raises on a structurally impossible reference
  (e.g. a claim's `member_id` not resolving to any Member) while tolerating Case 5's deliberately
  incomplete provider reference without inventing a placeholder node.
- **GraphRAG / Hybrid Retrieval** (`context/`) *(IMPLEMENTED — evidence assembly only, no
  answer generation)* — a lightweight, in-process hybrid retrieval layer (not Microsoft
  GraphRAG, LlamaIndex's GraphRAG, or any other specific external framework: no community
  detection, no graph summarization) that selectively combines the three evidence sources above
  into one `EvidencePackage` per claim investigation (`hybrid_retriever.py`:
  `build_evidence_package`). To restate the split this rests on: **Vector RAG = unstructured
  policy evidence**, **Knowledge Graph = relationship evidence**, **Tools = authoritative
  case-specific structured facts**, and **GraphRAG = selective assembly of those three into
  task-relevant context** — nothing more.
  - **Models** (`models.py`): `StructuredEvidence`/`PolicyEvidence`/`RelationshipEvidence` each
    subclass their source type (`CaseContext`/`RetrievedChunk`/`GraphRelationship`) so evidence
    can never drift from what `tools`/`rag`/`graph` actually produce; `MissingEvidence` states
    an absence as a plain fact, never a conclusion; `EvidencePackage` has **no answer field**.
  - **Graph-context filtering** (`graph_filter.py`): a raw graph neighborhood is never passed
    through as-is — it reaches other cases' entities through shared hub nodes (e.g. two claims
    for the same service). Filtering walks outward from the claim root through a fixed set of
    explicit patterns, scoping each step's allowed sources to exactly the node ids validated by
    the previous step, which is what actually prevents cross-case contamination (a relation-type
    filter alone would not, since a hub's edges share the same relation types you want to keep).
  - **Query enrichment** (`query_enrichment.py`): deterministically appends the claim's own
    `service_code`/`denial_reason_code` (only if present) to the retrieval query — no LLM
    rewrite, original query always preserved separately.
- **Context Assembler** (`application/context_assembler.py`) *(IMPLEMENTED, H1)* — the hybrid
  retriever's `EvidencePackage` (structured facts, retrieved documents, graph facts, all with
  source attribution preserved) is relabeled into a budgeted `AssembledContext` and fed into
  the generation prompt below — this is a pure relabeling/budgeting step over data the agent
  already produced in its one execution, never a second retrieval.
- **Prompt / AI Harness** (`prompts/`) *(IMPLEMENTED, H1/H6)* — versioned system/user prompts
  for the brief generator (`prompts/investigation_brief_prompt.py`) and the optional semantic
  judge (`prompts/investigation_judge_prompt.py`); each call is a single, constrained
  structured-output request, not an open-ended harness/tool-loop.
- **LLM** *(IMPLEMENTED, H1)* — the OpenAI Responses API's structured-output parsing
  (`application/llm_adapter.py`), constrained to a closed `InvestigationBrief` schema. Exactly
  one call per investigation; never invoked when evidence was already judged insufficient.
- **Grounding / Guardrails** *(IMPLEMENTED, H2 + H7 remediation + H8-adjacent catalog)* — a
  deterministic, non-LLM validator (`application/brief_validator.py`) checks evidence-reference
  existence, per-finding citation, an advisory-action allowlist, and a narrow authority-language
  pattern check, before any brief is presented as accepted. See
  [GUARDRAILS.md](GUARDRAILS.md) for the full catalog aggregating this and 15 other guardrails
  across every layer — implemented as a documentation/presentation layer, not a new `guardrails/`
  enforcement module (that package remains a placeholder by design; see `AGENTS.md`).
- **Answer OR Human Escalation** *(IMPLEMENTED, H1-H3, LOCAL ONLY)* — a validated brief is
  presented for human Accept/Mark-for-Review; anything that fails evidence sufficiency,
  generation, or validation is routed to a local human-review packet
  (`application/review_packet.py`). This is a **local, session-only record**, not a real
  external escalation/ticketing integration — no such integration exists or is planned here.
- **Observability** (`observability/`) *(still PLANNED)* — no production tracing/logging/
  monitoring exists; `AgentResult.actions_taken` is a debug-oriented execution trace only.
- **Governance** (`governance/`) *(still PLANNED)* — no model/system-card or risk-assessment
  artifacts exist yet.
- **Evaluation** (`evals/`) *(IMPLEMENTED, H0-H4)* — component golden-set evals per layer,
  8 end-to-end product-scenario checks (E01-E08), and 9 safety/failure-mode checks (S01-S09) —
  see [success_metrics.md](success_metrics.md) for the target metrics and
  [HUMANA_BUILD_STATUS.md](HUMANA_BUILD_STATUS.md) for actual results, including the honestly-
  reported 63.33% Policy Section Recall finding.

## Current implementation status

- **Structured Synthetic Data** — IMPLEMENTED (`data/*.json`, validated by `tools/models.py`).
- **Deterministic Tools** — IMPLEMENTED (`tools/*_tool.py`, `tools/data_store.py`,
  `tools/case_context.py`).
- **Vector RAG** — IMPLEMENTED, evidence retrieval only (`rag/document_loader.py`,
  `rag/chunking.py`, `rag/embeddings.py`, `rag/vector_store.py`, `rag/retriever.py`), with two
  selectable embedding providers (`"tfidf"` lexical baseline, `"semantic"` local
  sentence-embedding model), indexes isolated by provider and chunking config, and a retrieval
  golden set evaluated across all four combinations on Hit@1/3/5, SectionRecall@1/3/5, and
  Precision@1/3/5 (`evals/retrieval_golden_set.json`, `evals/retrieval_eval.py`). **No answer
  generation**: nothing in `rag/` calls an LLM or drafts a response from retrieved evidence.
- **Knowledge Graph** — IMPLEMENTED, structured relationship evidence only (`graph/builder.py`,
  `graph/models.py`, `graph/retriever.py`), built from `DataStore` (never reads `data/*.json`
  directly), with a graph golden set evaluated on Node Recall and Relationship Recall at
  `max_hops` 1/2/3 (`evals/graph_golden_set.json`, `evals/graph_retrieval_eval.py`). **No answer
  generation**: nothing in `graph/` calls an LLM or explains why a claim was approved or denied.
- **Hybrid GraphRAG (evidence assembly)** — IMPLEMENTED, evidence assembly only
  (`context/models.py`, `context/graph_filter.py`, `context/query_enrichment.py`,
  `context/hybrid_retriever.py`), combining structured facts, policy evidence, and
  claim-filtered graph relationships into one `EvidencePackage`. Two separate evaluations, on
  purpose: a regression suite (`evals/hybrid_regression_set.json`,
  `evals/hybrid_regression_eval.py`) that detects drift from past pipeline output, and an
  independently-authored quality golden set (`evals/hybrid_retrieval_golden_set.json`,
  `evals/hybrid_retrieval_eval.py`) scored on Structured Evidence Accuracy/Completeness, Policy
  Section Recall, Graph Relationship Recall, Missing Evidence Accuracy, and Cross-Case
  Contamination Rate. **No answer generation**: `EvidencePackage` has no final-answer field, and
  nothing in `context/` calls an LLM.
- **Agent Orchestration** — IMPLEMENTED, orchestration only (`agents/state.py`,
  `agents/nodes.py`, `agents/routing.py`, `agents/case_agent.py`), running a LangGraph
  `StateGraph` that calls the `investigate_claim` Skill (not `context/` directly) for evidence
  assembly and sufficiency judgment, with its own independently-authored golden set evaluated
  on Terminal Status Accuracy, Required Action Recall, Unexpected Action Rate, Human Review
  Recall, and Human Review Precision (`evals/agent_golden_set.json`, `evals/agent_eval.py`).
  **No answer generation, no autonomous claims decisions**: `AgentResult` has no final-answer
  field, and nothing in `agents/` calls an LLM.
- **Reusable Skills** — IMPLEMENTED, business capabilities only (`skills/base.py`,
  `skills/registry.py`, `skills/investigate_claim.py`, `skills/explain_benefit.py`,
  `skills/check_prior_authorization.py`, `skills/escalate_case.py`), all version `1.0.0`, each
  reusing existing `tools`/`context`/`rag` APIs rather than reimplementing them, with an
  independently-authored golden set evaluated on Skill Completion Accuracy, Required Evidence
  Recall, Missing-Evidence Accuracy, Correct Next-Capability Rate, and Unexpected Dependency/
  Tool Usage Rate (`evals/skill_golden_set.json`, `evals/skill_eval.py`). **No answer
  generation**: `SkillResult` has no final-answer field, and nothing in `skills/` calls an LLM.
- **Application layer (generation, validation, human review)** — IMPLEMENTED (H1/H2/H3):
  `application/investigation_service.py` orchestrates exactly one agent execution, then (only
  when evidence was already judged sufficient) one context-assembly + one LLM call to draft an
  `InvestigationBrief`, then one deterministic validation pass
  (`application/brief_validator.py`). `app.py` is a full Streamlit UI (not a placeholder shell)
  with Predefined Claims and Scenario Lab tabs, human Accept/Mark-for-Review actions, and a
  read-only Safety & Guardrails catalog expander.
- **Scenario Lab** — IMPLEMENTED (H7), an experimental single-user testing surface
  (`application/scenario_lab.py`) that runs a temporary, in-memory-only synthetic claim through
  the exact same pipeline above via a reversible `DataStore`/graph overlay — never a second
  investigation engine, never a write to `data/*.json`.
- **Optional semantic judge** — IMPLEMENTED (H6/H8), a strictly advisory, manually-invoked
  second model call (`application/semantic_judge.py`) scoring an already-validated brief on
  four dimensions with both a PASS/FAIL/UNCERTAIN verdict and a 1-5 rubric score. Not calibrated
  against a human-rated dataset; never a workflow gate. See
  [H6_SEMANTIC_JUDGE.md](H6_SEMANTIC_JUDGE.md).
- **Still not implemented**: production observability/tracing, governance/model-card artifacts,
  and any real external human-escalation integration (the current human-review path is local
  and session-only by design, not a stub for a future integration this repository intends to
  build).
