"""Semantic retrieval over the synthetic policy corpus.

Evidence only: search_policy returns ranked chunks of policy text with
their document/section provenance and a relevance score. It never
explains, summarizes, or synthesizes an answer -- that is a future
reasoning layer's job, not this module's.

"Semantic" in this module's name describes its role in the architecture
(the retrieval layer), not a claim about the active embedding provider --
retrieval can run over either the "tfidf" lexical baseline or the
"semantic" sentence-embedding provider (see rag/embeddings.py); which one
is active is controlled by `provider_name`, not by anything in this file.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from rag.chunking import LARGE, SMALL, ChunkingConfig
from rag.embeddings import PROVIDER_NAMES
from rag.models import RetrievedChunk
from rag.vector_store import VectorStore, build_index_from_documents

# The retriever's own default, distinct from VectorStore's mechanical default
# ("tfidf", used by lower-level tests that exercise indexing plumbing rather
# than retrieval quality): semantic + LARGE is the empirically recommended
# prototype default -- see evals/retrieval_eval.py's four-configuration
# comparison. It is still a prototype recommendation, not a production
# choice; see rag/embeddings.py's SemanticEmbeddingProvider docstring.
DEFAULT_CONFIG = LARGE
DEFAULT_PROVIDER_NAME = "semantic"


def search_policy(
    query: str,
    top_k: int = 3,
    config: ChunkingConfig = DEFAULT_CONFIG,
    provider_name: str = DEFAULT_PROVIDER_NAME,
) -> list[RetrievedChunk]:
    """Return up to top_k policy chunks most relevant to query, ranked by similarity.

    Evidence only: each result carries its chunk text, document id/title,
    section id/title, chunk id, and a similarity score -- never a
    generated answer or explanation.

    `config` selects which chunking configuration's index to search
    (SMALL or LARGE; see rag/chunking.py), and `provider_name` selects
    which embedding provider's index to search ("tfidf" lexical baseline
    or "semantic" sentence-embedding provider; see rag/embeddings.py).
    Retrieval is not hard-wired to one configuration or one embedding
    provider, so callers (e.g. evals/retrieval_eval.py) can compare all
    four combinations. If the selected index has not been built yet, it
    is built once automatically from the policy documents before the
    query runs.

    Raises ValueError if query is blank, or if provider_name is not one
    of PROVIDER_NAMES.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-blank string")

    store = VectorStore(config=config, provider_name=provider_name)
    if not store.exists():
        store = build_index_from_documents(config, provider_name=provider_name)

    return store.query(query, top_k=top_k)


def _main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Debug utility: run a semantic search against the synthetic policy corpus. "
            "Returns evidence only -- no answer is generated."
        ),
    )
    parser.add_argument("query", help="e.g. 'Does outpatient knee MRI require prior authorization?'")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--config", choices=["SMALL", "LARGE"], default=DEFAULT_CONFIG.name)
    parser.add_argument("--provider", choices=list(PROVIDER_NAMES), default=DEFAULT_PROVIDER_NAME)
    args = parser.parse_args(argv)

    config = SMALL if args.config == "SMALL" else LARGE
    results = search_policy(args.query, top_k=args.top_k, config=config, provider_name=args.provider)

    if not results:
        print("No results.")
        return

    for rank, result in enumerate(results, start=1):
        print(f"[{rank}] {result.document_title} ({result.document_id})")
        print(f"    section:   {result.section_title} ({result.section_id})")
        print(f"    chunk_id:  {result.chunk_id}")
        if result.score is not None:
            print(f"    score:     {result.score:.4f}")
        print(f"    text:      {result.text}")
        print()


if __name__ == "__main__":
    _main(sys.argv[1:])
