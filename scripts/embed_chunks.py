"""Embed the retrieval-eligible chunks and store the vectors in Qdrant.

Embeds every ``retrieval_eligible`` chunk (``text`` and ``table``) from the
chunk store with the configured model (Ollama ``bge-m3`` by default) and upserts
the vectors into a Qdrant collection keyed by ``chunk_id``.

Run from the project root:
    uv run python scripts/embed_chunks.py
    uv run python scripts/embed_chunks.py --resume
    uv run python scripts/embed_chunks.py --limit 1000 --batch-size 64

Pause/resume: each batch is committed atomically (``wait=True``). Interrupting
the run (Ctrl+C) is safe; re-running with ``--resume`` scrolls the point IDs
already present in the collection and skips them.
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from retrieval.embeddings import Embedder, QdrantStore, chunk_point_id
from retrieval.index import ChunkIndex

ROOT = Path(__file__).resolve().parents[1]

# Payload fields preserved for the dense retriever and future context expansion.
_PAYLOAD_FIELDS = (
    "chunk_id",
    "document_id",
    "chunk_type",
    "section_id",
    "section_path",
    "page_start",
    "page_end",
    "parent_chunk_id",
    "incident_ids",
    "candidate_incident_ids",
    "source_path",
    "text",
)


def _payload(chunk: dict) -> dict:
    return {field: chunk.get(field) for field in _PAYLOAD_FIELDS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=None, help="Override OLLAMA_BASE_URL")
    parser.add_argument("--model", default=None, help="Override OLLAMA_MODEL")
    parser.add_argument("--api-key", default=None, help="Override OLLAMA_API_KEY")
    parser.add_argument("--qdrant-url", default=None, help="Override QDRANT_URL")
    parser.add_argument("--collection", default=None, help="Qdrant collection name")
    parser.add_argument("--vector-size", type=int, default=None, help="Embedding dimension")
    parser.add_argument("--batch-size", type=int, default=None, help="Chunks embedded/upserted per batch")
    parser.add_argument("--limit", type=int, default=None, help="Embed only the first N chunks")
    parser.add_argument("--resume", action="store_true", help="Skip chunk IDs already present in Qdrant")
    args = parser.parse_args()

    embedder = Embedder(base_url=args.base_url, model=args.model, api_key=args.api_key)
    store = QdrantStore(
        url=args.qdrant_url,
        collection=args.collection,
        vector_size=args.vector_size or 1024,
    )
    batch_size = args.batch_size or 128

    index = ChunkIndex()
    chunks = index.chunks
    if args.limit is not None:
        chunks = chunks[: args.limit]

    store.ensure_collection()

    existing: set[str] = set()
    if args.resume:
        existing = store.existing_point_ids()
        print(f"Resume: {len(existing)} chunk IDs already present, skipping them.")

    pending = [chunk for chunk in chunks if chunk.get("chunk_id") not in existing]
    print(
        f"Embedding {len(pending)} of {len(chunks)} chunks "
        f"(model={embedder.model}, collection={store.collection}, batch={batch_size})."
    )

    embedded = 0
    skipped = len(chunks) - len(pending)
    started_at = datetime.now(UTC)
    try:
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            texts = [chunk.get("text", "") for chunk in batch]
            vectors = embedder.embed(texts)
            store.upsert_batch(
                [
                    {
                        "id": chunk_point_id(chunk["chunk_id"]),
                        "vector": vector,
                        "payload": _payload(chunk),
                    }
                    for chunk, vector in zip(batch, vectors)
                ]
            )
            embedded += len(batch)
            print(f"  embedded {embedded}/{len(pending)}")
    except KeyboardInterrupt:
        print("\nInterrupted. Committed batches are saved; re-run with --resume to continue.")
        return 130

    elapsed = (datetime.now(UTC) - started_at).total_seconds()
    summary = {
        "model": embedder.model,
        "collection": store.collection,
        "chunks_requested": len(chunks),
        "embedded": embedded,
        "skipped_existing": skipped,
        "collection_count": store.count(),
        "elapsed_seconds": round(elapsed, 2),
        "finished_at_utc": datetime.now(UTC).isoformat(),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())