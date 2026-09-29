"""End-to-end tests for the compiled claim-investigation agent."""

from __future__ import annotations

import ast
import inspect

import agents.case_agent as case_agent_module
import agents.nodes as nodes_module
from agents.case_agent import run_case_agent
from agents.state import AgentStatus


def _imported_module_names(module) -> set[str]:
    """Return every dotted module name this module actually imports
    (via `import x.y` or `from x.y import z`), ignoring prose mentions in
    docstrings/comments -- a plain substring check would false-positive on
    a comment explaining what ISN'T imported."""
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_case1_reaches_evidence_sufficient():
    result = run_case_agent("CLM-1001", "My claim was denied. What's wrong with it?")
    assert result.status == AgentStatus.EVIDENCE_SUFFICIENT
    assert result.actions_taken[-1] == "COMPLETE"


def test_case2_reaches_evidence_sufficient():
    result = run_case_agent("CLM-1002", "Was this claim paid correctly?")
    assert result.status == AgentStatus.EVIDENCE_SUFFICIENT


def test_case3_reaches_evidence_sufficient():
    result = run_case_agent("CLM-1003", "Why was my lab claim denied?")
    assert result.status == AgentStatus.EVIDENCE_SUFFICIENT


def test_case4_reaches_evidence_sufficient():
    result = run_case_agent("CLM-1004", "Why was my physical therapy claim denied?")
    assert result.status == AgentStatus.EVIDENCE_SUFFICIENT


def test_case5_reaches_needs_review():
    result = run_case_agent("CLM-1005", "Why was my specialist visit claim denied?")
    assert result.status == AgentStatus.NEEDS_REVIEW
    assert result.actions_taken[-1] == "NEEDS_REVIEW"
    assert "benefit" in result.missing_information
    assert "servicing_provider" in result.missing_information


def test_unknown_claim_reaches_error():
    result = run_case_agent("CLM-9999", "anything")
    assert result.status == AgentStatus.ERROR
    assert result.actions_taken == ["VALIDATE_REQUEST", "LOAD_CASE", "ERROR"]
    assert "CLM-9999" in result.error


def test_invalid_claim_id_reaches_error_before_load_case():
    result = run_case_agent("bad claim id", "anything")
    assert result.status == AgentStatus.ERROR
    assert result.actions_taken == ["VALIDATE_REQUEST", "ERROR"]


def test_empty_query_reaches_error():
    result = run_case_agent("CLM-1001", "   ")
    assert result.status == AgentStatus.ERROR
    assert result.actions_taken == ["VALIDATE_REQUEST", "ERROR"]


def test_max_steps_enforcement_terminates_cleanly():
    result = run_case_agent("CLM-1001", "My claim was denied. What's wrong with it?", max_steps=2)
    assert result.status == AgentStatus.MAX_STEPS_EXCEEDED
    assert result.actions_taken[-1] == "MAX_STEPS_EXCEEDED"
    assert result.step_count <= 3  # stopped promptly, did not run the full workflow


def test_actions_taken_preserves_deterministic_execution_order():
    result = run_case_agent("CLM-1001", "My claim was denied. What's wrong with it?")
    assert result.actions_taken == [
        "VALIDATE_REQUEST",
        "LOAD_CASE",
        "ASSESS_CASE",
        "BUILD_EVIDENCE",
        "ASSESS_EVIDENCE",
        "COMPLETE",
    ]

    second = run_case_agent("CLM-1001", "My claim was denied. What's wrong with it?")
    assert second.actions_taken == result.actions_taken


def test_evidence_package_is_preserved_on_the_result():
    result = run_case_agent("CLM-1001", "My claim was denied. What's wrong with it?")
    assert result.evidence_package is not None
    assert result.evidence_package.claim_id == "CLM-1001"
    assert result.evidence_package.policy_chunks
    assert result.evidence_package.graph_relationships


def test_evidence_package_is_none_when_workflow_errors_before_building_it():
    result = run_case_agent("CLM-9999", "anything")
    assert result.evidence_package is None


def test_agents_do_not_duplicate_retrieval_implementation():
    # agents/ must reuse tools.case_context directly (a plain tool-level
    # capability) and the investigate_claim SKILL for full claim
    # investigation -- it must never reimplement, or even directly
    # import, Vector RAG / Knowledge Graph / GraphRAG internals. As of the
    # Skills-layer integration, that includes context.hybrid_retriever
    # itself: only skills/investigate_claim.py may call it directly now.
    disallowed_modules = {
        "rag.vector_store",
        "rag.retriever",
        "rag.embeddings",
        "rag.chunking",
        "graph.builder",
        "graph.retriever",
        "context.graph_filter",
        "context.query_enrichment",
        "context.hybrid_retriever",
    }
    for module in (nodes_module, case_agent_module):
        imported = _imported_module_names(module)
        violations = imported & disallowed_modules
        assert not violations, f"{module.__name__} should not import {violations}"
