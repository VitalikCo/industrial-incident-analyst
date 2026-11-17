import json
from pathlib import Path

import fitz
import pytest

from scripts import parse_csb_documents as parser
from scripts.validate_chunk_store import validate_store


def _make_pdf(path: Path, pages: int = 2, repeated_header: bool = False) -> None:
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=612, height=792)
        if repeated_header:
            page.insert_text((72, 30), "CSB FINAL INVESTIGATION REPORT", fontsize=8)
        if index == 0:
            page.insert_text((72, 100), "1.0 Executive Summary", fontsize=16, fontname="hebo")
            page.insert_textbox(
                fitz.Rect(72, 125, 530, 280),
                "The incident occurred during maintenance at an industrial facility. "
                "A flammable vapour release occurred after a process line was opened. "
                "The pressure was 4.5 bar and the temperature was 80 C.",
                fontsize=11,
            )
        else:
            page.insert_text((72, 100), "1.1 Incident Description", fontsize=14, fontname="hebo")
            page.insert_textbox(
                fitz.Rect(72, 125, 530, 300),
                "Operators began maintenance after an isolation step. The valve was not verified. "
                "The release ignited and caused a fire. The report identifies several contributing factors.",
                fontsize=11,
            )
    doc.save(path)
    doc.close()


def _document_record(path: Path, root: Path) -> dict:
    rel = path.relative_to(root).as_posix()
    return {
        "document_id": "CSB-TEST-1",
        "title": "Synthetic CSB Incident Report",
        "local_path": rel,
        "text_source_path": rel,
        "source_sha256": "source-sha",
        "text_source_sha256": "text-sha",
        "csb_document_id": "9999",
        "document_type": "investigation_report",
        "corpus_role": "primary_incident_evidence",
        "investigation_id": "2020-01-I-TX",
        "candidate_investigation_id": "2020-01-I-TX",
        "recommendation_match_key": "2020-1-I-TX",
        "candidate_recommendation_match_key": "2020-1-I-TX",
        "incident_ids": [],
        "candidate_incident_ids": ["TEST_INCIDENT_1"],
        "source_case_id": "investigation:2020-1-I-TX",
        "language": "en",
        "retrieval_enabled": True,
        "classification_status": "automatic_candidate",
        "classification_method": "source_checked_candidate_table",
        "classification_confidence": "high",
        "mapping_status": "automatic_candidate_mapping",
        "recommendation_ids": ["2020-1-I-TX-R1"],
    }


def test_parse_document_preserves_pages_sections_and_candidate_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "ROOT", tmp_path)
    pdf = tmp_path / "data/raw/reports/synthetic.pdf"
    pdf.parent.mkdir(parents=True)
    _make_pdf(pdf, pages=2)

    document, pages, chunks, sections, quality = parser.parse_document(_document_record(pdf, tmp_path))

    assert document["document_id"] == "CSB-TEST-1"
    assert document["candidate_incident_ids"] == ["TEST_INCIDENT_1"]
    assert document["incident_ids"] == []
    assert len(pages) == 2
    assert pages[0]["page"] == 1 and pages[1]["page"] == 2
    assert any(section["heading"] == "Executive Summary" for section in sections)
    assert any(section["heading"] == "Incident Description" for section in sections)
    retrieval_chunks = [chunk for chunk in chunks if chunk["retrieval_eligible"]]
    parent_chunks = [chunk for chunk in chunks if chunk["chunk_type"] == "section_parent"]
    assert retrieval_chunks
    assert parent_chunks
    assert all(chunk["document_id"] == "CSB-TEST-1" for chunk in chunks)
    assert all(chunk["candidate_incident_ids"] == ["TEST_INCIDENT_1"] for chunk in chunks)
    assert all(chunk["incident_ids"] == [] for chunk in chunks)
    assert all(chunk["parent_chunk_id"] for chunk in retrieval_chunks if chunk["section_id"])
    assert quality["page_count_parsed"] == 2
    assert quality["text_coverage"] == 1.0
    assert pages[0]["numeric_token_count"] >= 2
    assert pages[0]["low_text"] is False


def test_repeated_running_header_and_page_number_are_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "ROOT", tmp_path)
    pdf = tmp_path / "reports/repeated.pdf"
    pdf.parent.mkdir(parents=True)
    _make_pdf(pdf, pages=5, repeated_header=True)

    _, pages, _, _, quality = parser.parse_document(_document_record(pdf, tmp_path))

    assert all("CSB FINAL INVESTIGATION REPORT" not in page["text"] for page in pages)
    assert quality["repeated_running_line_patterns_removed"] >= 1


def test_split_text_obeys_size_and_keeps_overlap():
    text = "Sentence one. " * 500
    chunks = parser.split_text(text, max_chars=1200, overlap_chars=100)
    assert len(chunks) > 1
    assert all(len(chunk) <= 1200 for chunk in chunks)
    assert any(chunks[index - 1][-30:] in chunks[index] for index in range(1, len(chunks)))


def test_select_documents_only_uses_retrieval_enabled_english_records():
    rows = [
        {"document_id": "A", "retrieval_enabled": True, "language": "en"},
        {"document_id": "B", "retrieval_enabled": False, "language": "en"},
        {"document_id": "C", "retrieval_enabled": True, "language": "es"},
    ]
    assert [row["document_id"] for row in parser.select_documents(rows)] == ["A"]
    assert {row["document_id"] for row in parser.select_documents(rows, include_non_retrieval=True)} == {"A", "B"}


def test_parse_catalog_writes_expected_chunk_store_and_quality(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "ROOT", tmp_path)
    pdf = tmp_path / "data/raw/reports/synthetic.pdf"
    pdf.parent.mkdir(parents=True)
    _make_pdf(pdf, pages=2)
    record = _document_record(pdf, tmp_path)
    catalog = tmp_path / "catalog.jsonl"
    catalog.write_text(json.dumps(record) + "\n", encoding="utf-8")
    output = tmp_path / "chunk_store"

    result = parser.parse_catalog(catalog, output)

    assert result["parsed_documents"] == 1
    assert result["page_count"] == 2
    assert result["retrieval_chunks"] > 0
    for filename in (
        "documents.jsonl", "pages.jsonl", "chunks.jsonl", "sections.jsonl",
        "parse_quality.json", "parse_quality.csv", "page_quality.csv",
    ):
        assert (output / filename).is_file()
    validation = validate_store(output)
    assert validation["valid"] is True
    assert validation["documents"] == 1


def test_parse_catalog_skips_missing_files_but_reports_them(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "ROOT", tmp_path)
    record = {
        "document_id": "CSB-MISSING", "retrieval_enabled": True, "language": "en",
        "local_path": "data/raw/reports/missing.pdf", "text_source_path": "data/raw/reports/missing.pdf",
    }
    catalog = tmp_path / "catalog.jsonl"
    catalog.write_text(json.dumps(record) + "\n", encoding="utf-8")

    result = parser.parse_catalog(catalog, tmp_path / "store")

    assert result["selected_documents"] == 1
    assert result["parsed_documents"] == 0
    assert result["skipped_missing_sources"] == 1
    assert result["errors"][0]["error"] == "local_text_source_missing"


def test_tesseract_fallback_fails_with_clear_message_when_not_installed(monkeypatch):
    monkeypatch.setattr(parser.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="Tesseract was requested"):
        parser.ocr_page_tesseract(None)


def test_table_of_contents_pages_are_preserved_but_not_indexed():
    blocks = [
        {"text": "CONTENTS"},
        {"text": "1.0 INTRODUCTION .............. 4"},
        {"text": "2.0 INCIDENT DESCRIPTION .............. 8"},
        {"text": "3.0 ANALYSIS .............. 15"},
    ]
    assert parser.is_table_of_contents_page(blocks)
    assert parser.classify_heading(blocks[1], 10.0) is None



def test_toc_page_is_not_added_to_the_previous_section_and_section_range_spans_children(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "ROOT", tmp_path)
    pdf = tmp_path / "reports/toc-and-sections.pdf"
    pdf.parent.mkdir(parents=True)
    doc = fitz.open()

    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Cover page", fontsize=18)

    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "CONTENTS", fontsize=18, fontname="hebo")
    page.insert_text((72, 130), "1.0 EXECUTIVE SUMMARY .............. 3", fontsize=10)
    page.insert_text((72, 155), "2.0 INCIDENT DESCRIPTION .............. 5", fontsize=10)
    page.insert_text((72, 180), "3.0 ANALYSIS .............. 8", fontsize=10)

    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "1.0 Executive Summary", fontsize=16, fontname="hebo")
    page.insert_textbox(
        fitz.Rect(72, 120, 530, 300),
        "This is the actual executive summary content, not a table of contents entry. "
        "A process release occurred during maintenance and resulted in a fire.", fontsize=11,
    )

    page = doc.new_page(width=612, height=792)
    page.insert_textbox(
        fitz.Rect(72, 100, 530, 320),
        "The investigation identified loss of containment and inadequate verification "
        "of isolation as important factors in the event sequence.", fontsize=11,
    )
    doc.save(pdf)
    doc.close()

    _, pages, chunks, sections, _ = parser.parse_document(_document_record(pdf, tmp_path))

    toc = next(page for page in pages if page["page"] == 2)
    assert toc["page_kind"] == "table_of_contents"
    toc_chunks = [chunk for chunk in chunks if chunk["chunk_type"] == "table_of_contents"]
    assert len(toc_chunks) == 1 and toc_chunks[0]["retrieval_eligible"] is False
    assert not any(chunk["page_start"] == 2 and chunk["retrieval_eligible"] for chunk in chunks)

    summary = next(section for section in sections if section["heading"] == "Executive Summary")
    assert summary["page_start"] == 3
    assert summary["page_end"] == 4
    parent = next(
        chunk for chunk in chunks
        if chunk["chunk_type"] == "section_parent" and chunk["section_id"] == summary["section_id"]
    )
    assert parent["page_start"] == 3 and parent["page_end"] == 4
    assert "CONTENTS" not in parent["text"]


def test_table_is_kept_as_one_chunk_with_caption_and_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "ROOT", tmp_path)
    pdf = tmp_path / "reports/table-report.pdf"
    pdf.parent.mkdir(parents=True)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 80), "Table 1: Process parameters", fontsize=11)
    xs, ys = [72, 250, 430], [120, 150, 180]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]), color=(0, 0, 0), width=0.8)
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y), color=(0, 0, 0), width=0.8)
    cells = [["Parameter", "Value"], ["Pressure", "4.5 bar"]]
    for row_index, row in enumerate(cells):
        for column_index, value in enumerate(row):
            page.insert_text((xs[column_index] + 8, ys[row_index] + 20), value, fontsize=10)
    doc.save(pdf)
    doc.close()

    _, pages, chunks, _, quality = parser.parse_document(_document_record(pdf, tmp_path))

    table_chunks = [chunk for chunk in chunks if chunk["chunk_type"] == "table"]
    assert len(table_chunks) == 1
    assert table_chunks[0]["retrieval_eligible"] is True
    assert "Table 1: Process parameters" in table_chunks[0]["text"]
    assert "4.5 bar" in table_chunks[0]["text"]
    assert table_chunks[0]["table_rows"][1] == ["Pressure", "4.5 bar"]
    assert pages[0]["table_count"] == 1
    assert quality["table_count"] == 1



def test_ocr_derivative_is_parsed_but_original_pdf_remains_the_source_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "ROOT", tmp_path)
    original = tmp_path / "data/raw/reports/original-report.pdf"
    derivative = tmp_path / "data/processed/ocr/original-report_ocr.pdf"
    derivative.parent.mkdir(parents=True)
    _make_pdf(derivative, pages=2)
    record = _document_record(derivative, tmp_path)
    record["local_path"] = "data/raw/reports/original-report.pdf"
    record["text_source_path"] = "data/processed/ocr/original-report_ocr.pdf"

    document, pages, _, _, _ = parser.parse_document(record)

    assert document["source_path"] == "data/raw/reports/original-report.pdf"
    assert document["parse_source_path"] == "data/processed/ocr/original-report_ocr.pdf"
    assert document["source_sha256_actual"] is None
    assert document["parse_source_sha256_actual"]
    assert len(pages) == 2



def test_repeated_identical_headings_on_same_page_get_distinct_sections_and_parents(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "ROOT", tmp_path)
    pdf = tmp_path / "reports/repeated-sections.pdf"
    pdf.parent.mkdir(parents=True)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "1.0 Executive Summary", fontsize=16, fontname="hebo")
    page.insert_textbox(
        fitz.Rect(72, 115, 530, 230),
        "First occurrence: the initial process release happened during maintenance. "
        "The investigation records the first sequence of events.",
        fontsize=11,
    )
    page.insert_text((72, 390), "1.0 Executive Summary", fontsize=16, fontname="hebo")
    page.insert_textbox(
        fitz.Rect(72, 415, 530, 540),
        "Second occurrence: a separate summary passage describes the later response. "
        "This text must not be merged into the first section.",
        fontsize=11,
    )
    doc.save(pdf)
    doc.close()

    _, _, chunks, sections, _ = parser.parse_document(_document_record(pdf, tmp_path))

    assert len(sections) == 2
    section_ids = [section["section_id"] for section in sections]
    assert len(set(section_ids)) == 2
    parent_chunks = [chunk for chunk in chunks if chunk["chunk_type"] == "section_parent"]
    assert len(parent_chunks) == 2
    assert {chunk["section_id"] for chunk in parent_chunks} == set(section_ids)
    first_parent = next(chunk for chunk in parent_chunks if chunk["section_id"] == section_ids[0])
    second_parent = next(chunk for chunk in parent_chunks if chunk["section_id"] == section_ids[1])
    assert "First occurrence" in first_parent["text"]
    assert "Second occurrence" not in first_parent["text"]
    assert "Second occurrence" in second_parent["text"]
    assert "First occurrence" not in second_parent["text"]


def _write_store_jsonl(store_dir: Path, name: str, rows: list[dict]) -> None:
    (store_dir / name).write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _minimal_valid_store(store_dir: Path) -> tuple[dict, dict, dict]:
    store_dir.mkdir(parents=True, exist_ok=True)
    document = {"document_id": "DOC-1"}
    page = {"document_id": "DOC-1", "page": 1}
    section = {
        "document_id": "DOC-1", "section_id": "DOC-1-s-1",
        "parent_section_id": None, "page_start": 1, "page_end": 1,
    }
    parent_chunk = {
        "document_id": "DOC-1", "chunk_id": "DOC-1-parent-1",
        "chunk_type": "section_parent", "retrieval_eligible": False,
        "section_id": "DOC-1-s-1", "parent_section_id": None,
        "page_start": 1, "page_end": 1, "source_pages": [1], "text": "Section heading",
    }
    _write_store_jsonl(store_dir, "documents.jsonl", [document])
    _write_store_jsonl(store_dir, "pages.jsonl", [page])
    _write_store_jsonl(store_dir, "sections.jsonl", [section])
    _write_store_jsonl(store_dir, "chunks.jsonl", [parent_chunk])
    return document, section, parent_chunk


def test_validator_rejects_duplicate_section_ids(tmp_path):
    store_dir = tmp_path / "store"
    _, section, _ = _minimal_valid_store(store_dir)
    _write_store_jsonl(store_dir, "sections.jsonl", [section, dict(section)])

    result = validate_store(store_dir)

    assert result["valid"] is False
    assert result["duplicate_section_ids"] == 1
    assert any("Duplicate section IDs" in error for error in result["errors"])


def test_validator_requires_exactly_one_parent_chunk_per_section(tmp_path):
    store_dir = tmp_path / "store"
    _, _, parent_chunk = _minimal_valid_store(store_dir)

    _write_store_jsonl(store_dir, "chunks.jsonl", [])
    missing_result = validate_store(store_dir)
    assert missing_result["valid"] is False
    assert missing_result["sections_without_parent_chunks"] == 1
    assert any("without a section_parent chunk" in error for error in missing_result["errors"])

    _write_store_jsonl(store_dir, "chunks.jsonl", [parent_chunk, dict(parent_chunk, chunk_id="DOC-1-parent-2")])
    duplicate_result = validate_store(store_dir)
    assert duplicate_result["valid"] is False
    assert duplicate_result["sections_with_multiple_parent_chunks"] == 1
    assert any("multiple section_parent chunks" in error for error in duplicate_result["errors"])
