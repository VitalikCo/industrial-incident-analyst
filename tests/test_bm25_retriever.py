"""Unit tests for the BM25 retriever, chunk index and recommendation ranker."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from retrieval.bm25 import BM25Retriever
from retrieval.index import ChunkIndex
from retrieval.recommendations import RecommendationRanker
from retrieval.tokenizer import tokenize


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
            "document_id": "CSB-0003",
            "chunk_id": "CSB-0003-c1",
            "chunk_type": "text",
            "retrieval_eligible": False,
            "incident_ids": [],
            "candidate_incident_ids": ["INC_TOC"],
            "investigation_id": "2001-01-I-XX",
            "source_case_id": "investigation:2001-1-I-XX",
            "page_start": 1,
            "page_end": 1,
            "section_path": [],
            "text": "Table of contents with hydrogen sulfide keywords that must not be indexed.",
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
            "incident_id": "INC_TOC",
            "titles": ["Topic Study"],
            "investigation_ids": ["2001-01-I-XX"],
            "document_ids": ["CSB-0003"],
            "recommendation_ids": [],
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

def test_retrieve_returns_incident_hits(index: ChunkIndex) -> None:
    retriever = BM25Retriever(index)
    hits = retriever.retrieve(
        "worker exposed to hydrogen sulfide at a waterflood station during operations",
        top_k=10,
    )
    assert hits
    assert hits[0]["incident_id"] == "INC_H2S"
    assert hits[0]["rank"] == 1
    assert hits[0]["title"] == "Aghorn Operating Inc. Waterflood Station Hydrogen Sulfide Release"
    assert hits[0]["evidence_chunks"]


def test_retrieve_excludes_source_case(index: ChunkIndex) -> None:
    retriever = BM25Retriever(index)
    exclusion = {
        "incident_id": "INC_H2S",
        "investigation_ids": ["2020-01-I-TX"],
        "document_ids": ["CSB-0001"],
        "recommendation_ids": ["2020-01-I-TX-1", "2020-01-I-TX-2"],
    }
    hits = retriever.retrieve(
        "worker exposed to hydrogen sulfide at a waterflood station during operations",
        top_k=10,
        exclusion=exclusion,
    )
    assert all(hit["incident_id"] != "INC_H2S" for hit in hits)


def test_retrieval_result_format(index: ChunkIndex) -> None:
    retriever = BM25Retriever(index)
    hits = retriever.retrieve("hydrogen sulfide waterflood station", top_k=10)
    record = {
        "query_id": "Q001",
        "retriever": "bm25",
        "results": [
            {"incident_id": hit["incident_id"], "rank": hit["rank"], "score": hit["score"]}
            for hit in hits
        ],
    }
    assert record["results"][0]["incident_id"] == "INC_H2S"
    assert record["results"][0]["rank"] == 1
    assert isinstance(record["results"][0]["score"], float)


def test_recommendation_ranker(index: ChunkIndex) -> None:
    ranker = RecommendationRanker(index)
    ranked = ranker.rank("INC_H2S", "personal hydrogen sulfide gas detection", top_n=5)
    assert ranked
    assert ranked[0]["recommendation_id"] == "2020-01-I-TX-1"
    assert ranked[0]["text"]



def test_tokenize() -> None:
    assert tokenize("Hydrogen sulfide (H2S) release!") == ["hydrogen", "sulfide", "h2s", "release"]
    assert tokenize("") == []
    assert tokenize("   ") == []


def test_index_skips_non_retrieval_chunks(index: ChunkIndex) -> None:
    assert len(index.chunks) == 6
    assert all(chunk["retrieval_eligible"] for chunk in index.chunks)
