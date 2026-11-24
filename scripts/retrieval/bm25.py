"""BM25 retrieval strategy over the CSB chunk store."""
from __future__ import annotations

from typing import Any

from rank_bm25 import BM25Okapi

from .base import BaseRetriever
from .index import ChunkIndex
from .tokenizer import tokenize


class BM25Retriever(BaseRetriever):
    """Incident-level BM25 retriever.

    Retrieval-eligible chunks are scored with BM25Okapi, chunk scores are
    aggregated per incident (sum over matching chunks), source-case exclusion
    is applied, and the top-k incidents are returned together with their best
    evidence chunks.
    """

    def __init__(self, index: ChunkIndex | None = None) -> None:
        super().__init__(index)
        # Keep only chunks with at least one token so BM25's average document
        # length stays positive.
        self._valid_chunk_indices: list[int] = []
        corpus: list[list[str]] = []
        for chunk_index, chunk in enumerate(self.index.chunks):
            tokens = tokenize(chunk.get("text", ""))
            if tokens:
                self._valid_chunk_indices.append(chunk_index)
                corpus.append(tokens)
        self._corpus = corpus
        self._bm25 = BM25Okapi(corpus)

    def score_chunks(self, query_text: str) -> list[tuple[int, float]]:
        """Return ``(chunk_index, score)`` pairs sorted by descending score."""
        query_tokens = tokenize(query_text)
        if not query_tokens:
            return []
        scores = self._bm25.get_scores(query_tokens)
        ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
        return [(self._valid_chunk_indices[corpus_index], float(score)) for corpus_index, score in ranked]

    def retrieve(
        self,
        query_text: str,
        top_k: int = 10,
        exclusion: dict[str, Any] | None = None,
        chunk_cutoff: int = 600,
    ) -> list[dict[str, Any]]:
        scored = self.score_chunks(query_text)[:chunk_cutoff]
        return self._build_hits(scored, top_k, exclusion)
