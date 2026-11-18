import csv
import json
from pathlib import Path

from scripts import build_relevance_pool as pool_builder
from scripts import validate_golden_set as golden_validator


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_pool_builder_unions_retrievers_hides_system_identity_and_removes_source_case(tmp_path):
    golden = tmp_path / "golden"
    golden.mkdir()
    queries = [{
        "query_id": "Q001", "query_text": "A generic process incident",
        "source_case": {"source_incident_id": "SOURCE", "source_investigation_id": "INV-SOURCE"},
        "source_case_exclusion": {
            "investigation_ids": ["INV-SOURCE"],
            "document_ids": ["DOC-SOURCE"],
            "recommendation_ids": ["REC-SOURCE"],
        },
    }]
    incidents = [
        {"incident_id": "SOURCE", "titles": ["Source case"], "investigation_ids": ["INV-SOURCE"],
         "document_ids": ["DOC-SOURCE"], "recommendation_ids": ["REC-SOURCE"],
         "primary_document_ids": [], "mapping_status": "candidate"},
        {"incident_id": "SIBLING", "titles": ["Sibling event"], "investigation_ids": ["INV-SOURCE"],
         "document_ids": ["DOC-SIBLING"], "recommendation_ids": [],
         "primary_document_ids": [], "mapping_status": "candidate"},
        {"incident_id": "CASE-A", "titles": ["A case"], "investigation_ids": ["INV-A"],
         "document_ids": ["DOC-A"], "recommendation_ids": [],
         "primary_document_ids": [], "mapping_status": "candidate"},
        {"incident_id": "CASE-B", "titles": ["B case"], "investigation_ids": ["INV-B"],
         "document_ids": ["DOC-B"], "recommendation_ids": [],
         "primary_document_ids": [], "mapping_status": "candidate"},
    ]
    docs = []
    pages = []
    write_jsonl(golden / "query_seeds.jsonl", queries)
    incident_path = tmp_path / "incident_catalog.jsonl"
    docs_path = tmp_path / "documents.jsonl"
    pages_path = tmp_path / "pages.jsonl"
    results_path = tmp_path / "retrieval_results.jsonl"
    write_jsonl(incident_path, incidents)
    write_jsonl(docs_path, docs)
    write_jsonl(pages_path, pages)
    write_jsonl(results_path, [
        {"query_id": "Q001", "retriever": "bm25", "results": [
            {"incident_id": "SOURCE", "rank": 1, "score": 10.0},
            {"incident_id": "CASE-A", "rank": 2, "score": 9.0},
        ]},
        {"query_id": "Q001", "retriever": "dense", "results": [
            {"incident_id": "SIBLING", "rank": 1, "score": 0.9},
            {"incident_id": "CASE-B", "rank": 2, "score": 0.8},
        ]},
    ])

    summary = pool_builder.build_pool(
        results_path, golden_dir=golden, incident_catalog_path=incident_path,
        chunk_documents_path=docs_path, chunk_pages_path=pages_path, top_k=10,
    )

    with (golden / "annotation_pool.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert {row["candidate_incident_id"] for row in rows} == {"CASE-A", "CASE-B"}
    assert all("retriever" not in row for row in rows)
    assert summary["source_case_hits_removed"] == 2
    provenance = [json.loads(line) for line in (golden / "pool_provenance.jsonl").read_text().splitlines()]
    assert len(provenance) == 2


def test_validator_accepts_thirty_query_seeds_with_valid_source_exclusions(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "processed/chunk_store").mkdir(parents=True)
    (data / "processed").mkdir(exist_ok=True)
    golden = tmp_path / "golden"
    golden.mkdir()
    queries = []
    incidents = []
    docs = []
    pages = []
    for i in range(30):
        qid = f"Q{i+1:03d}"
        iid = f"INC-{i+1:03d}"
        inv = f"INV-{i+1:03d}"
        did = f"DOC-{i+1:03d}"
        page_text = "Executive Summary. A chemical release occurred at an industrial facility."
        queries.append({
            "query_id": qid, "query_text": f"Incident scenario {i+1}", "query_status": "draft_for_human_review",
            "source_case": {"source_incident_id": iid, "source_investigation_id": inv,
                            "source_document_id": did, "source_evidence": {
                                "document_id": did, "page": 1, "supporting_quote": "Executive Summary. A chemical release"}},
            "source_case_exclusion": {"scope": "whole_investigation", "investigation_ids": [inv],
                                      "document_ids": [did], "recommendation_ids": []},
        })
        incidents.append({"incident_id": iid, "investigation_ids": [inv]})
        docs.append({"document_id": did})
        pages.append({"document_id": did, "page": 1, "page_kind": "content", "text": page_text})
    write_jsonl(golden / "query_seeds.jsonl", queries)
    write_jsonl(golden / "relevance_judgments.jsonl", [])
    write_jsonl(golden / "reference_answers.jsonl", [])
    write_jsonl(data / "processed/incident_catalog.jsonl", incidents)
    write_jsonl(data / "processed/chunk_store/documents.jsonl", docs)
    write_jsonl(data / "processed/chunk_store/pages.jsonl", pages)
    write_jsonl(data / "processed/recommendation_catalog.jsonl", [])
    monkeypatch.setattr(golden_validator, "DATA", data / "processed")

    result = golden_validator.validate(golden)

    assert result["structurally_valid"] is True
    assert result["ready_for_evaluation"] is False
    assert result["query_seeds"] == 30
    assert result["unique_source_investigations"] == 30


def test_validator_rejects_a_judgment_from_the_source_investigation(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "processed/chunk_store").mkdir(parents=True)
    golden = tmp_path / "golden"
    golden.mkdir()
    queries = []
    incidents = []
    docs = []
    pages = []
    for i in range(30):
        qid, iid, inv, did = f"Q{i:03d}", f"INC-{i:03d}", f"INV-{i:03d}", f"DOC-{i:03d}"
        page_text = "Executive Summary. A documented industrial incident."
        queries.append({
            "query_id": qid, "query_text": "scenario", "query_status": "approved",
            "source_case": {"source_incident_id": iid, "source_investigation_id": inv,
                            "source_document_id": did, "source_evidence": {"document_id": did, "page": 1,
                                "supporting_quote": "Executive Summary. A documented industrial"}},
            "source_case_exclusion": {"scope": "whole_investigation", "investigation_ids": [inv],
                                      "document_ids": [did], "recommendation_ids": []},
        })
        incidents.append({"incident_id": iid, "investigation_ids": [inv], "document_ids": [did], "recommendation_ids": []})
        docs.append({"document_id": did})
        pages.append({"document_id": did, "page": 1, "text": page_text})
    write_jsonl(golden / "query_seeds.jsonl", queries)
    write_jsonl(data / "processed/incident_catalog.jsonl", incidents)
    write_jsonl(data / "processed/chunk_store/documents.jsonl", docs)
    write_jsonl(data / "processed/chunk_store/pages.jsonl", pages)
    write_jsonl(data / "processed/recommendation_catalog.jsonl", [])
    judgment = {
        "query_id": "Q000", "incident_id": "INC-000", "grade": 2,
        "dimensions": {dim: "match" for dim in golden_validator.DIMENSIONS},
        "rationale": "This must fail because the source case is excluded.",
        "evidence": [{"document_id": "DOC-001", "page": 1, "supporting_quote": "Executive Summary"}],
        "annotator_id": "human_1",
    }
    write_jsonl(golden / "relevance_judgments.jsonl", [judgment])
    write_jsonl(golden / "reference_answers.jsonl", [])
    monkeypatch.setattr(golden_validator, "DATA", data / "processed")

    result = golden_validator.validate(golden)

    assert result["structurally_valid"] is False
    assert any("source incident was judged instead of excluded" in error for error in result["errors"])


def test_import_relevance_labels_writes_qrels_and_keeps_unjudged_rows_unknown(tmp_path):
    from scripts.import_relevance_labels import import_labels

    golden = tmp_path / "golden"
    golden.mkdir()
    write_jsonl(golden / "query_seeds.jsonl", [
        {"query_id": "Q001", "expected_abstention": None},
    ])
    fields = [
        "query_id", "candidate_incident_id", "relevance_grade",
        "substance_or_hazard", "equipment_or_process", "failure_mechanism",
        "event_sequence", "recommendation_transferability", "rationale",
        "evidence_document_id", "evidence_page", "evidence_section",
        "supporting_quote", "annotator_id", "annotation_status",
    ]
    rows = [
        {
            "query_id": "Q001", "candidate_incident_id": "CASE-A", "relevance_grade": "2",
            "substance_or_hazard": "match", "equipment_or_process": "match",
            "failure_mechanism": "match", "event_sequence": "partial",
            "recommendation_transferability": "match", "rationale": "Shared hazard and mechanism.",
            "evidence_document_id": "DOC-A", "evidence_page": "12",
            "evidence_section": "Contributing Factors", "supporting_quote": "A verified quote.",
            "annotator_id": "human_1", "annotation_status": "final",
        },
        {
            "query_id": "Q001", "candidate_incident_id": "CASE-B", "relevance_grade": "",
            "substance_or_hazard": "", "equipment_or_process": "", "failure_mechanism": "",
            "event_sequence": "", "recommendation_transferability": "", "rationale": "",
            "evidence_document_id": "", "evidence_page": "", "evidence_section": "",
            "supporting_quote": "", "annotator_id": "", "annotation_status": "",
        },
    ]
    with (golden / "annotation_pool.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    result = import_labels(golden)

    qrels = [json.loads(line) for line in (golden / "relevance_judgments.jsonl").read_text().splitlines()]
    assert len(qrels) == 1
    assert qrels[0]["grade"] == 2
    assert qrels[0]["incident_id"] == "CASE-A"
    assert result["unjudged_pool_rows"] == 1
    updated = json.loads((golden / "query_seeds.jsonl").read_text().splitlines()[0])
    assert updated["expected_abstention"] is None


def test_import_relevance_labels_can_mark_only_fully_judged_all_zero_queries_as_abstention(tmp_path):
    from scripts.import_relevance_labels import import_labels

    golden = tmp_path / "golden"
    golden.mkdir()
    write_jsonl(golden / "query_seeds.jsonl", [{"query_id": "Q001", "expected_abstention": None}])
    fields = [
        "query_id", "candidate_incident_id", "relevance_grade",
        "substance_or_hazard", "equipment_or_process", "failure_mechanism",
        "event_sequence", "recommendation_transferability", "rationale",
        "evidence_document_id", "evidence_page", "evidence_section",
        "supporting_quote", "annotator_id", "annotation_status",
    ]
    row = {
        "query_id": "Q001", "candidate_incident_id": "CASE-A", "relevance_grade": "0",
        "substance_or_hazard": "no_match", "equipment_or_process": "no_match",
        "failure_mechanism": "no_match", "event_sequence": "no_match",
        "recommendation_transferability": "no_match", "rationale": "No useful mechanism match.",
        "evidence_document_id": "DOC-A", "evidence_page": "12", "evidence_section": "Analysis",
        "supporting_quote": "A verified quote.", "annotator_id": "human_1", "annotation_status": "final",
    }
    with (golden / "annotation_pool.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow(row)

    result = import_labels(golden, mark_no_relevant_as_abstention=True)

    updated = json.loads((golden / "query_seeds.jsonl").read_text().splitlines()[0])
    assert updated["expected_abstention"] is True
    assert result["queries_human_marked_no_relevant"] == 1
