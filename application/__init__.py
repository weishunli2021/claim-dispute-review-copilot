"""H1: a thin downstream APPLICATION service, not a Skill and not a LangGraph node.

Consumes the existing agent workflow's own product-facing result
(agents.case_agent.run_case_agent -> AgentResult, which already carries the
EvidencePackage the agent built) exactly once per request, assembles a
labeled, budgeted context from it (application.context_assembler), and --
only when evidence was already judged sufficient -- calls one configured
LLM adapter (application.llm_adapter) to draft a structured
InvestigationBrief (application.models). Nothing here re-runs evidence
retrieval, duplicates the sufficiency rule, or touches agents/skills/
context/rag/graph's contracts. See docs/HUMANA_BUILD_STATUS.md's H1 entry.
"""
