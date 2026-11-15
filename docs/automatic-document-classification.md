# Automatic document classification policy

## Purpose

The project does not use a document-by-document human approval queue. It classifies the source corpus using CSB metadata, first-page PDF text/audit results, the preserved source-check candidate table, and explicit source-specific configuration overrides.

The classification is intended to make the corpus usable without requiring a person to open and approve every document. It does **not** imply that every candidate mapping has been independently verified by a human.

## Classification inputs

1. `data/processed/manifest.jsonl` — CSB page metadata, source URLs, local paths, collected recommendation identifiers, and checksums.
2. `data/processed/pdf_content_audit.json` — page counts, first-page preview, extraction quality and content warnings.
3. `data/processed/document_classification_candidates.csv` — 70 source-checked classification candidates carried forward from the previous proposal sheet. Human decision fields were removed. The `classification_profile` field uses only machine-classification labels (`primary_report`, `supplementary_case_study`, `primary_translation`, `supplementary_appendix`, `topic_study_excluded`); incident/investigation mapping, evidence note, confidence label and follow-up note are retained.
4. `configs/document_overrides.yaml` — explicit source-specific fixes, aliases, OCR text sources and supplemental publications. These entries are configuration facts, not a queue waiting for approval.
5. The official CSB recommendation workbook — recommendation text and status as of its recorded snapshot date.

## Current candidate table

The table contains 70 records:

| `classification_profile` | Count | Catalog treatment |
|---|---:|---|
| `primary_report` | 53 | Primary incident evidence; retrieval enabled when the text source and incident link are available |
| `supplementary_case_study` | 12 | Searchable supplementary incident evidence |
| `primary_translation` | 2 | Linked to the same incident and included in source-case exclusion; excluded from the English retrieval collection |
| `supplementary_appendix` | 1 | Supplementary incident evidence |
| `topic_study_excluded` | 2 | Excluded from incident ranking; can be retained separately for background use |
| **Total** | **70** | |

Of these candidates, 68 include candidate incident IDs. Topic-level studies intentionally have no incident ID. `high` and `medium-high` are qualitative confidence labels inherited from source checks; they are not probabilities and are not human-annotation grades. These profiles are classification inputs, not approvals.

## Provenance and status fields

Document catalog records distinguish:

- `classification_status`: `automatic_candidate`, `rule_based_candidate`, `explicit_source_override`, or a related explicit status;
- `classification_method`: the rule/source table/configuration that supplied the classification;
- `classification_confidence`, `classification_basis`, and `classification_note`: why the document was classified this way;
- `mapping_status`: whether an incident mapping is an `automatic_candidate_mapping` or an `explicit_override`;
- `incident_ids`: explicit source-specific mapping only;
- `candidate_incident_ids`: automatically assigned candidate mapping;
- `source_case_id`: the normalized investigation-level key used to group all publications in one case;
- `retrieval_enabled`: a separate decision based on the classification, the selected language and the availability of a local text source.

A candidate incident ID is never silently promoted into the verified `incident_ids` field. The catalog and evaluation report must continue to distinguish the two.

## Retrieval policy

- An English primary report with a candidate incident link can be retrieved when its selected text source exists.
- A CSB case study and an incident-specific appendix can be retrieved as supplementary evidence, but they do not become primary final reports.
- The Spanish translations CSB-6323 and CSB-6269 remain linked to their incidents for source-case exclusion, but are not part of the English retrieval collection. The candidate notes identify the separate English CSB document IDs 6304 and 6268 respectively; those English files must be collected before being treated as available retrieval evidence.
- Topic-level hazard studies are excluded from incident ranking.
- Missing files or text sources block retrieval even when a classification candidate requests inclusion.
- Explicit corrections and aliases in `configs/document_overrides.yaml` take precedence over candidate-table suggestions.

## Source-case exclusion

The unit of exclusion is the **whole investigation**, not only one incident ID or one PDF. The builder associates sibling documents with every incident already linked to their normalized investigation key. It also collects all recommendation IDs for that key, retaining verified and candidate recommendation links separately.

This means a report containing several distinct events can cause the source-exclusion bundle for one event to include documents and recommendations for sibling events. This is intentional and conservative: it can reduce the available candidate set, but prevents another volume, appendix, translation, executive summary or recommendation record from revealing the source answer.

`source_case_exclusion_ready=true` means that the catalog has a source-case mapping and has constructed the whole-investigation exclusion bundle. It does **not** mean that every candidate incident link has been human-verified. Check `source_case_exclusion_status` and `catalog_status` to distinguish explicit mapping from automatic-candidate scope.

## Known source corrections retained

The existing candidate notes identified the following issues; these are preserved in the candidate table and/or explicit overrides:

1. **Barton Solvents (CSB-5614):** classify as a CSB Case Study, not a full final investigation report.
2. **Cuisine Solutions (CSB-6323):** local file is the Spanish edition; CSB lists English report CSB-6304. Keep the same incident ID and prefer the English edition for English answers.
3. **Foundation Food Group (CSB-6269):** local file is the Spanish translation; CSB lists English report CSB-6268. Keep the same incident ID and prefer the English edition for English answers.
4. **Husky Energy Superior (CSB-6219):** classify the local file as supplementary Appendix D, not the main final report.
5. **CAPECO:** preserve printed dotted report number `2010.02.I.PR` as normalized display ID `2010-02-I-PR`; use `2010-2-I-PR` only as the recommendation join key.
6. **Tosco Avon:** preserve printed legacy report number `99-014-I-CA`; use `1999-14-I-CA` only as the recommendation join key.
7. **Sonat:** preserve `1998-002-I-LA`; use `1998-2-I-LA` only as the recommendation join key.
8. **T2 Laboratories:** retain the Board Voting Copy version note; the source page identifies it as the final report.
9. **Multi-event reports:** Honeywell Chemical Incidents, Honeywell Geismar, Midland Resource Recovery and Tesoro Martinez have more than one candidate incident ID. The exclusion policy conservatively groups all events by investigation number.
10. **Recommendation mappings:** the official recommendations workbook is the structured source for recommendation text/status; identifiers scraped from PDF text are not treated as a complete register.

## Rebuild

From the project root, on the machine that contains the original local PDF files:

```bash
uv run python scripts/build_document_catalog.py
uv run pytest -q
```

The generated catalogs are derived artifacts. Rebuild them after changing the manifest, PDF audit, classification candidate table, overrides or recommendation snapshot.
