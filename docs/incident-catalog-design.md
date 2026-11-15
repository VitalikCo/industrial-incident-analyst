# Incident catalog design and source-case exclusion

## 1. Why there are four catalog levels

The project contains four related but different entities. They must not be collapsed into one row per PDF.

| Entity | Meaning | Example |
|---|---|---|
| Document | One physical source file or one registered publication | A report volume, appendix, Spanish translation, executive summary |
| Investigation | A CSB investigation/report number, which can have several publications | Macondo investigation `2010-10-I-OS` |
| Incident | The historical event (or one event within a multi-event investigation) | `MACONDO_2010` |
| Recommendation | One recommendation register record | `2010-10-I-OS-R01` |

A multi-volume report is one investigation and one incident, not four incidents. A report can also cover multiple events; those events may have separate stable `incident_id` values when supported by the source.

## 2. Investigation identifiers and recommendation join keys

The printed identifier is retained as a source-facing value. The parser normalizes separators and OCR letter case but preserves year width and sequence width.

| Printed/source value | `investigation_id` | `recommendation_match_key` |
|---|---|---|
| `2010.02.I.PR` | `2010-02-I-PR` | `2010-2-I-PR` |
| `99-014-I-CA` | `99-014-I-CA` | `1999-14-I-CA` |
| `98-007-I-IA` | `98-007-I-IA` | `1998-7-I-IA` |
| `1998-002-I-LA` | `1998-002-I-LA` | `1998-2-I-LA` |
| `2008-3-I-FL` | `2008-3-I-FL` | `2008-3-I-FL` |
| `2019-03-I-lL` (OCR) | `2019-03-I-IL` | `2019-3-I-IL` |

The year expansion uses the configured pivot (`30`): `00–29` maps to `2000–2029`; `30–99` maps to `1930–1999`. This rule is a join convention, not a claim about the typography on the source PDF.

Recommendation IDs are split into the first four structural segments (year, sequence, record kind, state); the recommendation suffix is everything after that prefix. This handles state codes beginning with `R` and suffix variants such as `R1`, `R10A`, `RU1`, `RUR1`, and `R-1`.

The recommendation register distinguishes `incident_ids` (explicit/verified links) from `candidate_incident_ids` (links derived from automatic candidate mappings or inferred investigation associations). `incident_link_status` is `verified`, `candidate_only`, `mixed_verified_and_candidate`, or `unlinked`. Candidate links do not inflate verified-link counts. The source-case exclusion set nevertheless includes the union of recommendation IDs associated with the investigation.

Do not use `recommendation_match_key` as the displayed report number. Do not add leading zeroes or expand a two-digit year in `investigation_id` unless that is exactly how the source prints it.

## 3. Automatic classification and mapping provenance

Document-level human approval is not part of this project workflow. Classification comes from CSB metadata, first-page PDF audit rules, the 70-row source-checked candidate table, and explicit source-specific overrides in `configs/document_overrides.yaml`.

The catalog keeps the provenance separate:

- `incident_ids` holds explicit source-specific mappings;
- `candidate_incident_ids` holds automatic candidate mappings;
- `classification_status`, `classification_method`, `classification_confidence` and `classification_basis` describe how the document was classified;
- `mapping_status` distinguishes an automatic candidate from an explicit override.

A candidate link is never promoted to a verified link merely because it is used for retrieval or source-case exclusion. Confidence labels are qualitative and must not be described as calibrated probabilities.

See [automatic-document-classification.md](automatic-document-classification.md) for classification counts, language policy and source corrections.

## 3a. Retrieval eligibility and usable text

A document can be retrieval-enabled only when its selected `text_source_path` exists locally and can be parsed. For a native PDF, the original may serve as the text source; for a scanned or poor-quality PDF, a selected OCR derivative may be used as the text source while the original remains the cited source document. Missing text sources block retrieval regardless of classification or mapping status.

The local Spanish translations CSB-6323 and CSB-6269 are retained in the source-case exclusion set, but not in the English retrieval collection. Their candidate notes identify English editions CSB-6304 and CSB-6268 respectively; collect those editions before claiming they are available retrieval sources.

## 4. Whole-investigation source-case exclusion

If a query describes incident X, the system must not retrieve information that directly reveals the answer from that same source case. Exclusion is therefore performed at the incident level with a **whole-investigation bundle**.

For every mapped incident, the exclusion bundle collects:

- all primary, supplementary, translated and sibling `document_ids` assigned directly or inferred through the investigation key;
- all associated `investigation_ids` and normalized source-case IDs;
- the union of recommendation IDs connected through the investigation recommendation key;
- the recommendation workbook snapshot date.

A publication that shares an investigation key with a mapped incident is attached to every incident associated with that key, even when the publication has no direct incident ID. This ensures that a volume, appendix, executive summary, translation or correspondence file cannot sit outside the exclusion bundle.

`source_case_exclusion_ready=true` means the system has created the whole-investigation exclusion bundle from an explicit or automatic candidate mapping. It does **not** certify every candidate link as human-verified. `source_case_exclusion_status` distinguishes `verified_scope` from `whole_investigation_candidate_scope`. The full `source_case_exclusion` object carries the document IDs, recommendation IDs, investigation IDs, scope and snapshot date.

For multi-event investigations, the same documents and recommendation records may be included in the exclusion bundle for several events. This may over-exclude related sibling events. The policy intentionally favors preventing answer leakage over retaining every potentially useful candidate in evaluation.

## 5. Recommendation snapshots

The included workbook metadata says: **Recommendations data as of the September 16, 2026, Board Vote**. Each recommendation record carries `source_snapshot_date=2026-09-16`, source URL, workbook path, checksum metadata and collection timestamp.

Recommendation status is time-dependent. Answers must say “status as of 2026-09-16” (or the date of a later downloaded snapshot), not imply that a status remains current indefinitely.

## 6. Catalog outputs

The builder produces:

- `document_catalog.jsonl/csv`: one row per registered source document, file/text availability, classification provenance, incident mappings and retrieval eligibility;
- `investigation_catalog.jsonl/csv`: one row per investigation join key, printed/candidate ID aliases, linked documents and recommendations;
- `incident_catalog.jsonl/csv`: one row per stable incident ID, source documents, recommendation IDs, mapping status and whole-investigation exclusion set;
- `recommendation_catalog.jsonl/csv`: one row per recommendation, text, purpose, status, recipient, dates, incident links and snapshot provenance;
- `catalog_build_summary.json`: record counts and validation/readiness counts.

The generated catalogs are derived artifacts. Rebuild them after changing the manifest, source audit, classification candidate table, overrides or recommendation snapshot.

## 7. Current candidate mapping snapshot

The current source-checked candidate table has 70 rows: 53 primary reports, 12 supplementary case studies, two Spanish primary-report translations, one technical appendix and two topic studies excluded from incident ranking. Sixty-eight rows have candidate incident IDs; topic studies intentionally do not. None of these candidate rows is reported as a human annotation or human approval.

The explicit Macondo, TPC and Bio-Lab corrections in `configs/document_overrides.yaml` remain separate from the candidate table. They are source-specific configuration overrides and must not be deleted as part of removing the old review queue.

## 8. Known limitations

- The corpus contains selected CSB publications, not a census of industrial accidents.
- CSB investigation numbering and recommendation suffixes have legacy variations. Unknown source strings are retained rather than silently normalized into a false match.
- The recommendation workbook is a dated snapshot and may not reflect changes after that date.
- A recommendation prefix can cover multiple events within one investigation; whole-investigation exclusion may therefore over-exclude.
- Automatic candidate mappings are useful for corpus retrieval and leakage prevention but are not human-verified labels. Human relevance labels in the golden set remain a separate requirement.
- Source-case exclusion is limited to documents and recommendations present in the local snapshot/manifest. Missing source files still need to be recovered for retrieval, even though their identifiers can remain in exclusion metadata.
