"""Local persistent vector store for the Vector RAG evidence layer.

Wraps Chroma. Embeddings are always computed by an EmbeddingProvider (see
rag/embeddings.py) and passed to Chroma explicitly as vectors -- this
module never lets Chroma choose or compute its own embeddings, so
swapping embedding providers never requires touching this file.

Indexes are isolated by BOTH embedding provider AND chunking config, under
rag/index/<provider-name>/<config-name>/, e.g.:

    rag/index/
      tfidf/
        small/
        large/
      semantic/
        small/
        large/

Vectors from different embedding providers are never mixed in one Chroma
collection -- they aren't even comparable (different dimensionality,
different similarity semantics), so each (provider, config) pair gets its
own on-disk collection and its own fitted embedding state.
"""

from __future__ import annotations

from pathlib import Path

import chromadb
from chromadb.errors import NotFoundError

from rag.chunking import ChunkingConfig, chunk_documents
from rag.document_loader import load_all_policy_documents
from rag.embeddings import EmbeddingProvider, PROVIDER_NAMES, get_embedding_provider
from rag.models import Chunk, RetrievedChunk

DEFAULT_INDEX_ROOT = Path(__file__).resolve().parent / "index"
DEFAULT_PROVIDER_NAME = "tfidf"
COLLECTION_NAME = "policy_chunks"


class IndexNotBuiltError(RuntimeError):
    """Raised when querying a vector store whose index has not been built yet."""


class VectorStore:
    """A persistent Chroma collection of embedded policy chunks for one
    (embedding provider, chunking config) pair.
    """

    def __init__(
        self,
        config: ChunkingConfig,
        provider_name: str = DEFAULT_PROVIDER_NAME,
        index_root: Path | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        if provider_name.strip().lower() not in PROVIDER_NAMES:
            raise ValueError(
                f"Unsupported embedding provider {provider_name!r}. Supported: {PROVIDER_NAMES}."
            )

        self.config = config
        self.provider_name = provider_name.strip().lower()
        self.index_root = (index_root or DEFAULT_INDEX_ROOT) / self.provider_name / config.name.lower()
        self.chroma_path = self.index_root / "chroma"
        # Only meaningful for the "tfidf" provider (its fitted vocabulary);
        # ignored by providers with no corpus-specific state to persist.
        self.vocabulary_path = self.index_root / "vectorizer.joblib"

        self._embedding_provider = embedding_provider or get_embedding_provider(
            self.provider_name, self.vocabulary_path
        )
        self._client = chromadb.PersistentClient(path=str(self.chroma_path))

    def exists(self) -> bool:
        """Return True if this (provider, config) index has already been built."""
        try:
            self._client.get_collection(COLLECTION_NAME)
            return True
        except NotFoundError:
            return False

    def build_index(self, chunks: list[Chunk]) -> None:
        """(Re)build the index from chunks.

        Always rebuilds from scratch: any existing collection for this
        (provider, config) pair is dropped first, then a fresh one is
        created and the embedding provider is (re)fit on exactly this
        chunk set. This guarantees rebuilding never accumulates duplicate
        or stale chunks left over from a previous version of the corpus.
        """
        if not chunks:
            raise ValueError("Cannot build a vector index from zero chunks")

        try:
            self._client.delete_collection(COLLECTION_NAME)
        except NotFoundError:
            pass  # nothing to drop yet

        collection = self._client.create_collection(
            COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )

        texts = [chunk.text for chunk in chunks]
        self._embedding_provider.fit(texts)
        embeddings = self._embedding_provider.embed_documents(texts)

        collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            embeddings=embeddings,
            documents=texts,
            metadatas=[
                {
                    "document_id": chunk.document_id,
                    "document_title": chunk.document_title,
                    "section_id": chunk.section_id,
                    "section_title": chunk.section_title,
                }
                for chunk in chunks
            ],
        )

    def count(self) -> int:
        try:
            return self._client.get_collection(COLLECTION_NAME).count()
        except NotFoundError:
            raise IndexNotBuiltError(
                f"No {self.provider_name}/{self.config.name} index has been built yet "
                f"at {self.chroma_path}"
            ) from None

    def query(self, query_text: str, top_k: int = 3) -> list[RetrievedChunk]:
        """Return up to top_k chunks most similar to query_text, ranked by similarity.

        Raises IndexNotBuiltError if this (provider, config) index has not
        been built yet -- callers (see rag/retriever.py) should build it
        first rather than silently receiving an empty result set.
        """
        try:
            collection = self._client.get_collection(COLLECTION_NAME)
        except NotFoundError:
            raise IndexNotBuiltError(
                f"No {self.provider_name}/{self.config.name} index has been built yet "
                f"at {self.chroma_path}. Call build_index_from_documents() or "
                "VectorStore.build_index() first."
            ) from None

        query_embedding = self._embedding_provider.embed_query(query_text)
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["metadatas", "documents", "distances"],
        )

        retrieved: list[RetrievedChunk] = []
        ids = results["ids"][0]
        documents = results["documents"][0]
        metadatas = results["metadatas"][0]
        distances = results["distances"][0]

        for chunk_id, text, metadata, distance in zip(ids, documents, metadatas, distances):
            retrieved.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    document_id=metadata["document_id"],
                    document_title=metadata["document_title"],
                    section_id=metadata["section_id"],
                    section_title=metadata["section_title"],
                    text=text,
                    score=1.0 - distance,
                )
            )
        return retrieved


def build_index_from_documents(
    config: ChunkingConfig,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    documents_dir: Path | None = None,
    index_root: Path | None = None,
) -> VectorStore:
    """Load the policy corpus, chunk it under `config`, and build its
    (provider_name, config) index.

    The one entry point both the CLI below and rag/retriever.py's lazy
    build path use, so "load -> chunk -> embed -> index" is defined in
    exactly one place.
    """
    documents = load_all_policy_documents(documents_dir)
    chunks = chunk_documents(documents, config)
    store = VectorStore(config=config, provider_name=provider_name, index_root=index_root)
    store.build_index(chunks)
    return store


def _main() -> None:
    from rag.chunking import LARGE, SMALL

    for provider_name in PROVIDER_NAMES:
        for config in (SMALL, LARGE):
            store = build_index_from_documents(config, provider_name=provider_name)
            print(
                f"Built {provider_name}/{config.name} index: {store.count()} chunks "
                f"-> {store.chroma_path}"
            )


if __name__ == "__main__":
    _main()
