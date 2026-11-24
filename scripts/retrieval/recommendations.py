"""Rank an incident's linked CSB recommendations by BM25 similarity to the query."""
from __future__ import annotations

from typing import Any

from rank_bm25 import BM25Okapi

from .index import ChunkIndex
from .tokenizer import tokenize


class RecommendationRanker:
    """Ranks the recommendations linked to an incident against a query."""

    def __init__(self, index: ChunkIndex | None = None) -> None:
        self.index = index or ChunkIndex()

    def rank(self, incident_id: str, query_text: str, top_n: int = 5) -> list[dict[str, Any]]:
        incident = self.index.incident_by_id.get(incident_id)
        if not incident:
            return []
        recommendation_ids = list(
            dict.fromkeys(
                incident.get("recommendation_ids", []) + incident.get("candidate_recommendation_ids", [])
            )
        )
        pairs = [
            (self.index.recommendation_by_id[rid], tokenize(self.index.recommendation_by_id[rid].get("text", "")))
            for rid in recommendation_ids
            if rid in self.index.recommendation_by_id
        ]
        pairs = [(rec, tokens) for rec, tokens in pairs if tokens]
        if not pairs:
            return []
        bm25 = BM25Okapi([tokens for _, tokens in pairs])
        query_tokens = tokenize(query_text)
        if not query_tokens:
            return []
        scores = bm25.get_scores(query_tokens)
        ranked = sorted(zip([rec for rec, _ in pairs], scores), key=lambda pair: pair[1], reverse=True)
        return [
            {
                "recommendation_id": rec["recommendation_id"],
                "text": rec.get("text", ""),
                "status": rec.get("status"),
                "score": float(score),
            }
            for rec, score in ranked[:top_n]
        ]
