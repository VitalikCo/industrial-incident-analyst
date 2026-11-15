# Data card — CSB industrial incident corpus

## Purpose

Support retrieval and evidence-grounded analysis of historical industrial incidents investigated by the U.S. Chemical Safety and Hazard Investigation Board (CSB).

## Sources

1. CSB Completed Investigations: https://www.csb.gov/investigations/completed-investigations/
2. CSB Recommendations Statistics: https://www.csb.gov/recommendations/statistics/

## Unit of data

The primary unit is one CSB investigation and its explicitly linked final report PDF. Recommendations are also stored in a separate official spreadsheet and linked to investigations by recommendation identifier where possible.

## Collection procedure

The collection script follows the CSB archive pagination, parses investigation detail pages, downloads only PDFs whose page link is explicitly labelled as a final report, and computes SHA-256 checksums. The recommendations spreadsheet URL is discovered from the official CSB recommendations statistics page rather than hard-coded.

## Known limitations

- The archive may include studies or records without a downloadable final investigation report; these remain visible in the manifest but are not automatically treated as report PDFs.
- Some detail pages may expose multiple documents or use changed page markup. Failed or ambiguous cases remain flagged in the manifest/audit and are not silently promoted into retrieval. Source-specific corrections live in `configs/document_overrides.yaml`.
- The recommendation spreadsheet is a snapshot and may change after collection.
- The corpus represents CSB-investigated incidents, not all industrial accidents.
- CSB reports vary in layout, length, tables, scanned content, and terminology.

## Intended use and prohibited interpretation

The corpus supports academic retrieval and decision support. The system must not present itself as a regulator, certified investigator, or replacement for a qualified process-safety professional. It must not invent causal findings or recommendations.

## Reproducibility

Record the collection timestamp, source URLs, SHA-256 checksums, software version, and a copy of the downloaded recommendation spreadsheet. Keep local source files backed up before the project checkpoint.
