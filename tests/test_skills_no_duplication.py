"""Tests ensuring Skill modules reuse existing tool/context APIs and never
independently read data/*.json or reimplement vector/graph internals."""

from __future__ import annotations

import ast
import inspect

import skills.check_prior_authorization as check_prior_auth_module
import skills.escalate_case as escalate_case_module
import skills.explain_benefit as explain_benefit_module
import skills.investigate_claim as investigate_claim_module
import skills.investigate_dispute as investigate_dispute_module

SKILL_MODULES = [
    check_prior_auth_module,
    escalate_case_module,
    explain_benefit_module,
    investigate_claim_module,
    investigate_dispute_module,
]


def _imported_module_names(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_skills_do_not_reimplement_vector_or_graph_internals():
    disallowed = {
        "rag.vector_store",
        "rag.embeddings",
        "rag.document_loader",
        "graph.builder",
        "graph.retriever",
        "context.graph_filter",
        "context.query_enrichment",
    }
    for module in SKILL_MODULES:
        imported = _imported_module_names(module)
        violations = imported & disallowed
        assert not violations, f"{module.__name__} should not import {violations}"


def test_skills_never_read_data_json_directly():
    # Deliberately precise, not a blanket "data/" substring check -- every
    # skill module's own docstring legitimately explains that it does NOT
    # read data/*.json, which would false-positive on a naive check.
    # data_store/open(/json.load( (no trailing "s", meaning "read from a
    # file object") are the actual signals of direct file access.
    for module in SKILL_MODULES:
        source = inspect.getsource(module)
        assert "data_store" not in source
        assert "open(" not in source
        assert "json.load(" not in source


def test_investigate_claim_only_depends_on_hybrid_retriever():
    imported = _imported_module_names(investigate_claim_module)
    assert "context.hybrid_retriever" in imported
    assert "rag.retriever" not in imported
    assert "graph.retriever" not in imported


def test_investigate_dispute_only_depends_on_dispute_evidence_retriever():
    imported = _imported_module_names(investigate_dispute_module)
    assert "context.dispute_evidence_retriever" in imported
    assert "rag.retriever" not in imported
    assert "graph.retriever" not in imported
    assert "application.investigation_service" not in imported
    assert "application.llm_adapter" not in imported


def test_explain_benefit_and_check_prior_authorization_use_declared_tools_only():
    for module in (explain_benefit_module, check_prior_auth_module):
        imported = _imported_module_names(module)
        # Both are permitted to call rag.retriever.search_policy directly
        # (that's just USING the existing retrieval API, not reimplementing
        # it), but must never touch the lower-level vector/graph internals.
        assert "rag.vector_store" not in imported
        assert "rag.embeddings" not in imported
