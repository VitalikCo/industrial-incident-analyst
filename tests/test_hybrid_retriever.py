"""Unit tests for the hybrid retriever (RRF fusion + cross-encoder reranking)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from retrieval.bm25 import BM25Retriever
from retrieval.dense import DenseRetriever
from retrieval.hybrid import HybridRetriever, rrf_fuse
from retrieval.index import ChunkIndex


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class _FakeEmbedder:
    """Returns a fixed vector for any input, recording the calls."""

    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = vector or [0.1, 0.2, 0.3]
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [self.vector for _ in texts]


class _FakeStore:
    """Returns a fixed list of Qdrant points, recording the search calls."""

    def __init__(self, points: list[dict] | None = None) -> None:
        self.points = points or []
        self.searches: list[tuple[list[float], int]] = []

    def search(self, vector: list[float], limit: int = 100) -> list[dict]:
        self.searches.append((vector, limit))
        return self.points


class _FakeReranker:
    """Records calls and returns deterministic scores for reranker tests."""

    def __init__(self, score_fn=None) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.score_fn = score_fn or (lambda index, doc: 1.0 - index * 0.01)

    def rerank(self, query: str, documents: list[str], top_n: int | None = None) -> list[dict]:
        self.calls.append((query, list(documents)))
        results = [
            {"index": index, "relevance_score": self.score_fn(index, doc)}
            for index, doc in enumerate(documents)
        ]
        return sorted(results, key=lambda item: item["relevance_score"], reverse=True)

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
        {
            # Shared chunk: maps to two incidents (multi-event investigation).
            "document_id": "CSB-0008",
            "chunk_id": "CSB-0008-c1",
            "chunk_type": "text",
            "retrieval_eligible": True,
            "incident_ids": [],
            "candidate_incident_ids": ["INC_H2S", "INC_BATCH"],
            "investigation_id": "2020-01-I-TX",
            "source_case_id": "investigation:2020-1-I-TX",
            "page_start": 2,
            "page_end": 2,
            "section_path": ["Findings"],
            "text": "Both the hydrogen sulfide exposure and the runaway reaction hazards were "
            "identified in the same multi-event investigation.",
        },
        {
            # Long chunk: exceeds the 700-char evidence truncation.
            "document_id": "CSB-0009",
            "chunk_id": "CSB-0009-c1",
            "chunk_type": "text",
            "retrieval_eligible": True,
            "incident_ids": [],
            "candidate_incident_ids": ["INC_REFINERY"],
            "investigation_id": "2015-01-I-CA",
            "source_case_id": "investigation:2015-1-I-CA",
            "page_start": 6,
            "page_end": 6,
            "section_path": ["Recommendations"],
            "text": "A refinery crude unit fire started when naphtha leaked from a flange during startup. "
            + "This is a long evidence sentence repeated to exceed the 700-character evidence truncation. " * 20,
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


def _hybrid(
    index: ChunkIndex,
    dense_points: list[dict] | None = None,
    reranker: _FakeReranker | None = None,
    rerank: bool = True,
) -> HybridRetriever:
    embedder = _FakeEmbedder()
    store = _FakeStore(dense_points or [])
    dense = DenseRetriever(index=index, embedder=embedder, store=store)
    reranker = reranker or _FakeReranker()
    return HybridRetriever(index=index, bm25=BM25Retriever(index), dense=dense, reranker=reranker, rerank=rerank)



def test_rrf_fuse() -> None:
    fused = rrf_fuse([[(0, 10.0), (1, 5.0)], [(0, 0.9), (1, 0.8)]])
    assert fused[0][0] == 0
    assert fused[1][0] == 1
    assert fused[0][1] == pytest.approx(2 / 61)
    assert fused[1][1] == pytest.approx(2 / 62)


def test_retrieve_returns_incident_hits(index: ChunkIndex) -> None:
    retriever = _hybrid(
        index,
        dense_points=[
            {"chunk_id": "CSB-0001-c1", "score": 0.9},
            {"chunk_id": "CSB-0002-c1", "score": 0.5},
            {"chunk_id": "CSB-0004-c1", "score": 0.3},
            {"chunk_id": "CSB-0005-c1", "score": 0.2},
        ],
    )
    hits = retriever.retrieve("worker exposed to hydrogen sulfide at a waterflood station", top_k=10)
    assert hits
    assert hits[0]["incident_id"] == "INC_H2S"
    assert hits[0]["rank"] == 1
    assert hits[0]["title"] == "Aghorn Operating Inc. Waterflood Station Hydrogen Sulfide Release"
    assert hits[0]["evidence_chunks"]
    assert set(hits[0]) == {"incident_id", "title", "score", "rank", "evidence_chunks"}
    assert [hit["rank"] for hit in hits] == list(range(1, len(hits) + 1))


def test_retrieve_excludes_source_case(index: ChunkIndex) -> None:
    retriever = _hybrid(index, dense_points=[{"chunk_id": "CSB-0001-c1", "score": 0.9}])
    exclusion = {
        "incident_id": "INC_H2S",
        "investigation_ids": ["2020-01-I-TX"],
        "document_ids": ["CSB-0001"],
        "recommendation_ids": ["2020-01-I-TX-1", "2020-01-I-TX-2"],
    }
    hits = retriever.retrieve(
        "worker exposed to hydrogen sulfide at a waterflood station",
        top_k=10,
        exclusion=exclusion,
    )
    assert all(hit["incident_id"] != "INC_H2S" for hit in hits)
    # The reranker must never see a document from the excluded incident.
    assert all("Aghorn" not in doc for _, documents in retriever.reranker.calls for doc in documents)


def test_reranker_document_format(index: ChunkIndex) -> None:
    retriever = _hybrid(index, dense_points=[{"chunk_id": "CSB-0001-c1", "score": 0.9}])
    retriever.retrieve("hydrogen sulfide", top_k=10)
    assert len(retriever.reranker.calls) == 1
    _, documents = retriever.reranker.calls[0]
    h2s_docs = [doc for doc in documents if "Aghorn" in doc]
    assert h2s_docs
    assert h2s_docs[0].startswith(
        "Aghorn Operating Inc. Waterflood Station Hydrogen Sulfide Release | Executive Summary | "
        "A worker at an industrial waterflood station"
    )


def test_reranker_documents_use_full_chunk_text(index: ChunkIndex) -> None:
    retriever = _hybrid(index, dense_points=[{"chunk_id": "CSB-0009-c1", "score": 0.9}])
    retriever.retrieve("refinery crude unit fire naphtha flange startup", top_k=10)
    _, documents = retriever.reranker.calls[0]
    long_docs = [doc for doc in documents if "refinery crude unit fire" in doc]
    assert long_docs
    # The reranker receives the full chunk text, not the 700-char evidence truncation.
    assert len(long_docs[0]) > 700
    assert "repeated to exceed the 700-character evidence truncation" in long_docs[0]


def test_reranker_deduplicates_chunk_ids(index: ChunkIndex) -> None:
    retriever = _hybrid(
        index,
        dense_points=[
            {"chunk_id": "CSB-0001-c1", "score": 0.9},
            {"chunk_id": "CSB-0002-c1", "score": 0.5},
            {"chunk_id": "CSB-0008-c1", "score": 0.8},
        ],
    )
    retriever.retrieve("hydrogen sulfide runaway reaction", top_k=10)
    _, documents = retriever.reranker.calls[0]
    # The shared chunk maps to two incidents but is sent to the reranker exactly once.
    shared = [doc for doc in documents if "multi-event investigation" in doc]
    assert len(shared) == 1


def test_rerank_aggregates_max_of_top_k(index: ChunkIndex) -> None:
    # INC_H2S has two contributing chunks with scores 0.2 and 0.9; the incident
    # score must be the max (0.9), not the sum (1.1).
    def score_fn(_index: int, doc: str) -> float:
        if "waterflood station was exposed" in doc:
            return 0.2
        if "multi-event investigation" in doc:
            return 0.9
        return 0.1

    retriever = _hybrid(
        index,
        dense_points=[
            {"chunk_id": "CSB-0001-c1", "score": 0.9},
            {"chunk_id": "CSB-0008-c1", "score": 0.8},
        ],
        reranker=_FakeReranker(score_fn=score_fn),
    )
    hits = retriever.retrieve("hydrogen sulfide", top_k=10)
    h2s = next(hit for hit in hits if hit["incident_id"] == "INC_H2S")
    assert h2s["score"] == pytest.approx(0.9)


def test_retrieve_without_rerank_returns_rrf_top_k(index: ChunkIndex) -> None:
    retriever = _hybrid(
        index,
        dense_points=[{"chunk_id": "CSB-0001-c1", "score": 0.9}],
        rerank=False,
    )
    hits = retriever.retrieve("worker exposed to hydrogen sulfide at a waterflood station", top_k=10)
    assert hits
    assert hits[0]["incident_id"] == "INC_H2S"
    assert retriever.reranker.calls == []  # the reranker is never invoked


def test_retrieve_respects_top_k(index: ChunkIndex) -> None:
    retriever = _hybrid(
        index,
        dense_points=[
            {"chunk_id": "CSB-0001-c1", "score": 0.9},
            {"chunk_id": "CSB-0002-c1", "score": 0.8},
            {"chunk_id": "CSB-0004-c1", "score": 0.7},
            {"chunk_id": "CSB-0005-c1", "score": 0.6},
            {"chunk_id": "CSB-0006-c1", "score": 0.5},
            {"chunk_id": "CSB-0007-c1", "score": 0.4},
        ],
    )
    hits = retriever.retrieve("chemical release", top_k=3)
    assert len(hits) == 3
    assert [hit["rank"] for hit in hits] == [1, 2, 3]

