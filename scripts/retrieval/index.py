"""Chunk index: load the chunk store and the incident/recommendation catalogs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHUNKS = ROOT / "data/processed/chunk_store/chunks.jsonl"
DEFAULT_INCIDENT_CATALOG = ROOT / "data/processed/incident_catalog.jsonl"
DEFAULT_RECOMMENDATION_CATALOG = ROOT / "data/processed/recommendation_catalog.jsonl"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file into a list of dicts."""
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_no}: {exc}") from exc
    return rows


class ChunkIndex:
    """In-memory view over the retrieval-eligible chunk store and catalogs."""

    def __init__(
        self,
        chunks_path: Path = DEFAULT_CHUNKS,
        incident_catalog_path: Path = DEFAULT_INCIDENT_CATALOG,
        recommendation_catalog_path: Path = DEFAULT_RECOMMENDATION_CATALOG,
    ) -> None:
        self.chunks_path = Path(chunks_path)
        self.incident_catalog_path = Path(incident_catalog_path)
        self.recommendation_catalog_path = Path(recommendation_catalog_path)

        all_chunks = read_jsonl(self.chunks_path)
        self.chunks = [chunk for chunk in all_chunks if chunk.get("retrieval_eligible")]
        self.incidents = read_jsonl(self.incident_catalog_path)
        self.recommendations = read_jsonl(self.recommendation_catalog_path)

        self.incident_by_id = {row["incident_id"]: row for row in self.incidents}
        self.recommendation_by_id = {row["recommendation_id"]: row for row in self.recommendations}

        # Map each chunk to the incident IDs it supports (explicit first, then candidate).
        self.chunk_incident_ids: list[list[str]] = []
        for chunk in self.chunks:
            ids = list(dict.fromkeys(chunk.get("incident_ids", []) + chunk.get("candidate_incident_ids", [])))
            self.chunk_incident_ids.append([iid for iid in ids if iid in self.incident_by_id])

    def incident_title(self, incident_id: str) -> str:
        incident = self.incident_by_id.get(incident_id)
        if not incident:
            return incident_id
        titles = incident.get("titles") or []
        return " / ".join(titles) if titles else incident_id
