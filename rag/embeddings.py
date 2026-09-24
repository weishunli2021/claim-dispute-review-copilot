"""Embedding abstraction for the Vector RAG layer.

Two embedding providers are available, selectable by name -- never by
importing a specific implementation directly:

- "tfidf": TfidfLexicalEmbeddingProvider, a **lexical** baseline. It embeds
  by word-overlap statistics (TF-IDF), so it matches queries that share
  literal vocabulary with the corpus and misses paraphrases that don't.
  This is NOT semantic retrieval, even though it produces vectors and goes
  through the same Chroma pipeline -- see its class docstring.
- "semantic": SemanticEmbeddingProvider, backed by a local sentence-
  embedding model (default: sentence-transformers/all-MiniLM-L6-v2). It
  can match a paraphrase that shares no vocabulary with the corpus, at the
  cost of a much heavier dependency (torch) and non-trivial model load
  time. See its class docstring for why this specific model, and why it
  is a prototype choice, not a production one.

Callers (vector_store.py, retriever.py) depend only on the EmbeddingProvider
protocol and on get_embedding_provider(name) -- never on TfidfLexicalEmbeddingProvider
or SemanticEmbeddingProvider directly. Adding a third provider later means adding a
class and one branch in get_embedding_provider(), nothing else.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer

PROVIDER_NAMES = ("tfidf", "semantic")


class EmbeddingProvider(Protocol):
    """Anything that can turn text into fixed-length embedding vectors."""

    def fit(self, texts: list[str]) -> None:
        """Prepare the provider using the full corpus about to be embedded.

        For a provider backed by a fixed pretrained model (SemanticEmbeddingProvider)
        this is a no-op beyond making sure the model is loaded; for a corpus-fit
        provider (TfidfLexicalEmbeddingProvider) this fits the vocabulary. Callers
        always call this once, before any embed_* call, on the exact set of chunk
        texts being indexed.
        """
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class TfidfLexicalEmbeddingProvider:
    """A local, dependency-light **lexical** embedding baseline backed by TF-IDF.

    This is explicitly a lexical baseline, not semantic embedding retrieval:
    it represents text as weighted word/n-gram overlap statistics, so cosine
    similarity between two texts is high only to the extent they share literal
    vocabulary. "Does outpatient MRI need prior authorization?" and "Do I need
    permission from the plan before getting this scan?" describe the same
    concept but share almost no words, and this provider will not connect
    them -- see the SemanticEmbeddingProvider below, and the paraphrase
    queries in evals/retrieval_golden_set.json, for exactly this failure
    mode measured empirically.

    Chosen as the *baseline* because it requires no model download, no API
    key, and no torch dependency, and because a lot of this synthetic policy
    corpus's expected queries do share literal vocabulary with the source
    text (service codes, denial reason codes, plan names) -- a lexical
    method is a legitimate, cheap first cut for that portion of queries.

    The vectorizer's vocabulary is fit once, on the full set of chunk texts,
    when an index is built (see vector_store.py), and persisted alongside
    that index so later queries reuse the same fitted vocabulary instead of
    silently refitting on a single query string.
    """

    def __init__(self, vocabulary_path: Path | None = None) -> None:
        self._vocabulary_path = vocabulary_path
        self._vectorizer: TfidfVectorizer | None = None
        if vocabulary_path is not None and vocabulary_path.is_file():
            self._vectorizer = joblib.load(vocabulary_path)

    @property
    def is_fitted(self) -> bool:
        return self._vectorizer is not None

    def fit(self, texts: list[str]) -> None:
        if not texts:
            raise ValueError("Cannot fit an embedding vectorizer on an empty corpus")
        vectorizer = TfidfVectorizer()
        vectorizer.fit(texts)
        self._vectorizer = vectorizer
        if self._vocabulary_path is not None:
            self._vocabulary_path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(vectorizer, self._vocabulary_path)

    def _require_fitted(self) -> TfidfVectorizer:
        if self._vectorizer is None:
            raise RuntimeError(
                "TfidfLexicalEmbeddingProvider has no fitted vocabulary yet. Build the "
                "index first (see rag/vector_store.py: build_index_from_documents), or "
                "point vocabulary_path at an existing fitted vectorizer file."
            )
        return self._vectorizer

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectorizer = self._require_fitted()
        return vectorizer.transform(texts).toarray().tolist()

    def embed_query(self, text: str) -> list[float]:
        vectorizer = self._require_fitted()
        return vectorizer.transform([text]).toarray()[0].tolist()


_SENTENCE_TRANSFORMER_CACHE: dict[str, object] = {}


def _load_sentence_transformer(model_name: str):
    """Load (or reuse) a SentenceTransformer model, cached per process.

    Loading is not free (a few seconds even from a warm local cache), and
    search_policy() constructs a fresh VectorStore -- and therefore a fresh
    SemanticEmbeddingProvider -- on every call. Caching the underlying model
    object here, keyed by model name, means repeated construction reuses the
    already-loaded model instead of reloading it from disk every query.
    """
    if model_name not in _SENTENCE_TRANSFORMER_CACHE:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed. Add it to requirements.txt and "
                "`pip install -r requirements.txt` before using the semantic embedding "
                "provider."
            ) from exc
        _SENTENCE_TRANSFORMER_CACHE[model_name] = SentenceTransformer(model_name)
    return _SENTENCE_TRANSFORMER_CACHE[model_name]


class SemanticEmbeddingProvider:
    """A **semantic** embedding provider backed by a local sentence-embedding model.

    Default model: "sentence-transformers/all-MiniLM-L6-v2" -- a small
    (~80MB, 384-dimension output), widely-used general-purpose sentence
    embedding model that runs on CPU in reasonable time and needs no API
    key, chosen specifically because it is practical to run locally in
    this environment without a GPU or a hosted API. Unlike the TF-IDF
    baseline, it can recognize that "prior authorization" and "permission
    from the plan" are related concepts even though they share no words,
    because it was trained on sentence-similarity objectives rather than
    word-overlap statistics.

    Architectural tradeoffs, stated plainly:
    - Dependency weight: pulls in torch and transformers, multiple hundreds
      of MB, versus TF-IDF's near-zero footprint.
    - Latency: model load takes seconds (cached per-process here -- see
      _load_sentence_transformer), and each encode() call is slower than a
      TF-IDF transform, though still fast enough for this corpus's scale.
    - No corpus-specific fitting: fit() here is a no-op beyond ensuring the
      model is loaded -- there is no vocabulary to learn, which also means
      it cannot specialize to this corpus's specific terminology the way a
      fine-tuned or domain-adapted model could.

    This is a reasonable **prototype** choice for demonstrating semantic
    retrieval cheaply and locally. It is explicitly NOT a production model
    selection: a production system would benchmark multiple candidate
    models against a much larger, real golden set, weigh domain-specific
    fine-tuning, and evaluate hosted-embedding-API tradeoffs (latency,
    cost, data residency, larger/better models) that are out of scope for
    this prototype stage.
    """

    DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or self.DEFAULT_MODEL_NAME
        self._model = None

    def _require_model(self):
        if self._model is None:
            self._model = _load_sentence_transformer(self.model_name)
        return self._model

    def fit(self, texts: list[str]) -> None:
        if not texts:
            raise ValueError("Cannot fit an embedding provider on an empty corpus")
        self._require_model()  # pretrained model: no vocabulary to fit, just preload

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._require_model()
        return model.encode(list(texts), show_progress_bar=False).tolist()

    def embed_query(self, text: str) -> list[float]:
        model = self._require_model()
        return model.encode([text], show_progress_bar=False)[0].tolist()


def get_embedding_provider(
    provider_name: str, vocabulary_path: Path | None = None
) -> EmbeddingProvider:
    """Construct the embedding provider named by provider_name.

    This is the one place that maps a provider name to an implementation
    -- vector_store.py and retriever.py call this (directly or via
    get_default_embedding_provider) and never import TfidfLexicalEmbeddingProvider
    or SemanticEmbeddingProvider themselves. Adding a third provider means
    adding a branch here, not touching retrieval or vector-store code.

    "tfidf" -> TfidfLexicalEmbeddingProvider (lexical baseline).
    "semantic" -> SemanticEmbeddingProvider (local sentence-embedding model).

    vocabulary_path is only meaningful for "tfidf" (where a fitted
    vocabulary needs to be persisted alongside the vector index); it is
    ignored for "semantic", which has no corpus-specific state to persist.

    Raises ValueError for any other provider_name, with a clear message,
    rather than silently falling back to something unexpected.
    """
    name = provider_name.strip().lower()
    if name == "tfidf":
        return TfidfLexicalEmbeddingProvider(vocabulary_path=vocabulary_path)
    if name == "semantic":
        return SemanticEmbeddingProvider()
    raise ValueError(
        f"Unsupported embedding provider {provider_name!r}. Supported providers: "
        f"{PROVIDER_NAMES}. Add a new EmbeddingProvider implementation and a branch in "
        "get_embedding_provider() before configuring another one."
    )


def get_default_embedding_provider(vocabulary_path: Path | None = None) -> EmbeddingProvider:
    """Return the embedding provider configured via the RAG_EMBEDDING_PROVIDER
    environment variable (default: "tfidf"). Isolated here so a caller that
    doesn't care which provider is active (or a future hosted-provider
    integration reading its own API key from the environment) never has to
    duplicate this logic.
    """
    provider_name = os.environ.get("RAG_EMBEDDING_PROVIDER", "tfidf")
    return get_embedding_provider(provider_name, vocabulary_path=vocabulary_path)
