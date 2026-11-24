"""Embedding generation and Qdrant storage for the dense retriever.

The embedding model is served by Ollama through its OpenAI-compatible
``/v1/embeddings`` endpoint (default model ``bge-m3``, 1024-dim dense vectors).
Vectors are stored in a Qdrant collection keyed by ``chunk_id`` so that upserts
are idempotent and a paused run can be resumed by skipping points that already
exist.

Configuration resolution order (constructor argument > environment variable >
``configs/embeddings.yaml``):

- ``OLLAMA_BASE_URL`` / ``LLM_BASE_URL`` - Ollama base URL
- ``OLLAMA_API_KEY`` - bearer token (usually not needed for local Ollama)
- ``QDRANT_URL`` - Qdrant HTTP endpoint
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import yaml
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs/embeddings.yaml"

# Namespace for deriving deterministic Qdrant point IDs from chunk IDs.
_POINT_ID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def chunk_point_id(chunk_id: str) -> str:
    """Return a deterministic UUID5 point ID for a chunk ID.

    Qdrant point IDs must be unsigned integers or UUIDs, so the string
    ``chunk_id`` is hashed into a stable UUID. The original ``chunk_id`` is
    always stored in the point payload for lookups and resume.
    """
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, chunk_id))


class Embedder:
    """Embed text batches through an OpenAI-compatible embeddings endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        config_path: Path = DEFAULT_CONFIG,
    ) -> None:
        config = self._load_config(config_path)
        self.base_url = (
            base_url
            or os.environ.get("OLLAMA_BASE_URL")
            or os.environ.get("LLM_BASE_URL")
            or config.get("base_url")
            or ""
        )
        self.model = model or os.environ.get("OLLAMA_MODEL") or config.get("model") or ""
        self.api_key = api_key or os.environ.get("OLLAMA_API_KEY") or config.get("api_key") or "not-needed"
        if not self.base_url or not self.model:
            raise ValueError(
                "Embedding endpoint is not configured. Set the OLLAMA_BASE_URL and "
                "OLLAMA_MODEL environment variables (or pass --base-url/--model to "
                "scripts/embed_chunks.py)."
            )
        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)

    @staticmethod
    def _load_config(config_path: Path) -> dict[str, Any]:
        if not Path(config_path).is_file():
            return {}
        with Path(config_path).open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream) or {}
        return (data.get("embeddings") or {}) if isinstance(data, dict) else {}

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one dense vector per input text, in the same order."""
        if not texts:
            return []
        response = self._client.embeddings.create(model=self.model, input=texts)
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]


class QdrantStore:
    """Thin wrapper around a Qdrant collection of chunk vectors."""

    def __init__(
        self,
        url: str | None = None,
        collection: str | None = None,
        vector_size: int = 1024,
        distance: str = "Cosine",
        config_path: Path = DEFAULT_CONFIG,
    ) -> None:
        config = self._load_config(config_path)
        self.url = url or os.environ.get("QDRANT_URL") or config.get("qdrant_url") or "http://localhost:6333"
        self.collection = collection or config.get("collection") or "csb_chunks"
        self.vector_size = int(vector_size or config.get("vector_size", 1024))
        self.distance = distance or config.get("distance", "Cosine")
        self._client = QdrantClient(url=self.url)

    @staticmethod
    def _load_config(config_path: Path) -> dict[str, Any]:
        if not Path(config_path).is_file():
            return {}
        with Path(config_path).open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream) or {}
        return (data.get("embeddings") or {}) if isinstance(data, dict) else {}

    def ensure_collection(self) -> None:
        """Create the collection if it does not exist yet."""
        if self._client.collection_exists(self.collection):
            return
        self._client.create_collection(
            collection_name=self.collection,
            vectors_config=qdrant_models.VectorParams(
                size=self.vector_size,
                distance=qdrant_models.Distance.COSINE,
            ),
        )

    def upsert_batch(self, points: list[dict[str, Any]]) -> None:
        """Upsert a batch of ``{id, vector, payload}`` points (idempotent)."""
        if not points:
            return
        self._client.upsert(
            collection_name=self.collection,
            points=[
                qdrant_models.PointStruct(
                    id=point["id"],
                    vector=point["vector"],
                    payload=point["payload"],
                )
                for point in points
            ],
            wait=True,
        )

    def existing_point_ids(self) -> set[str]:
        """Return the set of chunk IDs already stored in the collection.

        Point IDs are UUIDs derived from chunk IDs, so the original ``chunk_id``
        is read back from each point's payload for resume comparisons.
        """
        ids: set[str] = set()
        next_offset: Any = None
        while True:
            response = self._client.scroll(
                collection_name=self.collection,
                limit=1000,
                offset=next_offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in response[0]:
                chunk_id = (point.payload or {}).get("chunk_id")
                if chunk_id:
                    ids.add(str(chunk_id))
            next_offset = response[1]
            if next_offset is None:
                break
        return ids

    def count(self) -> int:
        """Return the number of points in the collection."""
        return self._client.count(collection_name=self.collection, exact=True).count

    def search(self, vector: list[float], limit: int = 100) -> list[dict[str, Any]]:
        """Return the top *limit* points by cosine similarity.

        Each result carries the original ``chunk_id`` (from the payload) and
        the similarity score, so the dense retriever can map points back to
        ``ChunkIndex.chunks``.
        """
        response = self._client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [
            {
                "chunk_id": (point.payload or {}).get("chunk_id"),
                "score": float(point.score),
            }
            for point in response.points
        ]