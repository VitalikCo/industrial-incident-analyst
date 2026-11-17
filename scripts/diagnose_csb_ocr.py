"""Compare Tesseract page segmentation modes on selected CSB PDF pages.

Run from the Industrial Incident Analyst project root:

    uv run python scripts/diagnose_csb_ocr.py \
      --document-id CSB-6112 \
      --pages 131,133,151,154,156 \
      --dpis 300,400 \
      --psms 3,6,11,12 \
      --output-dir /tmp/pryor-trust-ocr-diagnostic

This is a diagnostic only. It does not modify the source PDF, catalog, OCR text
sources, or existing Chunk Store.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data/processed/document_catalog.jsonl"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def resolve_project_path(value: str) -> Path:
    candidate = (ROOT / value).resolve()
    if ROOT.resolve() not in candidate.parents:
        raise ValueError(f"Path is outside the project root: {value}")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", default="CSB-6112")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--pages", default="131,133,151,154,156",
                        help="Comma-separated, one-based PDF page numbers")
    parser.add_argument("--dpis", default="300,400", help="Comma-separated rasterization DPIs")
    parser.add_argument("--psms", default="3,6,11,12", help="Comma-separated Tesseract page segmentation modes")
    parser.add_argument("--language", default="eng")
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/pryor-trust-ocr-diagnostic"))
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    tesseract = shutil.which("tesseract")
    if not tesseract:
        parser.error("Tesseract executable not found. Install tesseract-ocr first.")
    if args.timeout < 1:
        parser.error("--timeout must be >= 1")

    try:
        pages = sorted({int(x.strip()) for x in args.pages.split(",") if x.strip()})
        dpis = sorted({int(x.strip()) for x in args.dpis.split(",") if x.strip()})
        psms = sorted({int(x.strip()) for x in args.psms.split(",") if x.strip()})
    except ValueError:
        parser.error("--pages, --dpis and --psms must be comma-separated integers")
    if not pages or min(pages) < 1 or not dpis or min(dpis) < 72 or not psms or any(p < 0 or p > 13 for p in psms):
        parser.error("Invalid page/DPI/PSM values")

    catalog = read_jsonl(args.catalog)
    matches = [row for row in catalog if row.get("document_id") == args.document_id]
    if len(matches) != 1:
        parser.error(f"Expected exactly one catalog record for {args.document_id}; found {len(matches)}")
    source_path = resolve_project_path(matches[0].get("local_path") or "")
    if not source_path.is_file():
        parser.error(f"Source PDF does not exist: {source_path}")

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    version = subprocess.run([tesseract, "--version"], capture_output=True, text=True, timeout=10)
    print(f"Tesseract: {(version.stdout or version.stderr).splitlines()[0]}")
    print(f"Source: {source_path}")

    with pymupdf.open(source_path) as pdf, tempfile.TemporaryDirectory(prefix="csb-ocr-compare-") as temp_dir:
        temp = Path(temp_dir)
        for page_no in pages:
            if page_no > len(pdf):
                print(f"Skipping page {page_no}: document has only {len(pdf)} pages", file=sys.stderr)
                continue
            page = pdf[page_no - 1]
            for dpi in dpis:
                pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
                image_path = temp / f"page-{page_no}-dpi-{dpi}.png"
                pix.save(image_path)
                for psm in psms:
                    result = subprocess.run(
                        [tesseract, str(image_path), "stdout", "-l", args.language,
                         "--oem", "1", "--psm", str(psm)],
                        capture_output=True, text=True, timeout=args.timeout,
                    )
                    text = result.stdout.strip() if result.returncode == 0 else ""
                    error = (result.stderr or "").strip()
                    output_name = f"page-{page_no:03d}-dpi-{dpi}-psm-{psm}.txt"
                    (output_dir / output_name).write_text(text + "\n", encoding="utf-8")
                    records.append({
                        "document_id": args.document_id,
                        "page": page_no,
                        "dpi": dpi,
                        "psm": psm,
                        "return_code": result.returncode,
                        "text_chars": len(text),
                        "word_count": len(text.split()),
                        "output_file": output_name,
                        "stderr_excerpt": error[:300],
                    })
                    print(f"page={page_no:3d} dpi={dpi:3d} psm={psm:2d} rc={result.returncode} chars={len(text):5d} file={output_name}")

    summary_path = output_dir / "summary.csv"
    with summary_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]) if records else [
            "document_id", "page", "dpi", "psm", "return_code", "text_chars", "word_count", "output_file", "stderr_excerpt"
        ])
        writer.writeheader()
        writer.writerows(records)

    best_by_page: dict[int, dict[str, Any]] = {}
    for row in records:
        if row["return_code"] == 0 and (
            row["page"] not in best_by_page or row["text_chars"] > best_by_page[row["page"]]["text_chars"]
        ):
            best_by_page[row["page"]] = row
    print("\nBest result per page:")
    for page_no in pages:
        row = best_by_page.get(page_no)
        if row:
            print(f"  page {page_no}: {row['text_chars']} chars (dpi={row['dpi']}, psm={row['psm']}) — {row['output_file']}")
        else:
            print(f"  page {page_no}: no successful OCR output")
    print(f"\nSummary: {summary_path}")
    return 0 if records and all(row["return_code"] == 0 for row in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
