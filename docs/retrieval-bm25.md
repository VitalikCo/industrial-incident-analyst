# BM25 retrieval and LLM answer pipeline

This stage adds the first retrieval strategy (BM25) and an LLM answer pipeline for the
55 query seeds in `data/golden/query_seeds.jsonl`. The design is extensible: a `Retriever`
base interface (`scripts/retrieval/base.py`) lets dense and hybrid strategies be added later
without changing the entry-point scripts.

## Components

| File | Purpose |
|---|---|
| `scripts/retrieval/index.py` | `ChunkIndex` — loads retrieval-eligible chunks and the incident/recommendation catalogs, maps chunks to incidents |
| `scripts/retrieval/bm25.py` | `BM25Retriever` — BM25Okapi over chunk text, incident-level aggregation, source-case exclusion before ranking |
| `scripts/retrieval/recommendations.py` | `RecommendationRanker` — ranks an incident's linked recommendations by BM25 similarity to the query |
| `scripts/retrieval/llm.py` | `LLMClient` — OpenAI-compatible chat completions, robust JSON extraction + retries |
| `scripts/retrieval/pipeline.py` | `AnswerPipeline` — retrieve → rank recommendations → grounded prompt → LLM → validated answer |
| `scripts/run_retrieval.py` | Exports incident-level BM25 results for `build_relevance_pool.py` |
| `scripts/run_answers.py` | Runs the full pipeline and writes the answers file |
| `configs/llm.yaml` | LLM defaults and environment-variable names |

## 1. Run BM25 retrieval

```bash
uv run python scripts/run_retrieval.py
```

Writes `runs/retrieval/retrieval_results.jsonl` (one record per query and retriever):

```json
{"query_id": "Q001", "retriever": "bm25",
 "results": [{"incident_id": "...", "rank": 1, "score": 12.4}]}
```

This is the exact format consumed by `scripts/build_relevance_pool.py`. Source-case
exclusion is applied before ranking, so the pool builder's defensive check finds nothing
to remove.

## 2. Run the LLM answer pipeline

The LLM endpoint is resolved in this order: CLI flag > environment variable > `configs/llm.yaml`.

```bash
# Set the endpoint (Ollama example; any OpenAI-compatible server works)
export LLM_BASE_URL=http://localhost:11434/v1
export LLM_MODEL=llama3.1:8b
export LLM_API_KEY=not-needed

# Smoke test on the first two queries, then run all 55
uv run python scripts/run_answers.py --limit 2
uv run python scripts/run_answers.py
```

Writes `runs/answers/bm25_answers.jsonl` (one record per query):

```json
{"query_id": "Q001",
 "similar_incidents": [
   {"incident": "...", "match_reason": "...",
    "related_recommendations": [{"id": "...", "topic": "..."}],
    "evidence_sources": ["..."]}
 ],
 "limitations": ["Recommendation statuses are as of the CSB snapshot 2026-09-16."],
 "abstained": false}
```

Useful flags:

- `--resume` — skip query IDs already present in the output file (append mode).
- `--query-ids Q001 Q002` — process only specific queries.
- `--base-url/--model/--api-key` — override the environment variables.
- `--top-k`, `--chunk-cutoff`, `--recs-per-incident` — tune retrieval and prompt size.

Per-query failures are recorded in `runs/answers/bm25_answers.failures.jsonl` and the run
continues; the script exits non-zero if any query failed.

## Notes

- Only `query_text` is sent to the retriever and the LLM; the hidden `source_case` metadata
  is used solely for exclusion.
- The answer schema matches the illustrative example: `similar_incidents` (up to 10),
  `limitations`, and `abstained`. The dated recommendation-snapshot limitation is added
  automatically whenever recommendations are cited.
- Qwen3 `thinking` models can spend the entire output-token budget on reasoning and return
  an empty `content`. The client disables thinking via `chat_template_kwargs` unless
  `enable_thinking: true` is set in `configs/llm.yaml`.
- `runs/` is gitignored; the answers and retrieval results are derived artifacts.