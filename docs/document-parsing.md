# Document Parsing and CSB chunk store

## Purpose

This stage converts the selected English CSB investigation reports and supplementary incident documents into a reproducible page/section/chunk store for BM25, dense and hybrid retrieval. The original PDF remains the citation source; if a separate OCR-derived PDF is selected in `text_source_path`, that derivative is used only for extraction and its path is recorded separately.

The parser preserves the provenance fields already present in `document_catalog.jsonl`: `document_id`, `csb_document_id`, printed `investigation_id`, candidate investigation key, explicit `incident_ids`, provisional `candidate_incident_ids`, recommendation IDs, and source-case identity. It does not promote candidate mappings to verified mappings.

## Relationship to Practice 03

The implementation follows the useful design principles in the lecturers' [Practice 03 — Document Parsing](https://github.com/Industrial-AI-Research-Lab/course-ml-for-industry-data/tree/main/practice-03-document-parsing):

- one parse record per source page, including reader and parser version;
- document, page, section and chunk metadata are retained;
- tables are extracted as independent whole chunks rather than being cut into arbitrary text windows;
- section headings form a hierarchy, and child chunks reference their section context;
- parsing quality and runtime are measured separately from answer quality.

Practice 03 uses a synthetic regulation/datasheet collection to compare PyMuPDF, Tesseract, EasyOCR, Docling and VLM readers. The CSB project uses a different default: PyMuPDF's native text layer plus table detection, because the selected investigation reports are predominantly born-digital PDFs. Tesseract is an optional page-level fallback for near-empty text-layer pages. This avoids installing large OCR/model dependencies for every run. A separate parser comparison on a small representative sample can be added later if the project needs evidence that a more expensive reader improves difficult scans.

## Parser and chunking policy

### Reader selection

1. `text_source_path` is preferred when it exists; otherwise the original `local_path` is used.
2. PyMuPDF reads text blocks in page reading order and records their PDF-point bounding boxes, font sizes and bold-font hints.
3. `page.find_tables()` is used to extract detected tables. The closest preceding table caption is kept with the table, and the text blocks inside the table area are removed from the paragraph stream to avoid duplicate content.
4. Repeated short lines in the top/bottom page margins are treated as running headers/footers when they recur on enough pages. Standalone page-number lines are removed.
5. Table-of-contents pages are preserved in `pages.jsonl` and `chunks.jsonl` for provenance, but their chunks are marked `retrieval_eligible=false` so page references do not pollute retrieval.
6. Optional Tesseract fallback can be enabled for pages with fewer than 80 extracted characters. OCR output is used only when it contains more text than the native layer.

### Section detection

The first-pass section detector combines:

- known CSB headings (for example, Executive Summary, Incident Description, Analysis, Causes and Recommendations);
- numbered headings;
- bold/larger typography;
- short all-caps headings.

This is a deterministic heuristic, not a claim of perfect semantic structure recovery. A heading may be missed or a short bold paragraph may be classified as a heading. The store preserves `detection_basis` so these cases can be audited. Section parent chunks are contextual records and are not directly indexed; retrievable text/table chunks point to their section and parent chunk.

### Chunking

- Text paragraphs are combined while they fit within the configured character budget, then split near paragraph/sentence boundaries when they exceed it.
- Default maximum text chunk length is 4,200 characters with 450 characters of overlap for oversized paragraphs.
- A detected table stays together as one `table` chunk, retaining its Markdown representation, extracted rows, caption, page and bounding box. Very large tables may exceed the text chunk limit intentionally; splitting a table can destroy row/column relationships.
- Each retrieval chunk records `page_start`, `page_end`, `source_pages`, `section_id`, `section_path`, and `parent_chunk_id` where available.

## Outputs

The default output directory is `data/processed/chunk_store/`:

| File | Contents |
|---|---|
| `documents.jsonl` | One record per parsed document, source paths/checksums, parser version and per-document quality summary |
| `pages.jsonl` | One record per parsed page, extracted text, dimensions, headings, block/table counts, reader and runtime |
| `sections.jsonl` | Detected section hierarchy, page ranges and detection basis |
| `chunks.jsonl` | Retrieval text/table chunks and non-retrieval section-parent/contents chunks |
| `parse_quality.json` | Aggregate parsing summary, document metrics and errors |
| `parse_quality.csv` | One row per document for convenient inspection |
| `page_quality.csv` | One row per page with text length, numeric token, table, heading, empty/low-text and runtime diagnostics |

Generated chunk stores and quality outputs are derived artifacts and should not be committed as large source data. Rebuild them from the catalog and local PDFs when parser settings or source files change.

## Commands

Run from the project root in Ubuntu WSL:

```bash
# Parse the documents currently enabled for English retrieval.
uv run python scripts/parse_csb_documents.py

# Validate all generated JSONL files and cross-references before indexing.
uv run python scripts/validate_chunk_store.py

# Small smoke test before processing the full corpus.
uv run python scripts/parse_csb_documents.py --document-id CSB-6203 --limit-pages 4 --output-dir /tmp/csb-parser-smoke
uv run python scripts/validate_chunk_store.py --store-dir /tmp/csb-parser-smoke
```

Optional OCR fallback (requires the Tesseract system executable and English language data):

```bash
sudo apt update
sudo apt install tesseract-ocr tesseract-ocr-eng
tesseract --list-langs
uv run python scripts/parse_csb_documents.py --ocr-fallback tesseract
```

Tesseract is not a Python dependency; it is an optional system tool. The default parser works without it. `--limit-pages` and `--limit-documents` are for smoke tests only; do not use them when generating the final chunk store.

The parser returns a non-zero exit code if a selected retrieval document has a missing text source or fails to parse. The output summary still records which documents were parsed, skipped or failed.

## Parsing quality measurements and limitations

The corpus parser streams completed document outputs to staged JSONL/CSV files instead of keeping every page and chunk from all reports in memory. Only the current PDF is held as parsing objects, which is safer for large, multi-volume CSB investigations.

The parser reports, per document:

- source page count and parsed page count;
- pages with text, empty pages and low-text pages;
- text coverage and mean characters per page;
- detected table and section counts;
- mean retrieval-chunk length;
- replacement-character count, OCR pages used and repeated running-line patterns removed;
- parser version and runtime;
- page-level text length, word count, numeric-token count and reader used.

These are objective diagnostics of extraction coverage and structural output. They are **not** CER/WER or proof that every extracted number/table cell is correct: the current CSB corpus does not contain page-level transcription ground truth. In particular, a page can have abundant extracted text while still containing a wrong digit or a malformed table. Before final evaluation, inspect a small stratified sample of pages/chunks from born-digital reports, appendices and OCR-derived documents, and record the observed failure modes. Do not present coverage as transcription accuracy.

`no_tables_detected` and `no_section_headings_detected` are diagnostic flags, not automatic parser failures: some valid pages/documents contain no tables or no heading on that page. `quality_status=review_needed` is raised for empty pages, many low-text pages or replacement characters.

## Reproducibility and next stage

The parser stores the selected parser name/version and actual parse source path for every page/document. After validation passes, the RAG indexing stage should use only chunks with `retrieval_eligible=true`. Keep the original `source_path` and page metadata for citations. Apply source-case exclusion by the investigation-level bundle from `incident_catalog.jsonl` before retrieval results are passed to the answer model.
