"""Answer pipeline: retrieval -> recommendation ranking -> LLM -> validated answer."""
from __future__ import annotations

from typing import Any

from .bm25 import BM25Retriever
from .index import ChunkIndex
from .llm import LLMClient
from .recommendations import RecommendationRanker
from .schemas import QueryAnswer

RECOMMENDATION_SNAPSHOT_DATE = "2026-09-16"

SYSTEM_PROMPT = """\
You are an industrial incident analyst working with a corpus of U.S. Chemical Safety and
Hazard Investigation Board (CSB) investigation reports. You are given a description of a new
incident and a ranked list of candidate historical incidents retrieved by a BM25 lexical
search, together with evidence snippets and candidate CSB safety recommendations.

Identify the historical incidents that are genuinely similar to the new incident and the CSB
recommendations that may be relevant to the new incident.

Rules:
- Base every claim on the provided evidence. Do not invent incident titles, recommendation
  IDs, page numbers, or facts.
- Include at most 10 incidents, ordered by relevance (most similar first).
- For each included incident provide:
  - "incident": the exact incident title.
  - "match_reason": one or two sentences explaining the similarity, grounded in the evidence.
  - "related_recommendations": only recommendations from the provided candidate list that are
    plausibly relevant to the new incident. Each has "id" (the official CSB recommendation ID)
    and "topic" (a short topic label).
  - "evidence_sources": citation strings, e.g. "CSB Recommendation 2020-01-I-TX-1" or
    "CSB-6220 p.7 (Executive Summary)".
- If no candidate incident is similar enough, set "abstained" to true and return an empty
  "similar_incidents" list.
- List any limitations in "limitations" (for example, recommendation statuses are as of the
  CSB snapshot 2026-09-16).

Respond with a single JSON object only, using exactly this schema:
{
  "similar_incidents": [
    {
      "incident": "...",
      "match_reason": "...",
      "related_recommendations": [{"id": "...", "topic": "..."}],
      "evidence_sources": ["..."]
    }
  ],
  "limitations": [],
  "abstained": false
}
"""


def _compact(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class AnswerPipeline:
    """Orchestrates retrieval, recommendation ranking and LLM answer generation."""

    def __init__(
        self,
        index: ChunkIndex | None = None,
        retriever: BM25Retriever | None = None,
        ranker: RecommendationRanker | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.index = index or ChunkIndex()
        self.retriever = retriever or BM25Retriever(self.index)
        self.ranker = ranker or RecommendationRanker(self.index)
        self.llm = llm or LLMClient()

    def _build_user_prompt(
        self,
        query_text: str,
        hits: list[dict[str, Any]],
        recs_per_incident: int,
    ) -> str:
        lines: list[str] = []
        lines.append("NEW INCIDENT DESCRIPTION:")
        lines.append(query_text)
        lines.append("")
        lines.append("CANDIDATE HISTORICAL INCIDENTS (ranked by BM25):")
        for hit in hits:
            lines.append("")
            lines.append(f"--- Incident rank {hit['rank']} ---")
            lines.append(f"incident_id: {hit['incident_id']}")
            lines.append(f"title: {hit['title']}")
            lines.append("evidence:")
            for chunk in hit.get("evidence_chunks", [])[:2]:
                section = " / ".join(chunk.get("section_path") or []) or "n/a"
                lines.append(
                    f"- {chunk.get('document_id')} p.{chunk.get('page_start')} "
                    f"(section: {section}): {_compact(chunk.get('text', ''), 600)}"
                )
            recommendations = self.ranker.rank(hit["incident_id"], query_text, top_n=recs_per_incident)
            if recommendations:
                lines.append("candidate recommendations:")
                for rec in recommendations:
                    status = rec.get("status") or "unknown"
                    lines.append(
                        f"- {rec['recommendation_id']} [{status}]: {_compact(rec.get('text', ''), 400)}"
                    )
            else:
                lines.append("candidate recommendations: none linked in the catalog")
        return "\n".join(lines)

    def answer_query(
        self,
        query: dict[str, Any],
        top_k: int = 10,
        chunk_cutoff: int = 600,
        recs_per_incident: int = 5,
    ) -> dict[str, Any]:
        query_text = query["query_text"]
        exclusion = query.get("source_case_exclusion")
        hits = self.retriever.retrieve(
            query_text, top_k=top_k, exclusion=exclusion, chunk_cutoff=chunk_cutoff
        )
        user_prompt = self._build_user_prompt(query_text, hits, recs_per_incident)
        raw = self.llm.chat_json(SYSTEM_PROMPT, user_prompt)
        answer = QueryAnswer.model_validate(raw)
        # Guarantee the dated snapshot limitation whenever recommendations are cited.
        if answer.similar_incidents and not any(
            RECOMMENDATION_SNAPSHOT_DATE in limitation for limitation in answer.limitations
        ):
            answer.limitations.append(
                f"Recommendation statuses are as of the CSB snapshot {RECOMMENDATION_SNAPSHOT_DATE}."
            )
        return {"query_id": query["query_id"], **answer.model_dump()}
