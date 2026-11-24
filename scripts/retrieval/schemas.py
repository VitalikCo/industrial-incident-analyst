"""Pydantic schemas for retrieval results and LLM-generated answers."""
from __future__ import annotations

from pydantic import BaseModel, Field


class IncidentHit(BaseModel):
    """One incident-level retrieval hit in the pool-builder format."""

    incident_id: str
    rank: int
    score: float


class RetrievalResult(BaseModel):
    """One record per query and retriever, as consumed by build_relevance_pool.py."""

    query_id: str
    retriever: str
    results: list[IncidentHit] = Field(default_factory=list)


class RecommendationCandidate(BaseModel):
    """A candidate CSB recommendation ranked for one incident."""

    recommendation_id: str
    text: str
    status: str | None = None
    score: float = 0.0


class RelatedRecommendation(BaseModel):
    """A recommendation selected by the LLM for a similar incident."""

    id: str
    topic: str


class SimilarIncident(BaseModel):
    """One similar incident in the final answer."""

    incident: str
    match_reason: str
    related_recommendations: list[RelatedRecommendation] = Field(default_factory=list)
    evidence_sources: list[str] = Field(default_factory=list)


class QueryAnswer(BaseModel):
    """The final answer object for one query."""

    similar_incidents: list[SimilarIncident] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    abstained: bool = False
