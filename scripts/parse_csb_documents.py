"""Parse CSB PDFs into page records, structural sections, and RAG-ready chunks.

Default reader: PyMuPDF text layer + table detection. Optional OCR fallback uses the
Tesseract CLI only on pages with too little native text. Source PDFs are read-only.

Run from the project root:
    uv run python scripts/parse_csb_documents.py
    uv run python scripts/parse_csb_documents.py --ocr-fallback tesseract
    uv run python scripts/parse_csb_documents.py --document-id CSB-6203 --limit-pages 5 --output-dir /tmp/csb-parse-smoke
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

# The parser deliberately uses PyMuPDF's block/table APIs rather than its optional
# layout extension. Suppress the one-time install suggestion so CLI stdout stays JSON.
os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")
import fitz

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data/processed/document_catalog.jsonl"
DEFAULT_OUTPUT = ROOT / "data/processed/chunk_store"
DEFAULT_MAX_CHARS = 4200
DEFAULT_OVERLAP_CHARS = 450
MIN_NATIVE_TEXT_CHARS = 80

# Common CSB report headings. These anchor rules help when the PDF text layer has
# flattened typography and font-size information is not reliable.
TOP_LEVEL_HEADINGS = {
    "executive summary", "summary", "introduction", "background", "incident description",
    "incident overview", "investigation", "analysis", "technical analysis", "findings",
    "causes", "contributing factors", "root causes", "root cause", "key lessons",
    "lessons learned", "safety issues", "recommendations", "safety recommendations",
    "conclusion", "appendix", "appendices", "references", "acknowledgements",
    "board determination", "board approval", "purpose", "scope",
}
SUBSECTION_HEADINGS = {
    "equipment involved", "process description", "facility background", "regulatory analysis",
    "emergency response", "incident response", "safety management systems", "management of change",
    "mechanical integrity", "hazard analysis", "training", "procedures", "contractor safety",
}

PAGE_NUMBER_RE = re.compile(r"^(?:page\s+)?\d+(?:\s+of\s+\d+)?$", re.I)
PAGE_FOOTER_RE = re.compile(r"^(?:page\s+)?\d+\s*(?:of|/)\s*\d+$", re.I)
NUMBERED_HEADING_RE = re.compile(r"^(?P<number>\d{1,2}(?:\.\d{1,2}){0,4})\.?\s+(?P<title>\S.*?)\s*$")
APPENDIX_HEADING_RE = re.compile(r"^(?:appendix|appendices)\s+[A-Z0-9][A-Z0-9.-]*(?:\s*[:—-].*)?$", re.I)
TABLE_CAPTION_RE = re.compile(r"^(?:table|figure|exhibit)\s+[A-Z0-9][A-Z0-9.-]*\s*[:.—–-]?\s*.*$", re.I)
TOC_LEADER_RE = re.compile(r"(?:\.{3,}|_{3,}|·{3,}|…{2,})\s*\d{1,3}\s*$")
NUMERIC_TOKEN_RE = re.compile(r"(?<!\w)(?:\d{1,3}(?:,\d{3})+|\d+(?:[.,]\d+)?)(?:\s*(?:%|ppm|ppb|°[CF]|mm/s|m/s|psi|bar|kPa|MPa|°C|°F))?", re.I)
REPLACEMENT_CHAR = "\ufffd"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    temp.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def project_path(value: str | None) -> Path | None:
    if not value:
        return None
    candidate = (ROOT / value).resolve()
    if ROOT.resolve() not in candidate.parents:
        return None
    return candidate


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_running_line(text: str) -> str:
    value = compact(text).casefold()
    value = re.sub(r"\bpage\s+\d+(?:\s+of\s+\d+)?\b", "page #", value)
    value = re.sub(r"\b\d+\b", "#", value)
    return value


def _table_to_markdown(rows: list[list[Any]]) -> str:
    cleaned = [[compact("" if cell is None else str(cell)).replace("|", "\\|") for cell in row] for row in rows]
    cleaned = [row for row in cleaned if any(cell for cell in row)]
    if not cleaned:
        return ""
    width = max(len(row) for row in cleaned)
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]
    header = cleaned[0]
    separator = ["---"] * width
    body = cleaned[1:] if len(cleaned) > 1 else []
    return "\n".join([
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
        *["| " + " | ".join(row) + " |" for row in body],
    ])


def extract_page_blocks(page: fitz.Page, *, extract_tables: bool = True) -> list[dict[str, Any]]:
    """Return reading-order text/table blocks with PDF-point bounding boxes."""
    payload = page.get_text("dict", sort=True)
    blocks: list[dict[str, Any]] = []
    for raw in payload.get("blocks", []):
        if raw.get("type") != 0:
            continue
        line_texts: list[str] = []
        sizes: list[float] = []
        bold = False
        fonts: list[str] = []
        for line in raw.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(span.get("text", "") for span in spans).strip()
            if text:
                line_texts.append(text)
            for span in spans:
                if span.get("text", "").strip():
                    sizes.append(float(span.get("size") or 0))
                    font = str(span.get("font") or "")
                    fonts.append(font)
                    bold = bold or "bold" in font.casefold() or "black" in font.casefold()
        text = "\n".join(line_texts).strip()
        if not text:
            continue
        bbox = [round(float(v), 1) for v in raw.get("bbox", [0, 0, 0, 0])]
        blocks.append({
            "kind": "text", "text": text, "bbox": bbox,
            "font_size": round(max(sizes), 1) if sizes else 0.0,
            "font_bold": bold, "fonts": sorted(set(fonts)),
            "sort_y": bbox[1], "sort_x": bbox[0],
        })

    if extract_tables:
        try:
            found = page.find_tables()
            tables = list(getattr(found, "tables", []) or [])
        except Exception:
            tables = []
        table_boxes: list[fitz.Rect] = []
        table_blocks: list[dict[str, Any]] = []
        for table_index, table in enumerate(tables, 1):
            try:
                bbox = [round(float(v), 1) for v in table.bbox]
                rows = table.extract()
                markdown = _table_to_markdown(rows)
            except Exception:
                continue
            if not markdown:
                continue
            rect = fitz.Rect(bbox)
            table_boxes.append(rect)
            caption = None
            caption_block = None
            # Captions normally sit directly above the table. Capture the closest one
            # and remove it from the paragraph stream to avoid duplicated text.
            above = [
                block for block in blocks
                if block["bbox"][3] <= rect.y0 + 2
                and rect.y0 - block["bbox"][3] <= 90
                and TABLE_CAPTION_RE.match(compact(block["text"]))
            ]
            if above:
                caption_block = max(above, key=lambda b: b["bbox"][3])
                caption = caption_block["text"]
            table_text = f"{caption}\n\n{markdown}" if caption else markdown
            table_blocks.append({
                "kind": "table", "text": table_text, "bbox": bbox,
                "font_size": 0.0, "font_bold": False, "fonts": [],
                "sort_y": bbox[1], "sort_x": bbox[0], "table_index": table_index,
                "table_caption": caption, "table_rows": rows,
            })
        if table_boxes:
            filtered: list[dict[str, Any]] = []
            caption_texts = {b.get("table_caption") for b in table_blocks if b.get("table_caption")}
            for block in blocks:
                rect = fitz.Rect(block["bbox"])
                if any(rect.intersects(table_rect) for table_rect in table_boxes):
                    # Keep a caption outside the table bbox; the paragraph copy would be a duplicate.
                    if compact(block["text"]) in {compact(c) for c in caption_texts}:
                        continue
                    continue
                if compact(block["text"]) in {compact(c) for c in caption_texts}:
                    continue
                filtered.append(block)
            blocks = filtered + table_blocks

    blocks.sort(key=lambda b: (b["sort_y"], b["sort_x"]))
    return blocks


def detect_repeated_running_lines(pages: list[list[dict[str, Any]]], page_heights: list[float]) -> set[str]:
    """Find short repeated lines in the top/bottom page margins (running headers/footers)."""
    counts: Counter[str] = Counter()
    page_count = len(pages)
    if page_count < 4:
        return set()
    for page_blocks, height in zip(pages, page_heights):
        seen: set[str] = set()
        for block in page_blocks:
            text = compact(block["text"])
            if not text or len(text) > 150:
                continue
            y0, y1 = block["bbox"][1], block["bbox"][3]
            if y0 <= height * 0.075 or y1 >= height * 0.925:
                key = normalize_running_line(text)
                if len(key) >= 4 and not PAGE_NUMBER_RE.match(text):
                    seen.add(key)
        counts.update(seen)
    threshold = max(3, math.ceil(page_count * 0.28))
    return {line for line, count in counts.items() if count >= threshold}


def is_page_number(text: str) -> bool:
    return bool(PAGE_NUMBER_RE.fullmatch(compact(text)) or PAGE_FOOTER_RE.fullmatch(compact(text)))


def is_table_of_contents_page(blocks: list[dict[str, Any]]) -> bool:
    texts = [compact(block.get("text", "")) for block in blocks if compact(block.get("text", ""))]
    if not texts:
        return False
    if any(text.casefold() in {"contents", "table of contents", "contents of report"} for text in texts):
        return True
    leader_lines = sum(bool(TOC_LEADER_RE.search(text)) for text in texts)
    return leader_lines >= 3 and leader_lines / max(len(texts), 1) >= 0.18


def _heading_keyword(text: str) -> str | None:
    normalized = compact(text).strip(" :—–-.").casefold()
    if normalized in TOP_LEVEL_HEADINGS:
        return "top"
    if normalized in SUBSECTION_HEADINGS:
        return "sub"
    if APPENDIX_HEADING_RE.fullmatch(normalized):
        return "top"
    return None


def classify_heading(block: dict[str, Any], body_font_size: float) -> dict[str, Any] | None:
    """Detect a heading from numbering, known report labels, or typography."""
    text = compact(block.get("text", ""))
    if not text or len(text) > 170 or len(text.split()) > 22:
        return None
    if is_page_number(text) or TABLE_CAPTION_RE.match(text) or TOC_LEADER_RE.search(text):
        return None
    numeric = NUMBERED_HEADING_RE.match(text)
    if numeric:
        number, title = numeric.group("number"), numeric.group("title").strip()
        if len(title) <= 120 and len(title.split()) <= 18 and not re.search(r"[.!?]$", title):
            parts = number.split(".")
            level = len(parts)
            if len(parts) > 1 and all(part == "0" for part in parts[1:]):
                level = 1
            return {"number": number, "title": title, "level": min(level, 6), "basis": "numbered_heading"}
    keyword = _heading_keyword(text)
    if keyword:
        return {"number": None, "title": text, "level": 1 if keyword == "top" else 2, "basis": "known_heading"}
    words = text.split()
    style_heading = (
        block.get("font_bold")
        and block.get("font_size", 0) >= max(9.5, body_font_size * 1.18)
        and len(words) <= 14
        and len(text) <= 105
        and not re.search(r"[.!?;,]$", text)
    )
    all_caps_heading = (
        len(words) <= 10 and len(text) <= 90 and text.upper() == text
        and any(ch.isalpha() for ch in text)
        and not re.search(r"[.!?]$", text)
    )
    if style_heading or all_caps_heading:
        return {"number": None, "title": text, "level": 2, "basis": "typography"}
    return None


def _section_id(document_id: str, path: list[str], start_page: int, occurrence: int) -> str:
    """Return a stable ID for this heading occurrence within one document.

    The same heading path can legitimately occur more than once on one page. Including
    the one-based heading occurrence prevents those distinct sections from collapsing
    into the same aggregate/parent chunk.
    """
    if occurrence < 1:
        raise ValueError("Section occurrence must be a positive integer")
    raw = f"{document_id}|{start_page}|{occurrence}|{'|'.join(path)}"
    suffix = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{document_id}-s-{suffix}"


def _chunk_id(document_id: str, page_start: int, page_end: int, section_id: str | None,
              chunk_type: str, text: str, ordinal: int) -> str:
    digest = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"{document_id}-p{page_start:04d}-{page_end:04d}-{chunk_type}-{ordinal:03d}-{digest}"


def split_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Split long text near paragraph/sentence boundaries with a bounded character overlap."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        hard_end = min(len(text), start + max_chars)
        end = hard_end
        if hard_end < len(text):
            window_start = start + int(max_chars * 0.62)
            candidates = [
                text.rfind("\n\n", window_start, hard_end),
                text.rfind("\n", window_start, hard_end),
                text.rfind(". ", window_start, hard_end),
                text.rfind("; ", window_start, hard_end),
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + (2 if text[boundary:boundary + 2] in {"\n\n", ". ", "; "} else 1)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
        # Never let overlap restart inside a long uninterrupted token.
        while start < end and not text[start].isspace() and start - (end - overlap_chars) < 80:
            start += 1
    return chunks


def ocr_page_tesseract(page: fitz.Page, *, language: str = "eng", dpi: int = 300,
                       timeout_s: int = 180) -> tuple[str, str]:
    executable = shutil.which("tesseract")
    if not executable:
        raise RuntimeError("Tesseract was requested but the 'tesseract' executable is not installed.")
    with tempfile.TemporaryDirectory(prefix="csb-ocr-") as tmp:
        image_path = Path(tmp) / "page.png"
        scale = dpi / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        pix.save(image_path)
        result = subprocess.run(
            [executable, str(image_path), "stdout", "-l", language, "--psm", "3"],
            check=False, capture_output=True, text=True, timeout=timeout_s,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Tesseract failed (exit {result.returncode}): {detail[:500]}")
        version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
        return result.stdout.strip(), compact(version.stdout.splitlines()[0] if version.stdout else "tesseract")


def _base_metadata(document: dict[str, Any], source_pdf: Path, parse_pdf: Path) -> dict[str, Any]:
    return {
        "document_id": document.get("document_id"),
        "title": document.get("title"),
        "csb_document_id": document.get("csb_document_id"),
        "document_type": document.get("document_type"),
        "corpus_role": document.get("corpus_role"),
        "investigation_id": document.get("investigation_id"),
        "candidate_investigation_id": document.get("candidate_investigation_id"),
        "recommendation_match_key": document.get("recommendation_match_key"),
        "candidate_recommendation_match_key": document.get("candidate_recommendation_match_key"),
        "incident_ids": document.get("incident_ids") or [],
        "candidate_incident_ids": document.get("candidate_incident_ids") or [],
        "source_case_id": document.get("source_case_id"),
        "source_path": document.get("local_path") or str(source_pdf.relative_to(ROOT)),
        "parse_source_path": str(parse_pdf.relative_to(ROOT)),
        "source_sha256": document.get("source_sha256"),
        "text_source_sha256": document.get("text_source_sha256"),
        "language": document.get("language") or "en",
        "classification_status": document.get("classification_status"),
        "classification_method": document.get("classification_method"),
        "classification_confidence": document.get("classification_confidence"),
        "mapping_status": document.get("mapping_status"),
        "recommendation_ids": document.get("recommendation_ids") or [],
    }


def parse_document(
    document: dict[str, Any],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    extract_tables: bool = True,
    ocr_fallback: str = "none",
    ocr_min_chars: int = MIN_NATIVE_TEXT_CHARS,
    tesseract_language: str = "eng",
    limit_pages: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Parse one catalog record into document metadata, pages, chunks and sections."""
    if max_chars < 500:
        raise ValueError("max_chars must be at least 500")
    if not 0 <= overlap_chars < max_chars:
        raise ValueError("overlap_chars must be >= 0 and smaller than max_chars")
    if limit_pages is not None and limit_pages < 1:
        raise ValueError("limit_pages must be >= 1 when provided")
    if ocr_min_chars < 0:
        raise ValueError("ocr_min_chars must be >= 0")
    source_pdf = project_path(document.get("local_path"))
    parse_pdf = project_path(document.get("text_source_path")) or source_pdf
    doc_id = str(document.get("document_id") or "UNKNOWN")
    if parse_pdf is None or not parse_pdf.is_file():
        raise FileNotFoundError(f"No readable PDF/text source for {doc_id}: {document.get('text_source_path') or document.get('local_path')}")
    if source_pdf is None:
        source_pdf = parse_pdf

    parser_version = f"PyMuPDF {fitz.VersionBind}"
    page_records_raw: list[dict[str, Any]] = []
    page_blocks_all: list[list[dict[str, Any]]] = []
    page_heights: list[float] = []
    ocr_used_pages: list[int] = []
    page_seconds: list[float] = []
    parse_started = time.perf_counter()

    with fitz.open(parse_pdf) as pdf:
        page_count_total = len(pdf)
        page_indexes = range(min(page_count_total, limit_pages)) if limit_pages else range(page_count_total)
        for index in page_indexes:
            page_no = index + 1
            page = pdf[index]
            t0 = time.perf_counter()
            blocks = extract_page_blocks(page, extract_tables=extract_tables)
            native_text = "\n\n".join(b["text"] for b in blocks).strip()
            reader = "pymupdf"
            page_parser_version = parser_version
            if ocr_fallback == "tesseract" and len(compact(native_text)) < ocr_min_chars:
                ocr_text, ocr_version = ocr_page_tesseract(page, language=tesseract_language)
                if len(compact(ocr_text)) > len(compact(native_text)):
                    blocks = [{
                        "kind": "text", "text": ocr_text, "bbox": [0, 0, page.rect.width, page.rect.height],
                        "font_size": 0.0, "font_bold": False, "fonts": [],
                        "sort_y": 0.0, "sort_x": 0.0,
                    }]
                    native_text = ocr_text
                    reader = "tesseract"
                    page_parser_version = ocr_version
                    ocr_used_pages.append(page_no)
            page_blocks_all.append(blocks)
            page_heights.append(float(page.rect.height))
            page_seconds.append(time.perf_counter() - t0)
            page_records_raw.append({
                "page": page_no, "native_text": native_text,
                "width_pt": round(float(page.rect.width), 1),
                "height_pt": round(float(page.rect.height), 1),
                "reader": reader, "parser_version": page_parser_version,
            })

    repeated_lines = detect_repeated_running_lines(page_blocks_all, page_heights)

    # Remove repeated running headers/footers with the correct page geometry.
    clean_pages: list[list[dict[str, Any]]] = []
    for blocks, height in zip(page_blocks_all, page_heights):
        filtered = []
        for block in blocks:
            text = compact(block["text"])
            if not text or is_page_number(text):
                continue
            key = normalize_running_line(text)
            if key in repeated_lines and (block["bbox"][1] <= height * 0.075 or block["bbox"][3] >= height * 0.925):
                continue
            filtered.append(block)
        clean_pages.append(filtered)

    # Estimate body font size from normal paragraphs; headings are then detected against it.
    body_sizes = [
        b["font_size"] for blocks in clean_pages for b in blocks
        if b["kind"] == "text" and b["font_size"] >= 6 and len(compact(b["text"]).split()) >= 12
    ]
    body_font_size = median(body_sizes) if body_sizes else 10.0

    base = _base_metadata(document, source_pdf, parse_pdf)
    page_records: list[dict[str, Any]] = []
    chunk_records: list[dict[str, Any]] = []
    section_records: list[dict[str, Any]] = []
    section_state: list[dict[str, Any]] = []
    section_aggregates: dict[str, dict[str, Any]] = {}
    section_records_by_id: dict[str, dict[str, Any]] = {}
    current_section_id: str | None = None
    current_section_path: list[str] = []
    section_occurrence = 0
    table_counter_by_page: Counter[int] = Counter()
    paragraph_counter_by_page: Counter[int] = Counter()
    page_quality_rows: list[dict[str, Any]] = []

    def update_open_section_ranges(page_no: int) -> None:
        """Extend page ranges for the current section and all of its ancestors."""
        for state in section_state:
            section_id = state.get("section_id")
            if not section_id:
                continue
            if section_id in section_aggregates:
                section_aggregates[section_id]["page_end"] = page_no
            if section_id in section_records_by_id:
                section_records_by_id[section_id]["page_end"] = page_no

    for raw, blocks, seconds in zip(page_records_raw, clean_pages, page_seconds):
        page_no = raw["page"]
        page_text_blocks: list[str] = []
        page_headings: list[str] = []
        page_chunk_candidates: list[dict[str, Any]] = []
        toc_page = is_table_of_contents_page(blocks)
        paragraph_buffer: list[str] = []
        paragraph_boxes: list[list[float]] = []

        def flush_paragraph_buffer() -> None:
            nonlocal paragraph_buffer, paragraph_boxes
            buffered_text = "\n\n".join(part for part in paragraph_buffer if part).strip()
            if not buffered_text:
                paragraph_buffer, paragraph_boxes = [], []
                return
            paragraph_counter_by_page[page_no] += 1
            ordinal = paragraph_counter_by_page[page_no]
            bbox = None
            if paragraph_boxes:
                bbox = [
                    round(min(box[0] for box in paragraph_boxes), 1),
                    round(min(box[1] for box in paragraph_boxes), 1),
                    round(max(box[2] for box in paragraph_boxes), 1),
                    round(max(box[3] for box in paragraph_boxes), 1),
                ]
            for part_index, part in enumerate(split_text(buffered_text, max_chars, overlap_chars), 1):
                cid = _chunk_id(doc_id, page_no, page_no, current_section_id, "text", part, ordinal * 100 + part_index)
                page_chunk_candidates.append({
                    **base, "chunk_id": cid, "chunk_type": "text", "retrieval_eligible": True,
                    "parent_section_id": current_section_id, "parent_chunk_id": None,
                    "section_id": current_section_id, "section_path": current_section_path.copy(),
                    "page_start": page_no, "page_end": page_no, "source_pages": [page_no],
                    "text": part, "paragraph_index": ordinal, "part_index": part_index,
                    "bbox_pt": bbox, "parser": "pymupdf",
                    "parser_version": parser_version,
                })
            paragraph_buffer, paragraph_boxes = [], []

        for block in blocks:
            text = block["text"].strip()
            if not text:
                continue
            # Keep table-of-contents pages for provenance, but never attach their lines
            # to a real report section or put them into the retrieval index.
            if toc_page:
                page_text_blocks.append(text)
                paragraph_buffer.append(text)
                paragraph_boxes.append(block.get("bbox") or [0, 0, 0, 0])
                continue

            heading = classify_heading(block, body_font_size) if block["kind"] == "text" else None
            if heading:
                flush_paragraph_buffer()
                section_occurrence += 1
                level = heading["level"]
                section_state[:] = section_state[:max(level - 1, 0)]
                section_state.append({
                    "title": heading["title"], "number": heading["number"],
                    "level": level, "page_start": page_no, "basis": heading["basis"],
                    "section_id": None,
                })
                current_section_path = [item["title"] for item in section_state]
                current_section_id = _section_id(
                    doc_id, current_section_path, page_no, section_occurrence
                )
                section_state[-1]["section_id"] = current_section_id
                parent_section_id = section_state[-2]["section_id"] if len(section_state) > 1 else None
                section_record = {
                    **base,
                    "section_id": current_section_id,
                    "parent_section_id": parent_section_id,
                    "heading": heading["title"], "heading_number": heading["number"],
                    "level": level, "section_path": current_section_path.copy(),
                    "page_start": page_no, "page_end": page_no,
                    "detection_basis": heading["basis"],
                }
                section_records.append(section_record)
                section_records_by_id[current_section_id] = section_record
                section_aggregates[current_section_id] = {
                    "section_id": current_section_id, "heading": heading["title"],
                    "section_path": current_section_path.copy(), "parent_section_id": parent_section_id,
                    "page_start": page_no, "page_end": page_no, "text_parts": [heading["title"]],
                }
                update_open_section_ranges(page_no)
                page_headings.append(heading["title"])
                page_text_blocks.append(text)
                continue

            section_id = current_section_id
            section_path = current_section_path.copy()
            page_text_blocks.append(text)
            if section_id and section_id in section_aggregates:
                section_aggregates[section_id]["text_parts"].append(text)
            update_open_section_ranges(page_no)

            if block["kind"] == "table":
                flush_paragraph_buffer()
                table_counter_by_page[page_no] += 1
                table_text = text
                cid = _chunk_id(doc_id, page_no, page_no, section_id, "table", table_text, table_counter_by_page[page_no])
                page_chunk_candidates.append({
                    **base, "chunk_id": cid, "chunk_type": "table", "retrieval_eligible": True,
                    "parent_section_id": section_id, "parent_chunk_id": None,
                    "section_id": section_id, "section_path": section_path,
                    "page_start": page_no, "page_end": page_no, "source_pages": [page_no],
                    "text": table_text, "table_index": block.get("table_index"),
                    "table_caption": block.get("table_caption"), "table_rows": block.get("table_rows"),
                    "bbox_pt": block.get("bbox"), "parser": "pymupdf+tables",
                    "parser_version": parser_version,
                })
                continue

            paragraph_buffer.append(text)
            paragraph_boxes.append(block.get("bbox") or [0, 0, 0, 0])
            if sum(len(part) for part in paragraph_buffer) >= max_chars:
                flush_paragraph_buffer()

        if toc_page:
            toc_text = "\n\n".join(paragraph_buffer).strip()
            if toc_text:
                cid = _chunk_id(doc_id, page_no, page_no, None, "table_of_contents", toc_text, 1)
                page_chunk_candidates.append({
                    **base, "chunk_id": cid, "chunk_type": "table_of_contents", "retrieval_eligible": False,
                    "parent_section_id": None, "parent_chunk_id": None,
                    "section_id": None, "section_path": [],
                    "page_start": page_no, "page_end": page_no, "source_pages": [page_no],
                    "text": toc_text, "parser": "pymupdf", "parser_version": parser_version,
                })
            paragraph_buffer, paragraph_boxes = [], []
        else:
            flush_paragraph_buffer()
        # Add the page's ordered content and chunks after heading updates.
        page_text = "\n\n".join(page_text_blocks).strip()
        page_record = {
            **base, "page_id": f"{doc_id}-p{page_no:04d}", "page": page_no,
            "width_pt": raw["width_pt"], "height_pt": raw["height_pt"],
            "text": page_text, "text_chars": len(page_text),
            "word_count": len(page_text.split()), "block_count": len(blocks),
            "table_count": sum(1 for b in blocks if b["kind"] == "table"),
            "heading_count": len(page_headings), "headings": page_headings,
            "numeric_token_count": len(NUMERIC_TOKEN_RE.findall(page_text)),
            "replacement_char_count": page_text.count(REPLACEMENT_CHAR),
            "low_text": len(page_text) < 100, "empty_text": not bool(page_text),
            "page_kind": "table_of_contents" if toc_page else "content",
            "section_path_end": current_section_path.copy(),
            "reader": raw["reader"], "parser_version": raw["parser_version"],
            "parse_seconds": round(seconds, 4),
        }
        page_records.append(page_record)
        chunk_records.extend(page_chunk_candidates)
        numeric_count = len(NUMERIC_TOKEN_RE.findall(page_text))
        replacement_count = page_text.count(REPLACEMENT_CHAR)
        low_text = len(page_text) < 100
        page_quality_rows.append({
            "document_id": doc_id, "page": page_no, "text_chars": len(page_text),
            "word_count": len(page_text.split()), "block_count": len(blocks),
            "table_count": page_record["table_count"], "heading_count": len(page_headings),
            "numeric_token_count": numeric_count, "replacement_char_count": replacement_count,
            "page_kind": "table_of_contents" if toc_page else "content",
            "low_text": low_text, "empty_text": not bool(page_text),
            "reader": raw["reader"], "parse_seconds": round(seconds, 4),
        })

    # Section parents store whole section context; child text/table chunks reference them.
    parent_chunk_by_section: dict[str, str] = {}
    for ordinal, (section_id, section) in enumerate(section_aggregates.items(), 1):
        parent_text = "\n\n".join(section["text_parts"]).strip()
        parent_id = _chunk_id(doc_id, section["page_start"], section["page_end"], section_id, "section", parent_text, ordinal)
        parent_chunk_by_section[section_id] = parent_id
        chunk_records.append({
            **base, "chunk_id": parent_id, "chunk_type": "section_parent", "retrieval_eligible": False,
            "parent_section_id": section.get("parent_section_id"), "parent_chunk_id": None,
            "section_id": section_id, "section_path": section["section_path"],
            "page_start": section["page_start"], "page_end": section["page_end"],
            "source_pages": list(range(section["page_start"], section["page_end"] + 1)),
            "text": parent_text, "parser": "pymupdf", "parser_version": parser_version,
        })
    for chunk in chunk_records:
        section_id = chunk.get("section_id")
        if section_id and chunk.get("chunk_type") != "section_parent":
            chunk["parent_chunk_id"] = parent_chunk_by_section.get(section_id)

    page_count_processed = len(page_records)
    total_chars = sum(row["text_chars"] for row in page_records)
    empty_pages = sum(not row["text"] for row in page_records)
    low_pages = sum(row["text_chars"] < 100 for row in page_records)
    table_count = sum(row["table_count"] for row in page_records)
    heading_count = sum(row["heading_count"] for row in page_records)
    text_coverage = (page_count_processed - empty_pages) / page_count_processed if page_count_processed else 0.0
    flags: list[str] = []
    if empty_pages:
        flags.append("pages_without_extractable_text")
    if low_pages / max(page_count_processed, 1) > 0.15:
        flags.append("many_low_text_pages")
    if any(row["replacement_char_count"] for row in page_quality_rows):
        flags.append("replacement_characters_present")
    if table_count == 0:
        flags.append("no_tables_detected")
    if heading_count == 0:
        flags.append("no_section_headings_detected")

    doc_quality = {
        "document_id": doc_id,
        "page_count_source": page_count_total,
        "page_count_parsed": page_count_processed,
        "total_text_chars": total_chars,
        "pages_with_text": page_count_processed - empty_pages,
        "empty_pages": empty_pages,
        "low_text_pages": low_pages,
        "text_coverage": round(text_coverage, 4),
        "table_count": table_count,
        "section_count": len(section_records),
        "chunk_count": sum(1 for c in chunk_records if c["retrieval_eligible"]),
        "parent_section_chunk_count": len(section_aggregates),
        "mean_chars_per_page": round(total_chars / max(page_count_processed, 1), 1),
        "mean_chars_per_retrieval_chunk": round(
            sum(len(c["text"]) for c in chunk_records if c["retrieval_eligible"])
            / max(sum(1 for c in chunk_records if c["retrieval_eligible"]), 1), 1
        ),
        "ocr_pages_used": ocr_used_pages,
        "repeated_running_line_patterns_removed": len(repeated_lines),
        "quality_flags": flags,
        "quality_status": "review_needed" if empty_pages or "many_low_text_pages" in flags or "replacement_characters_present" in flags else "parsed",
        "parse_seconds": round(time.perf_counter() - parse_started, 3),
        "reader": "pymupdf+tables" if extract_tables else "pymupdf",
        "parser_version": parser_version,
    }
    document_record = {
        **base,
        "page_count_source": page_count_total,
        "page_count_parsed": page_count_processed,
        "source_sha256_actual": sha256_file(source_pdf) if source_pdf.is_file() else None,
        "parse_source_sha256_actual": sha256_file(parse_pdf),
        "parser": doc_quality["reader"], "parser_version": parser_version,
        "ocr_pages_used": ocr_used_pages,
        "parse_quality": doc_quality,
    }
    return document_record, page_records, chunk_records, section_records, doc_quality


def select_documents(catalog: list[dict[str, Any]], document_ids: set[str] | None = None,
                     include_non_retrieval: bool = False) -> list[dict[str, Any]]:
    selected = []
    for row in catalog:
        if document_ids and row.get("document_id") not in document_ids:
            continue
        if not include_non_retrieval and not row.get("retrieval_enabled"):
            continue
        language = str(row.get("language") or "en").casefold()
        if language not in {"en", "english", "none", ""}:
            continue
        selected.append(row)
    return sorted(selected, key=lambda row: (row.get("investigation_id") or row.get("candidate_investigation_id") or "", row.get("document_id") or ""))


def parse_catalog(
    catalog_path: Path = CATALOG,
    output_dir: Path = DEFAULT_OUTPUT,
    *,
    document_ids: set[str] | None = None,
    limit_documents: int | None = None,
    limit_pages: int | None = None,
    include_non_retrieval: bool = False,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    extract_tables: bool = True,
    ocr_fallback: str = "none",
    ocr_min_chars: int = MIN_NATIVE_TEXT_CHARS,
    tesseract_language: str = "eng",
) -> dict[str, Any]:
    """Parse selected documents while streaming corpus outputs to keep memory bounded.

    Only one PDF's page/block/chunk objects are held in memory at a time. Output files
    are staged under the destination folder and replaced only after the parse loop ends.
    """
    catalog = read_jsonl(catalog_path)
    selected = select_documents(catalog, document_ids, include_non_retrieval)
    if limit_documents is not None:
        if limit_documents < 1:
            raise ValueError("limit_documents must be >= 1 when provided")
        selected = selected[:limit_documents]
    if limit_pages is not None and limit_pages < 1:
        raise ValueError("limit_pages must be >= 1 when provided")
    if max_chars < 500:
        raise ValueError("max_chars must be at least 500")
    if not 0 <= overlap_chars < max_chars:
        raise ValueError("overlap_chars must be >= 0 and smaller than max_chars")
    if ocr_min_chars < 0:
        raise ValueError("ocr_min_chars must be >= 0")

    output_dir.mkdir(parents=True, exist_ok=True)
    all_errors: list[dict[str, str]] = []
    quality_rows: list[dict[str, Any]] = []
    parsed_documents = 0
    skipped_missing = 0
    failed_documents = 0
    page_count = 0
    retrieval_chunk_count = 0
    table_chunk_count = 0
    parent_chunk_count = 0
    section_record_count = 0
    total_text_chars = 0
    pages_with_text = 0
    empty_pages = 0
    low_text_pages = 0
    replacement_pages = 0
    numeric_tokens = 0
    tables_detected = 0
    headings_detected = 0
    page_reader_counts: Counter[str] = Counter()

    jsonl_names = ("documents.jsonl", "pages.jsonl", "chunks.jsonl", "sections.jsonl")
    page_fields = [
        "document_id", "page", "text_chars", "word_count", "block_count", "table_count",
        "heading_count", "numeric_token_count", "replacement_char_count", "low_text",
        "empty_text", "page_kind", "reader", "parser_version", "parse_seconds",
    ]
    document_quality_fields = [
        "document_id", "page_count_source", "page_count_parsed", "total_text_chars", "pages_with_text",
        "empty_pages", "low_text_pages", "text_coverage", "table_count", "section_count",
        "chunk_count", "parent_section_chunk_count", "mean_chars_per_page",
        "mean_chars_per_retrieval_chunk", "ocr_pages_used", "repeated_running_line_patterns_removed",
        "quality_flags", "quality_status", "parse_seconds", "reader", "parser_version",
    ]

    with tempfile.TemporaryDirectory(prefix=".parse-stage-", dir=output_dir) as stage_dir:
        stage = Path(stage_dir)
        with ExitStack() as stack:
            jsonl_streams = {
                name: stack.enter_context((stage / name).open("w", encoding="utf-8"))
                for name in jsonl_names
            }
            page_csv_stream = stack.enter_context((stage / "page_quality.csv").open("w", encoding="utf-8-sig", newline=""))
            page_writer = csv.DictWriter(page_csv_stream, fieldnames=page_fields, extrasaction="ignore")
            page_writer.writeheader()

            for index, row in enumerate(selected, 1):
                source = project_path(row.get("local_path"))
                parse_source = project_path(row.get("text_source_path")) or source
                if parse_source is None or not parse_source.is_file():
                    skipped_missing += 1
                    all_errors.append({"document_id": row.get("document_id", ""), "error": "local_text_source_missing"})
                    continue
                print(f"[{index}/{len(selected)}] {row.get('document_id')} | {row.get('title')}", file=sys.stderr)
                try:
                    doc, pages, chunks, sections, quality = parse_document(
                        row, max_chars=max_chars, overlap_chars=overlap_chars,
                        extract_tables=extract_tables, ocr_fallback=ocr_fallback,
                        ocr_min_chars=ocr_min_chars, tesseract_language=tesseract_language,
                        limit_pages=limit_pages,
                    )
                except Exception as exc:
                    failed_documents += 1
                    all_errors.append({
                        "document_id": row.get("document_id", ""),
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    continue

                parsed_documents += 1
                for name, records in (
                    ("documents.jsonl", [doc]), ("pages.jsonl", pages),
                    ("chunks.jsonl", chunks), ("sections.jsonl", sections),
                ):
                    stream = jsonl_streams[name]
                    for record in records:
                        stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

                page_writer.writerows(pages)
                quality_rows.append(quality)
                page_count += len(pages)
                retrieval_chunk_count += sum(1 for chunk in chunks if chunk.get("retrieval_eligible"))
                table_chunk_count += sum(1 for chunk in chunks if chunk.get("chunk_type") == "table")
                parent_chunk_count += sum(1 for chunk in chunks if chunk.get("chunk_type") == "section_parent")
                section_record_count += len(sections)
                total_text_chars += sum(page.get("text_chars", 0) for page in pages)
                pages_with_text += sum(not page.get("empty_text") for page in pages)
                empty_pages += sum(bool(page.get("empty_text")) for page in pages)
                low_text_pages += sum(bool(page.get("low_text")) for page in pages)
                replacement_pages += sum(bool(page.get("replacement_char_count")) for page in pages)
                numeric_tokens += sum(page.get("numeric_token_count", 0) for page in pages)
                tables_detected += sum(page.get("table_count", 0) for page in pages)
                headings_detected += sum(page.get("heading_count", 0) for page in pages)
                page_reader_counts.update(page.get("reader", "unknown") for page in pages)

            document_quality_csv = stage / "parse_quality.csv"
            with document_quality_csv.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=document_quality_fields, extrasaction="ignore")
                writer.writeheader()
                for row in quality_rows:
                    safe = dict(row)
                    safe["ocr_pages_used"] = ";".join(str(x) for x in safe.get("ocr_pages_used", []))
                    safe["quality_flags"] = ";".join(safe.get("quality_flags", []))
                    writer.writerow(safe)

        quality_payload = {
            "built_at_utc": datetime.now(timezone.utc).isoformat(),
            "catalog_path": str(catalog_path),
            "selected_documents": len(selected), "parsed_documents": parsed_documents,
            "skipped_missing_sources": skipped_missing, "failed_documents": failed_documents,
            "page_count": page_count, "total_text_chars": total_text_chars,
            "pages_with_text": pages_with_text, "empty_pages": empty_pages,
            "low_text_pages": low_text_pages, "pages_with_replacement_characters": replacement_pages,
            "numeric_tokens_extracted": numeric_tokens, "tables_detected": tables_detected,
            "headings_detected": headings_detected, "reader_page_counts": dict(page_reader_counts),
            "mean_chars_per_page": round(total_text_chars / max(page_count, 1), 1),
            "retrieval_chunks": retrieval_chunk_count, "table_chunks": table_chunk_count,
            "section_parent_chunks": parent_chunk_count, "section_records": section_record_count,
            "documents_with_quality_flags": sum(bool(row["quality_flags"]) for row in quality_rows),
            "document_quality_status_counts": dict(Counter(row["quality_status"] for row in quality_rows)),
            "errors": all_errors,
            "outputs": [
                str((output_dir / name).relative_to(ROOT)) if ROOT in (output_dir / name).parents else str(output_dir / name)
                for name in (*jsonl_names, "parse_quality.json", "parse_quality.csv", "page_quality.csv")
            ],
            "quality_note": (
                "Coverage, empty/low-text pages, numeric-token counts, character anomalies, detected tables/headings, and runtime are objective parsing diagnostics. "
                "They are not CER/WER or a claim of transcription accuracy because this CSB corpus has no page-level ground-truth transcription."
            ),
        }
        (stage / "parse_quality.json").write_text(
            json.dumps(quality_payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Replace only after all output files have been written successfully.
        for name in (*jsonl_names, "parse_quality.json", "parse_quality.csv", "page_quality.csv"):
            (stage / name).replace(output_dir / name)

    return quality_payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--document-id", action="append", default=[], help="Limit to a document ID; may be repeated.")
    parser.add_argument("--limit-documents", type=int, default=None)
    parser.add_argument("--limit-pages", type=int, default=None, help="Smoke-test only the first N pages per document.")
    parser.add_argument("--include-non-retrieval", action="store_true", help="Include catalog rows not selected for retrieval.")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--overlap-chars", type=int, default=DEFAULT_OVERLAP_CHARS)
    parser.add_argument("--no-tables", action="store_true", help="Skip PyMuPDF table detection.")
    parser.add_argument("--ocr-fallback", choices=["none", "tesseract"], default="none")
    parser.add_argument("--ocr-min-chars", type=int, default=MIN_NATIVE_TEXT_CHARS)
    parser.add_argument("--tesseract-language", default="eng")
    args = parser.parse_args()
    if args.limit_documents is not None and args.limit_documents < 1:
        parser.error("--limit-documents must be >= 1")
    if args.limit_pages is not None and args.limit_pages < 1:
        parser.error("--limit-pages must be >= 1")
    if args.ocr_min_chars < 0:
        parser.error("--ocr-min-chars must be >= 0")
    if args.max_chars < 500:
        parser.error("--max-chars must be at least 500")
    if not 0 <= args.overlap_chars < args.max_chars:
        parser.error("--overlap-chars must be >=0 and less than --max-chars")
    result = parse_catalog(
        args.catalog, args.output_dir, document_ids=set(args.document_id) if args.document_id else None,
        limit_documents=args.limit_documents, limit_pages=args.limit_pages,
        include_non_retrieval=args.include_non_retrieval,
        max_chars=args.max_chars, overlap_chars=args.overlap_chars,
        extract_tables=not args.no_tables, ocr_fallback=args.ocr_fallback,
        ocr_min_chars=args.ocr_min_chars, tesseract_language=args.tesseract_language,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if not result["failed_documents"] and not result["skipped_missing_sources"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
