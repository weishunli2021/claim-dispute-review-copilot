"""Loading the billing-correction scenario's synthetic policy document, two
ways:

  - get_billing_policy_sections() -- deterministic, full-document read. All
    sections, every time, in document order. Used for a "browse the whole
    policy" view, and as the source corpus for the search below.
  - search_billing_policy() -- REAL semantic vector search (embeddings +
    cosine similarity) over that same document, ranked by relevance to a
    query. Reuses this codebase's existing Vector RAG engine (rag/chunking.py,
    rag/vector_store.py) and its LOCAL "semantic" embedding provider
    (sentence-transformers, no API key, no network call, no paid model call
    -- see rag/embeddings.py's SemanticEmbeddingProvider) -- the same
    provider context/hybrid_retriever.py already uses by default for the
    original investigation pipeline, so this adds no new dependency.

Deliberately NOT reused: rag.retriever.search_policy /
rag.document_loader.load_all_policy_documents. Those are hard-wired to the
SHARED six-document corpus (an explicit filename list, a required "synthetic
data notice" disclaimer marker, and a different section-header format) and
to the shared index under rag/index/. Mixing this scenario's policy into
that corpus, or that index, would mean either editing shared fixtures the
Golden Dataset & Evaluation tab's own retrieval evals assume an exact shape
for, or silently changing what a query elsewhere in the app might retrieve.
This module instead builds its OWN small PolicyDocument in memory from the
already-parsed BILL-N sections and its own, separate, persistent index
under rag/index_billing/ -- fully isolated, same engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rag.chunking import SMALL, ChunkingConfig, chunk_documents
from rag.models import PolicyDocument, PolicySection, RetrievedChunk
from rag.vector_store import VectorStore

DEFAULT_BILLING_POLICY_PATH = (
    Path(__file__).resolve().parent.parent / "documents" / "billing_correction_policy.md"
)
DEFAULT_BILLING_POLICY_INDEX_ROOT = Path(__file__).resolve().parent.parent / "rag" / "index_billing"

_SECTION_HEADER_PATTERN = re.compile(r"^##\s+(BILL-\d+):\s*(.+)$", re.MULTILINE)
_DOCUMENT_ID = "billing_correction_policy"
_DOCUMENT_TITLE = "Synthetic Billing Correction & Corrected-Claim Review Policy (FICTIONAL)"
_EMBEDDING_PROVIDER_NAME = "semantic"


class BillingPolicyLoadError(RuntimeError):
    """Raised when the synthetic billing policy document is missing or has
    no parseable BILL-N sections."""


@dataclass(frozen=True)
class BillingPolicySection:
    section_id: str
    title: str
    text: str


def _parse_sections(raw_text: str) -> list[BillingPolicySection]:
    matches = list(_SECTION_HEADER_PATTERN.finditer(raw_text))
    sections: list[BillingPolicySection] = []
    for index, match in enumerate(matches):
        section_id, title = match.group(1), match.group(2).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_text)
        sections.append(BillingPolicySection(section_id=section_id, title=title, text=raw_text[start:end].strip()))
    return sections


@lru_cache(maxsize=4)
def _load_sections(path_str: str) -> tuple[BillingPolicySection, ...]:
    path = Path(path_str)
    if not path.is_file():
        raise BillingPolicyLoadError(f"Missing synthetic billing policy document: {path}")
    sections = _parse_sections(path.read_text(encoding="utf-8"))
    if not sections:
        raise BillingPolicyLoadError(f"{path.name} has no parseable '## BILL-N: Title' sections.")
    return tuple(sections)


def get_billing_policy_sections(path: Path = DEFAULT_BILLING_POLICY_PATH) -> list[BillingPolicySection]:
    """Return all sections of the synthetic billing policy, in document
    order. Always the full, fixed set -- no ranking, no model call."""
    return list(_load_sections(str(path)))


def _as_policy_document(sections: tuple[BillingPolicySection, ...]) -> PolicyDocument:
    return PolicyDocument(
        document_id=_DOCUMENT_ID,
        document_title=_DOCUMENT_TITLE,
        source_path=str(DEFAULT_BILLING_POLICY_PATH),
        sections=[
            PolicySection(
                document_id=_DOCUMENT_ID,
                document_title=_DOCUMENT_TITLE,
                section_id=section.section_id,
                section_title=section.title,
                section_uid=f"{_DOCUMENT_ID}::{section.section_id}",
                text=section.text,
            )
            for section in sections
        ],
    )


@lru_cache(maxsize=4)
def _get_or_build_store(path_str: str, index_root_str: str, config_name: str) -> VectorStore:
    config = SMALL if config_name == "SMALL" else ChunkingConfig(name=config_name, chunk_size=900, chunk_overlap=120)
    store = VectorStore(config=config, provider_name=_EMBEDDING_PROVIDER_NAME, index_root=Path(index_root_str))
    if not store.exists():
        document = _as_policy_document(_load_sections(path_str))
        chunks = chunk_documents([document], config)
        store.build_index(chunks)
    return store


def search_billing_policy(
    query: str,
    top_k: int = 3,
    *,
    path: Path = DEFAULT_BILLING_POLICY_PATH,
    index_root: Path = DEFAULT_BILLING_POLICY_INDEX_ROOT,
    config: ChunkingConfig = SMALL,
) -> list[RetrievedChunk]:
    """Real semantic vector search over the synthetic billing policy,
    ranked by cosine similarity to `query`. Builds this scenario's own
    ISOLATED index (under `index_root`, never rag/index/) on first use;
    later calls reuse the persisted index. Raises ValueError for a blank
    query, matching rag.retriever.search_policy's own contract.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-blank string")
    store = _get_or_build_store(str(path), str(index_root), config.name)
    return store.query(query, top_k=top_k)
