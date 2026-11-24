# Embedding generation for the dense retriever

This stage embeds the retrieval-eligible chunks from the chunk store and stores
the vectors in Qdrant so a Dense (and later Hybrid) retriever can query them.

## What gets embedded

Only chunks with `retrieval_eligible: true` are embedded — the **child** `text`
and `table` chunks (11,266 in the current store). The `section_parent` chunks
are whole-section context records and are **not** retrieval units, so they are
not embedded. Each embedded point still carries its `parent_chunk_id` in the
payload, so a parent-document / context-expansion retriever can be added later
without re-embedding.

The parent/child structure is produced by the parser
(`scripts/parse_csb_documents.py`): one `section_parent` per section, with every
child chunk pointing at it via `parent_chunk_id`. The chunk store validator
(`scripts/validate_chunk_store.py`) guarantees every `parent_chunk_id` resolves
to an existing `section_parent`.

## Model and storage

- Embedding model: **`bge-m3`** served by Ollama through its OpenAI-compatible
  `/v1/embeddings` endpoint (1024-dim dense vectors).
- Vector store: **Qdrant** collection `csb_chunks` (Cosine distance, 1024 dims).
- Point ID: a deterministic UUID5 derived from `chunk_id` (Qdrant requires
  integer/UUID point IDs). The original `chunk_id` is stored in the payload.

## Run

The endpoint is resolved in this order: CLI flag > environment variable >
`configs/embeddings.yaml`.

```bash
# Set the Ollama endpoint (Ollama example; any OpenAI-compatible server works)
export OLLAMA_BASE_URL=http://localhost:11434/v1
export OLLAMA_MODEL=bge-m3
export QDRANT_URL=http://localhost:6333

# Smoke test on the first two chunks, then embed the full corpus
uv run python scripts/embed_chunks.py --limit 2 --batch-size 2
uv run python scripts/embed_chunks.py
```

Useful flags:

- `--resume` — skip chunk IDs already present in the collection (append mode).
- `--limit N` — embed only the first N chunks.
- `--batch-size N` — chunks embedded/upserted per batch (default 128).
- `--base-url/--model/--api-key/--qdrant-url/--collection/--vector-size` —
  override the environment variables / config.

## Pause and resume

Each batch is committed atomically (`wait=True`). Interrupting the run (Ctrl+C)
is safe: committed batches are already in Qdrant, and re-running with `--resume`
scrolls the point IDs already present and skips them. Upserts are idempotent by
point ID, so re-running without `--resume` is also safe (it just re-embeds).

## Validation

After a full run the script prints `collection_count`; it should equal the
number of retrieval-eligible chunks (11,266). To inspect the collection:

```bash
uv run python -c "from retrieval.embeddings import QdrantStore; print(QdrantStore().count())"
```

Unit tests: `tests/test_embeddings.py` (embedding order, config resolution,
collection creation, idempotent upsert, resume scroll, point-ID determinism).