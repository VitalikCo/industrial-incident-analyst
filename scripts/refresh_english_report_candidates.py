"""Re-key Cuisine Solutions / Foundation Food Group candidate rows to English CSB PDFs.

Run after refreshing the two CSB detail pages and rebuilding the PDF audit:
    uv run python scripts/refresh_english_report_candidates.py

The script refuses to proceed unless both English DocumentIds are present in the
manifest, their PDF files exist, and their audit rows are available. Candidate incident
IDs are preserved; only the source document IDs and file provenance are updated.
"""
from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/processed/manifest.jsonl"
AUDIT = ROOT / "data/processed/pdf_content_audit.json"
CANDIDATES = ROOT / "data/processed/document_classification_candidates.csv"

REPLACEMENTS = {
    "CSB-6323": {
        "new_document_id": "CSB-6304",
        "investigation_title": "Cuisine Solutions Ammonia Release",
        "classification_basis": (
            "The official CSB investigation page lists DocumentId 6304 as the English final report; "
            "DocumentId 6323 is the Spanish-language version."
        ),
    },
    "CSB-6269": {
        "new_document_id": "CSB-6268",
        "investigation_title": "Foundation Food Group Fatal Chemical Release",
        "classification_basis": (
            "The official CSB investigation page lists DocumentId 6268 as the English final report; "
            "DocumentId 6269 is the Spanish-language version."
        ),
    },
}


def csb_document_id(url: str | None) -> str | None:
    if not url:
        return None
    query = parse_qs(urlparse(url).query)
    values = query.get("DocumentId") or query.get("documentid") or []
    return values[0] if values else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_audit_by_path(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(row.get("local_pdf_path")): row
        for row in payload.get("records", [])
        if row.get("local_pdf_path")
    }


def refresh_candidate_rows(
    rows: list[dict[str, str]],
    manifest: list[dict[str, Any]],
    audit_by_path: dict[str, dict[str, Any]],
) -> int:
    manifest_by_id = {
        csb_document_id(item.get("final_report_url")): item
        for item in manifest
        if csb_document_id(item.get("final_report_url"))
    }
    row_by_id = {row.get("document_id", "").strip(): row for row in rows}

    missing_old = set(REPLACEMENTS) - set(row_by_id)
    if missing_old:
        raise ValueError(f"Candidate rows not found for old IDs: {sorted(missing_old)}")

    updates: list[tuple[dict[str, str], dict[str, Any], dict[str, Any], str]] = []
    for old_id, spec in REPLACEMENTS.items():
        new_id = spec["new_document_id"]
        manifest_id = new_id.removeprefix("CSB-")
        if manifest_id not in manifest_by_id:
            raise ValueError(
                f"English report {new_id} is not in manifest yet. Re-run collect_csb.py for the corresponding detail page first."
            )
        record = manifest_by_id[manifest_id]
        local_path = record.get("local_pdf_path")
        if not local_path:
            raise ValueError(f"Manifest record {new_id} has no local_pdf_path")
        resolved = (ROOT / local_path).resolve()
        if ROOT.resolve() not in resolved.parents or not resolved.is_file():
            raise FileNotFoundError(f"English PDF {new_id} missing: {local_path}")
        audit = audit_by_path.get(local_path)
        if audit is None:
            raise ValueError(f"No PDF audit row for {new_id} at {local_path}; run audit_pdf_contents.py first")
        if audit.get("audit_status") not in {"opened_and_text_extracted", "no_extractable_text_possible_scan"}:
            raise ValueError(f"English PDF {new_id} has audit status {audit.get('audit_status')!r}")
        updates.append((row_by_id[old_id], record, audit, new_id))

    # Apply only after every replacement has passed validation.
    for row, record, audit, new_id in updates:
        old_id = row["document_id"].strip()
        spec = REPLACEMENTS[old_id]
        row["document_id"] = new_id
        row["title"] = record.get("title") or spec["investigation_title"]
        row["local_pdf_path"] = record.get("local_pdf_path") or ""
        row["source_url"] = record.get("final_report_url") or ""
        row["page_count"] = str(audit.get("page_count") or "")
        row["pdf_audit_status"] = str(audit.get("audit_status") or "")
        flags = audit.get("flags") or []
        row["pdf_audit_flags"] = ";".join(str(flag) for flag in flags)
        row["first_page_preview"] = str(audit.get("first_page_preview") or "")
        row["sha256"] = str(audit.get("sha256") or record.get("pdf_sha256") or "")
        row["classification_profile"] = "primary_report"
        row["classification_confidence"] = "high"
        row["classification_basis"] = spec["classification_basis"]
        row["classification_follow_up"] = (
            f"Use English final report {new_id} for English retrieval. The Spanish translation remains an alternate-language source outside the English retrieval collection."
        )
    return len(updates)


def main() -> int:
    manifest = read_jsonl(MANIFEST)
    audit_by_path = load_audit_by_path(AUDIT)
    with CANDIDATES.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError("Candidate table has no header")
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    required = {"document_id", "classification_profile", "candidate_incident_ids"}
    missing = required - set(fieldnames)
    if missing:
        raise ValueError(f"Candidate table is missing required columns: {sorted(missing)}")

    updated = refresh_candidate_rows(rows, manifest, audit_by_path)
    fd, temp_name = tempfile.mkstemp(prefix=f"{CANDIDATES.name}.", suffix=".tmp", dir=CANDIDATES.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        Path(temp_name).replace(CANDIDATES)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise

    print(json.dumps({
        "updated_candidate_rows": updated,
        "document_ids": [spec["new_document_id"] for spec in REPLACEMENTS.values()],
        "classification_profile": "primary_report",
        "incident_mappings_preserved": True,
        "candidate_table": str(CANDIDATES.relative_to(ROOT)),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
