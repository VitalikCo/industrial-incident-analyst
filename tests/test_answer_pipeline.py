"""Unit tests for the LLM client, JSON extraction and the answer pipeline."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from retrieval.bm25 import BM25Retriever
from retrieval.index import ChunkIndex
from retrieval.llm import LLMClient, _extract_json
from retrieval.pipeline import AnswerPipeline, RECOMMENDATION_SNAPSHOT_DATE
from retrieval.recommendations import RecommendationRanker


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


@pytest.fixture
def index(tmp_path: Path) -> ChunkIndex:
    chunks = [
        {
            "document_id": "CSB-0001",
            "chunk_id": "CSB-0001-c1",
            "chunk_type": "text",
            "retrieval_eligible": True,
            "incident_ids": [],
            "candidate_incident_ids": ["INC_H2S"],
            "investigation_id": "2020-01-I-TX",
            "source_case_id": "investigation:2020-1-I-TX",
            "page_start": 1,
            "page_end": 1,
            "section_path": ["Executive Summary"],
            "text": "A worker at an industrial waterflood station was exposed to hydrogen sulfide "
            "during operations. The investigation addresses personal gas detection and "
            "hazardous-energy isolation.",
        },
        {
            "document_id": "CSB-0002",
            "chunk_id": "CSB-0002-c1",
            "chunk_type": "text",
            "retrieval_eligible": True,
            "incident_ids": [],
            "candidate_incident_ids": ["INC_BATCH"],
            "investigation_id": "2008-08-I-WV",
            "source_case_id": "investigation:2008-8-I-WV",
            "page_start": 3,
            "page_end": 3,
            "section_path": ["Analysis"],
            "text": "A runaway chemical reaction inside a pressure vessel caused overpressure, "
            "rupture and a prolonged unit fire.",
        },
        {
            "document_id": "CSB-0004",
            "chunk_id": "CSB-0004-c1",
            "chunk_type": "text",
            "retrieval_eligible": True,
            "incident_ids": [],
            "candidate_incident_ids": ["INC_DUST"],
            "investigation_id": "2003-01-I-KY",
            "source_case_id": "investigation:2003-1-I-KY",
            "page_start": 2,
            "page_end": 2,
            "section_path": ["Incident Description"],
            "text": "A combustible dust deflagration occurred inside a dust collector during a grinding operation.",
        },
        {
            "document_id": "CSB-0005",
            "chunk_id": "CSB-0005-c1",
            "chunk_type": "text",
            "retrieval_eligible": True,
            "incident_ids": [],
            "candidate_incident_ids": ["INC_AMMONIA"],
            "investigation_id": "2010-01-I-IL",
            "source_case_id": "investigation:2010-1-I-IL",
            "page_start": 4,
            "page_end": 4,
            "section_path": ["Analysis"],
            "text": "Anhydrous ammonia was released from a refrigeration system during a maintenance operation.",
        },
        {
            "document_id": "CSB-0006",
            "chunk_id": "CSB-0006-c1",
            "chunk_type": "text",
            "retrieval_eligible": True,
            "incident_ids": [],
            "candidate_incident_ids": ["INC_ETHYLENE"],
            "investigation_id": "2012-01-I-TX",
            "source_case_id": "investigation:2012-1-I-TX",
            "page_start": 5,
            "page_end": 5,
            "section_path": ["Findings"],
            "text": "An ethylene oxide release from a storage tank led to a vapor cloud explosion.",
        },
        {
            "document_id": "CSB-0007",
            "chunk_id": "CSB-0007-c1",
            "chunk_type": "text",
            "retrieval_eligible": True,
            "incident_ids": [],
            "candidate_incident_ids": ["INC_REFINERY"],
            "investigation_id": "2015-01-I-CA",
            "source_case_id": "investigation:2015-1-I-CA",
            "page_start": 6,
            "page_end": 6,
            "section_path": ["Recommendations"],
            "text": "A refinery crude unit fire started when naphtha leaked from a flange during startup.",
        },
    ]
    incidents = [
        {
            "incident_id": "INC_H2S",
            "titles": ["Aghorn Operating Inc. Waterflood Station Hydrogen Sulfide Release"],
            "investigation_ids": ["2020-01-I-TX"],
            "document_ids": ["CSB-0001"],
            "recommendation_ids": ["2020-01-I-TX-1", "2020-01-I-TX-2"],
            "candidate_recommendation_ids": [],
        },
        {
            "incident_id": "INC_BATCH",
            "titles": ["Batch Reactor Explosion"],
            "investigation_ids": ["2008-08-I-WV"],
            "document_ids": ["CSB-0002"],
            "recommendation_ids": ["2008-8-I-WV-R1"],
            "candidate_recommendation_ids": [],
        },
        {
            "incident_id": "INC_DUST",
            "titles": ["Dust Collector Deflagration"],
            "investigation_ids": ["2003-01-I-KY"],
            "document_ids": ["CSB-0004"],
            "recommendation_ids": [],
            "candidate_recommendation_ids": [],
        },
        {
            "incident_id": "INC_AMMONIA",
            "titles": ["Ammonia Refrigeration Release"],
            "investigation_ids": ["2010-01-I-IL"],
            "document_ids": ["CSB-0005"],
            "recommendation_ids": [],
            "candidate_recommendation_ids": [],
        },
        {
            "incident_id": "INC_ETHYLENE",
            "titles": ["Ethylene Oxide Vapor Cloud Explosion"],
            "investigation_ids": ["2012-01-I-TX"],
            "document_ids": ["CSB-0006"],
            "recommendation_ids": [],
            "candidate_recommendation_ids": [],
        },
        {
            "incident_id": "INC_REFINERY",
            "titles": ["Refinery Crude Unit Fire"],
            "investigation_ids": ["2015-01-I-CA"],
            "document_ids": ["CSB-0007"],
            "recommendation_ids": [],
            "candidate_recommendation_ids": [],
        },
    ]
    recommendations = [
        {
            "recommendation_id": "2020-01-I-TX-1",
            "text": "Provide personal hydrogen sulfide gas detection to workers at the waterflood station.",
            "status": "Open",
        },
        {
            "recommendation_id": "2020-01-I-TX-2",
            "text": "Implement a lockout/tagout program for hazardous-energy isolation.",
            "status": "Open",
        },
        {
            "recommendation_id": "2008-8-I-WV-R1",
            "text": "Improve reactive-chemistry hazard reviews for pressure vessels.",
            "status": "Closed",
        },
    ]
    store = tmp_path / "chunk_store"
    store.mkdir()
    _write_jsonl(store / "chunks.jsonl", chunks)
    _write_jsonl(tmp_path / "incident_catalog.jsonl", incidents)
    _write_jsonl(tmp_path / "recommendation_catalog.jsonl", recommendations)
    return ChunkIndex(
        chunks_path=store / "chunks.jsonl",
        incident_catalog_path=tmp_path / "incident_catalog.jsonl",
        recommendation_catalog_path=tmp_path / "recommendation_catalog.jsonl",
    )


def test_extract_json_plain() -> None:
    assert _extract_json('{"abstained": false}') == {"abstained": False}


def test_extract_json_code_fence() -> None:
    content = 'Here is the answer:\n```json\n{"abstained": true}\n```\n'
    assert _extract_json(content) == {"abstained": True}

def test_build_user_prompt_contains_query_and_incidents(index: ChunkIndex) -> None:
    class UnusedLLM:
        def chat_json(self, system: str, user: str) -> dict:
            raise AssertionError("chat_json should not be called for prompt building")

    retriever = BM25Retriever(index)
    ranker = RecommendationRanker(index)
    pipeline = AnswerPipeline(index=index, retriever=retriever, ranker=ranker, llm=UnusedLLM())  # type: ignore[arg-type]
    hits = retriever.retrieve(
        "worker exposed to hydrogen sulfide at a waterflood station during operations", top_k=10
    )
    prompt = pipeline._build_user_prompt(
        "worker exposed to hydrogen sulfide at a waterflood station during operations",
        hits,
        recs_per_incident=5,
    )
    assert "NEW INCIDENT DESCRIPTION:" in prompt
    assert "Aghorn Operating Inc. Waterflood Station Hydrogen Sulfide Release" in prompt
    assert "2020-01-I-TX-1" in prompt
    assert "CSB-0001 p.1" in prompt


def test_answer_query_validates_and_adds_snapshot_limitation(index: ChunkIndex) -> None:
    class FakeLLM:
        def chat_json(self, system: str, user: str) -> dict:
            return {
                "similar_incidents": [
                    {
                        "incident": "Aghorn Operating Inc. Waterflood Station Hydrogen Sulfide Release",
                        "match_reason": "Both involve H2S exposure and gas-detection concerns.",
                        "related_recommendations": [
                            {"id": "2020-01-I-TX-1", "topic": "Personal H2S detection"}
                        ],
                        "evidence_sources": ["CSB Recommendation 2020-01-I-TX-1"],
                    }
                ],
                "limitations": [],
                "abstained": False,
            }

    retriever = BM25Retriever(index)
    ranker = RecommendationRanker(index)
    pipeline = AnswerPipeline(index=index, retriever=retriever, ranker=ranker, llm=FakeLLM())  # type: ignore[arg-type]
    answer = pipeline.answer_query(
        {
            "query_id": "Q001",
            "query_text": "worker exposed to hydrogen sulfide at a waterflood station during operations",
            "source_case_exclusion": {
                "incident_id": "INC_BATCH",
                "investigation_ids": ["2008-08-I-WV"],
                "document_ids": ["CSB-0002"],
                "recommendation_ids": ["2008-8-I-WV-R1"],
            },
        }
    )
    assert answer["query_id"] == "Q001"
    assert answer["abstained"] is False
    assert answer["similar_incidents"][0]["incident"].startswith("Aghorn")
    assert any(RECOMMENDATION_SNAPSHOT_DATE in limitation for limitation in answer["limitations"])



def test_extract_json_balanced_span() -> None:
    content = 'prefix {"similar_incidents": [], "abstained": false} suffix'
    assert _extract_json(content) == {"similar_incidents": [], "abstained": False}


def test_extract_json_malformed_raises() -> None:
    with pytest.raises(ValueError):
        _extract_json("not json at all")


def test_llm_client_requires_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(ValueError, match="LLM endpoint is not configured"):
        LLMClient(config_path=Path("nonexistent-llm.yaml"))
