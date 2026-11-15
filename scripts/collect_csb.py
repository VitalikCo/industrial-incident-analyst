"""Resumable CSB collector with conservative primary-final-report selection.

Examples:
    uv run python scripts/collect_csb.py --max-pages 30 --max-reports 200
    uv run python scripts/collect_csb.py --only-detail-url https://www.csb.gov/bio-lab-inc-conyers-fire-and-chemical-release-/

The collector stores one primary investigation report per incident page. Appendices,
updates and urgent-recommendation-only documents are recorded as supplementary links,
not silently indexed as the primary final report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
RAW_REPORTS = ROOT / "data" / "raw" / "reports"
RAW_RECOMMENDATIONS = ROOT / "data" / "raw" / "recommendations"
MANIFEST = ROOT / "data" / "processed" / "manifest.jsonl"
SUMMARY = ROOT / "data" / "processed" / "collection_summary.json"
INDEX_URL = "https://www.csb.gov/investigations/completed-investigations/"
RECOMMENDATIONS_URL = "https://www.csb.gov/recommendations/statistics/"
USER_AGENT = "IndustrialIncidentAnalyst/0.3 (educational research; polite public-data collection)"
REC_ID_RE = re.compile(r"\b\d{4}-\d{2}-[A-Z]-[A-Z]{2}-\d+\b")
DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")

# Documents matching these labels are supporting material, not the main incident report.
SUPPLEMENTARY_LABELS = (
    "appendix", "appendices", "update i", "update ii", "update iii",
    "urgent recommendation", "consultant report", "testing data", "status change",
    "recommendation status", "investigation update", "factual update",
)
TRANSLATION_LABELS = (
    "spanish", "español", "fuga fatal", "fuga de", "informe de investigación",
    "reporte de investigación", "versión en español", "version en espanol",
)


@dataclass
class InvestigationRecord:
    title: str
    detail_url: str
    source_index_url: str
    location: str | None = None
    incident_date: str | None = None
    final_report_release_date: str | None = None
    incident_type: str | None = None
    summary: str | None = None
    recommendation_ids: list[str] | None = None
    final_report_url: str | None = None
    local_pdf_path: str | None = None
    pdf_sha256: str | None = None
    supplementary_documents: list[dict[str, str]] | None = None
    collection_status: str = "metadata_only"
    error: str | None = None
    collected_at_utc: str = ""


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*"})
    retry = Retry(
        total=3, connect=3, read=2, status=2, backoff_factor=2.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}), respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def clean_text(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower().strip())
    return value.strip("-")[:110] or "csb-investigation"


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    if urlparse(response.url).path.lower().endswith("/500.aspx"):
        raise RuntimeError(f"CSB redirected {url} to its /500.aspx error page")
    return BeautifulSoup(response.text, "html.parser")


def discover_detail_links(soup: BeautifulSoup, page_url: str) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    for anchor in soup.find_all("a", href=True):
        label = clean_text(anchor.get_text(" ", strip=True)) or ""
        if "full investigation details" not in label.lower():
            continue
        href = urljoin(page_url, anchor["href"])
        if urlparse(href).netloc.lower().endswith("csb.gov"):
            title = ""
            parent = anchor.parent
            for _ in range(4):
                if parent is None:
                    break
                candidates = [clean_text(a.get_text(" ", strip=True)) for a in parent.find_all("a", href=True)]
                candidates = [t for t in candidates if t and "investigation details" not in t.lower() and len(t) > 5]
                if candidates:
                    title = max(candidates, key=len)
                    break
                parent = parent.parent
            found[href] = title or "Untitled CSB investigation"
    return list(found.items())


def extract_label_value(text: str, label: str) -> str | None:
    pattern = rf"{re.escape(label)}\s*:?\s*(.+?)(?=\s+(?:Accident Occurred On|Final Report Released On|Accident Type|Investigation Status|Company):|\n|$)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return clean_text(match.group(1)) if match else None


def extract_date_after_label(text: str, label: str) -> str | None:
    match = re.search(rf"{re.escape(label)}\s*:?\s*({DATE_RE.pattern})", text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def is_translation_label(label: str) -> bool:
    """Return True for CSB report links explicitly labelled as translations."""
    low = clean_text(label) or ""
    low = low.casefold()
    return any(term in low for term in TRANSLATION_LABELS)


def csb_document_id(url: str | None) -> str | None:
    """Extract the case-insensitive DocumentId query parameter from a CSB file URL."""
    if not url:
        return None
    query = parse_qs(urlparse(url).query)
    values = query.get("DocumentId") or query.get("documentid") or []
    return values[0] if values else None


def select_primary_report(anchors: list[tuple[str, str]]) -> tuple[str | None, list[dict[str, str]]]:
    """Prefer an English final report; retain translations and other PDFs as supplements.

    CSB detail pages may list both an English final report and a Spanish translation.
    The earlier shortest-label preference selected the Spanish file for Cuisine Solutions
    and Foundation Food Group. Translation-only pages still fall back to the translation,
    but it is never preferred over an available non-translation final report.
    """
    candidates: list[tuple[str, str]] = []
    for label, url in anchors:
        if "final report" in label.casefold():
            candidates.append((label, url))

    english_primary: list[tuple[str, str]] = []
    translations: list[tuple[str, str]] = []
    supplementary: list[dict[str, str]] = []

    for label, url in candidates:
        low = label.casefold()
        if is_translation_label(label):
            translations.append((label, url))
            supplementary.append({"label": label, "url": url, "reason": "language_translation"})
        elif any(term in low for term in SUPPLEMENTARY_LABELS):
            supplementary.append({"label": label, "url": url, "reason": "supplementary_or_update"})
        else:
            english_primary.append((label, url))

    primary = english_primary or translations
    if not primary:
        # A page containing only an appendix/update must not be mistaken for the final report.
        return None, supplementary

    # If no English report exists, a translation is the only available primary source;
    # do not list the selected fallback as its own supplementary document.
    if not english_primary and translations:
        chosen_translation = translations[0]
        supplementary = [
            item for item in supplementary
            if item.get("url") != chosen_translation[1]
        ]

    # Prefer an explicit "FINAL REPORT: ..." label, then the most descriptive title.
    primary.sort(key=lambda item: ("final report:" not in item[0].casefold(), -len(item[0])))
    chosen_label, chosen_url = primary[0]

    for label, url in primary[1:]:
        supplementary.append({
            "label": label,
            "url": url,
            "reason": "alternate_final_report_language_or_version",
        })
    return chosen_url, supplementary


def parse_detail_page(session: requests.Session, detail_url: str, index_url: str, fallback_title: str, timeout: int) -> InvestigationRecord:
    soup = fetch_soup(session, detail_url, timeout)
    h1 = soup.find("h1")
    title = clean_text(h1.get_text(" ", strip=True)) if h1 else fallback_title
    text = soup.get_text("\n", strip=True)
    summary = None
    accident_heading = soup.find(string=re.compile(r"Accident Description", re.IGNORECASE))
    if accident_heading:
        node = accident_heading.parent
        container = node.parent if node and node.parent else node
        if container:
            candidate = clean_text(container.get_text(" ", strip=True))
            if candidate and len(candidate) > 40:
                summary = candidate[:1800]
    if not summary:
        marker = re.search(r"(?:On|At approximately)\s+.{40,1000}", text)
        if marker:
            summary = clean_text(marker.group(0))[:1800]

    report_anchors: list[tuple[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(detail_url, anchor["href"])
        label = clean_text(anchor.get_text(" ", strip=True)) or ""
        if "file.aspx?documentid=" in href.lower() and "final report" in label.lower():
            report_anchors.append((label, href))
    final_report_url, supplementary = select_primary_report(report_anchors)
    recommendations = sorted(set(REC_ID_RE.findall(text)))
    return InvestigationRecord(
        title=title or fallback_title,
        detail_url=detail_url,
        source_index_url=index_url,
        location=extract_label_value(text, "Location"),
        incident_date=extract_date_after_label(text, "Accident Occurred On"),
        final_report_release_date=extract_date_after_label(text, "Final Report Released On"),
        incident_type=extract_label_value(text, "Accident Type"),
        summary=summary,
        recommendation_ids=recommendations,
        final_report_url=final_report_url,
        supplementary_documents=supplementary,
        collection_status="detail_page_parsed" if final_report_url else "no_primary_final_report_link_found",
        collected_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_valid_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as file:
            magic = file.read(5)
        return path.is_file() and path.stat().st_size > 1024 and magic.startswith(b"%PDF")
    except OSError:
        return False


def download_pdf(session: requests.Session, record: InvestigationRecord, timeout: int, force: bool = False) -> None:
    if not record.final_report_url:
        return
    RAW_REPORTS.mkdir(parents=True, exist_ok=True)
    document_id = csb_document_id(record.final_report_url)
    suffix = f"--csb-{document_id}" if document_id else ""
    path = RAW_REPORTS / f"{slugify(record.title)}{suffix}.pdf"
    if not force and is_valid_pdf(path):
        record.local_pdf_path = str(path.relative_to(ROOT))
        record.pdf_sha256 = sha256_file(path)
        record.collection_status = "pdf_already_present"
        record.error = None
        return

    temp_path = path.with_suffix(".pdf.part")
    temp_path.unlink(missing_ok=True)
    response = session.get(record.final_report_url, timeout=timeout, stream=True)
    response.raise_for_status()
    first = b""
    try:
        with temp_path.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 128):
                if not chunk:
                    continue
                if not first:
                    first = chunk[:8]
                file.write(chunk)
    finally:
        response.close()
    if not first.startswith(b"%PDF"):
        temp_path.unlink(missing_ok=True)
        raise ValueError(f"URL did not return a PDF (first bytes: {first!r})")
    temp_path.replace(path)
    record.local_pdf_path = str(path.relative_to(ROOT))
    record.pdf_sha256 = sha256_file(path)
    record.collection_status = "downloaded" if not force else "replaced_with_primary_report"
    record.error = None


def download_recommendations_xlsx(session: requests.Session, timeout: int) -> dict[str, Any]:
    RAW_RECOMMENDATIONS.mkdir(parents=True, exist_ok=True)
    path = RAW_RECOMMENDATIONS / "csb_recommendations_latest.xlsx"
    metadata_path = RAW_RECOMMENDATIONS / "download_metadata.json"
    if path.exists() and path.stat().st_size > 1024 and path.read_bytes()[:2] == b"PK":
        info = {
            "label": "Existing local CSB recommendations spreadsheet (reused)",
            "source_page": RECOMMENDATIONS_URL,
            "local_path": str(path.relative_to(ROOT)),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "collected_at_utc": datetime.now(timezone.utc).isoformat(),
            "reused_existing_file": True,
        }
        if metadata_path.exists():
            try:
                info["download_url"] = json.loads(metadata_path.read_text(encoding="utf-8")).get("download_url")
            except (OSError, json.JSONDecodeError):
                pass
        return info
    soup = fetch_soup(session, RECOMMENDATIONS_URL, timeout)
    candidate = None
    for anchor in soup.find_all("a", href=True):
        label = clean_text(anchor.get_text(" ", strip=True)) or ""
        href = urljoin(RECOMMENDATIONS_URL, anchor["href"])
        if "recommendation" in label.lower() and ("data" in label.lower() or href.lower().endswith(".xlsx")):
            candidate = (label, href)
            break
    if not candidate:
        raise RuntimeError("Could not find the official recommendations spreadsheet link")
    label, url = candidate
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    if not response.content.startswith(b"PK"):
        raise ValueError("Recommendations download did not look like XLSX")
    path.write_bytes(response.content)
    return {
        "label": label, "source_page": RECOMMENDATIONS_URL, "download_url": url,
        "local_path": str(path.relative_to(ROOT)), "bytes": len(response.content),
        "sha256": hashlib.sha256(response.content).hexdigest(),
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def load_manifest() -> list[InvestigationRecord]:
    if not MANIFEST.exists():
        return []
    loaded: dict[str, InvestigationRecord] = {}
    allowed = set(InvestigationRecord.__dataclass_fields__)
    with MANIFEST.open("r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                record = InvestigationRecord(**{k: v for k, v in payload.items() if k in allowed})
                loaded[record.detail_url] = record
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                logging.warning("Skipping malformed manifest line %d: %s", line_no, exc)
    return list(loaded.values())


def is_complete(record: InvestigationRecord) -> bool:
    if record.local_pdf_path:
        return is_valid_pdf(ROOT / record.local_pdf_path)
    return record.collection_status == "no_primary_final_report_link_found"


def write_manifest(records: list[InvestigationRecord]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    temp = MANIFEST.with_suffix(".jsonl.tmp")
    with temp.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    temp.replace(MANIFEST)


def write_summary(records: list[InvestigationRecord], pages_checked: int, failures: int,
                  recommendations_info: dict[str, Any] | None, interrupted: bool) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for record in records:
        status_counts[record.collection_status] = status_counts.get(record.collection_status, 0) + 1
    summary = {
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "archive_pages_checked_this_run": pages_checked,
        "investigation_detail_pages_in_manifest": len(records),
        "records_with_valid_final_report_pdf": sum(
            bool(r.local_pdf_path and is_valid_pdf(ROOT / r.local_pdf_path)) for r in records
        ),
        "records_without_valid_final_report_pdf": sum(
            not bool(r.local_pdf_path and is_valid_pdf(ROOT / r.local_pdf_path)) for r in records
        ),
        "status_counts": status_counts,
        "failures_this_run": failures,
        "interrupted_by_user": interrupted,
        "recommendations_spreadsheet": recommendations_info,
        "manifest": str(MANIFEST.relative_to(ROOT)),
    }
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    temp = SUMMARY.with_suffix(".json.tmp")
    temp.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(SUMMARY)
    return summary


def collect(args: argparse.Namespace) -> int:
    RAW_REPORTS.mkdir(parents=True, exist_ok=True)
    session = build_session()
    records_by_url = {r.detail_url: r for r in (load_manifest() if args.resume else [])}
    logging.info("Loaded %d existing manifest records", len(records_by_url))
    pages_checked = 0
    failures = 0
    interrupted = False
    recommendations_info = None
    target_found = False

    try:
        for page_number in range(1, args.max_pages + 1):
            page_url = INDEX_URL if page_number == 1 else f"{INDEX_URL}?pg={page_number}"
            logging.info("Reading archive page %s", page_url)
            try:
                soup = fetch_soup(session, page_url, args.timeout)
                pages_checked += 1
                links = discover_detail_links(soup, page_url)
            except Exception as exc:
                failures += 1
                logging.error("Archive page failed (%s): %s", page_url, f"{type(exc).__name__}: {exc}")
                break
            if not links:
                logging.info("No investigation detail links found on page %d; stopping", page_number)
                break

            for detail_url, fallback_title in links:
                if args.only_detail_url and detail_url.rstrip("/") != args.only_detail_url.rstrip("/"):
                    continue
                target_found = target_found or bool(args.only_detail_url)
                existing = records_by_url.get(detail_url)
                force_reparse = bool(args.only_detail_url)
                if existing and is_complete(existing) and not force_reparse:
                    logging.debug("Skipping already collected record: %s", existing.title)
                    continue
                if detail_url not in records_by_url and len(records_by_url) >= args.max_reports:
                    break
                try:
                    record = parse_detail_page(session, detail_url, page_url, fallback_title, args.timeout)
                    old_url = existing.final_report_url if existing else None
                    force_download = bool(force_reparse and old_url and old_url != record.final_report_url)
                    if args.download_pdfs and record.final_report_url:
                        try:
                            download_pdf(session, record, args.timeout, force=force_download)
                        except Exception as exc:
                            record.error = f"PDF download failed: {type(exc).__name__}: {exc}"
                            record.collection_status = "pdf_download_failed"
                            failures += 1
                    elif not record.final_report_url:
                        record.collection_status = "no_primary_final_report_link_found"
                        # Preserve any old local file reference for audit; do not silently delete it.
                        if existing and existing.local_pdf_path:
                            record.local_pdf_path = existing.local_pdf_path
                            record.pdf_sha256 = existing.pdf_sha256
                            record.collection_status = "supplementary_document_only_existing_pdf_needs_review"
                    records_by_url[detail_url] = record
                    write_manifest(list(records_by_url.values()))
                    logging.info("%s — %s", record.collection_status, record.title)
                except Exception as exc:
                    failures += 1
                    failed = InvestigationRecord(
                        title=fallback_title, detail_url=detail_url, source_index_url=page_url,
                        collection_status="detail_page_failed", error=f"{type(exc).__name__}: {exc}",
                        collected_at_utc=datetime.now(timezone.utc).isoformat(),
                    )
                    records_by_url[detail_url] = failed
                    write_manifest(list(records_by_url.values()))
                    logging.error("Detail page failed (%s): %s", detail_url, failed.error)
                time.sleep(args.delay)
                if args.only_detail_url:
                    break
            if args.only_detail_url and target_found:
                break
            if len(records_by_url) >= args.max_reports:
                logging.info("Reached --max-reports=%d", args.max_reports)
                break
            time.sleep(args.delay)

        if args.only_detail_url and not target_found:
            logging.error("Target detail URL was not found in the archive: %s", args.only_detail_url)
            failures += 1
        if args.download_recommendations and not args.only_detail_url:
            try:
                recommendations_info = download_recommendations_xlsx(session, args.timeout)
                info_path = RAW_RECOMMENDATIONS / "download_metadata.json"
                info_path.write_text(json.dumps(recommendations_info, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:
                failures += 1
                logging.error("Recommendations spreadsheet failed: %s", f"{type(exc).__name__}: {exc}")
    except KeyboardInterrupt:
        interrupted = True
        logging.warning("Interrupted by user. Saving manifest and summary before exit.")
    finally:
        records = list(records_by_url.values())
        write_manifest(records)
        print(json.dumps(write_summary(records, pages_checked, failures, recommendations_info, interrupted), indent=2, ensure_ascii=False))

    return 130 if interrupted else (0 if records_by_url else 1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--max-reports", type=int, default=200)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--download-pdfs", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--download-recommendations", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--only-detail-url", default=None,
                        help="Reparse and refresh one detail page only; all other records are skipped")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.max_pages < 1 or args.max_reports < 1 or args.timeout < 1 or args.delay < 0:
        parser.error("max-pages, max-reports and timeout must be positive; delay cannot be negative")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return args


if __name__ == "__main__":
    sys.exit(collect(parse_args()))
