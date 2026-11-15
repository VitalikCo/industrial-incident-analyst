"""Inspect the actual content of downloaded CSB PDFs using PyMuPDF.

This is a read-only audit. It reports page counts, extractable text, PDF metadata,
short/empty documents and duplicate hashes. It does not modify source files.

Run from project root after adding PyMuPDF to the project dependencies:
    uv add 'PyMuPDF>=1.24,<2'
    uv run python scripts/audit_pdf_contents.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/processed/manifest.jsonl"
REPORTS_DIR = ROOT / "data/raw/reports"
OUT_DIR = ROOT / "data/processed"
JSON_OUT = OUT_DIR / "pdf_content_audit.json"
CSV_OUT = OUT_DIR / "pdf_content_audit.csv"


class AuditError(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_manifest() -> list[dict[str, Any]]:
    if not MANIFEST.exists():
        raise FileNotFoundError(MANIFEST)
    rows = []
    with MANIFEST.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            item["_manifest_line"] = line_no
            rows.append(item)
    return rows


def classify_preview(text: str, page_count: int, total_chars: int) -> list[str]:
    low = text.lower()
    flags: list[str] = []
    if page_count <= 2:
        flags.append("very_short_pdf")
    if total_chars < 500:
        flags.append("very_little_extractable_text")
    if "urgent recommendation" in low:
        flags.append("urgent_recommendation_document")
    if "appendix" in low[:2500] and ("testing data" in low[:2500] or "consultant report" in low[:2500]):
        flags.append("possible_appendix_or_consultant_report")
    if "investigation report" not in low[:5000] and "final report" not in low[:5000] and page_count <= 3:
        flags.append("document_type_needs_manual_review")
    return flags


def audit_one(record: dict[str, Any]) -> dict[str, Any]:
    rel = record.get("local_pdf_path")
    out: dict[str, Any] = {
        "manifest_line": record.get("_manifest_line"),
        "title": record.get("title"),
        "detail_url": record.get("detail_url"),
        "collection_status": record.get("collection_status"),
        "local_pdf_path": rel,
        "sha256": None,
        "page_count": None,
        "pdf_metadata_title": None,
        "total_text_chars": 0,
        "pages_with_text": 0,
        "avg_chars_per_page": 0.0,
        "first_page_preview": "",
        "audit_status": "no_pdf_path",
        "flags": [],
        "error": None,
    }
    if not rel:
        return out
    path = (ROOT / rel).resolve()
    if ROOT.resolve() not in path.parents:
        out.update(audit_status="unsafe_path", error="Path escapes project root")
        return out
    if not path.is_file():
        out.update(audit_status="missing_file", error="Manifest file path does not exist")
        return out

    out["sha256"] = sha256_file(path)
    try:
        with fitz.open(path) as doc:
            out["page_count"] = len(doc)
            out["pdf_metadata_title"] = doc.metadata.get("title") if doc.metadata else None
            page_char_counts = []
            first_preview = ""
            for i, page in enumerate(doc):
                text = page.get_text("text") or ""
                compact = re.sub(r"\s+", " ", text).strip()
                page_char_counts.append(len(compact))
                if i == 0:
                    first_preview = compact[:1200]
            total_chars = sum(page_char_counts)
            pages_with_text = sum(n > 30 for n in page_char_counts)
            out["total_text_chars"] = total_chars
            out["pages_with_text"] = pages_with_text
            out["avg_chars_per_page"] = round(total_chars / max(len(doc), 1), 1)
            out["first_page_preview"] = first_preview
            out["flags"] = classify_preview(first_preview, len(doc), total_chars)
            if len(doc) == 0:
                out["audit_status"] = "empty_pdf"
            elif total_chars == 0:
                out["audit_status"] = "no_extractable_text_possible_scan"
            else:
                out["audit_status"] = "opened_and_text_extracted"
    except Exception as exc:  # malformed/encrypted/corrupt PDFs must not stop the audit
        out.update(audit_status="pdf_open_failed", error=f"{type(exc).__name__}: {exc}")
    return out


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    rows = [audit_one(r) for r in manifest]
    hashes: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row.get("sha256"):
            hashes[row["sha256"]].append(row.get("local_pdf_path") or "")
    duplicate_groups = [paths for paths in hashes.values() if len(set(paths)) > 1]
    unique_pdf_count = len({r["sha256"] for r in rows if r.get("sha256")})
    status_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        status_counts[row["audit_status"]] += 1

    summary = {
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_records": len(rows),
        "pdf_records_audited": sum(bool(r.get("local_pdf_path")) for r in rows),
        "unique_pdf_hashes": unique_pdf_count,
        "duplicate_hash_groups": duplicate_groups,
        "audit_status_counts": dict(sorted(status_counts.items())),
        "records_with_flags": sum(bool(r["flags"]) for r in rows),
        "note": "This audit checks PDF readability and extractable text, not whether each file is the correct primary CSB report. Review first-page previews and flags manually.",
    }
    JSON_OUT.write_text(json.dumps({"summary": summary, "records": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    with CSV_OUT.open("w", encoding="utf-8", newline="") as stream:
        fields = ["manifest_line", "title", "local_pdf_path", "page_count", "total_text_chars",
                  "pages_with_text", "avg_chars_per_page", "audit_status", "flags", "pdf_metadata_title",
                  "first_page_preview", "detail_url", "collection_status", "sha256", "error"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            safe = dict(row)
            safe["flags"] = ";".join(safe["flags"])
            writer.writerow({k: safe.get(k) for k in fields})

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nSaved: {JSON_OUT.relative_to(ROOT)}")
    print(f"Saved: {CSV_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
