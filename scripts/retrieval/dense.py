"""Dense (vector-similarity) retrieval strategy over the Qdrant chunk store."""
from __future__ import annotations

from typing import Any

from .base import BaseRetriever
from .embeddings import Embedder, QdrantStore
from .index import ChunkIndex


class DenseRetriever(BaseRetriever):
    """Incident-level dense retriever.

    The query is embedded with the same model used to embed the corpus
    (Ollama ``bge-m3``), the top ``chunk_cutoff`` chunks are fetched from
    Qdrant by cosine similarity, and the shared aggregation logic turns the
    chunk scores into incident-level hits (sum per incident, source-case
    exclusion, evidence chunks).
    """

    def __init__(
        self,
        index: ChunkIndex | None = None,
        embedder: Embedder | None = None,
        store: QdrantStore | None = None,
    ) -> None:
        super().__init__(index)
        self.embedder = embedder or Embedder()
        self.store = store or QdrantStore()
        self._chunk_index_by_id = {
            chunk.get("chunk_id"): chunk_index
            for chunk_index, chunk in enumerate(self.index.chunks)
        }

    def score_chunks(self, query_text: str, limit: int = 600) -> list[tuple[int, float]]:
        """Return ``(chunk_index, score)`` pairs sorted by descending similarity.

        The query is embedded, Qdrant returns the top *limit* points by cosine
        similarity, and each point's ``chunk_id`` is mapped back to its index
        in ``ChunkIndex.chunks``. Points whose ``chunk_id`` is not in the index
        are skipped.
        """
        vector = self.embedder.embed([query_text])[0]
        scored: list[tuple[int, float]] = []
        for point in self.store.search(vector, limit=limit):
            chunk_id = point.get("chunk_id")
            chunk_index = self._chunk_index_by_id.get(chunk_id)
            if chunk_index is None:
                continue
            scored.append((chunk_index, float(point.get("score", 0.0))))
        return scored

    def retrieve(
        self,
        query_text: str,
        top_k: int = 10,
        exclusion: dict[str, Any] | None = None,
        chunk_cutoff: int = 600,
    ) -> list[dict[str, Any]]:
        scored = self.score_chunks(query_text, limit=chunk_cutoff)
        return self._build_hits(scored, top_k, exclusion)
