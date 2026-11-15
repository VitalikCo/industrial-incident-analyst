# Lecturer feedback — implementation decisions

The lecturer approved the project proposal. The following table records how the main suggestions are handled. Items described as optional in the lecturer's message are not treated as additional mandatory requirements.

| Feedback area | Project decision | Where it is measured |
|---|---|---|
| Relevance labels 0/1/2 and annotator agreement | Human-label the golden set with grades 0/1/2. One annotator is sufficient for the core evaluation; a second annotator and adjudication are optional extensions. | Golden-set labels; weighted kappa/raw agreement when double-labeling is performed |
| Source-case exclusion across every representation | Use an investigation-level exclusion bundle containing all catalogued sibling documents and all recommendation IDs. Automatic candidate mappings are kept distinct from explicit mappings. | Unit tests over document IDs, recommendation IDs, translations, appendices and multi-event investigations |
| Establish relevant set for Recall@5 | Pool top-10 candidates from each retriever, deduplicate by incident ID, add source-known cases and label the pooled set; disclose incomplete-pool limitation. | Recall@5 and nDCG@5 against judged pool; report pool depth |
| Explicit nDCG convention for no-relevant queries | nDCG is undefined when no judged relevant incident exists; exclude from primary nDCG mean and report separate slice. | `no_known_relevant_case` count and abstention metrics |
| Cause and recommendation correctness | Nest causes and recommendations under their historical incident, with `document_id`, page, section and supporting quote. Never attach historical causes to the new event. | Cause field-level correctness, recommendation-ID correctness and citation validity |
| Judge validation | Phi-4-mini is the candidate local judge; compare it with human labels on a development set. It is not assumed reliable before calibration. | Weighted kappa, PASS/FAIL agreement, false-PASS rate and failure-detection recall |
| V2 component attribution | Keep S0 BM25, S1 Dense and S2 Hybrid-RRF. Add one-factor ablations for reranking and abstention threshold. | Paired comparison on exactly the same cases |
| Typed tool errors and output limits | Tool contracts specify types, validation, max query length, max `top_k`, result truncation and structured error codes. | Contract tests, invalid-input tests, output-limit tests and trace inspection |
| Recommendation status snapshots | Every recommendation stores the workbook snapshot date, source URL and checksum. Status prose is always dated. | Catalog audit and answer citation checks |

## Output contract for historical findings

Each historical incident owns its own causes, recommendations and evidence. A cause must never float at the top level where it could be mistaken for a finding about the newly described event.

```json
{
  "historical_incidents": [
    {
      "incident_id": "TPC_PORT_NECHES_2019",
      "match_grade": 2,
      "documented_causes": [
        {
          "cause_type": "contributing_factor",
          "claim": "A source-grounded historical finding.",
          "evidence": {
            "document_id": "CSB-6203",
            "page": 42,
            "section": "Contributing Factors",
            "supporting_quote": "Short verbatim excerpt from the report."
          }
        }
      ],
      "recommendations": [
        {
          "recommendation_id": "2020-2-I-TX-R1",
          "text": "Recommendation text from the register.",
          "status": "Open",
          "status_as_of": "2026-09-16",
          "evidence": {
            "document_id": "CSB-6203",
            "page": 118,
            "section": "Recommendations"
          }
        }
      ]
    }
  ]
}
```

This is a schema illustration only. Actual recommendation IDs, status, page numbers and quotes must be loaded from the corresponding report/register and validated.

## Judge fallback policy

Before using Phi-4-mini for automated scoring, calibrate it on a labelled development set. Pre-register a minimum agreement target (suggested starting point: weighted kappa ≥ 0.60) and inspect false-PASS errors. If it misses the target, reduce the judge's role to triage and use human labels for final scores. Long answers should be judged as short claim/evidence pairs rather than one large unbounded paragraph.
