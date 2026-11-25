Local reranker service
----------------------
Endpoint : POST http://localhost:7997/rerank   (Cohere-compatible, JSON)
Auth     : none (local only)
Model    : BAAI/bge-reranker-v2-m3 (multilingual cross-encoder)

Request  : {
  "model":    "BAAI/bge-reranker-v2-m3",
  "query":    "<user query>",
  "documents": ["<chunk text 1>", "<chunk text 2>", ...],
  "top_n":    5                     // optional, default = return all
}

Response : {
  "results": [ {"index": 0, "relevance_score": 0.97}, ... ]  // sorted, best first
}

Semantics:
- "index" refers to the position in the ORIGINAL documents array — use it
  to map scores back to your chunk objects (id, source, text, metadata).
- "relevance_score" is sigmoid-squashed, roughly 0..1, and is
  query-relative: meaningful for ranking WITHIN one request, not
  comparable across different queries.

Client
------
`scripts/retrieval/reranker.py` provides `RerankerClient`, used by the hybrid
retriever (`scripts/retrieval/hybrid.py`). Configuration resolution order:
constructor argument > environment variable > `configs/reranker.yaml`.

- `RERANKER_BASE_URL` - base URL of the reranker service (default
  `http://localhost:7997`).
- `RERANKER_MODEL` - model name (default `BAAI/bge-reranker-v2-m3`).

```python
from retrieval.reranker import RerankerClient

client = RerankerClient()
results = client.rerank("query text", ["doc 1", "doc 2", ...])
# results == [{"index": 1, "relevance_score": 0.97}, {"index": 0, ...}]
```

`rerank(query, documents, top_n=None)` posts ONE request per query and returns
`[{"index": ..., "relevance_score": ...}, ...]` sorted by descending score.
It retries transient failures with exponential backoff (`max_retries`, default
3) and raises `RuntimeError` if the service stays unavailable.

Performance notes:
- The first request after the service starts can take minutes while the model
  loads (cold start). The default `timeout_seconds` is 600 to absorb this.
- The hybrid retriever sends one
  document per unique contributing chunk (up to 3 per incident, ~60-100 per
  query).
