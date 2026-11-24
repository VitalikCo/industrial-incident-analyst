"""Retriever interface shared by BM25, dense and hybrid strategies."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


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
