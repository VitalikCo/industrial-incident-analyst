"""Validate the generated CSB page/section/chunk store before RAG indexing."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE = ROOT / "data/processed/chunk_store"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows = []
    with path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON at {path}:{line_no}: {exc}") from exc
    return rows


def validate_store(store_dir: Path = DEFAULT_STORE) -> dict[str, Any]:
    documents = read_jsonl(store_dir / "documents.jsonl")
    pages = read_jsonl(store_dir / "pages.jsonl")
    chunks = read_jsonl(store_dir / "chunks.jsonl")
    sections = read_jsonl(store_dir / "sections.jsonl")

    errors: list[str] = []
    warnings: list[str] = []
    doc_ids = [row.get("document_id") for row in documents]
    duplicate_docs = [key for key, count in Counter(doc_ids).items() if count > 1]
    if duplicate_docs:
        errors.append(f"Duplicate document IDs: {duplicate_docs[:10]}")

    page_ids = [row.get("page_id") for row in pages]
    duplicate_pages = [key for key, count in Counter(page_ids).items() if count > 1]
    if duplicate_pages:
        errors.append(f"Duplicate page IDs: {duplicate_pages[:10]}")

    chunk_ids = [row.get("chunk_id") for row in chunks]
    duplicate_chunks = [key for key, count in Counter(chunk_ids).items() if count > 1]
    if duplicate_chunks:
        errors.append(f"Duplicate chunk IDs: {duplicate_chunks[:10]}")

    known_docs = set(doc_ids)
    known_pages = {(row.get("document_id"), row.get("page")) for row in pages}
    known_chunks = set(chunk_ids)
    section_id_values = [row.get("section_id") for row in sections]
    missing_section_ids = [index for index, value in enumerate(section_id_values) if not value]
    if missing_section_ids:
        errors.append(f"Sections with missing section_id (row indexes): {missing_section_ids[:10]}")
    duplicate_sections = [
        key for key, count in Counter(value for value in section_id_values if value).items()
        if count > 1
    ]
    if duplicate_sections:
        errors.append(f"Duplicate section IDs: {duplicate_sections[:10]}")

    section_by_id = {row.get("section_id"): row for row in sections if row.get("section_id")}
    section_ids = set(section_by_id)
    for section in sections:
        start, end = section.get("page_start"), section.get("page_end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            errors.append(f"Section {section.get('section_id')} has invalid page range")
        parent_section_id = section.get("parent_section_id")
        if parent_section_id and parent_section_id not in section_ids:
            errors.append(f"Section {section.get('section_id')} references absent parent section {parent_section_id}")
        if start and end:
            missing = [p for p in range(start, end + 1) if (section.get("document_id"), p) not in known_pages]
            if missing:
                errors.append(f"Section {section.get('section_id')} references absent pages: {missing[:5]}")
    retrieval_chunks = 0
    parent_chunks = 0
    section_parent_chunk_counts: Counter[str] = Counter()
    for chunk in chunks:
        doc_id = chunk.get("document_id")
        if doc_id not in known_docs:
            errors.append(f"Chunk {chunk.get('chunk_id')} references unknown document {doc_id}")
        if not chunk.get("text", "").strip():
            errors.append(f"Chunk {chunk.get('chunk_id')} has empty text")
        page_start, page_end = chunk.get("page_start"), chunk.get("page_end")
        if not isinstance(page_start, int) or not isinstance(page_end, int) or page_start < 1 or page_end < page_start:
            errors.append(f"Chunk {chunk.get('chunk_id')} has invalid page range")
        if chunk.get("chunk_type") == "section_parent":
            parent_chunks += 1
            parent_section_chunk_id = chunk.get("section_id")
            if not parent_section_chunk_id:
                errors.append(f"Section parent {chunk.get('chunk_id')} has no section_id")
            else:
                section_parent_chunk_counts[parent_section_chunk_id] += 1
            if chunk.get("retrieval_eligible"):
                errors.append(f"Section parent {chunk.get('chunk_id')} must not be directly indexed")
        elif chunk.get("retrieval_eligible"):
            retrieval_chunks += 1
        source_pages = chunk.get("source_pages") or list(range(page_start, page_end + 1))
        missing_pages = [p for p in source_pages if (doc_id, p) not in known_pages]
        if missing_pages:
            errors.append(f"Chunk {chunk.get('chunk_id')} references absent pages: {missing_pages[:5]}")
        parent_id = chunk.get("parent_chunk_id")
        if parent_id and parent_id not in known_chunks:
            errors.append(f"Chunk {chunk.get('chunk_id')} references absent parent chunk {parent_id}")
        section_id = chunk.get("section_id")
        if section_id and section_id not in section_ids:
            errors.append(f"Chunk {chunk.get('chunk_id')} references absent section {section_id}")
        parent_section_id = chunk.get("parent_section_id")
        if parent_section_id and parent_section_id not in section_ids:
            errors.append(f"Chunk {chunk.get('chunk_id')} references absent parent section {parent_section_id}")
        if set(chunk.get("incident_ids") or []) & set(chunk.get("candidate_incident_ids") or []):
            errors.append(f"Chunk {chunk.get('chunk_id')} mixes explicit and candidate incident IDs")
        if len(chunk.get("text", "")) > 16000 and chunk.get("retrieval_eligible") and chunk.get("chunk_type") != "table":
            warnings.append(f"Oversized retrieval chunk: {chunk.get('chunk_id')} ({len(chunk.get('text', ''))} chars)")

    sections_without_parent_chunks = [
        section_id for section_id in sorted(section_ids)
        if section_parent_chunk_counts[section_id] == 0
    ]
    sections_with_multiple_parent_chunks = [
        section_id for section_id, count in section_parent_chunk_counts.items() if count > 1
    ]
    if sections_without_parent_chunks:
        errors.append(
            "Sections without a section_parent chunk: "
            f"{sections_without_parent_chunks[:10]}"
        )
    if sections_with_multiple_parent_chunks:
        errors.append(
            "Sections with multiple section_parent chunks: "
            f"{sections_with_multiple_parent_chunks[:10]}"
        )

    summary = {
        "documents": len(documents), "pages": len(pages), "chunks_total": len(chunks),
        "retrieval_chunks": retrieval_chunks, "section_parent_chunks": parent_chunks,
        "sections": len(sections),
        "sections_without_parent_chunks": len(sections_without_parent_chunks),
        "sections_with_multiple_parent_chunks": len(sections_with_multiple_parent_chunks),
        "duplicate_section_ids": len(duplicate_sections),
        "errors": errors, "warnings": warnings,
        "valid": not errors,
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-dir", type=Path, default=DEFAULT_STORE)
    args = parser.parse_args()
    result = validate_store(args.store_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
