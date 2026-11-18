# Golden Set — initial query seeds (v0.1)

This directory currently contains **30 grounded draft query scenarios**, not a completed golden set.

## Files

- `query_seeds.jsonl` — 30 anonymized English scenarios, query facets, source-report evidence, and the full source-investigation exclusion bundle. The `source_case` and `source_case_exclusion` fields are evaluation metadata; pass only `query_text` to the system under test.
- `query_review.csv` — editable human review sheet for the 30 scenario texts. Edit `query_text` and set `query_status` to `approved` or `needs_revision`; then apply edits with `scripts/apply_golden_query_review.py`.
- `seed_build_summary.json` — counts/provenance summary for this seed build.
- `relevance_judgments.jsonl` — intentionally empty until candidate pools have been labelled by a human.
- `reference_answers.jsonl` — intentionally empty until relevant historical incidents have been labelled and their causes/recommendations cited.
- `pools/` — retrieval-pool artefacts created after BM25/Dense/Hybrid runs.
- `annotations/` — reserved for adjudication and annotator-agreement artefacts.

The 30 query seeds are tied to 30 distinct CSB investigation IDs. Each source investigation is excluded by its complete investigation-level bundle, including sibling documents and linked recommendation records. Twenty-nine source incident mappings are candidate mappings and one is an explicit mapping; the mapping provenance is retained rather than silently treating all 30 as verified.

## Not ready for evaluation yet

No relevance labels or historical reference answers have been fabricated. The final golden set is produced only after:

1. human approval of the query texts;
2. a pooled set of candidate incidents from the same BM25, Dense and Hybrid retrieval runs (top 10 from each, deduplicated by `incident_id`);
3. human relevance grades 0/1/2 with rationale and source evidence;
4. historical cause/recommendation reference records for positive cases, with document/page/section evidence.

Run `python scripts/validate_golden_set.py` to check the seed structure. It should report `structurally_valid: true` and `ready_for_evaluation: false` at this stage.
