"""Client for the local Cohere-compatible reranker service.

The reranker is a cross-encoder (``BAAI/bge-reranker-v2-m3``) served locally.
It re-scores candidate documents against a query and returns a sigmoid-squashed
``relevance_score`` in roughly ``0..1``. Scores are query-relative: they are
meaningful for ranking within one request, not comparable across queries.

Configuration resolution order (constructor argument > environment variable >
``configs/reranker.yaml``):

- ``RERANKER_BASE_URL`` - base URL of the reranker service
- ``RERANKER_MODEL`` - model name
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs/reranker.yaml"


class RerankerClient:
    """Thin wrapper around the local ``/rerank`` endpoint.

    ``rerank`` posts one request per query with the full documents array and
    returns ``[{"index": ..., "relevance_score": ...}, ...]`` sorted by
    descending score. ``index`` refers to the position in the original
    documents array, so callers can map scores back to their chunk objects.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        config_path: Path = DEFAULT_CONFIG,
    ) -> None:
        config = self._load_config(config_path)
        self.base_url = (
            base_url
            or os.environ.get("RERANKER_BASE_URL")
            or config.get("base_url")
            or "http://localhost:7997"
        ).rstrip("/")
        self.model = (
            model
            or os.environ.get("RERANKER_MODEL")
            or config.get("model")
            or "BAAI/bge-reranker-v2-m3"
        )
        self.timeout_seconds = float(timeout_seconds or config.get("timeout_seconds", 600))
        self.max_retries = int(max_retries or config.get("max_retries", 3))

    @staticmethod
    def _load_config(config_path: Path) -> dict[str, Any]:
        if not Path(config_path).is_file():
            return {}
        with Path(config_path).open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream) or {}
        return (data.get("reranker") or {}) if isinstance(data, dict) else {}

    def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return ``[{"index": ..., "relevance_score": ...}, ...]`` sorted by score.

        *index* is the position in *documents*. When *top_n* is given only the
        top *top_n* results are returned; by default the service scores every
        document.
        """
        if not documents:
            return []
        payload: dict[str, Any] = {"model": self.model, "query": query, "documents": documents}
        if top_n is not None:
            payload["top_n"] = top_n
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    f"{self.base_url}/rerank",
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                data = response.json()
                results = data.get("results", [])
                return sorted(
                    results,
                    key=lambda item: float(item.get("relevance_score", 0.0)),
                    reverse=True,
                )
            except Exception as exc:  # noqa: BLE001 - retry any transient failure
                last_error = exc
                if attempt < self.max_retries - 1:
                    time.sleep(2**attempt)
        raise RuntimeError(
            f"Reranker request failed after {self.max_retries} attempts: {last_error}"
        ) from last_error
