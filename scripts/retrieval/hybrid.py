"""Hybrid (BM25 + dense) retrieval strategy with cross-encoder reranking.

The hybrid retriever fuses the BM25 and dense chunk-level rankings with
Reciprocal Rank Fusion (RRF), aggregates the fused chunk scores per incident
with the shared ``BaseRetriever`` logic, and then re-ranks the top candidate
incidents with the local cross-encoder reranker (``RerankerClient``).

Pipeline per query:

1. Score chunks with BM25 and dense, fuse with RRF: ``1 / (k + rank)`` per
   ranking, summed per chunk.
2. Aggregate to incident level and apply source-case exclusion
   (``BaseRetriever._aggregate_incidents``).
3. Take the top ``top_k * 2`` incidents by hybrid score (20 when ``top_k`` is
   10) as reranker candidates.
4. Build one reranker document per unique contributing chunk
   (``"<incident title> | <section path> | <chunk text>"``, full text), POST
   once to the reranker, and map the returned scores back to incidents.
5. Aggregate chunk scores per incident with max-of-top-k and return the top
   ``top_k`` incidents in the same hit schema as BM25/dense.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .base import MAX_EVIDENCE_CHUNKS, BaseRetriever
from .bm25 import BM25Retriever
from .dense import DenseRetriever
from .index import ChunkIndex
from .reranker import RerankerClient

RRF_K = 60


def rrf_fuse(
    rankings: list[list[tuple[int, float]]],
    k: int = RRF_K,
) -> list[tuple[int, float]]:
    """Fuse chunk-level rankings with Reciprocal Rank Fusion.

    *rankings* is a list of ``(chunk_index, score)`` lists, each sorted by
    descending score. Each chunk receives ``1 / (k + rank)`` per ranking it
    appears in; the fused list is sorted by descending RRF score.
    """
    fused: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, (chunk_index, _score) in enumerate(ranking, start=1):
            fused[chunk_index] += 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda item: item[1], reverse=True)


class HybridRetriever(BaseRetriever):
    """Incident-level hybrid retriever: RRF fusion + cross-encoder reranking.

    When ``rerank`` is False the retriever skips the cross-encoder and returns
    the top ``top_k`` incidents by RRF score directly (useful for ablations and
    for running without the reranker service).
    """

    def __init__(
        self,
        index: ChunkIndex | None = None,
        bm25: BM25Retriever | None = None,
        dense: DenseRetriever | None = None,
        reranker: RerankerClient | None = None,
        rerank: bool = True,
    ) -> None:
        super().__init__(index)
        self.bm25 = bm25 or BM25Retriever(self.index)
        self.dense = dense or DenseRetriever(self.index)
        self.reranker = reranker or RerankerClient()
        self.rerank = rerank

    def score_chunks(self, query_text: str, chunk_cutoff: int = 600) -> list[tuple[int, float]]:
        """Return fused ``(chunk_index, rrf_score)`` pairs sorted by descending score."""
        bm25_scored = self.bm25.score_chunks(query_text)[:chunk_cutoff]
        dense_scored = self.dense.score_chunks(query_text, limit=chunk_cutoff)
        return rrf_fuse([bm25_scored, dense_scored])

    def retrieve(
        self,
        query_text: str,
        top_k: int = 10,
        exclusion: dict[str, Any] | None = None,
        chunk_cutoff: int = 600,
    ) -> list[dict[str, Any]]:
        fused = self.score_chunks(query_text, chunk_cutoff)
        aggregated = self._aggregate_incidents(fused, exclusion)
        if not aggregated:
            return []
        ranked = sorted(aggregated.items(), key=lambda item: item[1]["score"], reverse=True)
        candidates = ranked[: top_k * 2]
        if self.rerank:
            reranked = self._rerank(query_text, candidates)
        else:
            reranked = [(incident_id, agg["score"]) for incident_id, agg in candidates]
        hits = []
        for incident_id, score in reranked[:top_k]:
            hit = self._build_hit(incident_id, aggregated[incident_id])
            hit["score"] = float(score)
            hits.append(hit)
        for rank, hit in enumerate(hits, start=1):
            hit["rank"] = rank
        return hits

    def _rerank(
        self,
        query_text: str,
        candidates: list[tuple[str, dict[str, Any]]],
    ) -> list[tuple[str, float]]:
        """Rerank *candidates* with the cross-encoder and return ``(incident_id, score)``.

        One reranker document is built per unique contributing chunk
        (``"<incident title> | <section path> | <chunk text>"``, full text).
        A chunk that maps to several incidents is sent once and its score is
        attributed to every incident it supports. Chunk scores are aggregated
        per incident with max-of-top-k; incidents without any reranked chunk
        keep their hybrid score as a fallback.
        """
        # Map every unique chunk to all candidate incidents it supports.
        chunk_incidents: dict[str, list[str]] = defaultdict(list)
        for incident_id, agg in candidates:
            for chunk in agg["chunks"][:MAX_EVIDENCE_CHUNKS]:
                chunk_incidents[chunk["chunk_id"]].append(incident_id)

        documents: list[dict[str, Any]] = []
        seen_chunk_ids: set[str] = set()
        for incident_id, agg in candidates:
            title = self.index.incident_title(incident_id)
            for chunk in agg["chunks"][:MAX_EVIDENCE_CHUNKS]:
                chunk_id = chunk["chunk_id"]
                if chunk_id in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(chunk_id)
                section_path = " > ".join(chunk["section_path"] or [])
                text = " ".join(chunk["text"].split())
                documents.append(
                    {
                        "chunk_id": chunk_id,
                        "incident_ids": chunk_incidents[chunk_id],
                        "text": f"{title} | {section_path} | {text}",
                    }
                )

        if not documents:
            return [(incident_id, agg["score"]) for incident_id, agg in candidates]

        results = self.reranker.rerank(query_text, [doc["text"] for doc in documents])
        incident_scores: dict[str, list[float]] = defaultdict(list)
        for result in results:
            index = result.get("index")
            if not isinstance(index, int) or not (0 <= index < len(documents)):
                continue
            doc = documents[index]
            score = float(result.get("relevance_score", 0.0))
            for incident_id in doc["incident_ids"]:
                incident_scores[incident_id].append(score)

        reranked: list[tuple[str, float]] = []
        for incident_id, agg in candidates:
            scores = incident_scores.get(incident_id, [])
            score = max(scores) if scores else agg["score"]
            reranked.append((incident_id, float(score)))
        reranked.sort(key=lambda item: item[1], reverse=True)
        return reranked

