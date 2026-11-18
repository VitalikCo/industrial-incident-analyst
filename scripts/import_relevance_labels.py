"""Convert the filled blinded annotation_pool.csv into relevance_judgments.jsonl.

The import is deliberately strict: a row is either fully labelled with grade, rationale,
evidence and annotator ID, or left blank. Missing grades remain unjudged (not grade 0).
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "data/golden"
DIMENSIONS = [
    "substance_or_hazard", "equipment_or_process", "failure_mechanism",
    "event_sequence", "recommendation_transferability",
]
VALID_GRADES = {"0", "1", "2"}
VALID_DIMENSIONS = {"match", "partial", "no_match", "unknown"}


def import_labels(golden_dir: Path, *, mark_no_relevant_as_abstention: bool = False) -> dict[str, Any]:
    pool_path = golden_dir / "annotation_pool.csv"
    seed_path = golden_dir / "query_seeds.jsonl"
    output_path = golden_dir / "relevance_judgments.jsonl"
    if not pool_path.is_file():
        raise FileNotFoundError(f"Annotation pool not found: {pool_path}. Build it after retrieval runs first.")
    seeds = [json.loads(line) for line in seed_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    query_ids = {row["query_id"] for row in seeds}
    judgments: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    query_rows: dict[str, list[dict[str, Any]]] = {}

    with pool_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row_no, row in enumerate(rows, 2):
        query_id = (row.get("query_id") or "").strip()
        incident_id = (row.get("candidate_incident_id") or "").strip()
        grade_text = (row.get("relevance_grade") or "").strip()
        if not grade_text:
            continue
        if query_id not in query_ids:
            raise ValueError(f"Row {row_no}: unknown query_id {query_id!r}")
        if grade_text not in VALID_GRADES:
            raise ValueError(f"Row {row_no}: relevance_grade must be 0, 1 or 2")
        key = (query_id, incident_id)
        if key in seen:
            raise ValueError(f"Row {row_no}: duplicate query/incident judgment {key}")
        seen.add(key)
        dimensions = {name: (row.get(name) or "").strip() for name in DIMENSIONS}
        for name, value in dimensions.items():
            if value not in VALID_DIMENSIONS:
                raise ValueError(f"Row {row_no}: {name} must be one of {sorted(VALID_DIMENSIONS)}")
        rationale = (row.get("rationale") or "").strip()
        evidence_document_id = (row.get("evidence_document_id") or "").strip()
        evidence_page = (row.get("evidence_page") or "").strip()
        evidence_section = (row.get("evidence_section") or "").strip()
        supporting_quote = (row.get("supporting_quote") or "").strip()
        annotator_id = (row.get("annotator_id") or "").strip()
        if not rationale or not evidence_document_id or not evidence_page or not evidence_section or not supporting_quote or not annotator_id:
            raise ValueError(f"Row {row_no}: rationale, source evidence and annotator_id are required")
        try:
            page_number = int(evidence_page)
        except ValueError as exc:
            raise ValueError(f"Row {row_no}: evidence_page must be an integer") from exc
        judgment = {
            "query_id": query_id,
            "incident_id": incident_id,
            "grade": int(grade_text),
            "dimensions": dimensions,
            "rationale": rationale,
            "evidence": [{
                "document_id": evidence_document_id,
                "page": page_number,
                "section": evidence_section,
                "supporting_quote": supporting_quote,
            }],
            "annotator_id": annotator_id,
            "annotation_status": (row.get("annotation_status") or "final").strip() or "final",
        }
        judgments.append(judgment)
        query_rows.setdefault(query_id, []).append(judgment)

    # Replace the qrels file atomically; unjudged rows do not become grade 0.
    temp = output_path.with_suffix(".jsonl.tmp")
    with temp.open("w", encoding="utf-8") as stream:
        for row in judgments:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    temp.replace(output_path)

    abstention_updates = 0
    if mark_no_relevant_as_abstention:
        # Only mark a query as no-known-relevant when every pooled candidate row for
        # that query was judged and all resulting grades are zero.
        pool_counts: dict[str, int] = {}
        for row in rows:
            qid = (row.get("query_id") or "").strip()
            pool_counts[qid] = pool_counts.get(qid, 0) + 1
        for seed in seeds:
            qid = seed["query_id"]
            judgments_for_query = query_rows.get(qid, [])
            if pool_counts.get(qid, 0) and len(judgments_for_query) == pool_counts[qid] and all(item["grade"] == 0 for item in judgments_for_query):
                seed["expected_abstention"] = True
                seed["abstention_status"] = "human_reviewed_no_relevant_case_in_judged_pool"
                abstention_updates += 1
            elif judgments_for_query and any(item["grade"] > 0 for item in judgments_for_query):
                seed["expected_abstention"] = False
                seed["abstention_status"] = "human_reviewed_relevant_case_exists"
        temp_seed = seed_path.with_suffix(".jsonl.tmp")
        with temp_seed.open("w", encoding="utf-8") as stream:
            for seed in seeds:
                stream.write(json.dumps(seed, ensure_ascii=False) + "\n")
        temp_seed.replace(seed_path)

    summary = {
        "annotation_pool_rows": len(rows),
        "imported_relevance_judgments": len(judgments),
        "queries_with_any_labels": len(query_rows),
        "queries_with_positive_labels": sum(any(item["grade"] > 0 for item in items) for items in query_rows.values()),
        "queries_human_marked_no_relevant": abstention_updates,
        "unjudged_pool_rows": len(rows) - len(judgments),
        "relevance_judgments_path": str(output_path.relative_to(ROOT)) if ROOT in output_path.parents else str(output_path),
    }
    (golden_dir / "annotation_import_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--mark-no-relevant-as-abstention", action="store_true")
    args = parser.parse_args()
    result = import_labels(args.golden_dir, mark_no_relevant_as_abstention=args.mark_no_relevant_as_abstention)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
