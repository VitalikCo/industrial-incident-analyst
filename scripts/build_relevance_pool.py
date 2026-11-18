"""Build a blinded human relevance-labeling pool from BM25/Dense/Hybrid results.

Expected retrieval_results.jsonl record shape (one record per query and retriever):
{
  "query_id": "Q001",
  "retriever": "bm25",
  "results": [
    {"incident_id": "...", "rank": 1, "score": 12.4}, ...
  ]
}

The annotator CSV intentionally omits retriever names, ranks and scores. These are
written to a separate provenance file not given to annotators.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = ROOT / "data/golden"
DEFAULT_CATALOG = ROOT / "data/processed/incident_catalog.jsonl"
DEFAULT_DOCS = ROOT / "data/processed/chunk_store/documents.jsonl"
DEFAULT_PAGES = ROOT / "data/processed/chunk_store/pages.jsonl"
ALLOWED_RETRIEVERS = {"bm25", "dense", "hybrid"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def candidate_brief(incident: dict[str, Any], docs_by_id: dict[str, dict[str, Any]], pages_by_doc: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    candidate_docs = [
        document_id for document_id in incident.get("primary_document_ids", [])
        if document_id in docs_by_id
    ]
    if not candidate_docs:
        candidate_docs = [document_id for document_id in incident.get("document_ids", []) if document_id in docs_by_id]
    for document_id in candidate_docs:
        pages = pages_by_doc.get(document_id, [])
        likely = []
        for page in pages:
            text = compact(page.get("text", ""))
            if page.get("page_kind") == "table_of_contents" or len(text) < 300:
                continue
            if re.match(r"^(?:executive summary|abstract|\d+(?:\.\d+)*\s+executive summary)", text, re.I):
                likely.append(page)
        if not likely:
            likely = [page for page in pages if page.get("page_kind") != "table_of_contents" and len(compact(page.get("text", ""))) > 500][:1]
        if likely:
            page = max(likely, key=lambda row: len(row.get("text", "")))
            text = compact(page.get("text", ""))
            # Keep the first compact paragraph/brief manageable for the annotator sheet.
            return {
                "document_id": document_id,
                "page": page.get("page"),
                "section": "Executive Summary/Abstract (verify in source report)",
                "brief": text[:1200],
            }
    return {"document_id": "", "page": "", "section": "", "brief": ""}


def excluded_by_source_case(candidate: dict[str, Any], query: dict[str, Any]) -> str | None:
    exclusion = query.get("source_case_exclusion", {})
    source_incident = query.get("source_case", {}).get("source_incident_id")
    if candidate.get("incident_id") == source_incident:
        return "source_incident_id"
    if set(candidate.get("investigation_ids", [])) & set(exclusion.get("investigation_ids", [])):
        return "source_investigation_id"
    if set(candidate.get("document_ids", [])) & set(exclusion.get("document_ids", [])):
        return "source_document_id"
    if set(candidate.get("recommendation_ids", [])) & set(exclusion.get("recommendation_ids", [])):
        return "source_recommendation_id"
    return None


def build_pool(
    retrieval_results_path: Path,
    golden_dir: Path = DEFAULT_GOLDEN,
    incident_catalog_path: Path = DEFAULT_CATALOG,
    chunk_documents_path: Path = DEFAULT_DOCS,
    chunk_pages_path: Path = DEFAULT_PAGES,
    top_k: int = 10,
) -> dict[str, Any]:
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    queries = read_jsonl(golden_dir / "query_seeds.jsonl")
    query_by_id = {row["query_id"]: row for row in queries}
    incidents = read_jsonl(incident_catalog_path)
    incident_by_id = {row["incident_id"]: row for row in incidents}
    docs = read_jsonl(chunk_documents_path)
    docs_by_id = {row["document_id"]: row for row in docs}
    pages = read_jsonl(chunk_pages_path)
    pages_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        pages_by_doc[page["document_id"]].append(page)

    results = read_jsonl(retrieval_results_path)
    pooled: dict[tuple[str, str], dict[str, Any]] = {}
    provenance: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    leakage_exclusions: list[dict[str, Any]] = []
    seen_query_retriever: set[tuple[str, str]] = set()

    for result_record in results:
        query_id = result_record.get("query_id")
        retriever = str(result_record.get("retriever", "")).lower()
        if query_id not in query_by_id:
            raise ValueError(f"Retrieval result contains unknown query_id: {query_id}")
        if retriever not in ALLOWED_RETRIEVERS:
            raise ValueError(f"Unsupported retriever {retriever!r}; use {sorted(ALLOWED_RETRIEVERS)}")
        if (query_id, retriever) in seen_query_retriever:
            raise ValueError(f"Duplicate retrieval-results record for {query_id}/{retriever}")
        seen_query_retriever.add((query_id, retriever))
        query = query_by_id[query_id]
        hits = sorted(result_record.get("results", []), key=lambda hit: int(hit.get("rank", 10**9)))[:top_k]
        seen_incidents: set[str] = set()
        for hit in hits:
            incident_id = hit.get("incident_id")
            if incident_id not in incident_by_id:
                raise ValueError(f"Unknown incident_id in retrieval result: {incident_id}")
            if incident_id in seen_incidents:
                continue
            seen_incidents.add(incident_id)
            candidate = incident_by_id[incident_id]
            leakage_reason = excluded_by_source_case(candidate, query)
            if leakage_reason:
                leakage_exclusions.append({
                    "query_id": query_id, "retriever": retriever,
                    "incident_id": incident_id, "reason": leakage_reason,
                    "rank": hit.get("rank"),
                })
                continue
            key = (query_id, incident_id)
            if key not in pooled:
                brief = candidate_brief(candidate, docs_by_id, pages_by_doc)
                pooled[key] = {
                    "query_id": query_id,
                    "query_text": query["query_text"],
                    "candidate_incident_id": incident_id,
                    "candidate_title": " / ".join(candidate.get("titles", [])),
                    "candidate_investigation_ids": candidate.get("investigation_ids", []),
                    "candidate_document_ids": candidate.get("document_ids", []),
                    "candidate_mapping_status": candidate.get("mapping_status"),
                    "candidate_catalog_status": candidate.get("catalog_status"),
                    "candidate_brief_document_id": brief["document_id"],
                    "candidate_brief_page": brief["page"],
                    "candidate_brief_section": brief["section"],
                    "candidate_brief": brief["brief"],
                }
            provenance[key].append({
                "retriever": retriever,
                "rank": hit.get("rank"),
                "score": hit.get("score"),
            })

    # Keep queries grouped, but shuffle candidate incidents within each query with a
    # fixed seed. The annotator must not infer system rank/order from the sheet.
    rows = [pooled[key] for key in sorted(pooled)]
    grouped_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped_rows[row["query_id"]].append(row)
    rng = random.Random(20261117)
    rows = []
    for query_id in sorted(grouped_rows):
        group = grouped_rows[query_id]
        rng.shuffle(group)
        rows.extend(group)
    fields = [
        "query_id", "query_text", "candidate_incident_id", "candidate_title",
        "candidate_investigation_ids", "candidate_document_ids", "candidate_mapping_status",
        "candidate_catalog_status", "candidate_brief_document_id", "candidate_brief_page",
        "candidate_brief_section", "candidate_brief", "relevance_grade",
        "substance_or_hazard", "equipment_or_process", "failure_mechanism", "event_sequence",
        "recommendation_transferability", "rationale", "evidence_document_id",
        "evidence_page", "evidence_section", "supporting_quote", "annotator_id", "annotation_status",
    ]
    golden_dir.mkdir(parents=True, exist_ok=True)
    with (golden_dir / "annotation_pool.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            safe = dict(row)
            safe["candidate_investigation_ids"] = ";".join(safe["candidate_investigation_ids"])
            safe["candidate_document_ids"] = ";".join(safe["candidate_document_ids"])
            safe.update({field: "" for field in fields if field not in safe})
            writer.writerow(safe)

    provenance_rows = [
        {"query_id": qid, "incident_id": iid, "retrieval_provenance": prov}
        for (qid, iid), prov in sorted(provenance.items())
    ]
    (golden_dir / "pool_provenance.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in provenance_rows), encoding="utf-8"
    )
    (golden_dir / "pool_excluded_source_leakage.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in leakage_exclusions), encoding="utf-8"
    )
    counts = defaultdict(int)
    for row in rows:
        counts[row["query_id"]] += 1
    expected_pairs = {(qid, retriever) for qid in query_by_id for retriever in sorted(ALLOWED_RETRIEVERS)}
    missing_pairs = sorted(expected_pairs - seen_query_retriever)
    summary = {
        "query_count": len(queries),
        "retrieval_result_records": len(results),
        "retrievers_present": sorted({row.get("retriever") for row in results}),
        "top_k_per_retriever": top_k,
        "pooled_query_incident_pairs": len(rows),
        "queries_with_pool": sum(counts[qid] > 0 for qid in query_by_id),
        "min_pool_depth": min(counts.values()) if counts else 0,
        "max_pool_depth": max(counts.values()) if counts else 0,
        "source_case_hits_removed": len(leakage_exclusions),
        "missing_query_retriever_pairs": len(missing_pairs),
        "missing_pairs_examples": [list(pair) for pair in missing_pairs[:12]],
        "annotation_sheet_hides_retriever_identity": True,
        "candidate_order_shuffled_with_fixed_seed": True,
        "files": ["annotation_pool.csv", "pool_provenance.jsonl", "pool_excluded_source_leakage.jsonl"],
    }
    (golden_dir / "pool_build_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retrieval-results", type=Path, required=True)
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    result = build_pool(args.retrieval_results, golden_dir=args.golden_dir, top_k=args.top_k)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
