"""Smoke tests for the project scaffolding.

Structural/dependency scaffolding checks only -- business-logic behavior is
covered by each layer's own tests (tools/, rag/, graph/, context/, agents/,
skills/, and, as of H1, application/).
"""

import ast
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_DIRS = [
    "agents",
    "skills",
    "tools",
    "data",
    "documents",
    "rag",
    "graph",
    "context",
    "prompts",
    "guardrails",
    "evals",
    "observability",
    "governance",
    "application",
    "tests",
    "docs",
]

PACKAGE_DIRS = [
    "agents",
    "skills",
    "tools",
    "rag",
    "graph",
    "context",
    "prompts",
    "guardrails",
    "evals",
    "observability",
    "application",
    "tests",
]

# Evidence-only layers: no direct provider-SDK import or live API call may
# ever appear here, at any future stage -- see
# test_openai_sdk_confined_to_generation_adapter below. `application` is
# deliberately excluded: it is the one place, added in H1, where the
# generation adapter (application/llm_adapter.py) is allowed to import the
# approved provider SDK.
EVIDENCE_ONLY_PACKAGES = [
    "agents",
    "skills",
    "tools",
    "rag",
    "graph",
    "context",
]


def test_expected_top_level_directories_exist():
    missing = [d for d in EXPECTED_DIRS if not (ROOT / d).is_dir()]
    assert not missing, f"Missing expected directories: {missing}"


def test_package_directories_have_init():
    missing = [d for d in PACKAGE_DIRS if not (ROOT / d / "__init__.py").is_file()]
    assert not missing, f"Missing __init__.py in: {missing}"


def test_required_root_files_exist():
    for filename in [
        "requirements.txt",
        ".env.example",
        ".gitignore",
        "README.md",
        "app.py",
        "docs/architecture.md",
        "docs/success_metrics.md",
    ]:
        assert (ROOT / filename).is_file(), f"Missing required file: {filename}"


def test_app_py_is_valid_python():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    ast.parse(source)


def test_no_unapproved_llm_provider_or_external_graph_dependencies():
    # As of the Vector RAG, Knowledge Graph, and Agent Orchestration stages,
    # langchain-text-splitters, chromadb, sentence-transformers, networkx,
    # langgraph, and langchain-core are all intentional (see rag/, graph/,
    # agents/), so none of them are disallowed here anymore.
    #
    # SUPERSEDES the former test_no_llm_synthesis_or_external_graph_dependencies_yet,
    # which forbade `openai` project-wide. That was a project-wide phase
    # constraint for "no LLM integration yet" (H0). H1 is authorized to add
    # exactly one provider SDK (openai, for application/llm_adapter.py's
    # Responses-API structured-output adapter -- see
    # docs/HUMANA_BUILD_STATUS.md's H1 entry) -- so the blanket
    # requirements.txt check is now too broad and is replaced by the
    # module-boundary check below (test_openai_sdk_confined_to_generation_adapter),
    # which is the actual architectural rule: the approved provider SDK is
    # permitted only in the generation adapter; direct provider imports/API
    # calls remain forbidden in every evidence-only layer. `anthropic` and
    # `neo4j` remain disallowed -- this project uses exactly one LLM
    # provider (no failover/multi-provider routing) and NetworkX, not an
    # external graph database, on purpose (see graph/builder.py).
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    disallowed = ["anthropic", "neo4j"]
    found = [pkg for pkg in disallowed if pkg in requirements]
    assert not found, f"requirements.txt should not yet include: {found}"


def test_openai_sdk_confined_to_generation_adapter():
    """The approved provider SDK (openai) may be imported ONLY inside
    application/ (specifically application/llm_adapter.py's adapter, plus
    any test double built alongside it) -- never in an evidence-only layer
    (agents/, skills/, tools/, rag/, graph/, context/). This is the
    runtime-relevant half of the H1 SDK-boundary rule; the other half
    (evidence-only layers must keep working without API credentials) is
    covered by tests/test_application_investigation_service.py.
    """
    import importlib
    import pkgutil

    violations: list[str] = []
    for package_name in EVIDENCE_ONLY_PACKAGES:
        package = importlib.import_module(package_name)
        for module_info in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}."):
            module = importlib.import_module(module_info.name)
            source = inspect.getsource(module)
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "openai" for a in node.names):
                    violations.append(module_info.name)
                elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "openai":
                    violations.append(module_info.name)
    assert not violations, f"openai SDK must not be imported outside application/: {violations}"
