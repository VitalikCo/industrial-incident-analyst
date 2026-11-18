"""Append 25 new source-grounded query seeds to an existing approved 30-query set.

Run from the project root after copying this patch into the repository:
    uv run python scripts/extend_golden_set.py

The script preserves all existing query records and their approved status. The 25 new
queries are added as draft_for_human_review and must be reviewed/approved separately.
It uses only existing catalog and Chunk Store JSONL files; raw PDFs are not needed.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data/golden"
DATA = ROOT / "data/processed"
DEFINITIONS = GOLDEN / "additional_query_seed_definitions.jsonl"
SEEDS = GOLDEN / "query_seeds.jsonl"
REVIEW = GOLDEN / "query_review.csv"

REVIEW_FIELDS = [
    "query_id", "query_text", "source_document_id", "source_document_title",
    "source_page", "source_section", "source_incident_id", "source_investigation_id",
    "source_mapping_status", "excluded_document_ids", "excluded_recommendation_ids",
    "query_status",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    temp.replace(path)


def short_quote(text: str, max_words: int = 24) -> str:
    return " ".join(re.sub(r"\s+", " ", text or "").strip().split()[:max_words])


def extend_golden_set(
    *, golden_dir: Path = GOLDEN, data_dir: Path = DATA,
    definitions_path: Path | None = None,
) -> dict[str, Any]:
    definitions_path = definitions_path or (golden_dir / DEFINITIONS.name)
    seeds_path = golden_dir / SEEDS.name
    review_path = golden_dir / REVIEW.name
    if not seeds_path.is_file() or not review_path.is_file():
        raise FileNotFoundError(
            "The existing approved Golden Set was not found. Expected both "
            f"{seeds_path} and {review_path}. This is an additive patch and must be applied "
            "to the local project that already contains the approved 30 queries."
        )

    existing = read_jsonl(seeds_path)
    definitions = read_jsonl(definitions_path)
    incidents = read_jsonl(data_dir / "incident_catalog.jsonl")
    documents = read_jsonl(data_dir / "chunk_store/documents.jsonl")
    pages = read_jsonl(data_dir / "chunk_store/pages.jsonl")

    existing_by_id = {row.get("query_id"): row for row in existing}
    if len(existing_by_id) != len(existing):
        raise ValueError("Existing query_seeds.jsonl contains duplicate query_id values")
    if len(existing) < 30:
        raise ValueError(f"Expected at least 30 existing query seeds, found {len(existing)}")
    unapproved_existing = [row.get("query_id") for row in existing if row.get("query_status") != "approved"]
    if unapproved_existing:
        raise ValueError(
            "The existing 30 queries were expected to be approved before expansion; "
            f"not approved: {unapproved_existing[:10]}"
        )

    incident_by_doc: dict[str, list[dict[str, Any]]] = {}
    for incident in incidents:
        # Primary reports are preferred, but a CSB case study can be the best available
        # incident narrative. Any source document is acceptable only when it maps to one
        # unambiguous incident and is included in that incident's exclusion bundle.
        for document_id in incident.get("document_ids", []):
            incident_by_doc.setdefault(document_id, []).append(incident)
    document_by_id = {row["document_id"]: row for row in documents}
    page_by_key = {(row["document_id"], row["page"]): row for row in pages}

    existing_source_investigations: set[str] = set()
    for row in existing:
        source = row.get("source_case", {})
        exclusion = row.get("source_case_exclusion", {})
        investigation_ids = exclusion.get("investigation_ids") or [source.get("source_investigation_id")]
        if not investigation_ids or any(not value for value in investigation_ids):
            raise ValueError(f"{row.get('query_id')}: missing source investigation exclusion metadata")
        existing_source_investigations.update(investigation_ids)

    new_records: list[dict[str, Any]] = []
    new_review_rows: list[dict[str, Any]] = []
    new_source_investigations: set[str] = set()
    for definition in definitions:
        qid = definition.get("query_id")
        if not qid or qid in existing_by_id:
            raise ValueError(f"Duplicate or missing query_id in additional definitions: {qid!r}")
        document_id = definition.get("document_id")
        if document_id not in document_by_id:
            raise ValueError(f"{qid}: source document {document_id!r} is absent from Chunk Store")
        mapped_incidents = incident_by_doc.get(document_id, [])
        if len(mapped_incidents) != 1:
            raise ValueError(
                f"{qid}: primary document {document_id} maps to {len(mapped_incidents)} incident records; "
                "choose a source with one unambiguous incident mapping"
            )
        incident = mapped_incidents[0]
        if not incident.get("source_case_exclusion_ready"):
            raise ValueError(f"{qid}: source incident {incident['incident_id']} is not exclusion-ready")
        investigation_ids = incident.get("investigation_ids", [])
        exclusion = incident.get("source_case_exclusion", {})
        exclusion_investigation_ids = exclusion.get("investigation_ids", [])
        source_case_ids = exclusion.get("source_case_ids", [])
        if not investigation_ids or len(source_case_ids) != 1 or not exclusion_investigation_ids:
            raise ValueError(f"{qid}: expected one source investigation bundle, got {investigation_ids}")
        # Some CSB IDs have formatting aliases (e.g. 2008-03-I-FL / 2008-3-I-FL).
        # Treat the full exclusion bundle as one identity, not as multiple cases.
        investigation_id = investigation_ids[0]
        overlap = set(exclusion_investigation_ids) & (existing_source_investigations | new_source_investigations)
        if overlap:
            raise ValueError(f"{qid}: source investigation alias(es) {sorted(overlap)} are reused by another query")

        page_number = definition.get("page")
        page = page_by_key.get((document_id, page_number))
        if page is None:
            raise ValueError(f"{qid}: source page {document_id} p.{page_number} is absent")
        if page.get("page_kind") == "table_of_contents" or len(page.get("text", "")) < 250:
            raise ValueError(f"{qid}: source page is not a substantive text page")
        if exclusion.get("scope") != "whole_investigation":
            raise ValueError(f"{qid}: source exclusion scope is not whole_investigation")
        if investigation_id not in exclusion.get("investigation_ids", []):
            raise ValueError(f"{qid}: source investigation is missing from its exclusion bundle")
        if document_id not in exclusion.get("document_ids", []):
            raise ValueError(f"{qid}: source document is missing from its exclusion bundle")
        if not exclusion.get("source_case_ids"):
            raise ValueError(f"{qid}: source exclusion bundle has no source_case_ids")

        document = document_by_id[document_id]
        section_path = page.get("section_path_end") or []
        section_name = definition.get("section") or " / ".join(section_path) or "Unlabelled section"
        if isinstance(section_name, list):
            section_name = " / ".join(section_name)
        source_evidence = {
            "document_id": document_id,
            "page": page_number,
            "section": section_name,
            "supporting_quote": short_quote(page.get("text", "")),
        }
        record = {
            "query_id": qid,
            "query_text": definition["query_text"],
            "query_status": "draft_for_human_review",
            "language": "en",
            "query_facets": definition.get("facets", {}),
            "source_case": {
                "source_incident_id": incident["incident_id"],
                "source_investigation_id": investigation_id,
                "source_case_id": source_case_ids[0],
                "source_document_id": document_id,
                "source_document_title": document.get("title"),
                "source_mapping_status": incident.get("mapping_status"),
                "source_catalog_status": incident.get("catalog_status"),
                "source_parse_quality_status": document.get("parse_quality", {}).get("quality_status"),
                "source_evidence": source_evidence,
            },
            "source_case_exclusion": {
                "scope": exclusion["scope"],
                "incident_id": exclusion.get("incident_id"),
                "source_case_ids": exclusion.get("source_case_ids", []),
                "investigation_ids": exclusion.get("investigation_ids", []),
                "document_ids": exclusion.get("document_ids", []),
                "recommendation_ids": exclusion.get("recommendation_ids", []),
                "recommendation_snapshot_date": exclusion.get("recommendation_snapshot_date"),
                "status": incident.get("source_case_exclusion_status"),
            },
            "expected_abstention": None,
            "relevance_annotation_status": "pending_retrieval_pool_and_human_labels",
            "reference_answer_status": "pending_relevance_labels",
        }
        new_records.append(record)
        new_source_investigations.update(exclusion_investigation_ids)
        new_review_rows.append({
            "query_id": qid,
            "query_text": record["query_text"],
            "source_document_id": document_id,
            "source_document_title": document.get("title"),
            "source_page": page_number,
            "source_section": section_name,
            "source_incident_id": incident["incident_id"],
            "source_investigation_id": investigation_id,
            "source_mapping_status": incident.get("mapping_status"),
            "excluded_document_ids": ";".join(exclusion.get("document_ids", [])),
            "excluded_recommendation_ids": ";".join(exclusion.get("recommendation_ids", [])),
            "query_status": "draft_for_human_review",
        })

    # The extension is all-or-nothing: write only after every source passes validation.
    with review_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError("query_review.csv has no header")
        fieldnames = list(reader.fieldnames)
        review_rows = list(reader)
    if "query_id" not in fieldnames:
        raise ValueError("query_review.csv is missing query_id")
    review_by_id = {row.get("query_id", "").strip(): row for row in review_rows}
    if len(review_by_id) != len(review_rows) or set(review_by_id) != set(existing_by_id):
        raise ValueError("query_review.csv IDs do not match existing query_seeds.jsonl")
    if set(fieldnames) != set(REVIEW_FIELDS):
        missing = [field for field in REVIEW_FIELDS if field not in fieldnames]
        if missing:
            raise ValueError(f"query_review.csv is missing required columns: {missing}")

    final_seeds = existing + new_records
    final_review_rows = review_rows + new_review_rows
    write_jsonl_atomic(seeds_path, final_seeds)
    temp_csv = review_path.with_suffix(".csv.tmp")
    with temp_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(final_review_rows)
    temp_csv.replace(review_path)

    from collections import Counter
    summary = {
        "previous_query_count": len(existing),
        "added_query_count": len(new_records),
        "total_query_count": len(final_seeds),
        "approved_queries_preserved": sum(row.get("query_status") == "approved" for row in existing),
        "new_queries_awaiting_review": len(new_records),
        "unique_source_investigations": len({row["source_case"]["source_investigation_id"] for row in final_seeds}),
        "new_source_mapping_status_counts": dict(Counter(row["source_case"]["source_mapping_status"] for row in new_records)),
        "source_case_exclusion_ready": all(row["source_case_exclusion"]["scope"] == "whole_investigation" for row in new_records),
        "relevance_labels_created": 0,
        "reference_answers_created": 0,
        "status": "55_query_seeds_pending_review_of_new_25_and_human_relevance_annotation",
    }
    (golden_dir / "extension_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    seed_summary_path = golden_dir / "seed_build_summary.json"
    seed_summary = {}
    if seed_summary_path.is_file():
        try:
            seed_summary = json.loads(seed_summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            seed_summary = {}
    seed_summary.update({
        "query_seed_count": len(final_seeds),
        "unique_source_investigations": summary["unique_source_investigations"],
        "source_mapping_status_counts": dict(Counter(
            row["source_case"]["source_mapping_status"] for row in final_seeds
        )),
        "source_exclusion_ready_count": sum(
            row["source_case_exclusion"].get("scope") == "whole_investigation" for row in final_seeds
        ),
        "relevance_labels_created": 0,
        "reference_answers_created": 0,
        "status": "55_query_seeds_30_approved_25_pending_human_review_not_final_golden_set",
    })
    seed_summary_path.write_text(
        json.dumps(seed_summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden-dir", type=Path, default=GOLDEN)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--definitions", type=Path, default=None)
    args = parser.parse_args()
    summary = extend_golden_set(
        golden_dir=args.golden_dir, data_dir=args.data_dir, definitions_path=args.definitions,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
