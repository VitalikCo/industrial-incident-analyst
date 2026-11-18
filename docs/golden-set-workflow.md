# Golden Set workflow

## Purpose and status

The initial dataset is `data/golden/query_seeds.jsonl`: 30 draft query scenarios drawn from 30 distinct CSB investigation cases. They are anonymized paraphrases of source-report executive summaries/abstracts. Source report IDs, titles, pages and whole-investigation exclusion bundles are retained in evaluation metadata, not in the query text sent to the system.

This seed file is **not yet the final golden set**. It has zero human relevance judgments and zero reference answer records. We do not auto-label relevance or ask an LLM to invent the gold labels.

## 1. Review the 30 query scenarios

Open `data/golden/query_review.csv`. Review every scenario for factual accuracy and whether it reads like a realistic new incident description. You may edit `query_text`. Set `query_status` to:

- `approved` — suitable to freeze as an evaluation query;
- `needs_revision` — revise and review again;
- `draft_for_human_review` — not yet checked.

Apply the CSV edits without changing provenance/exclusion metadata:

```bash
uv run python scripts/apply_golden_query_review.py
```

Only `query_text` and `query_status` are applied. Do not put the source company name, source report title, exact CSB report number or source incident ID into the query text: those fields could make the held-out source case identifiable lexically.

## 2. Run retrieval and create the pooled candidate set

For every query, each retriever exports an incident-level result record to JSONL. The common input format is one record per query and retriever:

```json
{
  "query_id": "Q001",
  "retriever": "bm25",
  "results": [
    {"incident_id": "ANOTHER_HISTORICAL_INCIDENT", "rank": 1, "score": 12.4}
  ]
}
```

Allowed retriever values are `bm25`, `dense`, and `hybrid`. Export the top 10 incidents per query from each retriever. Each incident must be an aggregation of all matching documents for that historical incident; do not count several PDFs/volumes as several incidents.

The pool builder unions the three top-10 lists, deduplicates by `incident_id`, and defensively removes any candidate sharing the query's excluded source investigation, document, or recommendation IDs:

```bash
uv run python scripts/build_relevance_pool.py \
  --retrieval-results runs/retrieval/retrieval_results.jsonl \
  --top-k 10
```

Outputs:

- `annotation_pool.csv` — blinded sheet for human annotation. It includes query and candidate summaries but does not show retriever names, ranks or scores.
- `pool_provenance.jsonl` — internal mapping from pooled candidate to the retriever/rank/score that surfaced it. Do not give this file to annotators before labels are fixed.
- `pool_excluded_source_leakage.jsonl` — any returned source-case hits removed by the defensive check. The retrieval implementation should already filter source-case IDs **before ranking**; this file is a guardrail, not a substitute for query-time exclusion.
- `pool_build_summary.json` — pool sizes and source-exclusion counts.

If a source-known relevant incident is found outside the top-10 pool during inspection, add it to the pool using the same format and document why it was added. Pooling is a practical approximation, not an exhaustive census of all relevant CSB cases.

## 3. Human relevance labels

Label each query–incident pair in `annotation_pool.csv` using grades:

- `0` — judged not meaningfully relevant;
- `1` — partially relevant: one useful similarity dimension, but limited transferability;
- `2` — strongly relevant: a meaningful combination of hazard/material, equipment/process and failure mechanism/event sequence.

Record all five dimensions using `match`, `partial`, `no_match`, or `unknown`:

- `substance_or_hazard`;
- `equipment_or_process`;
- `failure_mechanism`;
- `event_sequence`;
- `recommendation_transferability`.

Add a short rationale and evidence from the candidate's own CSB source: `evidence_document_id`, `evidence_page`, `evidence_section`, and a short `supporting_quote`. A common substance or industry alone is not enough for grade 2. Do not see the internal `pool_provenance.jsonl` while labelling.

After annotations are complete, convert the labelled CSV rows to `relevance_judgments.jsonl` with this structure:

```json
{
  "query_id": "Q001",
  "incident_id": "ANOTHER_HISTORICAL_INCIDENT",
  "grade": 2,
  "dimensions": {
    "substance_or_hazard": "match",
    "equipment_or_process": "match",
    "failure_mechanism": "match",
    "event_sequence": "partial",
    "recommendation_transferability": "match"
  },
  "rationale": "Why this historical incident is strongly relevant.",
  "evidence": [
    {
      "document_id": "CSB-XXXX",
      "page": 42,
      "section": "Contributing Factors",
      "supporting_quote": "Short quote verified against the extracted page."
    }
  ],
  "annotator_id": "human_1",
  "annotation_status": "final"
}
```

A label of 0 is an explicit judged non-relevant case; an absent judgment is **unknown**, not 0.

## 4. Reference causes and recommendations

For every positively labelled historical incident, create a record in `reference_answers.jsonl`. Causes and recommendations must be nested beneath their historical `incident_id`; never present a historical cause as a finding about the new incident.

Each documented cause should include `cause_type`, a concise source-grounded `claim`, and evidence with `document_id`, `page`, `section`, and `supporting_quote`.

Each recommendation should include its official `recommendation_id`, text, and status. Status must carry `status_as_of` equal to the dated workbook snapshot (currently `2026-09-16`). Include a report citation where the recommendation appears in the report, and retain the workbook snapshot as the status source. Candidate recommendation-to-incident links must remain visibly provisional; verify the recommendation ID against the incident's investigation key before using it as a reference.

## 5. Metrics and conventions

- Primary retrieval metric: `nDCG@5` using graded relevance 0/1/2.
- Secondary retrieval metric: `Recall@5` against the judged pooled relevant set.
- Report the mean, query count, judged pool depth, and the pooling limitation.
- If a query has no judged relevant incident, nDCG is undefined: exclude it from the primary nDCG mean and report the `no_known_relevant_case` slice separately. Set `expected_abstention=true` only after human review concludes that no relevant case is known in the judged pool.
- Retrieval metrics and answer-quality metrics are separate. Cause correctness, recommendation-ID correctness, citation validity and unsupported claims are evaluated in a later answer-evaluation layer.

Use the same frozen query IDs and qrels to compare S0 BM25, S1 Dense and S2 Hybrid-RRF. When Dense and Hybrid are added, rebuild the pool as the union of all three systems' top-10 results and annotate newly surfaced incidents before scoring the final comparison.

## 6. Judge calibration

Do not use the LLM judge to create human gold labels. Once qrels are fixed, validate the chosen local judge (Phi-4-mini 3.8B or Llama 3.2 3B via Ollama) against the human grades on a development subset. Report raw agreement and weighted Cohen's kappa. If agreement is below the predeclared threshold (suggested starting point: 0.60), use the judge for triage only and retain human labels as ground truth.

## 7. Validation and compilation

```bash
uv run python scripts/validate_golden_set.py

# After all 30+ queries are approved, judged and have a relevant case or an
# explicitly reviewed expected_abstention=true status:
uv run python scripts/validate_golden_set.py --require-approved-queries --require-judgments
uv run python scripts/compile_golden_set.py
```

The initial seed-only validation should be structurally valid but not evaluation-ready. Do not compile `golden_set.jsonl` until all required human labels and evidence have been supplied.
