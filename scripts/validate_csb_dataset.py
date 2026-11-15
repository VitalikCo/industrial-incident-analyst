#!/usr/bin/env python3
"""Validate a locally collected CSB dataset without changing any source files.

Run from the project root:
    uv run python scripts/validate_csb_dataset.py

The script reports missing/corrupt PDFs, checksum mismatches, duplicate files,
partial downloads, unreferenced PDFs, recommendation workbook integrity, and
records whose titles may indicate a study or guidance publication rather than an
individual incident investigation. Those records are flagged for human review;
they are never deleted or automatically excluded.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/processed/manifest.jsonl"
REPORTS_DIR = ROOT / "data/raw/reports"
RECOMMENDATIONS = ROOT / "data/raw/recommendations/csb_recommendations_latest.xlsx"
REC_METADATA = ROOT / "data/raw/recommendations/download_metadata.json"
OUT_DIR = ROOT / "data/processed"
SUMMARY_OUT = OUT_DIR / "dataset_validation.json"
CSV_OUT = OUT_DIR / "dataset_validation_records.csv"

# Intentionally conservative: these are review hints, not automatic exclusions.
NON_INCIDENT_HINTS = re.compile(
    r"\b(study|key lessons|lessons to prevent|management of change|"
    r"emergency shutdown systems|remote isolation|preventing harm|"
    r"hazard investigation|hazard management)\b",
    re.IGNORECASE,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_manifest() -> list[dict[str, Any]]:
    if not MANIFEST.exists():
        raise FileNotFoundError(f"Manifest not found: {MANIFEST}")
    records: list[dict[str, Any]] = []
    with MANIFEST.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                item["_manifest_line"] = line_no
                records.append(item)
            except json.JSONDecodeError as exc:
                records.append({"_manifest_line": line_no, "_parse_error": str(exc)})
    return records


def inspect_pdf(record: dict[str, Any]) -> dict[str, Any]:
    rel_path = record.get("local_pdf_path")
    result = {
        "pdf_state": "no_pdf_path",
        "pdf_exists": False,
        "pdf_size_bytes": None,
        "pdf_sha256_actual": None,
        "checksum_matches": None,
    }
    if not rel_path:
        return result
    path = (ROOT / rel_path).resolve()
    # Prevent malformed manifest paths from reading outside the project.
    if ROOT.resolve() not in path.parents:
        result["pdf_state"] = "unsafe_manifest_path"
        return result
    if not path.is_file():
        result["pdf_state"] = "missing_file"
        return result
    result["pdf_exists"] = True
    result["pdf_size_bytes"] = path.stat().st_size
    with path.open("rb") as stream:
        magic = stream.read(5)
    if magic[:4] != b"%PDF":
        result["pdf_state"] = "invalid_pdf_signature"
        return result
    if path.stat().st_size < 1024:
        result["pdf_state"] = "suspiciously_small_pdf"
        return result
    actual_hash = sha256_file(path)
    expected_hash = record.get("pdf_sha256")
    result["pdf_sha256_actual"] = actual_hash
    result["checksum_matches"] = (actual_hash == expected_hash) if expected_hash else None
    if expected_hash and actual_hash != expected_hash:
        result["pdf_state"] = "checksum_mismatch"
    else:
        result["pdf_state"] = "valid_pdf"
    return result


def inspect_recommendations() -> dict[str, Any]:
    result: dict[str, Any] = {
        "exists": RECOMMENDATIONS.is_file(),
        "path": str(RECOMMENDATIONS.relative_to(ROOT)),
        "valid_zip_xlsx": False,
        "size_bytes": None,
        "sha256_actual": None,
        "metadata_sha256_matches": None,
        "snapshot_label": None,
        "metadata_collected_at_utc": None,
    }
    if not RECOMMENDATIONS.is_file():
        return result
    result["size_bytes"] = RECOMMENDATIONS.stat().st_size
    result["sha256_actual"] = sha256_file(RECOMMENDATIONS)
    try:
        with zipfile.ZipFile(RECOMMENDATIONS) as archive:
            bad_member = archive.testzip()
            result["valid_zip_xlsx"] = bad_member is None and "[Content_Types].xml" in archive.namelist()
            if bad_member:
                result["zip_error_member"] = bad_member
    except (zipfile.BadZipFile, OSError) as exc:
        result["zip_error"] = str(exc)
    if REC_METADATA.is_file():
        try:
            metadata = json.loads(REC_METADATA.read_text(encoding="utf-8"))
            expected = metadata.get("sha256")
            result["metadata_sha256_matches"] = (
                result["sha256_actual"] == expected if expected else None
            )
            result["snapshot_label"] = metadata.get("label")
            result["metadata_collected_at_utc"] = metadata.get("collected_at_utc")
        except (OSError, json.JSONDecodeError) as exc:
            result["metadata_error"] = str(exc)
    return result


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw_records = read_manifest()
    rows: list[dict[str, Any]] = []
    hash_to_paths: dict[str, list[str]] = defaultdict(list)
    referenced_paths: set[Path] = set()

    for record in raw_records:
        row = {
            "manifest_line": record.get("_manifest_line"),
            "title": record.get("title"),
            "detail_url": record.get("detail_url"),
            "collection_status": record.get("collection_status"),
            "error": record.get("error"),
            "review_hint": "possible_non_incident_publication"
            if NON_INCIDENT_HINTS.search(record.get("title") or "")
            else "",
        }
        if record.get("_parse_error"):
            row["pdf_state"] = "manifest_json_error"
            row["error"] = record["_parse_error"]
        else:
            row.update(inspect_pdf(record))
        if record.get("local_pdf_path"):
            referenced_paths.add((ROOT / record["local_pdf_path"]).resolve())
        if row.get("pdf_sha256_actual"):
            hash_to_paths[row["pdf_sha256_actual"]].append(record.get("local_pdf_path") or "")
        rows.append(row)

    actual_pdf_paths = sorted(REPORTS_DIR.glob("*.pdf")) if REPORTS_DIR.exists() else []
    partial_paths = sorted(REPORTS_DIR.glob("*.part")) if REPORTS_DIR.exists() else []
    orphan_pdfs = [str(p.relative_to(ROOT)) for p in actual_pdf_paths if p.resolve() not in referenced_paths]
    duplicates = [paths for paths in hash_to_paths.values() if len(paths) > 1]
    state_counts: dict[str, int] = defaultdict(int)
    status_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        state_counts[row.get("pdf_state", "unknown")] += 1
        status_counts[row.get("collection_status", "unknown")] += 1

    recommendation_info = inspect_recommendations()
    report = {
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "manifest_records": len(raw_records),
        "manifest_parse_errors": sum(bool(r.get("_parse_error")) for r in raw_records),
        "pdf_directory_files": len(actual_pdf_paths),
        "partial_download_files": [str(p.relative_to(ROOT)) for p in partial_paths],
        "pdf_state_counts": dict(sorted(state_counts.items())),
        "collection_status_counts": dict(sorted(status_counts.items())),
        "valid_pdf_records": state_counts.get("valid_pdf", 0),
        "missing_or_invalid_pdf_records": sum(
            n for state, n in state_counts.items() if state not in {"valid_pdf", "no_pdf_path"}
        ),
        "records_without_pdf_path": state_counts.get("no_pdf_path", 0),
        "orphan_pdf_files": orphan_pdfs,
        "duplicate_pdf_hash_groups": duplicates,
        "records_flagged_for_manual_type_review": [
            {"title": r.get("title"), "detail_url": r.get("detail_url"), "hint": r["review_hint"]}
            for r in rows if r.get("review_hint")
        ],
        "recommendations_workbook": recommendation_info,
    }
    SUMMARY_OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with CSV_OUT.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nSaved: {SUMMARY_OUT.relative_to(ROOT)}")
    print(f"Saved: {CSV_OUT.relative_to(ROOT)}")
    return 0 if report["manifest_parse_errors"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
