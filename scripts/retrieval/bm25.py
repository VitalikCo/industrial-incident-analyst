"""BM25 retrieval strategy over the CSB chunk store."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from rank_bm25 import BM25Okapi

from .base import Retriever
from .index import ChunkIndex
from .tokenizer import tokenize

MAX_EVIDENCE_CHARS = 700
MAX_EVIDENCE_CHUNKS = 3


class BM25Retriever(Retriever):
    """Incident-level BM25 retriever.

    Retrieval-eligible chunks are scored with BM25Okapi, chunk scores are
    aggregated per incident (sum over matching chunks), source-case exclusion
    is applied, and the top-k incidents are returned together with their best
    evidence chunks.
    """

    def __init__(self, index: ChunkIndex | None = None) -> None:
        self.index = index or ChunkIndex()
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

    def excluded_incident_ids(self, exclusion: dict[str, Any] | None) -> set[str]:
        """Incident IDs that must be hidden for a query's source-case exclusion."""
        if not exclusion:
            return set()
        source_incident = exclusion.get("incident_id")
        investigation_ids = set(exclusion.get("investigation_ids", []))
        document_ids = set(exclusion.get("document_ids", []))
        recommendation_ids = set(exclusion.get("recommendation_ids", []))
        excluded: set[str] = set()
        for incident_id, incident in self.index.incident_by_id.items():
            if source_incident and incident_id == source_incident:
                excluded.add(incident_id)
                continue
            if set(incident.get("investigation_ids", [])) & investigation_ids:
                excluded.add(incident_id)
                continue
            if set(incident.get("document_ids", [])) & document_ids:
                excluded.add(incident_id)
                continue
            if set(incident.get("recommendation_ids", [])) & recommendation_ids:
                excluded.add(incident_id)
                continue
            if set(incident.get("candidate_recommendation_ids", [])) & recommendation_ids:
                excluded.add(incident_id)
        return excluded

    def retrieve(
        self,
        query_text: str,
        top_k: int = 10,
        exclusion: dict[str, Any] | None = None,
        chunk_cutoff: int = 600,
    ) -> list[dict[str, Any]]:
        scored = self.score_chunks(query_text)[:chunk_cutoff]
        incident_scores: dict[str, float] = defaultdict(float)
        incident_chunks: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for chunk_index, score in scored:
            if score <= 0.0:
                continue
            chunk = self.index.chunks[chunk_index]
            for incident_id in self.index.chunk_incident_ids[chunk_index]:
                incident_scores[incident_id] += score
                incident_chunks[incident_id].append(
                    {
                        "chunk_id": chunk.get("chunk_id"),
                        "document_id": chunk.get("document_id"),
                        "page_start": chunk.get("page_start"),
                        "page_end": chunk.get("page_end"),
                        "section_path": chunk.get("section_path") or [],
                        "text": chunk.get("text", ""),
                        "score": float(score),
                    }
                )

        excluded = self.excluded_incident_ids(exclusion)
        hits: list[dict[str, Any]] = []
        for incident_id, score in incident_scores.items():
            if incident_id in excluded:
                continue
            chunks = sorted(incident_chunks[incident_id], key=lambda item: item["score"], reverse=True)
            evidence = []
            for chunk in chunks[:MAX_EVIDENCE_CHUNKS]:
                text = " ".join(chunk["text"].split())
                evidence.append(
                    {
                        "chunk_id": chunk["chunk_id"],
                        "document_id": chunk["document_id"],
                        "page_start": chunk["page_start"],
                        "page_end": chunk["page_end"],
                        "section_path": chunk["section_path"],
                        "text": text[:MAX_EVIDENCE_CHARS],
                    }
                )
            hits.append(
                {
                    "incident_id": incident_id,
                    "title": self.index.incident_title(incident_id),
                    "score": float(score),
                    "evidence_chunks": evidence,
                }
            )

        hits.sort(key=lambda hit: hit["score"], reverse=True)
        hits = hits[:top_k]
        for rank, hit in enumerate(hits, start=1):
            hit["rank"] = rank
        return hits
