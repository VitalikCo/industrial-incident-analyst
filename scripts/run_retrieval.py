"""Run a retrieval strategy over the golden query seeds and export incident-level results.

Output format (one JSON object per line) is consumed by scripts/build_relevance_pool.py:

    {"query_id": "Q001", "retriever": "bm25",
     "results": [{"incident_id": "...", "rank": 1, "score": 12.4}]}

The output file is merged per retriever: records for the current retriever are
replaced, records for other retrievers are preserved, so the file can hold the
union of all strategies (bm25, dense, hybrid) for build_relevance_pool.py.

Run from the project root:
    uv run python scripts/run_retrieval.py
    uv run python scripts/run_retrieval.py --retriever dense
    uv run python scripts/run_retrieval.py --top-k 10 --chunk-cutoff 600
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from retrieval.bm25 import BM25Retriever
from retrieval.dense import DenseRetriever
from retrieval.index import ChunkIndex, read_jsonl

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUERIES = ROOT / "data/golden/query_seeds.jsonl"
DEFAULT_OUTPUT = ROOT / "runs/retrieval/retrieval_results.jsonl"


def _build_retriever(name: str, index: ChunkIndex):
    """Instantiate the retrieval strategy by name."""
    if name == "dense":
        return DenseRetriever(index)
    return BM25Retriever(index)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES, help="Path to query_seeds.jsonl")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output JSONL path")
    parser.add_argument("--retriever", choices=["bm25", "dense"], default="bm25", help="Retrieval strategy")
    parser.add_argument("--top-k", type=int, default=10, help="Incidents returned per query")
    parser.add_argument("--chunk-cutoff", type=int, default=600, help="Chunks scored per query before aggregation")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N queries")
    args = parser.parse_args()

    queries = read_jsonl(args.queries)
    if args.limit is not None:
        queries = queries[: args.limit]

    index = ChunkIndex()
    retriever = _build_retriever(args.retriever, index)

    records = []
    for query in queries:
        hits = retriever.retrieve(
            query["query_text"],
            top_k=args.top_k,
            exclusion=query.get("source_case_exclusion"),
            chunk_cutoff=args.chunk_cutoff,
        )
        records.append(
            {
                "query_id": query["query_id"],
                "retriever": args.retriever,
                "results": [
                    {"incident_id": hit["incident_id"], "rank": hit["rank"], "score": hit["score"]}
                    for hit in hits
                ],
            }
        )

    # Merge per retriever: keep other retrievers' records, replace this one's.
    existing: list[dict] = []
    if args.output.is_file():
        existing = [row for row in read_jsonl(args.output) if row.get("retriever") != args.retriever]
    merged = existing + records

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for record in merged:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = {
        "queries": len(records),
        "retriever": args.retriever,
        "top_k": args.top_k,
        "output": str(args.output),
        "queries_with_full_results": sum(1 for record in records if len(record["results"]) == args.top_k),
        "queries_with_any_results": sum(1 for record in records if record["results"]),
        "min_results": min((len(record["results"]) for record in records), default=0),
        "max_results": max((len(record["results"]) for record in records), default=0),
        "records_in_file": len(merged),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
