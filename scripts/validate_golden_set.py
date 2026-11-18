"""Validate query seeds, human relevance judgments, evidence and source-case exclusion."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data/golden"
DATA = ROOT / "data/processed"
VALID_GRADES = {0, 1, 2}
VALID_DIMENSION_LABELS = {"match", "partial", "no_match", "unknown"}
DIMENSIONS = [
    "substance_or_hazard", "equipment_or_process", "failure_mechanism",
    "event_sequence", "recommendation_transferability",
]


def read_jsonl(path: Path, *, optional: bool = False) -> list[dict[str, Any]]:
    if not path.exists() and optional:
        return []
    if not path.is_file():
        raise FileNotFoundError(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def validate(
    golden_dir: Path = GOLDEN,
    *,
    min_queries: int = 30,
    require_approved_queries: bool = False,
    require_judgments: bool = False,
) -> dict[str, Any]:
    queries = read_jsonl(golden_dir / "query_seeds.jsonl")
    judgments = read_jsonl(golden_dir / "relevance_judgments.jsonl", optional=True)
    answers = read_jsonl(golden_dir / "reference_answers.jsonl", optional=True)
    incidents = read_jsonl(DATA / "incident_catalog.jsonl")
    pages = read_jsonl(DATA / "chunk_store/pages.jsonl")
    documents = read_jsonl(DATA / "chunk_store/documents.jsonl")
    recommendations = read_jsonl(DATA / "recommendation_catalog.jsonl")

    errors: list[str] = []
    warnings: list[str] = []
    query_by_id = {row.get("query_id"): row for row in queries}
    if len(query_by_id) != len(queries):
        errors.append("Duplicate query_id values in query_seeds.jsonl")
    incident_by_id = {row["incident_id"]: row for row in incidents}
    page_by_key = {(row["document_id"], row["page"]): row for row in pages}
    doc_by_id = {row["document_id"]: row for row in documents}
    recommendation_by_id = {row["recommendation_id"]: row for row in recommendations}

    source_investigations: list[str] = []
    unapproved = []
    for query in queries:
        qid = query.get("query_id")
        source = query.get("source_case", {})
        exclusion = query.get("source_case_exclusion", {})
        if not query.get("query_text", "").strip():
            errors.append(f"{qid}: empty query_text")
        if query.get("query_status") != "approved":
            unapproved.append(qid)
        source_incident_id = source.get("source_incident_id")
        source_incident = incident_by_id.get(source_incident_id)
        if source_incident is None:
            errors.append(f"{qid}: unknown source incident_id {source_incident_id}")
        source_investigation_id = source.get("source_investigation_id")
        if source_investigation_id:
            source_investigations.append(source_investigation_id)
        if exclusion.get("scope") != "whole_investigation":
            errors.append(f"{qid}: source exclusion scope is not whole_investigation")
        if source_investigation_id not in exclusion.get("investigation_ids", []):
            errors.append(f"{qid}: source investigation is missing from its exclusion bundle")
        source_doc_id = source.get("source_document_id")
        if source_doc_id not in doc_by_id:
            errors.append(f"{qid}: source document {source_doc_id} is absent from chunk_store/documents.jsonl")
        if source_doc_id not in exclusion.get("document_ids", []):
            errors.append(f"{qid}: source document is missing from the exclusion bundle")
        source_evidence = source.get("source_evidence", {})
        source_page = page_by_key.get((source_evidence.get("document_id"), source_evidence.get("page")))
        if source_page is None:
            errors.append(f"{qid}: source evidence page does not exist")
        elif source_evidence.get("supporting_quote") and norm(source_evidence["supporting_quote"]) not in norm(source_page.get("text", "")):
            errors.append(f"{qid}: source evidence quote does not match the extracted page text")
        if source_incident and source_investigation_id not in source_incident.get("investigation_ids", []):
            errors.append(f"{qid}: source incident does not include the stated investigation ID")

    duplicate_sources = [value for value, count in Counter(source_investigations).items() if count > 1]
    if duplicate_sources:
        errors.append(f"Source investigation reused by multiple query seeds: {duplicate_sources[:10]}")
    if len(queries) < min_queries:
        errors.append(f"Only {len(queries)} query seeds; minimum is {min_queries}")
    if require_approved_queries and unapproved:
        errors.append(f"Queries not approved by a human: {unapproved[:10]}")

    seen_qrels: set[tuple[str, str]] = set()
    judgments_by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for judgment in judgments:
        qid = judgment.get("query_id")
        incident_id = judgment.get("incident_id")
        if qid not in query_by_id:
            errors.append(f"Judgment references unknown query_id {qid}")
            continue
        if incident_id not in incident_by_id:
            errors.append(f"{qid}: judgment references unknown incident_id {incident_id}")
            continue
        key = (qid, incident_id)
        if key in seen_qrels:
            errors.append(f"Duplicate relevance judgment for {qid}/{incident_id}")
        seen_qrels.add(key)
        query = query_by_id[qid]
        source = query.get("source_case", {})
        exclusion = query.get("source_case_exclusion", {})
        candidate = incident_by_id[incident_id]
        if incident_id == source.get("source_incident_id"):
            errors.append(f"{qid}: source incident was judged instead of excluded")
        if set(candidate.get("investigation_ids", [])) & set(exclusion.get("investigation_ids", [])):
            errors.append(f"{qid}: judgment includes a sibling incident from the excluded source investigation")
        if set(candidate.get("document_ids", [])) & set(exclusion.get("document_ids", [])):
            errors.append(f"{qid}: judgment includes an incident sharing an excluded source document")
        if set(candidate.get("recommendation_ids", [])) & set(exclusion.get("recommendation_ids", [])):
            errors.append(f"{qid}: judgment includes an incident sharing excluded source recommendations")

        grade = judgment.get("grade")
        if grade not in VALID_GRADES:
            errors.append(f"{qid}/{incident_id}: grade must be integer 0, 1 or 2")
        dims = judgment.get("dimensions", {})
        for dimension in DIMENSIONS:
            if dims.get(dimension) not in VALID_DIMENSION_LABELS:
                errors.append(f"{qid}/{incident_id}: dimension {dimension} must be one of {sorted(VALID_DIMENSION_LABELS)}")
        if not judgment.get("rationale", "").strip():
            errors.append(f"{qid}/{incident_id}: missing rationale")
        evidence_list = judgment.get("evidence", [])
        if not evidence_list:
            errors.append(f"{qid}/{incident_id}: at least one source evidence citation is required")
        for evidence in evidence_list:
            page = page_by_key.get((evidence.get("document_id"), evidence.get("page")))
            if page is None:
                errors.append(f"{qid}/{incident_id}: evidence page does not exist: {evidence.get('document_id')} p.{evidence.get('page')}")
            elif evidence.get("supporting_quote") and norm(evidence["supporting_quote"]) not in norm(page.get("text", "")):
                errors.append(f"{qid}/{incident_id}: supporting quote does not match {evidence.get('document_id')} p.{evidence.get('page')}")
        if not judgment.get("annotator_id"):
            errors.append(f"{qid}/{incident_id}: missing annotator_id")
        judgments_by_query[qid].append(judgment)

    answers_by_query = {row.get("query_id"): row for row in answers}
    if len(answers_by_query) != len(answers):
        errors.append("Duplicate query_id values in reference_answers.jsonl")
    answer_covered_positive_ids: dict[str, set[str]] = defaultdict(set)
    for answer in answers:
        qid = answer.get("query_id")
        if qid not in query_by_id:
            errors.append(f"Reference answer references unknown query_id {qid}")
            continue
        qrel_positive_ids = {
            row.get("incident_id") for row in judgments_by_query.get(qid, []) if row.get("grade", 0) > 0
        }
        for historical in answer.get("historical_incidents", []):
            incident_id = historical.get("incident_id")
            if incident_id not in qrel_positive_ids:
                errors.append(f"{qid}: reference answer includes {incident_id}, which has no positive relevance judgment")
            if incident_id:
                answer_covered_positive_ids[qid].add(incident_id)
            for cause in historical.get("documented_causes", []):
                evidence = cause.get("evidence", {})
                page = page_by_key.get((evidence.get("document_id"), evidence.get("page")))
                if page is None:
                    errors.append(f"{qid}/{incident_id}: cause evidence page missing")
                elif evidence.get("supporting_quote") and norm(evidence["supporting_quote"]) not in norm(page.get("text", "")):
                    errors.append(f"{qid}/{incident_id}: cause quote not found on cited page")
            for recommendation in historical.get("recommendations", []):
                rid = recommendation.get("recommendation_id")
                if rid not in recommendation_by_id:
                    errors.append(f"{qid}/{incident_id}: unknown recommendation_id {rid}")
                elif recommendation.get("status_as_of") != recommendation_by_id[rid].get("source_snapshot_date"):
                    errors.append(f"{qid}/{incident_id}: recommendation status_as_of does not match source snapshot for {rid}")
                evidence = recommendation.get("evidence", {})
                if evidence:
                    page = page_by_key.get((evidence.get("document_id"), evidence.get("page")))
                    if page is None:
                        errors.append(f"{qid}/{incident_id}: recommendation evidence page missing")

    missing_reference_incidents = []
    for qid, rows in judgments_by_query.items():
        for judgment in rows:
            if judgment.get("grade", 0) > 0 and judgment.get("incident_id") not in answer_covered_positive_ids.get(qid, set()):
                missing_reference_incidents.append(f"{qid}/{judgment.get('incident_id')}")
    if missing_reference_incidents:
        warnings.append(
            "Positive relevance judgments without historical reference-answer records: "
            f"{missing_reference_incidents[:10]}"
        )

    complete_query_ids = {
        qid for qid in query_by_id
        if qid in judgments_by_query and (
            any(row.get("grade", 0) > 0 for row in judgments_by_query[qid])
            or query_by_id[qid].get("expected_abstention") is True
        )
    }
    positive_query_ids = {
        qid for qid, rows in judgments_by_query.items() if any(row.get("grade", 0) > 0 for row in rows)
    }
    no_relevant_query_ids = {
        qid for qid in judgments_by_query if rows_are_all_zero(judgments_by_query[qid])
    }
    ready = (
        len(queries) >= min_queries
        and not unapproved
        and len(complete_query_ids) == len(queries)
        and not missing_reference_incidents
        and not errors
    )
    summary = {
        "query_seeds": len(queries),
        "unique_source_investigations": len(set(source_investigations)),
        "approved_queries": len(queries) - len(unapproved),
        "relevance_judgments": len(judgments),
        "queries_with_any_judgments": len(judgments_by_query),
        "queries_with_positive_relevant_cases": len(positive_query_ids),
        "queries_with_no_judged_relevant_case": len(no_relevant_query_ids),
        "queries_ready_for_ranking_metrics": len(complete_query_ids),
        "reference_answer_records": len(answers),
        "positive_relevance_cases_missing_reference_answers": len(missing_reference_incidents),
        "structurally_valid": not errors,
        "ready_for_evaluation": ready,
        "errors": errors,
        "warnings": warnings,
        "notes": [
            "A seed-only dataset is not yet a final golden set.",
            "nDCG is undefined for queries with no judged relevant incident; report that slice separately.",
            "The relevance pool is incomplete by design: annotate the union of top-10 BM25/Dense/Hybrid results plus source-known cases.",
        ],
    }
    return summary


def rows_are_all_zero(rows: list[dict[str, Any]]) -> bool:
    return bool(rows) and all(row.get("grade") == 0 for row in rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden-dir", type=Path, default=GOLDEN)
    parser.add_argument("--min-queries", type=int, default=30)
    parser.add_argument("--require-approved-queries", action="store_true")
    parser.add_argument("--require-judgments", action="store_true", help="Return non-zero until every query has judged candidates and either positive relevance or expected_abstention=true.")
    args = parser.parse_args()
    result = validate(
        args.golden_dir, min_queries=args.min_queries,
        require_approved_queries=args.require_approved_queries,
        require_judgments=args.require_judgments,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    (args.golden_dir / "validation_summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not result["structurally_valid"]:
        return 1
    if args.require_judgments and not result["ready_for_evaluation"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
