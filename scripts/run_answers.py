"""Run the full retrieval + LLM answer pipeline over the golden query seeds.

Writes one JSON object per query to runs/answers/bm25_answers.jsonl:

    {"query_id": "Q001", "similar_incidents": [...], "limitations": [...], "abstained": false}

The LLM endpoint is resolved from CLI flags, then environment variables
(LLM_BASE_URL, LLM_MODEL, LLM_API_KEY), then configs/llm.yaml.

Run from the project root:
    uv run python scripts/run_answers.py
    uv run python scripts/run_answers.py --limit 2
    uv run python scripts/run_answers.py --resume
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from retrieval.bm25 import BM25Retriever
from retrieval.index import ChunkIndex, read_jsonl
from retrieval.llm import LLMClient
from retrieval.pipeline import AnswerPipeline
from retrieval.recommendations import RecommendationRanker

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUERIES = ROOT / "data/golden/query_seeds.jsonl"
DEFAULT_OUTPUT = ROOT / "runs/answers/bm25_answers.jsonl"
DEFAULT_FAILURES = ROOT / "runs/answers/bm25_answers.failures.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES, help="Path to query_seeds.jsonl")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Answers JSONL path")
    parser.add_argument("--failures", type=Path, default=DEFAULT_FAILURES, help="Failures JSONL path")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N queries")
    parser.add_argument("--query-ids", nargs="*", default=None, help="Process only these query IDs")
    parser.add_argument("--top-k", type=int, default=10, help="Incidents returned per query")
    parser.add_argument("--chunk-cutoff", type=int, default=600, help="Chunks scored per query before aggregation")
    parser.add_argument("--recs-per-incident", type=int, default=5, help="Candidate recommendations per incident")
    parser.add_argument("--base-url", default=None, help="Override LLM_BASE_URL")
    parser.add_argument("--model", default=None, help="Override LLM_MODEL")
    parser.add_argument("--api-key", default=None, help="Override LLM_API_KEY")
    parser.add_argument("--resume", action="store_true", help="Skip query IDs already present in the output file")
    args = parser.parse_args()

    queries = read_jsonl(args.queries)
    if args.query_ids:
        wanted = set(args.query_ids)
        queries = [query for query in queries if query["query_id"] in wanted]
    if args.limit is not None:
        queries = queries[: args.limit]

    index = ChunkIndex()
    retriever = BM25Retriever(index)
    ranker = RecommendationRanker(index)
    llm = LLMClient(base_url=args.base_url, model=args.model, api_key=args.api_key)
    pipeline = AnswerPipeline(index=index, retriever=retriever, ranker=ranker, llm=llm)

    existing_ids: set[str] = set()
    if args.resume and args.output.is_file():
        for line in args.output.read_text(encoding="utf-8").splitlines():
            if line.strip():
                existing_ids.add(json.loads(line)["query_id"])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.resume else "w"
    succeeded = 0
    failed = 0
    abstained = 0
    with args.output.open(mode, encoding="utf-8") as out_stream, args.failures.open(
        mode, encoding="utf-8"
    ) as fail_stream:
        for query in queries:
            query_id = query["query_id"]
            if query_id in existing_ids:
                print(f"{query_id}: skipped (already answered)")
                continue
            try:
                answer = pipeline.answer_query(
                    query,
                    top_k=args.top_k,
                    chunk_cutoff=args.chunk_cutoff,
                    recs_per_incident=args.recs_per_incident,
                )
            except Exception as exc:  # noqa: BLE001 - record the failure and continue
                failed += 1
                fail_stream.write(
                    json.dumps(
                        {
                            "query_id": query_id,
                            "error": str(exc),
                            "failed_at_utc": datetime.now(timezone.utc).isoformat(),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                fail_stream.flush()
                print(f"{query_id}: FAILED - {exc}")
                continue
            out_stream.write(json.dumps(answer, ensure_ascii=False) + "\n")
            out_stream.flush()
            succeeded += 1
            abstained += int(answer["abstained"])
            print(f"{query_id}: {len(answer['similar_incidents'])} incidents, abstained={answer['abstained']}")

    summary = {
        "queries_requested": len(queries),
        "succeeded": succeeded,
        "failed": failed,
        "abstained": abstained,
        "output": str(args.output),
        "failures": str(args.failures),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
