# Hybrid retrieval (BM25 + dense RRF) with cross-encoder reranking

This stage adds the hybrid retrieval strategy on top of the BM25 and dense
retrievers. It fuses the two chunk-level rankings with Reciprocal Rank Fusion
(RRF), aggregates to incident level with the same shared logic as BM25/dense,
and then re-ranks the top candidate incidents with the local cross-encoder
reranker (see [reranker.md](reranker.md)). The output is incident-level top-10
in the exact same hit schema as BM25/dense, so `run_retrieval.py`,
`run_answers.py` and `build_relevance_pool.py` need no downstream changes.

## Components

| File | Purpose |
|---|---|
| `scripts/retrieval/hybrid.py` | `HybridRetriever` — RRF fusion of BM25 + dense chunk rankings, incident aggregation, cross-encoder reranking |
| `scripts/retrieval/reranker.py` | `RerankerClient` — POSTs one request per query to the local `/rerank` endpoint |
| `scripts/retrieval/base.py` | `BaseRetriever._aggregate_incidents` / `_build_hit` — shared aggregation, exclusion and evidence building (used by BM25, dense and hybrid) |
| `configs/reranker.yaml` | Reranker endpoint/model/timeout defaults |
| `scripts/run_retrieval.py` | Exports incident-level results (`--retriever hybrid`) |
| `scripts/run_answers.py` | Runs the full pipeline (`--retriever hybrid`) |

## Pipeline per query

1. **Chunk-level RRF fusion.** `HybridRetriever.score_chunks` scores chunks
   with BM25 (`BM25Retriever.score_chunks`) and dense
   (`DenseRetriever.score_chunks`, Qdrant cosine similarity) and fuses the two
   rankings with `rrf_fuse`: each chunk gets `1 / (60 + rank)` per ranking,
   summed across rankings.
2. **Aggregate and exclude.** `BaseRetriever._aggregate_incidents` sums the
   fused chunk scores per incident (union of `incident_ids` and
   `candidate_incident_ids`), drops incidents in the query's
   `source_case_exclusion` bundle **before** reranking, and keeps the full
   contributing chunks per incident.
3. **Candidate selection.** The top `top_k * 2` incidents by hybrid score are
   the reranker candidates (20 when `top_k` is 10).
4. **Rerank.** `HybridRetriever._rerank` builds one reranker document per
   unique contributing chunk:
   `"<incident title> | <section_path joined with ' > '> | <chunk text>"`,
   using the FULL chunk text (not the 700-char evidence truncation). Chunk IDs
   are deduplicated across incidents (a chunk can map to several incidents in
   multi-event investigations), and each chunk's score is attributed to every
   incident it supports. The documents are sent to the reranker in ONE request.
5. **Aggregate rerank scores per incident.** Chunk scores are combined with
   **max-of-top-k** (robust to the sigmoid scale; incidents with more chunks
   are not inflated). Incidents without any reranked chunk keep their hybrid
   score as a fallback.
6. **Return top-10.** The top `top_k` incidents are returned in the same hit
   schema as `_build_hits`: `{incident_id, title, score, rank, evidence_chunks}`.

## Run

```bash
# Reranked hybrid (default): RRF top-20 -> cross-encoder -> top-10
uv run python scripts/run_retrieval.py --retriever hybrid

# RRF-only hybrid (no reranker; useful for ablations and smoke tests)
uv run python scripts/run_retrieval.py --retriever hybrid --no-rerank

# Smoke test on the first query
uv run python scripts/run_retrieval.py --retriever hybrid --limit 1
```

Writes `runs/retrieval/retrieval_results.jsonl` (one record per query and
retriever). The file is merged per retriever, so `bm25`, `dense` and `hybrid`
records coexist for `build_relevance_pool.py`:

```json
{"query_id": "Q001", "retriever": "hybrid",
 "results": [{"incident_id": "...", "rank": 1, "score": 0.91}]}
```

Hybrid scores are the reranker's sigmoid `relevance_score` (roughly 0..1,
query-relative). With `--no-rerank` they are RRF scores (small positive
values). Source-case exclusion is applied before reranking, so the pool
builder's defensive check finds nothing to remove.

## LLM answer pipeline

```bash
uv run python scripts/run_answers.py --retriever hybrid --limit 2
uv run python scripts/run_answers.py --retriever hybrid
```

Writes `runs/answers/hybrid_answers.jsonl` in the same schema as the BM25 and
dense answers. The prompt labels the candidates as ranked by "hybrid BM25 +
dense (reranked)".

## Notes

- Only `query_text` is sent to the retrievers and the reranker; the hidden
  `source_case` metadata is used solely for exclusion.
- The reranker is a local service; the first request after it starts can take
  minutes (model cold start). `configs/reranker.yaml` sets
  `timeout_seconds: 600` to absorb this. Steady state is roughly 1-2 seconds
  per document, so a full 55-query run takes a while.
- `--no-rerank` is the one-factor ablation for reranking referenced in
  [teacher-feedback-implementation.md](teacher-feedback-implementation.md).
- `runs/` is gitignored; the answers and retrieval results are derived
  artifacts.
