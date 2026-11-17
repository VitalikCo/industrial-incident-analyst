# Industrial Incident Analyst

An evidence-grounded system for finding similar industrial incidents, their documented causes, and the safety recommendations that followed.

## Current milestone: CSB data snapshot

The first milestone collects publicly available data from the U.S. Chemical Safety and Hazard Investigation Board (CSB):

- completed-investigation detail pages;
- final investigation report PDFs and selected supplementary publications;
- the official recommendations spreadsheet as a dated snapshot;
- a JSONL manifest with URLs, dates, locations, incident types, recommendation IDs, local file paths, and SHA-256 checksums.

Sources:

- <https://www.csb.gov/investigations/completed-investigations/>
- <https://www.csb.gov/recommendations/statistics/>

## Requirements

- Python 3.11–3.13
- `uv` (recommended)
- Internet access for initial data collection

## Setup

```bash
uv sync
```

## Collect the initial local snapshot

From the project root:

```bash
uv run python scripts/collect_csb.py --max-pages 20 --max-reports 100 --verbose
```

The script stops when no further archive entries are found. It downloads only links explicitly labelled as final reports and records missing/failed PDFs in the manifest.

To collect metadata and the recommendation spreadsheet without downloading report PDFs:

```bash
uv run python scripts/collect_csb.py --no-download-pdfs
```

Outputs:

```text
data/
├── raw/
│   ├── reports/                         # downloaded source report PDFs
│   └── recommendations/
│       ├── csb_recommendations_latest.xlsx
│       └── download_metadata.json
└── processed/
    └── manifest.jsonl
```

## Validate the collection

```bash
uv run pytest -q
```

Inspect `data/processed/collection_summary.json`, `manifest.jsonl`, and the PDF content audit. Documents with poor or missing text are blocked from retrieval until a usable text source exists. The system does **not** require a human to approve every document in the corpus.

## Data handling

`data/raw/` is excluded from Git to avoid pushing large source PDFs and third-party files. Keep a separate local backup of the downloaded data before the course checkpoint. Do not commit credentials or `.env` files.

## Automatic document classification and catalog build

Document-level manual review queues have been removed. The builder uses:

- CSB source metadata and the first-page PDF audit for deterministic rule-based classification;
- `data/processed/document_classification_candidates.csv` for the 70 existing source-checked classification candidates;
- `configs/document_overrides.yaml` for a small number of explicit source-specific corrections, aliases, and registered supplemental files;
- the official CSB recommendations workbook and its snapshot metadata.

Machine-generated candidate links remain labelled as candidates. They are **not** added to verified `incident_ids` and are not represented as human-approved. Confidence values such as `high` and `medium-high` are qualitative source-check labels, not calibrated probabilities. The candidate file is data/provenance, not a human annotation sheet.

Rebuild all catalogs after extracting this update into the project that contains your local report PDFs:

```bash
uv run python scripts/build_document_catalog.py
```

The archive includes catalogs already migrated to the automatic-classification schema. Their `file_exists` and `text_source_exists` flags were carried forward from the previous local catalog snapshot (`2026-11-13T00:13:33Z`); they were not rechecked in this uploaded ZIP because it does not contain the complete `data/raw/reports/` directory. Run the builder on your original WSL project to refresh those filesystem checks.

The builder writes:

```text
data/processed/
├── document_catalog.jsonl / document_catalog.csv
├── investigation_catalog.jsonl / investigation_catalog.csv
├── incident_catalog.jsonl / incident_catalog.csv
├── recommendation_catalog.jsonl / recommendation_catalog.csv
└── catalog_build_summary.json
```

**Important:** rebuild the catalogs before running retrieval or evaluation. The catalog outputs included in an archive may predate a change to the builder. The rebuild must run on the machine where `data/raw/reports/` contains the original downloaded PDFs; the source PDFs are not recreated by this code update.

See [docs/automatic-document-classification.md](docs/automatic-document-classification.md), [docs/incident-catalog-design.md](docs/incident-catalog-design.md), and [docs/data-card.md](docs/data-card.md).

## Evaluation and the golden set

Document classification is automatic, but the evaluation labels remain human-grounded. Build a pooled set of at least 30 labelled query–incident examples (50+ is the target for the highest course rubric band). Use relevance grades 0/1/2 and evidence from the CSB sources. These labels are also needed to check whether the Phi-4-mini judge agrees with human judgments. This is a separate task from reviewing all 70 document records.

The report used to formulate a golden-set query must be excluded from retrieval for that query. The incident catalog creates a whole-investigation exclusion bundle containing catalogued source documents and linked recommendation records. For multi-event investigations, this may also exclude sibling events; the policy intentionally favors avoiding answer leakage over retaining every potentially relevant item.

## Document Parsing and chunk store

The Document Parsing stage creates page-, section- and chunk-level records from the English documents currently enabled for retrieval. It uses PyMuPDF text extraction and table detection by default; an optional Tesseract fallback can OCR pages with a nearly empty text layer.

```bash
# First run a small smoke test against a document with a local text source.
uv run python scripts/parse_csb_documents.py --document-id CSB-6203 --limit-pages 4 --output-dir /tmp/csb-parser-smoke
uv run python scripts/validate_chunk_store.py --store-dir /tmp/csb-parser-smoke

# Then parse the complete selected corpus and validate it.
uv run python scripts/parse_csb_documents.py
uv run python scripts/validate_chunk_store.py
```

The full store is written to `data/processed/chunk_store/` (`documents.jsonl`, `pages.jsonl`, `sections.jsonl`, `chunks.jsonl`, `parse_quality.json`, `parse_quality.csv`). The original PDFs remain the citation sources; `text_source_path` is used for extraction when a separate OCR derivative exists. Explicit and candidate incident IDs remain separate in every chunk. Read [docs/document-parsing.md](docs/document-parsing.md) for parser rules, chunking, quality metrics, limitations and the optional OCR setup. The design adapts the structural chunk-store and page-provenance ideas from the lecturers' [Practice 03](https://github.com/Industrial-AI-Research-Lab/course-ml-for-industry-data/tree/main/practice-03-document-parsing).

## Recommendation status snapshots

Recommendation status is time-dependent. Every answer must identify the snapshot date used for a status claim (currently `2026-09-16` in the included workbook metadata), rather than describing the value as indefinitely current.
