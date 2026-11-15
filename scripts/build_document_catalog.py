"""Build document, investigation, incident, and recommendation catalogs.

Run from the project root:
    uv run python scripts/build_document_catalog.py

The builder applies source-backed automatic document classifications. Candidate mappings
remain explicitly provisional (never reported as human-verified), while source-case
exclusion conservatively includes every catalogued document and recommendation sharing
an investigation key.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "data/processed/manifest.jsonl"
AUDIT_PATH = ROOT / "data/processed/pdf_content_audit.json"
OVERRIDES_PATH = ROOT / "configs/document_overrides.yaml"
CLASSIFICATION_CANDIDATES_PATH = ROOT / "data/processed/document_classification_candidates.csv"
RECOMMENDATIONS_PATH = ROOT / "data/raw/recommendations/csb_recommendations_latest.xlsx"
RECOMMENDATIONS_METADATA_PATH = ROOT / "data/raw/recommendations/download_metadata.json"

OUT_JSONL = ROOT / "data/processed/document_catalog.jsonl"
OUT_CSV = ROOT / "data/processed/document_catalog.csv"
OUT_INVESTIGATIONS_JSONL = ROOT / "data/processed/investigation_catalog.jsonl"
OUT_INVESTIGATIONS_CSV = ROOT / "data/processed/investigation_catalog.csv"
OUT_INCIDENTS_JSONL = ROOT / "data/processed/incident_catalog.jsonl"
OUT_INCIDENTS_CSV = ROOT / "data/processed/incident_catalog.csv"
OUT_RECOMMENDATIONS_JSONL = ROOT / "data/processed/recommendation_catalog.jsonl"
OUT_RECOMMENDATIONS_CSV = ROOT / "data/processed/recommendation_catalog.csv"
OUT_SUMMARY = ROOT / "data/processed/catalog_build_summary.json"

# CSB report numbers occur as YYYY-N-I-XX, YYYY-NN-I-XX, YYYY-NNN-I-XX,
# and legacy YY-NNN-I-XX. Some covers use dots instead of hyphens and OCR can
# change the case of state letters. This regex extracts the printed identifier;
# it does NOT expand a two-digit year or rewrite the sequence width.
REPORT_ID_RE = re.compile(
    r"(?<![A-Z0-9])"
    r"(?P<report_id>(?P<year>\d{4}|\d{2})\s*[.-]\s*"
    r"(?P<sequence>\d{1,3})\s*[.-]\s*(?P<kind>[IH])\s*[.-]\s*"
    r"(?P<state>[A-Z]{2}))"
    r"(?![A-Z0-9])",
    flags=re.IGNORECASE,
)
DATE_IN_LABEL_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s+(\d{4})\b",
    re.IGNORECASE,
)
EXPLICIT_MAPPING_STATUSES = {"explicit_override", "explicit_alias_override"}

# These labels are carried forward from the source-check proposal sheet. They are
# machine-applied candidates, not human approvals and not calibrated probabilities.
AUTOMATIC_CLASSIFICATION_MAP = {
    "primary_report": ("investigation_report", "primary_incident_evidence", True, "en"),
    "supplementary_case_study": (
        "incident_case_study", "supplementary_incident_evidence", True, "en"
    ),
    "primary_translation": (
        "investigation_report_translation", "primary_incident_evidence", False, "es"
    ),
    "supplementary_appendix": (
        "technical_appendix", "supplementary_incident_evidence", True, "en"
    ),
    "topic_study_excluded": ("hazard_investigation_study", "excluded", False, None),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    records = []
    with path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
    return records


def read_audit(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, dict[str, Any]] = {}
    for record in payload.get("records", []):
        local_path = record.get("local_pdf_path")
        if local_path:
            result[local_path] = record
    return result


def csb_document_id(url: str | None) -> str | None:
    if not url:
        return None
    values = parse_qs(urlparse(url).query)
    document_id = values.get("DocumentId") or values.get("documentid")
    return document_id[0] if document_id else None


def normalize_report_id(value: str | None) -> str | None:
    """Normalize separators and OCR letter case, preserving year/sequence widths.

    Examples:
      2010.02.I.PR -> 2010-02-I-PR
      1998-002-I-LA -> 1998-002-I-LA
      98-007-I-IA -> 98-007-I-IA
      2019-03-I-lL -> 2019-03-I-IL
    """
    if not value:
        return None
    match = REPORT_ID_RE.search(str(value))
    if not match:
        return None
    raw = re.sub(r"\s+", "", match.group("report_id"))
    parts = re.split(r"[.-]", raw)
    if len(parts) != 4:
        return None
    state = parts[3]
    # This exact OCR error is present in the AB Specialty Silicones cover preview:
    # lowercase 'l' was read where the state code begins with capital 'I'.
    if state == "lL":
        state = "IL"
    else:
        state = state.upper()
    return f"{parts[0]}-{parts[1]}-{parts[2].upper()}-{state}"


def extract_report_id(text: str | None) -> tuple[str | None, str | None]:
    """Return (raw printed token, display-normalized token) from text."""
    if not text:
        return None, None
    match = REPORT_ID_RE.search(text)
    if not match:
        return None, None
    raw = re.sub(r"\s+", "", match.group("report_id"))
    return raw, normalize_report_id(raw)


def recommendation_match_key(
    value: str | None, *, two_digit_year_pivot: int = 30
) -> str | None:
    """Create a comparison key for joining report IDs to CSB recommendation prefixes.

    This key is intentionally separate from `investigation_id`. It expands 2-digit
    years only for matching and removes leading zeroes from the sequence because the
    recommendation workbook uses e.g. 1998-7 while a PDF cover can print 98-007.
    With pivot 30: 00–29 -> 2000–2029; 30–99 -> 1930–1999.
    """
    normalized = normalize_report_id(value)
    if not normalized:
        return None
    parts = normalized.split("-")
    if len(parts) != 4:
        return None
    year, sequence, kind, state = parts
    if len(year) == 2:
        yy = int(year)
        year = str(2000 + yy if yy <= two_digit_year_pivot - 1 else 1900 + yy)
    return f"{year}-{int(sequence)}-{kind}-{state}"


def recommendation_prefix_from_record_number(record_number: str | None) -> str | None:
    """Return the four-part investigation prefix from a CSB recommendation ID.

    Split by the first four structural fields rather than stripping a generic ``-R...``
    suffix with regex: state abbreviations such as ``RI`` also begin with R.
    This correctly handles R1, R10A, RU1, RUR1 and R-1 suffix families.
    """
    if not record_number:
        return None
    parts = str(record_number).strip().upper().split("-")
    if len(parts) < 5:
        return None
    year, sequence, kind, state, suffix = parts[:5]
    if not re.fullmatch(r"\d{4}", year):
        return None
    if not re.fullmatch(r"\d{1,3}", sequence):
        return None
    if kind not in {"I", "H", "B"}:
        return None
    if not re.fullmatch(r"[A-Z]{2}", state):
        return None
    if not suffix.startswith("R"):
        return None
    return "-".join(parts[:4])


def guess_document_type(title: str, preview: str) -> tuple[str, str]:
    low = f"{title} {preview}".lower()
    if "congressional letter" in low or "committee on education" in low:
        return "congressional_correspondence", "supplementary_non_investigation_material"
    if "urgent recommendation" in low:
        return "urgent_recommendation", "supplementary_incident_evidence"
    if "safety bulletin" in low:
        return "safety_bulletin", "supplementary_incident_evidence"
    if "safety study" in low:
        return "safety_study", "supplementary_topic_level_evidence"
    if "hazard investigation" in low and "investigation report" not in low:
        return "hazard_investigation_study", "supplementary_topic_level_evidence"
    if (
        "prepared for the us chemical safety" in low
        or "prepared for the u.s. chemical safety" in low
        or "metallurgical consultant" in low
        or "metallurgical analysis" in low
        or "prepared by atlas engineering" in low
        or "failure analysis associates" in low
    ):
        return "third_party_technical_report", "supplementary_incident_evidence"
    if "case study" in low and REPORT_ID_RE.search(preview):
        return "csb_incident_case_study_candidate", "primary_candidate_needs_review"
    if "investigation report" in low and REPORT_ID_RE.search(preview):
        return "csb_investigation_report_candidate", "primary_candidate_needs_review"
    return "unclassified", "unclassified"


def resolve_local(path_value: str | None) -> tuple[str, bool, int | None, str | None]:
    if not path_value:
        return "", False, None, None
    path = (ROOT / path_value).resolve()
    if ROOT.resolve() not in path.parents:
        return str(path_value), False, None, None
    if not path.is_file():
        return str(path_value), False, None, None
    return str(path_value), True, path.stat().st_size, sha256_file(path)


def _policy(overrides: dict[str, Any]) -> dict[str, Any]:
    return overrides.get("report_id_policy", {}) or {}


def record_from_manifest(
    row: dict[str, Any],
    audit: dict[str, Any] | None,
    override: dict[str, Any] | None,
    *,
    two_digit_year_pivot: int = 30,
) -> dict[str, Any]:
    title = row.get("title") or "Untitled CSB document"
    preview = (audit or {}).get("first_page_preview") or ""
    guessed_type, guessed_role = guess_document_type(title, preview)
    path, file_exists, size, actual_hash = resolve_local(row.get("local_pdf_path"))
    doc_id = csb_document_id(row.get("final_report_url"))
    if doc_id:
        document_id = f"CSB-{doc_id}"
    else:
        document_id = f"CSB-SHA256-{(actual_hash or row.get('pdf_sha256') or hashlib.sha256(row.get('detail_url','').encode()).hexdigest())[:16]}"

    raw_report_number, extracted_investigation_id = extract_report_id(preview)
    investigation_id = (override or {}).get("investigation_id") or extracted_investigation_id
    if override:
        guessed_type = override.get("document_type", guessed_type)
        guessed_role = override.get("corpus_role", guessed_role)

    match_value = (override or {}).get("recommendation_match_key") or investigation_id or raw_report_number
    match_key = recommendation_match_key(match_value, two_digit_year_pivot=two_digit_year_pivot)

    text_path_value = (override or {}).get("text_source_path") or row.get("local_pdf_path")
    text_path, text_exists, text_size, text_hash = resolve_local(text_path_value)

    return {
        "document_id": (override or {}).get("document_id", document_id),
        "title": title,
        "source_detail_urls": [row.get("detail_url")] if row.get("detail_url") else [],
        "source_url": row.get("final_report_url"),
        "csb_document_id": doc_id,
        "raw_investigation_number": raw_report_number,
        "investigation_id": investigation_id,
        "recommendation_match_key": match_key,
        "incident_ids": (override or {}).get("incident_ids", []),
        "candidate_incident_ids": [],
        "document_type": guessed_type,
        "corpus_role": guessed_role,
        "retrieval_requested": bool((override or {}).get("retrieval_enabled", False)),
        "retrieval_enabled": bool((override or {}).get("retrieval_enabled", False) and text_exists),
        "retrieval_block_reason": (
            "text_source_missing"
            if (override or {}).get("retrieval_enabled", False) and not text_exists
            else None
        ),
        "local_path": path,
        "file_exists": file_exists,
        "file_size_bytes": size,
        "source_sha256": actual_hash or row.get("pdf_sha256"),
        "text_source_path": text_path,
        "text_source_exists": text_exists,
        "text_source_size_bytes": text_size,
        "text_source_sha256": text_hash,
        "page_count": (audit or {}).get("page_count"),
        "language": (override or {}).get("language"),
        "text_extraction_method": (override or {}).get("text_extraction_method", "native_pdf_text_candidate"),
        "ocr_status": (override or {}).get("ocr_status"),
        "ocr_pages_reviewed": (override or {}).get("ocr_pages_reviewed", []),
        "ocr_pages_to_review": (override or {}).get("ocr_pages_to_review", []),
        "recommendation_ids": row.get("recommendation_ids") or [],
        "collection_status": row.get("collection_status"),
        "classification_status": "explicit_source_override" if (override or {}).get("incident_ids") else "rule_based_candidate",
        "classification_method": "explicit_source_override" if override else "title_and_first_page_rules",
        "classification_confidence": "high" if override else ("medium" if guessed_type != "unclassified" else "low"),
        "classification_basis": (override or {}).get("quality_note") or "CSB title and first-page text classification rule.",
        "classification_note": (override or {}).get("notes"),
        "mapping_status": "explicit_override" if (override or {}).get("incident_ids") else ("source_metadata_candidate" if investigation_id else "unmapped"),
        "source_case_id": f"investigation:{match_key}" if match_key else None,
        "quality_note": (override or {}).get("quality_note"),
        "notes": (override or {}).get("notes"),
    }


def registered_document_record(spec: dict[str, Any], *, two_digit_year_pivot: int = 30) -> dict[str, Any]:
    path, exists, size, digest = resolve_local(spec.get("local_path"))
    text_path_value = spec.get("text_source_path") or spec.get("local_path")
    text_path, text_exists, text_size, text_digest = resolve_local(text_path_value)
    raw_report_number = spec.get("raw_investigation_number") or spec.get("investigation_id")
    investigation_id = spec.get("investigation_id") or normalize_report_id(raw_report_number)
    match_value = spec.get("recommendation_match_key") or investigation_id or raw_report_number
    return {
        "document_id": spec["document_id"],
        "title": spec["title"],
        "source_detail_urls": spec.get("source_detail_urls", []),
        "source_url": spec.get("source_url"),
        "csb_document_id": spec.get("csb_document_id"),
        "raw_investigation_number": raw_report_number,
        "investigation_id": investigation_id,
        "recommendation_match_key": recommendation_match_key(match_value, two_digit_year_pivot=two_digit_year_pivot),
        "incident_ids": spec.get("incident_ids", []),
        "candidate_incident_ids": [],
        "document_type": spec["document_type"],
        "corpus_role": spec["corpus_role"],
        "retrieval_requested": bool(spec.get("retrieval_enabled", False)),
        "retrieval_enabled": bool(spec.get("retrieval_enabled", False) and text_exists),
        "retrieval_block_reason": (
            "text_source_missing"
            if spec.get("retrieval_enabled", False) and not text_exists
            else None
        ),
        "local_path": path,
        "file_exists": exists,
        "file_size_bytes": size,
        "source_sha256": digest,
        "text_source_path": text_path,
        "text_source_exists": text_exists,
        "text_source_size_bytes": text_size,
        "text_source_sha256": text_digest,
        "page_count": spec.get("expected_pages"),
        "expected_words": spec.get("expected_words"),
        "language": spec.get("language", "en"),
        "text_extraction_method": spec.get("text_extraction_method", "native_pdf_text"),
        "ocr_status": spec.get("ocr_status"),
        "ocr_pages_reviewed": spec.get("ocr_pages_reviewed", []),
        "ocr_pages_to_review": spec.get("ocr_pages_to_review", []),
        "recommendation_ids": spec.get("recommendation_ids", []),
        "collection_status": "explicit_source_registration",
        "classification_status": "explicit_source_override",
        "classification_method": "configs_document_overrides",
        "classification_confidence": "high",
        "classification_basis": spec.get("notes") or "Explicit source registration in configs/document_overrides.yaml.",
        "classification_note": spec.get("quality_note"),
        "mapping_status": "explicit_override" if spec.get("incident_ids") else "source_registered",
        "source_case_id": f"investigation:{recommendation_match_key(match_value, two_digit_year_pivot=two_digit_year_pivot)}" if recommendation_match_key(match_value, two_digit_year_pivot=two_digit_year_pivot) else None,
        "quality_note": spec.get("quality_note"),
        "notes": spec.get("notes"),
    }


def merge_aliases(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge repeated archive references to the same physical document."""
    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        digest = record.get("source_sha256")
        key = f"sha256:{digest}" if digest else f"doc:{record['document_id']}"
        if key not in grouped:
            grouped[key] = record
            record["source_title_aliases"] = [record["title"]]
            continue
        target = grouped[key]
        for url in record.get("source_detail_urls", []):
            if url and url not in target["source_detail_urls"]:
                target["source_detail_urls"].append(url)
        for title in [record.get("title"), *record.get("source_title_aliases", [])]:
            if title and title not in target["source_title_aliases"]:
                target["source_title_aliases"].append(title)
        for field in ["recommendation_ids", "incident_ids", "candidate_incident_ids"]:
            target[field] = sorted(set(target.get(field, []) + record.get(field, [])))
        if not target.get("file_exists") and record.get("file_exists"):
            for field in [
                "local_path", "file_exists", "file_size_bytes", "source_sha256",
                "text_source_path", "text_source_exists", "text_source_size_bytes",
                "text_source_sha256", "page_count",
            ]:
                target[field] = record.get(field)
    return list(grouped.values())


def _split_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        values = value
    else:
        text = str(value).strip()
        if not text or text.lower() in {"nan", "none"}:
            return []
        values = re.split(r"[;|]", text)
    return sorted({str(item).strip() for item in values if str(item).strip()})


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def read_classification_candidates(path: Path) -> dict[str, dict[str, Any]]:
    """Read source-backed machine classification candidates keyed by document ID."""
    if not path.exists():
        raise FileNotFoundError(
            f"Classification candidate table not found: {path}. "
            "Restore data/processed/document_classification_candidates.csv before rebuilding catalogs."
        )
    result: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            doc_id = (row.get("document_id") or "").strip()
            if not doc_id:
                continue
            if doc_id in result:
                raise ValueError(f"Duplicate document_id in classification candidates: {doc_id}")
            decision = (row.get("classification_profile") or "").strip()
            if decision not in AUTOMATIC_CLASSIFICATION_MAP:
                raise ValueError(
                    f"Unsupported classification_profile {decision!r} for {doc_id}; "
                    f"expected one of {sorted(AUTOMATIC_CLASSIFICATION_MAP)}"
                )
            result[doc_id] = row
    return result


def _is_explicit_mapping(record: dict[str, Any]) -> bool:
    """True only for source-specific overrides (and legacy verified records)."""
    status = str(record.get("mapping_status") or "").strip()
    return bool(record.get("incident_ids")) and status in EXPLICIT_MAPPING_STATUSES


def apply_automatic_classifications(
    records: list[dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
    *,
    two_digit_year_pivot: int = 30,
) -> None:
    """Apply non-human document classifications without upgrading candidate links.

    The candidate table contains machine-generated, source-backed proposals. Their
    incident IDs stay in ``candidate_incident_ids``; explicit config overrides stay
    in ``incident_ids``. English retrieval eligibility is independent of the
    source-case exclusion set, which also includes translations and excluded studies.
    """
    for record in records:
        doc_id = record.get("document_id")
        row = candidates.get(str(doc_id))
        explicit = _is_explicit_mapping(record)

        # Establish a usable automatic type for deterministic cover/title cases.
        base_type = record.get("document_type") or "unclassified"
        base_role = record.get("corpus_role") or "unclassified"
        if base_type == "csb_investigation_report_candidate":
            record["document_type"] = "investigation_report"
            record["corpus_role"] = "primary_incident_evidence"
            record["retrieval_requested"] = True
        elif base_type == "csb_incident_case_study_candidate":
            record["document_type"] = "incident_case_study"
            record["corpus_role"] = "supplementary_incident_evidence"
            record["retrieval_requested"] = True
        elif base_role == "supplementary_incident_evidence" and record.get("investigation_id"):
            record["retrieval_requested"] = True
        elif base_role == "supplementary_topic_level_evidence":
            record["retrieval_requested"] = False

        record.setdefault("classification_status", "rule_based_candidate")
        record.setdefault("classification_method", "title_and_first_page_rules")
        record.setdefault("classification_confidence", "medium" if base_type != "unclassified" else "low")
        record.setdefault("classification_basis", "CSB title and first-page text classification rule.")
        record.setdefault("classification_note", None)
        record.setdefault("candidate_incident_ids", [])
        record.setdefault("candidate_investigation_id", record.get("investigation_id"))
        record.setdefault(
            "candidate_recommendation_match_key",
            recommendation_match_key(record.get("investigation_id"), two_digit_year_pivot=two_digit_year_pivot),
        )
        record.setdefault(
            "mapping_status",
            "explicit_override" if explicit else ("source_metadata_candidate" if record.get("investigation_id") else "unmapped"),
        )

        if row:
            decision = (row.get("classification_profile") or "").strip()
            doc_type, role, retrieval_requested, language = AUTOMATIC_CLASSIFICATION_MAP[decision]
            proposed_investigation_id = (row.get("candidate_investigation_id") or "").strip() or None
            proposed_incident_ids = _split_values(row.get("candidate_incident_ids"))
            candidate_key = recommendation_match_key(
                proposed_investigation_id, two_digit_year_pivot=two_digit_year_pivot
            )

            record.update({
                "classification_profile": decision,
                "classification_method": "source_checked_candidate_table",
                "classification_status": "explicit_source_override" if explicit else "automatic_candidate",
                "classification_confidence": (row.get("classification_confidence") or "unspecified").strip(),
                "classification_basis": (row.get("classification_basis") or "").strip() or None,
                "classification_note": (row.get("classification_note") or "").strip() or None,
                "classification_follow_up": (row.get("classification_follow_up") or "").strip() or None,
                "candidate_investigation_id": record.get("investigation_id") if explicit else (proposed_investigation_id or record.get("investigation_id")),
                "candidate_recommendation_match_key": (
                    record.get("recommendation_match_key") if explicit
                    else candidate_key or record.get("candidate_recommendation_match_key")
                ),
                "candidate_incident_ids": (
                    _split_values(record.get("candidate_incident_ids")) if explicit else proposed_incident_ids
                ),
                "classification_source": "existing_source_checked_proposal_sheet",
            })

            # Do not let the proposal overwrite an explicit source-specific correction.
            if not explicit:
                record["document_type"] = doc_type
                record["corpus_role"] = role
                record["retrieval_requested"] = retrieval_requested
                record["language"] = language or record.get("language") or "en"
                record["mapping_status"] = (
                    "automatic_candidate_mapping" if proposed_incident_ids
                    else "automatic_investigation_candidate" if proposed_investigation_id
                    else "unmapped"
                )
                if candidate_key:
                    record["source_case_id"] = f"investigation:{candidate_key}"

            record["retrieval_enabled"] = False
            if retrieval_requested and not explicit:
                if language and language != "en":
                    record["retrieval_block_reason"] = "non_english_translation_excluded_from_english_retrieval"
                elif not record.get("text_source_exists"):
                    record["retrieval_block_reason"] = "text_source_missing"
                elif not proposed_incident_ids:
                    record["retrieval_block_reason"] = "incident_mapping_missing"
                else:
                    record["retrieval_enabled"] = True
                    record["retrieval_block_reason"] = None
            elif explicit:
                record["retrieval_enabled"] = bool(
                    record.get("retrieval_requested") and record.get("text_source_exists")
                )
                record["retrieval_block_reason"] = (
                    None if record["retrieval_enabled"] else
                    "text_source_missing" if record.get("retrieval_requested") else None
                )
            else:
                record["retrieval_block_reason"] = (
                    "classification_excluded_from_incident_retrieval" if not retrieval_requested else None
                )
            continue

        # Rule-based documents without a curated candidate remain usable only when
        # their source identity and text are present. Incident linkage may be inferred
        # from another publication with the same investigation key.
        if explicit:
            record["classification_status"] = "explicit_source_override"
            record["mapping_status"] = "explicit_override"
            record["retrieval_enabled"] = bool(
                record.get("retrieval_requested") and record.get("text_source_exists")
            )
            if record.get("retrieval_requested") and not record.get("text_source_exists"):
                record["retrieval_block_reason"] = "text_source_missing"
        else:
            record["mapping_status"] = (
                "source_metadata_candidate" if record.get("investigation_id") else "unmapped"
            )
            if record.get("source_case_id") is None and record.get("recommendation_match_key"):
                record["source_case_id"] = f"investigation:{record['recommendation_match_key']}"
            record["retrieval_enabled"] = bool(
                record.get("retrieval_requested") and record.get("text_source_exists")
            )
            if record.get("retrieval_requested") and not record.get("text_source_exists"):
                record["retrieval_block_reason"] = "text_source_missing"
            elif not record.get("retrieval_requested"):
                record["retrieval_block_reason"] = "not_selected_by_automatic_classification"


def apply_known_alias_groups(
    records: list[dict[str, Any]],
    alias_groups: list[dict[str, Any]],
    *,
    two_digit_year_pivot: int = 30,
) -> None:
    """Apply explicit source-checked aliases without bypassing text availability checks."""
    for alias_group in alias_groups:
        target_urls = {u.rstrip("/") for u in alias_group.get("source_detail_urls", [])}
        for record in records:
            same_doc_id = record.get("document_id") == alias_group.get("document_id")
            same_url = bool(target_urls.intersection(
                u.rstrip("/") for u in record.get("source_detail_urls", [])
            ))
            if not (same_doc_id or same_url):
                continue
            record["title"] = alias_group.get("canonical_title", record["title"])
            record["investigation_id"] = alias_group.get("investigation_id", record.get("investigation_id"))
            record["incident_ids"] = alias_group.get("incident_ids", record.get("incident_ids", []))
            record["document_type"] = "investigation_report"
            record["corpus_role"] = "primary_incident_evidence"
            record["retrieval_requested"] = True
            record["retrieval_enabled"] = bool(record.get("text_source_exists"))
            record["retrieval_block_reason"] = (
                None if record.get("text_source_exists") else "text_source_missing"
            )
            record["mapping_status"] = "explicit_override"
            record["classification_status"] = "explicit_alias_override"
            record["classification_method"] = "configs_document_overrides_known_alias_groups"
            record["classification_confidence"] = "high"
            record["classification_basis"] = alias_group.get("note")
            record["source_case_id"] = f"investigation:{recommendation_match_key(record.get('investigation_id'), two_digit_year_pivot=two_digit_year_pivot)}"
            record["notes"] = alias_group.get("note")
            record["recommendation_match_key"] = recommendation_match_key(
                record.get("investigation_id"), two_digit_year_pivot=two_digit_year_pivot
            )


def build_catalog(
    manifest_path: Path = MANIFEST_PATH,
    audit_path: Path = AUDIT_PATH,
    overrides_path: Path = OVERRIDES_PATH,
    classification_candidates_path: Path = CLASSIFICATION_CANDIDATES_PATH,
) -> list[dict[str, Any]]:
    manifest = read_jsonl(manifest_path)
    audit = read_audit(audit_path)
    overrides = yaml.safe_load(overrides_path.read_text(encoding="utf-8")) or {}
    policy = _policy(overrides)
    pivot = int(policy.get("two_digit_year_pivot", 30))
    manual_overrides = {
        item["match"]["detail_url"].rstrip("/"): item
        for item in overrides.get("manual_overrides", [])
    }
    excluded_detail_urls = {
        item.rstrip("/") for item in overrides.get("replace_manifest_detail_urls", [])
    }
    # The old 12-page Macondo congressional-letter PDF is registered explicitly below.
    excluded_detail_urls.add("https://www.csb.gov/macondo-blowout-and-explosion")

    records: list[dict[str, Any]] = []
    for row in manifest:
        detail_url = (row.get("detail_url") or "").rstrip("/")
        if detail_url in excluded_detail_urls:
            continue
        row_audit = audit.get(row.get("local_pdf_path"))
        override = manual_overrides.get(detail_url)
        records.append(record_from_manifest(row, row_audit, override, two_digit_year_pivot=pivot))

    records.extend(
        registered_document_record(item, two_digit_year_pivot=pivot)
        for item in overrides.get("manual_documents", [])
    )
    records = merge_aliases(records)

    apply_known_alias_groups(
        records,
        overrides.get("known_alias_groups", []),
        two_digit_year_pivot=pivot,
    )

    apply_automatic_classifications(
        records,
        read_classification_candidates(classification_candidates_path),
        two_digit_year_pivot=pivot,
    )
    records.sort(key=lambda item: (
        item.get("candidate_investigation_id") or item.get("investigation_id") or "",
        item.get("document_id") or "",
    ))
    return records


def build_investigation_catalog(
    documents: list[dict[str, Any]],
    overrides: dict[str, Any],
    recommendations: list[dict[str, Any]] | None = None,
    *,
    two_digit_year_pivot: int = 30,
) -> list[dict[str, Any]]:
    """One record per investigation, with printed/proposed ID aliases kept separately."""
    grouped: dict[str, dict[str, Any]] = {}
    rec_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in recommendations or []:
        if rec.get("recommendation_match_key"):
            rec_by_key[rec["recommendation_match_key"]].append(rec)

    def get_group(investigation_id: str) -> dict[str, Any]:
        key = recommendation_match_key(
            investigation_id, two_digit_year_pivot=two_digit_year_pivot
        ) or f"id:{investigation_id}"
        return grouped.setdefault(key, {
            "investigation_id": investigation_id,
            "investigation_id_aliases": [],
            "recommendation_match_key": key.removeprefix("id:") if not key.startswith("id:") else None,
            "incident_ids": [],
            "candidate_incident_ids": [],
            "document_ids": [],
            "recommendation_ids": [],
            "source_detail_urls": [],
            "mapping_status": "source_metadata_candidate",
        })

    for document in documents:
        ids = sorted(set(filter(None, [
            document.get("investigation_id"), document.get("candidate_investigation_id")
        ])))
        if not ids:
            continue
        # The automatic candidate identifier is the preferred join identity; the
        # printed identifier remains preserved separately on the document record.
        preferred_id = document.get("candidate_investigation_id") or document.get("investigation_id")
        item = get_group(preferred_id)
        for current_id in ids:
            if current_id not in item["investigation_id_aliases"]:
                item["investigation_id_aliases"].append(current_id)
        if document.get("investigation_id"):
            item["investigation_id"] = document["investigation_id"]
        for value in document.get("incident_ids", []):
            if value not in item["incident_ids"]:
                item["incident_ids"].append(value)
        for value in (
            document.get("candidate_incident_ids", [])
            + document.get("inferred_candidate_incident_ids", [])
        ):
            if value not in item["candidate_incident_ids"]:
                item["candidate_incident_ids"].append(value)
        doc_id = document.get("document_id")
        if doc_id and doc_id not in item["document_ids"]:
            item["document_ids"].append(doc_id)
        for url in document.get("source_detail_urls", []):
            if url and url not in item["source_detail_urls"]:
                item["source_detail_urls"].append(url)
        for rec_id in document.get("recommendation_ids", []):
            if rec_id not in item["recommendation_ids"]:
                item["recommendation_ids"].append(rec_id)

    for override in overrides.get("investigation_overrides", []):
        investigation_id = override["investigation_id"]
        item = get_group(investigation_id)
        if investigation_id not in item["investigation_id_aliases"]:
            item["investigation_id_aliases"].append(investigation_id)
        for field in ["incident_ids", "recommendation_ids", "source_detail_urls"]:
            if field in override:
                item[field] = sorted(set(item.get(field, []) + override[field]))
        if override.get("mapping_status") or override.get("incident_ids"):
            item["mapping_status"] = "explicit_override"

    for item in grouped.values():
        key = item.get("recommendation_match_key")
        for rec in rec_by_key.get(key, []):
            if rec["recommendation_id"] not in item["recommendation_ids"]:
                item["recommendation_ids"].append(rec["recommendation_id"])
        for field in [
            "investigation_id_aliases", "incident_ids", "candidate_incident_ids",
            "document_ids", "recommendation_ids", "source_detail_urls",
        ]:
            item[field] = sorted(set(item.get(field, [])))
        if item["incident_ids"]:
            item["mapping_status"] = "verified_or_explicit_override"
        elif item["candidate_incident_ids"]:
            item["mapping_status"] = "automatic_candidate_mapping"
    return sorted(grouped.values(), key=lambda row: row["investigation_id"])


def _to_iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return str(value).strip()


def read_recommendation_snapshot(metadata_path: Path) -> dict[str, Any]:
    if not metadata_path.exists():
        return {}
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    label = metadata.get("label", "")
    match = DATE_IN_LABEL_RE.search(label)
    snapshot_date = None
    if match:
        try:
            snapshot_date = datetime.strptime(match.group(0), "%B %d, %Y").date().isoformat()
        except ValueError:
            snapshot_date = None
    return {
        "snapshot_date": snapshot_date,
        "snapshot_label": label or None,
        "source_page": metadata.get("source_page"),
        "download_url": metadata.get("download_url"),
        "local_path": metadata.get("local_path"),
        "sha256": metadata.get("sha256"),
        "collected_at_utc": metadata.get("collected_at_utc"),
    }


def load_recommendation_catalog(
    workbook_path: Path = RECOMMENDATIONS_PATH,
    metadata_path: Path = RECOMMENDATIONS_METADATA_PATH,
    *,
    two_digit_year_pivot: int = 30,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not workbook_path.exists():
        return [], read_recommendation_snapshot(metadata_path)
    snapshot = read_recommendation_snapshot(metadata_path)
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    worksheet = workbook["rec"] if "rec" in workbook.sheetnames else workbook.active
    iterator = worksheet.iter_rows(values_only=True)
    headers = [str(x).strip() if x is not None else "" for x in next(iterator)]
    required = ["Record Number", "Recommendation Text"]
    missing = [name for name in required if name not in headers]
    if missing:
        raise ValueError(f"Recommendations workbook is missing required columns: {missing}")
    indexes = {name: headers.index(name) for name in headers if name}

    def get(row: tuple[Any, ...], name: str) -> Any:
        idx = indexes.get(name)
        return row[idx] if idx is not None and idx < len(row) else None

    records = []
    for row in iterator:
        recommendation_id = str(get(row, "Record Number") or "").strip()
        if not recommendation_id:
            continue
        prefix = recommendation_prefix_from_record_number(recommendation_id)
        match_key = recommendation_match_key(prefix, two_digit_year_pivot=two_digit_year_pivot)
        records.append({
            "recommendation_id": recommendation_id,
            "recommendation_prefix": prefix,
            "recommendation_match_key": match_key,
            "case_folder_title": str(get(row, "Case/Folder title") or "").strip() or None,
            "status": str(get(row, "Recommendation Status") or "").strip() or None,
            "purpose": str(get(row, "Recommendation Purpose") or "").strip() or None,
            "date_issued": _to_iso(get(row, "Recommendation Date Issued")),
            "text": str(get(row, "Recommendation Text") or "").strip() or None,
            "urgent": str(get(row, "Urgent") or "").strip() or None,
            "recipient_type": str(get(row, "Recipient Type") or "").strip() or None,
            "recipient_name": str(get(row, "Recipient-Sort Name") or "").strip() or None,
            "board_action_dates": [
                value for value in [
                    _to_iso(get(row, "Board Action Date 1")),
                    _to_iso(get(row, "Board Action Date 2")),
                    _to_iso(get(row, "Board Action Date 3")),
                ] if value
            ],
            "date_closed": _to_iso(get(row, "Date Closed")),
            "incident_ids": [],
            "source_snapshot_date": snapshot.get("snapshot_date"),
            "source_snapshot_label": snapshot.get("snapshot_label"),
            "source_workbook": str(workbook_path.relative_to(ROOT)),
            "source_page": snapshot.get("source_page"),
            "collected_at_utc": snapshot.get("collected_at_utc"),
        })
    workbook.close()
    return records, snapshot


def build_incident_catalog(
    documents: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    *,
    two_digit_year_pivot: int = 30,
    snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build incident-level provenance and whole-investigation exclusion sets.

    Explicit config mappings are kept distinct from automatic candidate mappings.
    Sibling documents sharing an investigation key are conservatively included in
    every associated incident's exclusion bundle. Automatic candidates remain clearly
    provisional, but can be used for retrieval and leakage prevention. Recommendation
    ``incident_ids`` contains explicit mappings only; candidate links are separate.
    """
    snapshot = snapshot or {}
    recommendations_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in recommendations:
        key = rec.get("recommendation_match_key")
        if key:
            recommendations_by_key[key].append(rec)

    incident_groups: dict[str, dict[str, Any]] = {}
    investigation_key_to_incidents: dict[str, set[str]] = defaultdict(set)

    def ensure_group(incident_id: str) -> dict[str, Any]:
        return incident_groups.setdefault(incident_id, {
            "incident_id": incident_id,
            "titles": [],
            "investigation_ids": [],
            "document_ids": [],
            "confirmed_document_ids": [],
            "provisional_document_ids": [],
            "inferred_provisional_document_ids": [],
            "primary_document_ids": [],
            "supplementary_document_ids": [],
            "topic_reference_document_ids": [],
            "excluded_document_ids": [],
            "recommendation_ids": [],
            "verified_recommendation_ids": [],
            "candidate_recommendation_ids": [],
            "recommendation_match_keys": [],
            "verified_recommendation_match_keys": [],
            "candidate_recommendation_match_keys": [],
            "source_detail_urls": [],
            "mapping_status": "unmapped",
            "mapping_statuses": [],
            "retrieval_enabled": False,
            "recommendation_snapshot_date": snapshot.get("snapshot_date"),
        })

    def document_keys(document: dict[str, Any]) -> set[str]:
        keys = {
            str(document.get(field)).strip()
            for field in ("recommendation_match_key", "candidate_recommendation_match_key")
            if document.get(field)
        }
        for field in ("investigation_id", "candidate_investigation_id", "verified_investigation_id"):
            value = document.get(field)
            key = recommendation_match_key(value, two_digit_year_pivot=two_digit_year_pivot)
            if key:
                keys.add(key)
        return keys

    def effective_role_and_decision(document: dict[str, Any]) -> tuple[str | None, str | None]:
        return document.get("corpus_role"), document.get("classification_profile")

    def add_document_to_group(
        document: dict[str, Any],
        incident_id: str,
        *,
        confirmed: bool,
        inferred: bool = False,
        matched_keys: set[str] | None = None,
    ) -> None:
        group = ensure_group(incident_id)
        title = document.get("title")
        if title and title not in group["titles"]:
            group["titles"].append(title)

        investigation_ids = sorted(set(filter(None, [
            document.get("investigation_id"),
            document.get("candidate_investigation_id"),
            document.get("verified_investigation_id"),
        ])))
        for inv_id in investigation_ids:
            if inv_id not in group["investigation_ids"]:
                group["investigation_ids"].append(inv_id)

        doc_id = document.get("document_id")
        if doc_id:
            if doc_id not in group["document_ids"]:
                group["document_ids"].append(doc_id)
            if confirmed:
                if doc_id not in group["confirmed_document_ids"]:
                    group["confirmed_document_ids"].append(doc_id)
            else:
                if doc_id not in group["provisional_document_ids"]:
                    group["provisional_document_ids"].append(doc_id)
                if inferred and doc_id not in group["inferred_provisional_document_ids"]:
                    group["inferred_provisional_document_ids"].append(doc_id)

        role, decision = effective_role_and_decision(document)
        if role == "primary_incident_evidence":
            if doc_id and doc_id not in group["primary_document_ids"]:
                group["primary_document_ids"].append(doc_id)
        elif role == "supplementary_incident_evidence":
            if doc_id and doc_id not in group["supplementary_document_ids"]:
                group["supplementary_document_ids"].append(doc_id)
        elif role == "supplementary_topic_level_evidence":
            if doc_id and doc_id not in group["topic_reference_document_ids"]:
                group["topic_reference_document_ids"].append(doc_id)
        if role in {"excluded", "excluded_document"} or decision == "exclude":
            if doc_id and doc_id not in group["excluded_document_ids"]:
                group["excluded_document_ids"].append(doc_id)

        for url in document.get("source_detail_urls", []):
            if url and url not in group["source_detail_urls"]:
                group["source_detail_urls"].append(url)
        status = document.get("mapping_status") or "source_metadata_candidate"
        if status not in group["mapping_statuses"]:
            group["mapping_statuses"].append(status)

        if confirmed:
            group["mapping_status"] = "verified_or_explicit_override"
        elif group["mapping_status"] != "verified_or_explicit_override":
            group["mapping_status"] = "automatic_candidate_mapping"

        # Retrieval eligibility is independent of whether the incident link is a
        # human-verified override or an automatic candidate. The provenance remains
        # explicit, and evaluation source exclusion uses the complete case bundle.
        has_text = bool(document.get("text_source_exists", False))
        group["retrieval_enabled"] = group["retrieval_enabled"] or bool(
            document.get("retrieval_enabled") and has_text
        )

        keys = matched_keys if matched_keys is not None else document_keys(document)
        target = (
            "verified_recommendation_match_keys"
            if confirmed else "candidate_recommendation_match_keys"
        )
        for key in keys:
            if key and key not in group[target]:
                group[target].append(key)
            if key and key not in group["recommendation_match_keys"]:
                group["recommendation_match_keys"].append(key)

    # Pass 1: attach explicit confirmed and provisional incident mappings, and build
    # a normalized investigation-key -> incident index for conservative propagation.
    for document in documents:
        confirmed_ids = set(_split_values(document.get("incident_ids")))
        candidate_ids = set(_split_values(document.get("candidate_incident_ids")))
        all_ids = sorted(confirmed_ids | candidate_ids)
        keys = document_keys(document)
        for incident_id in all_ids:
            confirmed = incident_id in confirmed_ids and _is_explicit_mapping(document)
            add_document_to_group(document, incident_id, confirmed=bool(confirmed), matched_keys=keys)
            for key in keys:
                investigation_key_to_incidents[key].add(incident_id)

    # Pass 2: attach every sibling publication sharing an investigation key to all
    # incidents mapped to that investigation. This deliberately over-excludes in
    # multi-event investigations, but prevents an appendix, volume, translation,
    # summary or correspondence file from escaping source-case exclusion.
    for document in documents:
        keys = document_keys(document)
        if not keys:
            continue
        direct_ids = set(_split_values(document.get("incident_ids"))) | set(
            _split_values(document.get("candidate_incident_ids"))
        )
        linked_ids = set().union(*(investigation_key_to_incidents.get(key, set()) for key in keys))
        inferred_ids = sorted(linked_ids - direct_ids)
        if inferred_ids:
            existing = set(_split_values(document.get("inferred_candidate_incident_ids")))
            document["inferred_candidate_incident_ids"] = sorted(existing | set(inferred_ids))
            document["inferred_candidate_mapping_basis"] = "shared_investigation_recommendation_match_key"
        for incident_id in inferred_ids:
            matching_keys = {key for key in keys if incident_id in investigation_key_to_incidents.get(key, set())}
            add_document_to_group(
                document,
                incident_id,
                confirmed=False,
                inferred=True,
                matched_keys=matching_keys,
            )

    # Pass 3: resolve verified vs candidate recommendation links separately.
    recommendation_verified_incidents: dict[str, set[str]] = defaultdict(set)
    recommendation_candidate_incidents: dict[str, set[str]] = defaultdict(set)
    for incident_id, group in incident_groups.items():
        verified_ids: set[str] = set()
        candidate_ids: set[str] = set()
        for key in group["verified_recommendation_match_keys"]:
            verified_ids.update(rec["recommendation_id"] for rec in recommendations_by_key.get(key, []))
        for key in group["candidate_recommendation_match_keys"]:
            candidate_ids.update(rec["recommendation_id"] for rec in recommendations_by_key.get(key, []))
        candidate_ids -= verified_ids
        all_ids = verified_ids | candidate_ids
        group["verified_recommendation_ids"] = sorted(verified_ids)
        group["candidate_recommendation_ids"] = sorted(candidate_ids)
        group["recommendation_ids"] = sorted(all_ids)
        for rec_id in verified_ids:
            recommendation_verified_incidents[rec_id].add(incident_id)
        for rec_id in candidate_ids:
            recommendation_candidate_incidents[rec_id].add(incident_id)

    for rec in recommendations:
        rec_id = rec["recommendation_id"]
        verified = recommendation_verified_incidents.get(rec_id, set())
        candidate = recommendation_candidate_incidents.get(rec_id, set()) - verified
        rec["incident_ids"] = sorted(verified)
        rec["candidate_incident_ids"] = sorted(candidate)
        if verified and candidate:
            rec["incident_link_status"] = "mixed_verified_and_candidate"
        elif verified:
            rec["incident_link_status"] = "verified"
        elif candidate:
            rec["incident_link_status"] = "candidate_only"
        else:
            rec["incident_link_status"] = "unlinked"

    # Finalize stable, sorted group fields and conservative source-case exclusion sets.
    for group in incident_groups.values():
        for field in [
            "titles", "investigation_ids", "document_ids", "confirmed_document_ids",
            "provisional_document_ids", "inferred_provisional_document_ids",
            "primary_document_ids", "supplementary_document_ids",
            "topic_reference_document_ids", "excluded_document_ids", "recommendation_ids",
            "verified_recommendation_ids", "candidate_recommendation_ids",
            "recommendation_match_keys", "verified_recommendation_match_keys",
            "candidate_recommendation_match_keys", "source_detail_urls", "mapping_statuses",
        ]:
            group[field] = sorted(set(group[field]))

        has_confirmed_mapping = bool(group["confirmed_document_ids"])
        has_automatic_mapping = bool(group["provisional_document_ids"])
        if has_confirmed_mapping and has_automatic_mapping:
            group["catalog_status"] = "mixed_verified_and_automatic_candidate"
        elif has_confirmed_mapping:
            group["catalog_status"] = "verified_or_explicit_override"
        elif has_automatic_mapping:
            group["catalog_status"] = "automatic_candidate_mapping"
        else:
            group["catalog_status"] = "unmapped"

        # All catalogued documents and all recommendations on the shared
        # investigation key are included in this conservative exclusion bundle.
        # A provisional mapping does not mean a false claim of verification; it is
        # simply sufficient to exclude the entire candidate source case from eval.
        group["source_case_exclusion_ready"] = bool(
            group["document_ids"] and (has_confirmed_mapping or has_automatic_mapping)
        )
        group["source_case_exclusion_status"] = (
            "verified_scope" if has_confirmed_mapping and not has_automatic_mapping
            else "whole_investigation_candidate_scope" if has_automatic_mapping
            else "unavailable"
        )
        group["retrieval_enabled"] = bool(
            group["source_case_exclusion_ready"] and group["retrieval_enabled"]
        )
        source_case_keys = sorted(set(group["recommendation_match_keys"]))
        group["source_case_exclusion"] = {
            "incident_id": group["incident_id"],
            "source_case_ids": [f"investigation:{key}" for key in source_case_keys],
            "scope": "whole_investigation",
            "investigation_ids": group["investigation_ids"],
            "document_ids": group["document_ids"],
            # The union is intentionally conservative for exclusion. Recommendation
            # catalog rows separately expose verified vs candidate incident links.
            "recommendation_ids": group["recommendation_ids"],
            "recommendation_snapshot_date": snapshot.get("snapshot_date"),
        }

    return sorted(incident_groups.values(), key=lambda row: row["incident_id"])

def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _write_csv(path: Path, records: list[dict[str, Any]], fields: list[str], list_fields: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = dict(record)
            for field in list_fields:
                if field in row:
                    row[field] = ";".join(str(x) for x in row.get(field, []))
            writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-jsonl", type=Path, default=OUT_JSONL)
    parser.add_argument("--output-csv", type=Path, default=OUT_CSV)
    args = parser.parse_args()

    overrides = yaml.safe_load(OVERRIDES_PATH.read_text(encoding="utf-8")) or {}
    pivot = int(_policy(overrides).get("two_digit_year_pivot", 30))

    documents = build_catalog()
    recommendations, snapshot = load_recommendation_catalog(two_digit_year_pivot=pivot)
    incidents = build_incident_catalog(
        documents, recommendations, two_digit_year_pivot=pivot, snapshot=snapshot
    )
    investigations = build_investigation_catalog(
        documents, overrides, recommendations, two_digit_year_pivot=pivot
    )

    _write_jsonl(args.output_jsonl, documents)
    document_fields = [
        "document_id", "title", "csb_document_id", "raw_investigation_number",
        "investigation_id", "candidate_investigation_id", "recommendation_match_key",
        "incident_ids", "candidate_incident_ids", "inferred_candidate_incident_ids",
        "inferred_candidate_mapping_basis", "document_type", "corpus_role",
        "retrieval_requested", "retrieval_enabled", "retrieval_block_reason", "local_path", "file_exists", "text_source_path",
        "text_source_exists", "page_count", "language", "text_extraction_method",
        "ocr_status", "ocr_pages_reviewed", "ocr_pages_to_review",
        "source_case_id", "mapping_status", "classification_status", "classification_method",
        "classification_profile", "classification_confidence", "classification_basis",
        "classification_note", "classification_follow_up", "classification_source",
        "source_detail_urls", "source_title_aliases", "source_sha256", "quality_note", "notes",
    ]
    _write_csv(args.output_csv, documents, document_fields, {
        "incident_ids", "candidate_incident_ids", "inferred_candidate_incident_ids",
        "ocr_pages_reviewed", "ocr_pages_to_review", "source_detail_urls", "source_title_aliases",
    })

    _write_jsonl(OUT_INVESTIGATIONS_JSONL, investigations)
    investigation_fields = [
        "investigation_id", "recommendation_match_key", "incident_ids",
        "candidate_incident_ids", "document_ids", "recommendation_ids",
        "source_detail_urls", "mapping_status",
    ]
    _write_csv(OUT_INVESTIGATIONS_CSV, investigations, investigation_fields, {
        "incident_ids", "candidate_incident_ids", "document_ids",
        "recommendation_ids", "source_detail_urls",
    })

    _write_jsonl(OUT_INCIDENTS_JSONL, incidents)
    incident_fields = [
        "incident_id", "titles", "investigation_ids", "document_ids",
        "confirmed_document_ids", "provisional_document_ids",
        "primary_document_ids", "supplementary_document_ids",
        "topic_reference_document_ids", "excluded_document_ids",
        "recommendation_ids", "verified_recommendation_ids", "candidate_recommendation_ids",
        "recommendation_match_keys", "verified_recommendation_match_keys",
        "candidate_recommendation_match_keys", "inferred_provisional_document_ids", "source_detail_urls",
        "mapping_status", "catalog_status", "mapping_statuses", "retrieval_enabled",
        "recommendation_snapshot_date", "source_case_exclusion_ready", "source_case_exclusion_status",
    ]
    _write_csv(OUT_INCIDENTS_CSV, incidents, incident_fields, {
        "titles", "investigation_ids", "document_ids", "confirmed_document_ids",
        "provisional_document_ids", "primary_document_ids", "supplementary_document_ids",
        "topic_reference_document_ids",
        "excluded_document_ids", "recommendation_ids", "verified_recommendation_ids",
        "candidate_recommendation_ids", "recommendation_match_keys",
        "verified_recommendation_match_keys", "candidate_recommendation_match_keys",
        "inferred_provisional_document_ids", "source_detail_urls", "mapping_statuses",
    })

    _write_jsonl(OUT_RECOMMENDATIONS_JSONL, recommendations)
    recommendation_fields = [
        "recommendation_id", "recommendation_prefix", "recommendation_match_key",
        "case_folder_title", "status", "purpose", "date_issued", "text", "urgent",
        "recipient_type", "recipient_name", "board_action_dates", "date_closed",
        "incident_ids", "candidate_incident_ids", "incident_link_status",
        "source_snapshot_date", "source_snapshot_label",
        "source_workbook", "source_page", "collected_at_utc",
    ]
    _write_csv(OUT_RECOMMENDATIONS_CSV, recommendations, recommendation_fields, {
        "board_action_dates", "incident_ids", "candidate_incident_ids",
    })

    incident_status_counts: dict[str, int] = defaultdict(int)
    for incident in incidents:
        incident_status_counts[incident["catalog_status"]] += 1
    summary = {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_records": len(read_jsonl(MANIFEST_PATH)),
        "document_catalog_records": len(documents),
        "investigation_catalog_records": len(investigations),
        "incident_catalog_records": len(incidents),
        "incident_catalog_status_counts": dict(sorted(incident_status_counts.items())),
        "incidents_ready_for_source_case_exclusion": sum(
            bool(item.get("source_case_exclusion_ready")) for item in incidents
        ),
        "incidents_with_provisional_documents": sum(
            bool(item.get("provisional_document_ids")) for item in incidents
        ),
        "recommendation_catalog_records": len(recommendations),
        "recommendations_linked_to_incident_ids": sum(bool(r.get("incident_ids")) for r in recommendations),
        "recommendations_with_candidate_incident_links": sum(
            bool(r.get("candidate_incident_ids")) for r in recommendations
        ),
        "verified_recommendation_incident_links": sum(len(r.get("incident_ids", [])) for r in recommendations),
        "candidate_recommendation_incident_links": sum(
            len(r.get("candidate_incident_ids", [])) for r in recommendations
        ),
        "documents_with_inferred_candidate_incident_mapping": sum(
            bool(d.get("inferred_candidate_incident_ids")) for d in documents
        ),
        "inferred_provisional_document_incident_links": sum(
            len(d.get("inferred_candidate_incident_ids", [])) for d in documents
        ),
        "recommendation_snapshot": snapshot,
        "classification_candidate_records": len(read_classification_candidates(CLASSIFICATION_CANDIDATES_PATH)),
        "automatically_classified_documents": sum(
            d.get("classification_status") == "automatic_candidate" for d in documents
        ),
        "documents_with_rule_based_classification": sum(
            d.get("classification_method") == "title_and_first_page_rules" for d in documents
        ),
        "documents_requested_but_blocked_by_missing_text": sum(
            bool(d.get("retrieval_requested")) and d.get("retrieval_block_reason") == "text_source_missing"
            for d in documents
        ),
        "documents_requested_but_blocked_by_missing_incident_mapping": sum(
            bool(d.get("retrieval_requested")) and d.get("retrieval_block_reason") == "incident_mapping_missing"
            for d in documents
        ),
        "incidents_enabled_for_retrieval": sum(bool(item.get("retrieval_enabled")) for item in incidents),
        "explicit_source_override_documents": sum(
            d.get("mapping_status") == "explicit_override" for d in documents
        ),
        "documents_with_candidate_incident_ids": sum(bool(d.get("candidate_incident_ids")) for d in documents),
        "documents_with_local_files": sum(bool(d.get("file_exists")) for d in documents),
        "documents_with_text_source_files": sum(bool(d.get("text_source_exists")) for d in documents),
        "outputs": [
            str(args.output_jsonl), str(args.output_csv),
            str(OUT_INVESTIGATIONS_JSONL), str(OUT_INVESTIGATIONS_CSV),
            str(OUT_INCIDENTS_JSONL), str(OUT_INCIDENTS_CSV),
            str(OUT_RECOMMENDATIONS_JSONL), str(OUT_RECOMMENDATIONS_CSV),
        ],
        "important_note": (
            "Document-level manual review is not part of the workflow. Candidate classifications and "
            "candidate incident mappings are machine-applied from source-check metadata and remain "
            "explicitly provisional; only configs/document_overrides.yaml mappings are marked as "
            "explicit overrides. Source-case exclusion uses the whole investigation key and includes "
            "all catalogued sibling documents plus the union of linked recommendation IDs. For a "
            "multi-event investigation this can over-exclude sibling events, intentionally favoring "
            "leakage prevention. Recommendation incident_ids contains explicit/verified links only; "
            "candidate_incident_ids retains automatic candidate links. Recommendation statuses are "
            "always tied to the dated source workbook snapshot."
        ),
    }
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
