"""Retriever interface and shared incident-level aggregation logic.

Shared by the BM25, dense and hybrid strategies.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any

from .index import ChunkIndex

MAX_EVIDENCE_CHARS = 700
MAX_EVIDENCE_CHUNKS = 3


class Retriever(ABC):
    """A retrieval strategy that returns incident-level hits for a query."""

    @abstractmethod
    def retrieve(
        self,
        query_text: str,
        top_k: int = 10,
        exclusion: dict[str, Any] | None = None,
        chunk_cutoff: int = 600,
    ) -> list[dict[str, Any]]:
        """Return up to *top_k* incident-level hits.

        Each hit is a dict with at least:
          incident_id, title, score, rank, evidence_chunks

        *exclusion* is the query's ``source_case_exclusion`` bundle; incidents
        sharing its investigation/document/recommendation IDs must be filtered
        out before ranking.
        """


class BaseRetriever(Retriever):
    """Shared incident-level aggregation and source-case exclusion logic.

    Subclasses implement chunk-level scoring (``score_chunks``) and call
    ``_build_hits`` to aggregate chunk scores per incident, apply the query's
    source-case exclusion and attach the best evidence chunks.
    """

    def __init__(self, index: ChunkIndex | None = None) -> None:
        self.index = index or ChunkIndex()

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

    def _build_hits(
        self,
        scored: list[tuple[int, float]],
        top_k: int,
        exclusion: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Aggregate chunk scores per incident and return the top-k hits.

        *scored* is a list of ``(chunk_index, score)`` pairs sorted by
        descending score. Chunk scores are summed per incident (union of
        ``incident_ids`` and ``candidate_incident_ids``), incidents in
        *exclusion* are dropped before ranking, and each hit carries its best
        evidence chunks.
        """
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
