# Expanding the approved query set from 30 to 55

The user's existing first 30 queries have already been human-approved. This patch is additive: it preserves those records and appends 25 new, source-grounded English query seeds (`Q031`–`Q055`) drawn from 25 additional CSB investigations.

## Apply without replacing the project

Copy these patch files into the existing local repository, preserving the directory structure:

- `data/golden/additional_query_seed_definitions.jsonl`
- `scripts/extend_golden_set.py`
- `tests/test_extend_golden_set.py`
- `docs/golden-set-expansion.md`

Do not replace `data/golden/query_seeds.jsonl` or `query_review.csv` with files from this patch. The append script reads the already-approved local versions and retains them.

Run from the repository root:

```bash
uv run python scripts/extend_golden_set.py
uv run python scripts/validate_golden_set.py
```

Expected expansion summary:

```json
{
  "previous_query_count": 30,
  "added_query_count": 25,
  "total_query_count": 55,
  "approved_queries_preserved": 30,
  "new_queries_awaiting_review": 25,
  "unique_source_investigations": 55,
  "source_case_exclusion_ready": true
}
```

The 25 new rows are deliberately `draft_for_human_review`; the original 30 remain `approved`. Review `Q031`–`Q055` in `data/golden/query_review.csv`, edit their `query_text` if needed, and set `query_status=approved`. Then apply the review:

```bash
uv run python scripts/apply_golden_query_review.py
uv run python scripts/validate_golden_set.py
```

The validator should report 55 query seeds, 55 unique source investigations and 55 approved queries, with `structurally_valid=true`. `ready_for_evaluation` remains false because relevance judgments and reference answers have not yet been created.

## Grounding and exclusion

Each additional seed is linked to a parsed source document and page from the existing Chunk Store. The append script resolves the document to a single incident record, checks that the page is substantive, preserves the source mapping status and stores an exact short quote from the page. Every source is excluded using the catalog's whole-investigation bundle, including associated documents and recommendation IDs.

The 25 new source mappings are all `automatic_candidate_mapping`, not manually verified. This provenance remains explicit in each query. The source report is used to formulate the query but is never made available to the system for that query; its whole-investigation bundle is excluded at retrieval time.

Original report PDFs are not needed to append or review these query seeds because the text and page metadata are already present in the Chunk Store. A PDF is needed only for a later visual source check if the extracted text is ambiguous.

## After all 55 queries are approved

Do not invent relevance labels. Run a first BM25 retrieval over all 55 query texts with source-case exclusion active, export the top 10 **incident-level** candidates per query, then repeat with Dense and Hybrid. Build one pooled candidate set from the union of BM25/Dense/Hybrid top-10 lists, deduplicated by `incident_id`. Human annotators label the pooled candidates 0/1/2 with rationale and evidence. Only then create reference causes/recommendations and freeze the qrels for the S0/S1/S2 comparison.
