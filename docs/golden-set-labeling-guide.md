# Golden-set relevance labeling guide

## Unit of judgment

Judge a **historical incident**, not an individual PDF. Several volumes, appendices, translations, summaries, and recommendations may all describe one incident. They must not be counted as several relevant incidents.

Annotators compare the new incident description with the historical incident using the facts supported by its CSB source documents. Do not infer missing details from general industrial knowledge.

## Relevance scale

### 0 — Not meaningfully relevant

The incidents do not share a useful safety mechanism or process pattern. A superficial word, industry, or consequence match is insufficient.

Examples:
- Both events involved a fire, but one was an electrical cabinet fire and the other was a combustible-dust deflagration with no shared mechanism.
- Both incidents occurred at refineries, but the equipment, hazard, and failure mechanism are unrelated.

### 1 — Partially relevant

The incidents share one important dimension, but the historical case offers limited direct help for understanding the described incident. Similarity may be based on a shared substance/hazard, equipment family, or broad failure mode, while other core circumstances differ.

Examples:
- Both incidents involve hydrogen sulfide, but one is an exposure during tank cleaning and the other is a process-system release caused by loss of containment.
- Both involve pressure vessels, but the failure mechanisms differ materially.
- Both involve hot work, but only one has a combustible atmosphere and ignition sequence similar to the query.

### 2 — Strongly relevant

The historical incident shares a meaningful combination of hazard/material, equipment or process context, and failure mechanism/event sequence. Its documented causes or recommendations could directly inform an analyst's review of the new incident.

Examples:
- Both incidents involve a flammable vapor cloud released from process equipment, accumulation in a congested area, and ignition during nearby work.
- Both involve a reactive chemical decomposition after incompatible materials were mixed in a vessel, with a similar loss-of-control sequence.
- Both involve a toxic gas release during maintenance where isolation and residual hazardous material are central to the event.

## Similarity dimensions to record

For every candidate incident, annotate the dimensions that justify the grade:

- `substance_or_hazard`: same chemical, energy source, or hazard class;
- `equipment_or_process`: same or closely related equipment/process;
- `failure_mechanism`: same underlying physical, chemical, or organizational failure mechanism;
- `event_sequence`: similar sequence of initiating event, escalation, and consequence;
- `recommendation_transferability`: whether the historical recommendation may be relevant to the new scenario.

A shared substance alone or equipment name alone must not automatically receive grade 2. A grade-2 label should normally include a clear mechanism or event-sequence match.

## Annotation procedure

1. Read the incident query and the candidate incident's source materials.
2. Record the evidence used to compare them, including document ID, page, section, and a short supporting quote.
3. Assign grade 0, 1, or 2.
4. Write one sentence explaining the match or mismatch.
5. Do not inspect which retrieval system produced the candidate; pool and shuffle candidates before annotation.
6. At least one human annotator labels the pooled candidates without seeing which retriever produced them. A second independent annotator is an optional quality extension; when used, preserve both labels before discussion and resolve disagreements only after preserving the original labels.

## Agreement reporting

When two annotators are used, report the number of independently double-labelled pairs and the confusion matrix. Use weighted Cohen's kappa for the ordinal 0/1/2 labels and report raw agreement as a supplementary measure. If only one annotator is available, report that limitation explicitly rather than inventing an agreement statistic. Adjudicated labels, when available, become the final qrels; do not overwrite the independent labels.

## Pooling and incomplete relevance judgments

For each query, form a candidate pool from the union of top-10 results from BM25, Dense, and Hybrid retrieval, plus any source-known relevant incidents found during annotation. Deduplicate by stable `incident_id`, then shuffle before human labeling.

Recall@5 and nDCG@5 are therefore measured against this **pooled judged set**, not a guaranteed exhaustive list of every relevant CSB incident. Report pool depth and this limitation. If new systems later retrieve unjudged incidents, send them to annotation before treating them as non-relevant.

Queries with no judged relevant incident have undefined nDCG for the primary ranking comparison. Exclude them from the nDCG mean and report their count as a separate `no_known_relevant_case` slice. Evaluate abstention behavior on this slice separately; do not silently convert an undefined nDCG into a perfect score.


## Operational workflow in this repository

The initial repository seed file is `data/golden/query_seeds.jsonl` (30 anonymized, draft query scenarios). The `source_case` and `source_case_exclusion` fields are metadata for evaluation and leakage prevention; only `query_text` is sent to a retriever. Review/edit `data/golden/query_review.csv`, approve each query, and apply changes with `uv run python scripts/apply_golden_query_review.py`.

After S0 BM25, S1 Dense and S2 Hybrid have run, export the top 10 incident-level results from each as `runs/retrieval/retrieval_results.jsonl`, then build the blinded union pool with `uv run python scripts/build_relevance_pool.py --retrieval-results runs/retrieval/retrieval_results.jsonl --top-k 10`. The annotation CSV hides retriever identity/rank/score; `pool_provenance.jsonl` is internal and must not be shown to annotators. Source-case exclusion is enforced at the whole-investigation level.

Human labels are stored in `data/golden/relevance_judgments.jsonl`; historical causes and recommendations with page-grounded citations are stored in `data/golden/reference_answers.jsonl`. No labels are auto-filled. Run `uv run python scripts/validate_golden_set.py` during development, and use `--require-approved-queries --require-judgments` before compiling the final set. See [golden-set-workflow.md](golden-set-workflow.md) for schemas, commands and answer-reference rules.
