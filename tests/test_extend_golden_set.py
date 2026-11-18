import csv
import json
from pathlib import Path

import pytest

from scripts.extend_golden_set import extend_golden_set


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def make_project(tmp_path: Path):
    golden = tmp_path / "data/golden"
    data = tmp_path / "data/processed"
    golden.mkdir(parents=True)
    (data / "chunk_store").mkdir(parents=True)

    old = []
    for i in range(30):
        inv = f"OLD-INV-{i:03d}"
        old.append({
            "query_id": f"Q{i+1:03d}",
            "query_text": f"Previously approved query {i+1}",
            "query_status": "approved",
            "source_case": {"source_investigation_id": inv, "source_mapping_status": "automatic_candidate_mapping"},
            "source_case_exclusion": {"investigation_ids": [inv]},
        })
    write_jsonl(golden / "query_seeds.jsonl", old)
    fields = [
        "query_id", "query_text", "source_document_id", "source_document_title",
        "source_page", "source_section", "source_incident_id", "source_investigation_id",
        "source_mapping_status", "excluded_document_ids", "excluded_recommendation_ids", "query_status",
    ]
    with (golden / "query_review.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in old:
            writer.writerow({"query_id": row["query_id"], "query_text": row["query_text"], "query_status": "approved"})

    page_text = (
        "Executive Summary. A process vessel ruptured during an industrial operation and released "
        "flammable material. The release ignited and caused a fire, leading to worker injuries. "
        "This long source passage supports an evidence-based test scenario and preserves the exact context for citation. "
        "Additional process details are included to exceed the substantive-page threshold used by the seed builder."
    )
    incident = {
        "incident_id": "INC-NEW", "titles": ["New source incident"],
        "investigation_ids": ["NEW-INV"], "document_ids": ["DOC-NEW"],
        "primary_document_ids": ["DOC-NEW"], "source_case_exclusion_ready": True,
        "source_case_exclusion_status": "whole_investigation_candidate_scope",
        "mapping_status": "automatic_candidate_mapping", "catalog_status": "automatic_candidate_mapping",
        "source_case_exclusion": {
            "incident_id": "INC-NEW", "source_case_ids": ["investigation:NEW-INV"],
            "scope": "whole_investigation", "investigation_ids": ["NEW-INV"],
            "document_ids": ["DOC-NEW"], "recommendation_ids": ["NEW-INV-R1"],
            "recommendation_snapshot_date": "2026-09-16",
        },
    }
    write_jsonl(data / "incident_catalog.jsonl", [incident])
    write_jsonl(data / "chunk_store/documents.jsonl", [{
        "document_id": "DOC-NEW", "title": "New source incident report",
        "parse_quality": {"quality_status": "parsed"},
    }])
    write_jsonl(data / "chunk_store/pages.jsonl", [{
        "document_id": "DOC-NEW", "page": 5, "page_kind": "content",
        "text": page_text, "section_path_end": ["Executive Summary"],
    }])
    definitions = golden / "additional.jsonl"
    write_jsonl(definitions, [{
        "query_id": "Q031", "document_id": "DOC-NEW", "page": 5,
        "section": "Executive Summary", "query_text": "An anonymized process-vessel incident description.",
        "facets": {"failure_mechanism": ["vessel rupture"]},
    }])
    return golden, data, definitions, page_text


def test_extend_golden_set_appends_new_draft_queries_and_preserves_approved_ones(tmp_path):
    golden, data, definitions, page_text = make_project(tmp_path)

    result = extend_golden_set(golden_dir=golden, data_dir=data, definitions_path=definitions)

    seeds = [json.loads(line) for line in (golden / "query_seeds.jsonl").read_text().splitlines()]
    assert len(seeds) == 31
    assert all(row["query_status"] == "approved" for row in seeds[:30])
    assert seeds[-1]["query_id"] == "Q031"
    assert seeds[-1]["query_status"] == "draft_for_human_review"
    assert seeds[-1]["source_case_exclusion"]["scope"] == "whole_investigation"
    assert seeds[-1]["source_case"]["source_evidence"]["supporting_quote"] in page_text
    assert result["approved_queries_preserved"] == 30
    assert result["new_queries_awaiting_review"] == 1
    assert result["total_query_count"] == 31

    with (golden / "query_review.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 31
    assert rows[-1]["query_status"] == "draft_for_human_review"


def test_extend_golden_set_rejects_existing_unapproved_queries(tmp_path):
    golden, data, definitions, _ = make_project(tmp_path)
    seeds_path = golden / "query_seeds.jsonl"
    seeds = [json.loads(line) for line in seeds_path.read_text().splitlines()]
    seeds[0]["query_status"] = "draft_for_human_review"
    write_jsonl(seeds_path, seeds)

    with pytest.raises(ValueError, match="were expected to be approved"):
        extend_golden_set(golden_dir=golden, data_dir=data, definitions_path=definitions)


def test_extend_golden_set_rejects_a_source_document_that_is_not_exclusion_ready(tmp_path):
    golden, data, definitions, _ = make_project(tmp_path)
    incident_path = data / "incident_catalog.jsonl"
    incident = json.loads(incident_path.read_text().splitlines()[0])
    incident["source_case_exclusion_ready"] = False
    write_jsonl(incident_path, [incident])

    with pytest.raises(ValueError, match="not exclusion-ready"):
        extend_golden_set(golden_dir=golden, data_dir=data, definitions_path=definitions)
