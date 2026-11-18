"""Apply human edits from data/golden/query_review.csv to query_seeds.jsonl.

Only query_text and query_status are changed. Source IDs and exclusion bundles are
always retained from the machine-built seed metadata.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "data/golden"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()
    query_path = args.golden_dir / "query_seeds.jsonl"
    review_path = args.golden_dir / "query_review.csv"
    queries = [json.loads(line) for line in query_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {row["query_id"]: row for row in queries}
    if len(by_id) != len(queries):
        raise ValueError("query_seeds.jsonl contains duplicate query_id values")

    with review_path.open("r", encoding="utf-8-sig", newline="") as stream:
        review_rows = list(csv.DictReader(stream))
    review_by_id = {row.get("query_id", "").strip(): row for row in review_rows}
    if len(review_by_id) != len(review_rows):
        raise ValueError("query_review.csv contains duplicate query_id values")
    if set(review_by_id) != set(by_id):
        missing = sorted(set(by_id) - set(review_by_id))
        extra = sorted(set(review_by_id) - set(by_id))
        raise ValueError(f"Query review IDs do not match seeds. Missing={missing}; extra={extra}")

    changed = 0
    for query_id, row in by_id.items():
        review = review_by_id[query_id]
        query_text = (review.get("query_text") or "").strip()
        status = (review.get("query_status") or "draft_for_human_review").strip()
        if not query_text:
            raise ValueError(f"{query_id}: query_text cannot be empty")
        if status not in {"draft_for_human_review", "approved", "needs_revision"}:
            raise ValueError(f"{query_id}: unsupported query_status {status!r}")
        if row["query_text"] != query_text or row["query_status"] != status:
            changed += 1
        row["query_text"] = query_text
        row["query_status"] = status

    temp = query_path.with_suffix(".jsonl.tmp")
    with temp.open("w", encoding="utf-8") as stream:
        for row in queries:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    temp.replace(query_path)
    print(json.dumps({
        "queries": len(queries),
        "records_changed": changed,
        "approved_queries": sum(row["query_status"] == "approved" for row in queries),
        "query_seeds": str(query_path.relative_to(ROOT)),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
