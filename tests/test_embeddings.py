"""Unit tests for the embedding generation and Qdrant storage module."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from retrieval.embeddings import Embedder, QdrantStore, chunk_point_id


class _FakeEmbeddings:
    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors

    def create(self, model: str, input: list[str]) -> SimpleNamespace:
        assert model == "bge-m3"
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=vector)
                for index, vector in enumerate(self._vectors)
            ]
        )


class _FakeOpenAI:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.embeddings = _FakeEmbeddings(vectors)


class _FakeQdrant:
    def __init__(self) -> None:
        self.collections: set[str] = set()
        self.points: dict[str, dict] = {}
        self.created: list[str] = []
        self.upserted: list[dict] = []

    def collection_exists(self, name: str) -> bool:
        return name in self.collections

    def create_collection(self, collection_name: str, vectors_config) -> None:
        self.collections.add(collection_name)
        self.created.append(collection_name)

    def upsert(self, collection_name: str, points, wait: bool) -> None:
        for point in points:
            self.points[str(point.id)] = {"vector": point.vector, "payload": point.payload}
        self.upserted.extend(points)

    def scroll(self, collection_name: str, limit: int, offset, with_payload, with_vectors):
        ids = sorted(self.points)
        start = 0 if offset is None else ids.index(str(offset)) + 1
        page = ids[start : start + limit]
        next_offset = page[-1] if start + limit < len(ids) else None
        return [
            SimpleNamespace(id=pid, payload=self.points[pid]["payload"]) for pid in page
        ], next_offset

    def count(self, collection_name: str, exact: bool) -> SimpleNamespace:
        return SimpleNamespace(count=len(self.points))


@pytest.fixture
def fake_qdrant() -> _FakeQdrant:
    return _FakeQdrant()


def test_chunk_point_id_is_deterministic_uuid() -> None:
    first = chunk_point_id("CSB-5589-p0001-0001-table-001-98260d7bbb")
    second = chunk_point_id("CSB-5589-p0001-0001-table-001-98260d7bbb")
    assert first == second
    assert len(first) == 36 and first.count("-") == 4
    assert chunk_point_id("a") != chunk_point_id("b")


def test_embedder_returns_vectors_in_input_order(monkeypatch) -> None:
    vectors = [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]
    monkeypatch.setattr("retrieval.embeddings.OpenAI", lambda **kwargs: _FakeOpenAI(vectors))
    embedder = Embedder(base_url="http://localhost:11434/v1", model="bge-m3")
    result = embedder.embed(["a", "b", "c"])
    assert result == vectors


def test_embedder_requires_configuration(monkeypatch) -> None:
    monkeypatch.setattr("retrieval.embeddings.OpenAI", lambda **kwargs: _FakeOpenAI([]))
    with pytest.raises(ValueError, match="not configured"):
        Embedder(base_url="", model="", config_path=Path("nonexistent-embeddings.yaml"))


def test_ensure_collection_creates_when_absent(fake_qdrant: _FakeQdrant, monkeypatch) -> None:
    monkeypatch.setattr("retrieval.embeddings.QdrantClient", lambda **kwargs: fake_qdrant)
    store = QdrantStore(url="http://localhost:6333", collection="csb_chunks", vector_size=1024)
    store.ensure_collection()
    assert fake_qdrant.created == ["csb_chunks"]
    # Second call is a no-op.
    store.ensure_collection()
    assert fake_qdrant.created == ["csb_chunks"]


def test_upsert_batch_is_idempotent(fake_qdrant: _FakeQdrant, monkeypatch) -> None:
    monkeypatch.setattr("retrieval.embeddings.QdrantClient", lambda **kwargs: fake_qdrant)
    store = QdrantStore(url="http://localhost:6333", collection="csb_chunks")
    point = {"id": "CSB-1-c1", "vector": [0.1, 0.2], "payload": {"chunk_id": "CSB-1-c1"}}
    store.upsert_batch([point])
    store.upsert_batch([point])
    assert store.count() == 1


def test_existing_point_ids_scrolls_all(fake_qdrant: _FakeQdrant, monkeypatch) -> None:
    monkeypatch.setattr("retrieval.embeddings.QdrantClient", lambda **kwargs: fake_qdrant)
    store = QdrantStore(url="http://localhost:6333", collection="csb_chunks")
    store.upsert_batch(
        [
            {"id": f"CSB-{i}-c1", "vector": [0.0, 0.0], "payload": {"chunk_id": f"CSB-{i}-c1"}}
            for i in range(2500)
        ]
    )
    ids = store.existing_point_ids()
    assert len(ids) == 2500
    assert "CSB-0-c1" in ids and "CSB-2499-c1" in ids