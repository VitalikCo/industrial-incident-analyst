# Dense retrieval and LLM answer pipeline

This stage adds the dense (vector-similarity) retrieval strategy on top of the
chunk embeddings stored in Qdrant (see [embeddings.md](embeddings.md)). It
reuses the same `Retriever` interface, incident-level aggregation, source-case
exclusion and LLM answer pipeline as BM25, so the two strategies are directly
comparable.

## Components

| File | Purpose |
|---|---|
| `scripts/retrieval/dense.py` | `DenseRetriever` — embeds the query, queries Qdrant by cosine similarity, aggregates chunk scores per incident |
| `scripts/retrieval/embeddings.py` | `QdrantStore.search` — top-k vector search returning `chunk_id` + score |
| `scripts/retrieval/base.py` | `BaseRetriever` — shared incident aggregation, source-case exclusion and evidence building (used by BM25 and dense) |
| `scripts/run_retrieval.py` | Exports incident-level results for `build_relevance_pool.py` (`--retriever dense`) |
| `scripts/run_answers.py` | Runs the full pipeline and writes the answers file (`--retriever dense`) |

## 1. Run dense retrieval

```bash
uv run python scripts/run_retrieval.py --retriever dense
```

Writes `runs/retrieval/retrieval_results.jsonl` (one record per query and
retriever). The file is **merged per retriever**: records for the current
retriever are replaced, records for other retrievers are preserved, so running
`--retriever bm25` and then `--retriever dense` leaves both strategies in the
file for `build_relevance_pool.py`.

```json
{"query_id": "Q001", "retriever": "dense",
 "results": [{"incident_id": "...", "rank": 1, "score": 0.87}]}
```

Dense scores are cosine similarities (higher = more similar). Source-case
exclusion is applied before ranking, so the pool builder's defensive check
finds nothing to remove.

## 2. Run the LLM answer pipeline

The LLM endpoint is resolved in this order: CLI flag > environment variable > `configs/llm.yaml`.

```bash
# Set the endpoint (Ollama example; any OpenAI-compatible server works)
export LLM_BASE_URL=http://localhost:11434/v1
export LLM_MODEL=llama3.1:8b
export LLM_API_KEY=not-needed

# Smoke test on the first two queries, then run all 55
uv run python scripts/run_answers.py --retriever dense --limit 2
uv run python scripts/run_answers.py --retriever dense
```

Writes `runs/answers/dense_answers.jsonl` (one record per query), in the same
schema as the BM25 answers. The prompt labels the candidates as ranked by
"dense vector similarity" so the LLM knows the retrieval basis.

Useful flags (same as BM25):

- `--resume` — skip query IDs already present in the output file.
- `--query-ids Q001 Q002` — process only specific queries.
- `--base-url/--model/--api-key` — override the environment variables.
- `--top-k`, `--chunk-cutoff`, `--recs-per-incident` — tune retrieval and prompt size.

Per-query failures are recorded in `runs/answers/dense_answers.failures.jsonl`
and the run continues; the script exits non-zero if any query failed.

## Notes

- The query is embedded with the same model used to embed the corpus
  (`bge-m3` via Ollama), so query and chunk vectors live in the same space.
- `--chunk-cutoff` (default 600) is the number of chunks fetched from Qdrant
  per query before incident aggregation; raise it if a query surfaces fewer
  than `top_k` distinct incidents.
- `runs/` is gitignored; the answers and retrieval results are derived artifacts.
