"""Compile approved query seeds, human qrels and historical reference answers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.validate_golden_set import validate

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data/golden"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden-dir", type=Path, default=GOLDEN)
    args = parser.parse_args()
    summary = validate(args.golden_dir, min_queries=30, require_approved_queries=True, require_judgments=True)
    if not summary["ready_for_evaluation"]:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 1

    seeds = read_jsonl(args.golden_dir / "query_seeds.jsonl")
    judgments = read_jsonl(args.golden_dir / "relevance_judgments.jsonl")
    answers = read_jsonl(args.golden_dir / "reference_answers.jsonl")
    judgments_by_query: dict[str, list[dict[str, Any]]] = {}
    for row in judgments:
        judgments_by_query.setdefault(row["query_id"], []).append(row)
    answers_by_query = {row["query_id"]: row for row in answers}
    final_rows = []
    for seed in seeds:
        final_rows.append({
            **seed,
            "relevance_judgments": judgments_by_query.get(seed["query_id"], []),
            "reference_answer": answers_by_query.get(seed["query_id"], {"historical_incidents": []}),
            "evaluation_status": "approved_human_labeled",
        })
    output = args.golden_dir / "golden_set.jsonl"
    temp = output.with_suffix(".jsonl.tmp")
    with temp.open("w", encoding="utf-8") as stream:
        for row in final_rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    temp.replace(output)
    print(json.dumps({"golden_set_records": len(final_rows), "output": str(output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
