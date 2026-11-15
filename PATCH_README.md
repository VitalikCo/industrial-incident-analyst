# Prefer English CSB final reports for the two translated records

The current local files were identified as the Spanish CSB PDFs:

- Cuisine Solutions: CSB DocumentId `6323` (Spanish). The English final report is DocumentId `6304`.
- Foundation Food Group: CSB DocumentId `6269` (Spanish). The English final report is DocumentId `6268`.

This patch makes the CSB collector prefer an English final report when an investigation page lists both English and Spanish finals. It records the translation link as a supplementary `language_translation` item. It also writes downloaded PDFs with a `--csb-<DocumentId>` suffix so refreshing the English original does not overwrite the existing Spanish file.

## Apply in Ubuntu WSL

Extract this patch into the project root (the directory containing `scripts/`, `tests/`, and `data/`).

Then refresh the two CSB detail pages only:

```bash
uv run python scripts/collect_csb.py --only-detail-url https://www.csb.gov/cuisine-solutions-ammonia-release-/
uv run python scripts/collect_csb.py --only-detail-url https://www.csb.gov/foundation-food-group-fatal-chemical-release-/
```

Expected new PDF names:

```text
data/raw/reports/cuisine-solutions-ammonia-release--csb-6304.pdf
data/raw/reports/foundation-food-group-fatal-chemical-release--csb-6268.pdf
```

Run the PDF audit and update the two candidate rows to the English DocumentIds:

```bash
uv run python scripts/audit_pdf_contents.py
uv run python scripts/refresh_english_report_candidates.py
```

The candidate updater changes CSB-6323 to CSB-6304 and CSB-6269 to CSB-6268, updates the file/audit provenance, changes the classification profile to `primary_report`, and preserves both candidate incident IDs. It refuses to update if the English PDFs are not in the manifest, are missing locally, or have no successful audit row.

Finally rebuild and test:

```bash
uv run python scripts/build_document_catalog.py
uv run pytest -q
```

The existing unsuffixed files (`cuisine-solutions-ammonia-release.pdf` and `foundation-food-group-fatal-chemical-release.pdf`) are the Spanish versions. Keep them; the collector downloads the English PDFs to distinct DocumentId-suffixed filenames.

## Expected catalog effect

The two English PDFs replace the two Spanish IDs as the main manifest documents; the number of manifest/document rows should not increase. The Cuisine Solutions and Foundation Food Group incidents should become retrieval-enabled, bringing the expected incident retrieval count from 75 to 77, provided their PDF audits succeed. Candidate incident links remain candidate mappings, not human-verified links.
