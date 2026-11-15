import csv

import pytest

from scripts.build_document_catalog import (
    apply_automatic_classifications,
    apply_known_alias_groups,
    build_catalog,
    build_incident_catalog,
    build_investigation_catalog,
    csb_document_id,
    extract_report_id,
    guess_document_type,
    merge_aliases,
    normalize_report_id,
    read_classification_candidates,
    recommendation_match_key,
    recommendation_prefix_from_record_number,
)


def test_extracts_csb_document_id_case_insensitively():
    assert csb_document_id("https://www.csb.gov/file.aspx?DocumentId=5993") == "5993"
    assert csb_document_id("https://www.csb.gov/file.aspx?documentid=6053") == "6053"


def test_classifies_supplementary_material_conservatively():
    doc_type, role = guess_document_type(
        "Macondo Blowout and Explosion",
        "U.S. Chemical Safety and Hazard Investigation Board Urgent Recommendations",
    )
    assert doc_type == "urgent_recommendation"
    assert role == "supplementary_incident_evidence"


def test_classifies_investigation_report_as_rule_based_candidate():
    doc_type, role = guess_document_type(
        "TPC Port Neches Explosions and Fire",
        "U.S. Chemical Safety and Hazard Investigation Board Investigation Report "
        "Port Neches, Texas | No. 2020-02-I-TX",
    )
    assert doc_type == "csb_investigation_report_candidate"
    assert role == "primary_candidate_needs_review"


def test_read_classification_candidates_fails_fast_when_required_file_is_missing(tmp_path):
    path = tmp_path / "missing_candidates.csv"
    with pytest.raises(FileNotFoundError, match="Classification candidate table not found"):
        read_classification_candidates(path)


def test_build_catalog_consumes_candidate_table_without_human_review_fields(tmp_path):
    manifest_path = tmp_path / "manifest.jsonl"
    manifest_path.write_text(
        '{"title":"CAPECO final report","detail_url":"https://csb.test/capeco",'
        '"final_report_url":"https://csb.test/file.aspx?DocumentId=1",'
        '"local_pdf_path":"data/raw/reports/not-present.pdf","recommendation_ids":[]}\n',
        encoding="utf-8",
    )
    audit_path = tmp_path / "audit.json"
    audit_path.write_text('{"records":[]}', encoding="utf-8")
    overrides_path = tmp_path / "overrides.yaml"
    overrides_path.write_text("version: 1\nreport_id_policy:\n  two_digit_year_pivot: 30\n", encoding="utf-8")
    candidates_path = tmp_path / "classification_candidates.csv"
    with candidates_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "document_id", "classification_profile", "candidate_investigation_id",
            "candidate_incident_ids", "classification_confidence", "classification_basis",
        ])
        writer.writeheader()
        writer.writerow({
            "document_id": "CSB-1", "classification_profile": "primary_report",
            "candidate_investigation_id": "2010.02.I.PR",
            "candidate_incident_ids": "CAPECO_2009_10_23",
            "classification_confidence": "high",
            "classification_basis": "Report cover and source page identify this investigation.",
        })

    records = build_catalog(manifest_path, audit_path, overrides_path, candidates_path)
    assert len(records) == 1
    record = records[0]
    assert record["classification_status"] == "automatic_candidate"
    assert record["mapping_status"] == "automatic_candidate_mapping"
    assert record["incident_ids"] == []
    assert record["candidate_incident_ids"] == ["CAPECO_2009_10_23"]
    assert record["retrieval_requested"] is True
    assert record["retrieval_enabled"] is False
    assert record["retrieval_block_reason"] == "text_source_missing"
    assert "review_status" not in record
    assert "reviewed_by" not in record


def test_read_classification_candidates_rejects_unknown_decisions(tmp_path):
    path = tmp_path / "candidates.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["document_id", "classification_profile"])
        writer.writeheader()
        writer.writerow({"document_id": "CSB-1", "classification_profile": "approve_anything"})
    with pytest.raises(ValueError, match="Unsupported classification_profile"):
        read_classification_candidates(path)


def test_automatic_primary_mapping_enables_retrieval_but_stays_provisional():
    record = {
        "document_id": "CSB-1", "title": "CAPECO final report",
        "investigation_id": None, "recommendation_match_key": None,
        "incident_ids": [], "candidate_incident_ids": [],
        "document_type": "unclassified", "corpus_role": "unclassified",
        "retrieval_requested": False, "retrieval_enabled": False,
        "text_source_exists": True,
    }
    candidate = {
        "CSB-1": {
            "classification_profile": "primary_report",
            "candidate_investigation_id": "2010.02.I.PR",
            "candidate_incident_ids": "CAPECO_2009_10_23",
            "classification_confidence": "high",
            "classification_basis": "PDF cover report number and official CSB page support the match.",
        }
    }
    apply_automatic_classifications([record], candidate)
    assert record["document_type"] == "investigation_report"
    assert record["corpus_role"] == "primary_incident_evidence"
    assert record["candidate_investigation_id"] == "2010.02.I.PR"
    assert record["candidate_recommendation_match_key"] == "2010-2-I-PR"
    assert record["incident_ids"] == []
    assert record["candidate_incident_ids"] == ["CAPECO_2009_10_23"]
    assert record["mapping_status"] == "automatic_candidate_mapping"
    assert record["classification_status"] == "automatic_candidate"
    assert record["classification_confidence"] == "high"
    assert record["retrieval_enabled"] is True


def test_translation_is_kept_for_source_exclusion_but_not_english_retrieval():
    record = {
        "document_id": "CSB-1", "investigation_id": None,
        "incident_ids": [], "candidate_incident_ids": [],
        "document_type": "unclassified", "corpus_role": "unclassified",
        "retrieval_requested": False, "retrieval_enabled": False,
        "text_source_exists": True,
    }
    candidate = {
        "CSB-1": {
            "classification_profile": "primary_translation",
            "candidate_investigation_id": "2021-03-I-GA",
            "candidate_incident_ids": "FOUNDATION_FOOD_GROUP_2021_01_28",
            "classification_confidence": "high",
            "classification_basis": "CSB identifies this file as the Spanish-language edition.",
            "classification_follow_up": "Prefer the English CSB edition for English generation.",
        }
    }
    apply_automatic_classifications([record], candidate)
    assert record["document_type"] == "investigation_report_translation"
    assert record["language"] == "es"
    assert record["candidate_incident_ids"] == ["FOUNDATION_FOOD_GROUP_2021_01_28"]
    assert record["retrieval_enabled"] is False
    assert record["retrieval_block_reason"] == "classification_excluded_from_incident_retrieval"


def test_topic_study_is_not_added_to_incident_retrieval():
    record = {
        "document_id": "CSB-1", "incident_ids": [], "candidate_incident_ids": [],
        "document_type": "unclassified", "corpus_role": "unclassified",
        "retrieval_requested": False, "retrieval_enabled": False,
        "text_source_exists": True,
    }
    apply_automatic_classifications([record], {
        "CSB-1": {
            "classification_profile": "topic_study_excluded",
            "candidate_investigation_id": "2006-H-1",
            "candidate_incident_ids": "",
            "classification_confidence": "high",
            "classification_basis": "The cover labels the publication as a hazard study.",
        }
    })
    assert record["document_type"] == "hazard_investigation_study"
    assert record["corpus_role"] == "excluded"
    assert record["retrieval_enabled"] is False
    assert record["retrieval_block_reason"] == "classification_excluded_from_incident_retrieval"


def test_explicit_source_override_is_not_demoted_by_candidate_row():
    record = {
        "document_id": "CSB-1", "investigation_id": "2003-13-I-LA",
        "recommendation_match_key": "2003-13-I-LA",
        "incident_ids": ["HONEYWELL_EVENT_1"], "candidate_incident_ids": [],
        "document_type": "investigation_report", "corpus_role": "primary_incident_evidence",
        "retrieval_requested": True, "retrieval_enabled": True,
        "text_source_exists": True, "mapping_status": "explicit_override",
    }
    apply_automatic_classifications([record], {
        "CSB-1": {
            "classification_profile": "primary_report",
            "candidate_investigation_id": "2010-02-I-PR",
            "candidate_incident_ids": "WRONG_CANDIDATE_ID",
            "classification_confidence": "high",
            "classification_basis": "Candidate proposal must not override a source-specific correction.",
        }
    })
    assert record["incident_ids"] == ["HONEYWELL_EVENT_1"]
    assert record["candidate_incident_ids"] == []
    assert record["candidate_recommendation_match_key"] == "2003-13-I-LA"
    assert record["mapping_status"] == "explicit_override"
    assert record["retrieval_enabled"] is True


def test_merges_same_physical_pdf_but_preserves_archive_aliases():
    docs = [
        {"document_id": "CSB-6330", "title": "Bio Lab Conyers Chemical Release",
         "source_detail_urls": ["https://csb.test/conyers"], "source_sha256": "same",
         "recommendation_ids": ["R1"], "incident_ids": ["BIO_LAB_2020"]},
        {"document_id": "CSB-6330", "title": "Bio-Lab Lake Charles Chemical Fire and Release",
         "source_detail_urls": ["https://csb.test/lake-charles"], "source_sha256": "same",
         "recommendation_ids": ["R2"], "incident_ids": ["BIO_LAB_2020"]},
    ]
    result = merge_aliases(docs)
    assert len(result) == 1
    assert len(result[0]["source_detail_urls"]) == 2
    assert set(result[0]["recommendation_ids"]) == {"R1", "R2"}
    assert result[0]["incident_ids"] == ["BIO_LAB_2020"]


def test_investigation_catalog_keeps_one_incident_across_multiple_volumes():
    docs = [
        {"document_id": "CSB-5930", "investigation_id": "2010-10-I-OS",
         "candidate_investigation_id": "2010-10-I-OS",
         "incident_ids": ["MACONDO_2010"], "candidate_incident_ids": [],
         "recommendation_ids": [], "source_detail_urls": ["https://csb.test/macondo"],
         "mapping_status": "explicit_override"},
        {"document_id": "CSB-5931", "investigation_id": "2010-10-I-OS",
         "candidate_investigation_id": "2010-10-I-OS",
         "incident_ids": ["MACONDO_2010"], "candidate_incident_ids": [],
         "recommendation_ids": [], "source_detail_urls": ["https://csb.test/macondo"],
         "mapping_status": "explicit_override"},
    ]
    overrides = {"investigation_overrides": [{
        "investigation_id": "2010-10-I-OS", "incident_ids": ["MACONDO_2010"],
        "recommendation_ids": ["2010-10-I-OS-1"],
        "source_detail_urls": ["https://csb.test/macondo"], "mapping_status": "explicit_override",
    }]}
    result = build_investigation_catalog(docs, overrides)
    assert len(result) == 1
    assert result[0]["incident_ids"] == ["MACONDO_2010"]
    assert set(result[0]["document_ids"]) == {"CSB-5930", "CSB-5931"}
    assert result[0]["recommendation_ids"] == ["2010-10-I-OS-1"]
    assert result[0]["mapping_status"] == "verified_or_explicit_override"


def test_report_id_parser_supports_real_csb_cover_variants():
    cases = {
        "REPORT NO. 2010.02.I.PR": "2010-02-I-PR",
        "Report No. 99-014-I-CA": "99-014-I-CA",
        "Report No. 98-007-I-IA": "98-007-I-IA",
        "Report No. 1998-002-I-LA": "1998-002-I-LA",
        "Report No. 2008-3-I-FL": "2008-3-I-FL",
        "Report No. 2019-03-I-lL": "2019-03-I-IL",
    }
    for source, expected in cases.items():
        assert normalize_report_id(source) == expected


def test_extract_report_id_keeps_raw_and_normalized_values():
    raw, normalized = extract_report_id("Board report: Report No. 2010.02.I.PR / final")
    assert raw == "2010.02.I.PR"
    assert normalized == "2010-02-I-PR"


def test_recommendation_match_key_is_separate_from_printed_report_number():
    assert recommendation_match_key("2010.02.I.PR") == "2010-2-I-PR"
    assert recommendation_match_key("99-014-I-CA") == "1999-14-I-CA"
    assert recommendation_match_key("98-007-I-IA") == "1998-7-I-IA"
    assert recommendation_match_key("1998-002-I-LA") == "1998-2-I-LA"
    assert recommendation_match_key("2019-03-I-lL") == "2019-3-I-IL"


def test_recommendation_prefix_handles_multi_digit_suffixes():
    assert recommendation_prefix_from_record_number("1998-6-I-NJ-R10A") == "1998-6-I-NJ"
    assert recommendation_prefix_from_record_number("2010-10-I-OS-R01") == "2010-10-I-OS"


def test_recommendation_prefix_handles_legacy_suffix_variants():
    assert recommendation_prefix_from_record_number("2007-1-I-NC-RU1") == "2007-1-I-NC"
    assert recommendation_prefix_from_record_number("2009-14-I-TX-RUR1") == "2009-14-I-TX"
    assert recommendation_prefix_from_record_number("2011-3-I-WV-R-1") == "2011-3-I-WV"
    assert recommendation_prefix_from_record_number("2003-8-I-RI-R1") == "2003-8-I-RI"
    assert recommendation_prefix_from_record_number("2005-4-B-TX-R1") == "2005-4-B-TX"
    assert recommendation_match_key("2005-4-B-TX") is None


def test_report_number_does_not_match_topic_level_hazard_study():
    assert normalize_report_id("Report No. 2006-H-1") is None
    assert normalize_report_id("Report No. 2001-01-H") is None


def test_automatic_candidate_incident_has_complete_source_exclusion_and_retrieval():
    docs = [{
        "document_id": "CSB-1", "title": "CAPECO primary report",
        "investigation_id": "2010-02-I-PR", "candidate_investigation_id": "2010-02-I-PR",
        "recommendation_match_key": "2010-2-I-PR", "candidate_recommendation_match_key": "2010-2-I-PR",
        "incident_ids": [], "candidate_incident_ids": ["CAPECO_2009_10_23"],
        "document_type": "investigation_report", "corpus_role": "primary_incident_evidence",
        "mapping_status": "automatic_candidate_mapping", "classification_status": "automatic_candidate",
        "retrieval_enabled": True, "text_source_exists": True,
        "source_detail_urls": ["https://csb.test/capeco"],
    }]
    recs = [{"recommendation_id": "2010-2-I-PR-R1", "recommendation_match_key": "2010-2-I-PR", "incident_ids": []}]
    row = build_incident_catalog(docs, recs, snapshot={"snapshot_date": "2026-09-16"})[0]
    assert row["catalog_status"] == "automatic_candidate_mapping"
    assert row["mapping_status"] == "automatic_candidate_mapping"
    assert row["source_case_exclusion_ready"] is True
    assert row["source_case_exclusion_status"] == "whole_investigation_candidate_scope"
    assert row["source_case_exclusion"]["scope"] == "whole_investigation"
    assert row["source_case_exclusion"]["document_ids"] == ["CSB-1"]
    assert row["source_case_exclusion"]["recommendation_ids"] == ["2010-2-I-PR-R1"]
    assert row["retrieval_enabled"] is True
    assert row["recommendation_snapshot_date"] == "2026-09-16"
    assert recs[0]["incident_ids"] == []
    assert recs[0]["candidate_incident_ids"] == ["CAPECO_2009_10_23"]


def test_verified_primary_plus_automatic_sibling_is_exclusion_ready():
    docs = [
        {
            "document_id": "CSB-PRIMARY", "title": "Primary report",
            "investigation_id": "2010-2-I-PR", "recommendation_match_key": "2010-2-I-PR",
            "incident_ids": ["CAPECO_2009_10_23"], "candidate_incident_ids": [],
            "corpus_role": "primary_incident_evidence", "mapping_status": "explicit_override",
            "retrieval_enabled": True, "text_source_exists": True,
        },
        {
            "document_id": "CSB-APPENDIX", "title": "Appendix",
            "candidate_investigation_id": "2010-2-I-PR", "candidate_recommendation_match_key": "2010-2-I-PR",
            "incident_ids": [], "candidate_incident_ids": ["CAPECO_2009_10_23"],
            "corpus_role": "supplementary_incident_evidence", "mapping_status": "automatic_candidate_mapping",
            "retrieval_enabled": True, "text_source_exists": True,
        },
    ]
    row = build_incident_catalog(docs, [], snapshot={})[0]
    assert row["catalog_status"] == "mixed_verified_and_automatic_candidate"
    assert row["confirmed_document_ids"] == ["CSB-PRIMARY"]
    assert row["provisional_document_ids"] == ["CSB-APPENDIX"]
    assert row["source_case_exclusion_ready"] is True
    assert row["retrieval_enabled"] is True


def test_known_alias_override_does_not_enable_retrieval_without_text_source():
    records = [{
        "document_id": "CSB-6330", "title": "Bio Lab Conyers archive alias",
        "source_detail_urls": ["https://www.csb.gov/bio-lab-conyers-chemical-release-/"],
        "investigation_id": None, "incident_ids": [], "text_source_exists": False,
        "retrieval_enabled": False,
    }]
    aliases = [{
        "document_id": "CSB-6330", "investigation_id": "2020-05-I-LA",
        "canonical_title": "Bio-Lab Lake Charles final report",
        "incident_ids": ["BIOLAB_LAKE_CHARLES_2020"],
        "source_detail_urls": ["https://www.csb.gov/bio-lab-conyers-chemical-release-/"],
        "note": "Two archive pages point to one final report.",
    }]
    apply_known_alias_groups(records, aliases)
    assert records[0]["incident_ids"] == ["BIOLAB_LAKE_CHARLES_2020"]
    assert records[0]["mapping_status"] == "explicit_override"
    assert records[0]["retrieval_requested"] is True
    assert records[0]["retrieval_enabled"] is False
    assert records[0]["retrieval_block_reason"] == "text_source_missing"


def test_incident_retrieval_requires_text_source_even_if_flag_is_stale_true():
    docs = [{
        "document_id": "CSB-1", "title": "Source with missing local text",
        "investigation_id": "2010-2-I-PR", "recommendation_match_key": "2010-2-I-PR",
        "incident_ids": ["CAPECO_2009_10_23"], "candidate_incident_ids": [],
        "corpus_role": "primary_incident_evidence", "mapping_status": "explicit_override",
        "retrieval_enabled": True, "text_source_exists": False,
    }]
    row = build_incident_catalog(docs, [], snapshot={})[0]
    assert row["source_case_exclusion_ready"] is True
    assert row["retrieval_enabled"] is False


def test_candidate_without_incident_mapping_is_not_retrieval_eligible():
    record = {
        "document_id": "CSB-1", "investigation_id": "2010-2-I-PR",
        "recommendation_match_key": "2010-2-I-PR", "incident_ids": [],
        "candidate_incident_ids": [], "document_type": "unclassified",
        "corpus_role": "unclassified", "retrieval_requested": False,
        "retrieval_enabled": False, "text_source_exists": True,
    }
    apply_automatic_classifications([record], {
        "CSB-1": {
            "classification_profile": "primary_report",
            "candidate_investigation_id": "2010-2-I-PR",
            "candidate_incident_ids": "",
            "classification_confidence": "medium-high",
            "classification_basis": "Candidate document type, no stable incident link.",
        }
    })
    assert record["retrieval_requested"] is True
    assert record["retrieval_enabled"] is False
    assert record["retrieval_block_reason"] == "incident_mapping_missing"


def test_unmapped_sibling_is_included_in_whole_investigation_exclusion():
    docs = [
        {
            "document_id": "CSB-PRIMARY", "title": "Primary report",
            "investigation_id": "1998-002-I-LA", "recommendation_match_key": "1998-2-I-LA",
            "incident_ids": ["SONAT_1998"], "candidate_incident_ids": [],
            "corpus_role": "primary_incident_evidence", "mapping_status": "explicit_override",
            "retrieval_enabled": True, "text_source_exists": True,
        },
        {
            "document_id": "CSB-UNMAPPED-APPENDIX", "title": "Sibling appendix",
            "candidate_investigation_id": "98-002-I-LA", "candidate_recommendation_match_key": "1998-2-I-LA",
            "incident_ids": [], "candidate_incident_ids": [], "corpus_role": "supplementary_incident_evidence",
            "mapping_status": "source_metadata_candidate", "retrieval_enabled": False,
        },
    ]
    recommendations = [{"recommendation_id": "1998-2-I-LA-R1", "recommendation_match_key": "1998-2-I-LA", "incident_ids": []}]
    row = build_incident_catalog(docs, recommendations, snapshot={"snapshot_date": "2026-09-16"})[0]
    assert row["document_ids"] == ["CSB-PRIMARY", "CSB-UNMAPPED-APPENDIX"]
    assert "CSB-UNMAPPED-APPENDIX" in row["inferred_provisional_document_ids"]
    assert docs[1]["inferred_candidate_incident_ids"] == ["SONAT_1998"]
    assert row["source_case_exclusion_ready"] is True
    assert row["source_case_exclusion"]["recommendation_ids"] == ["1998-2-I-LA-R1"]


def test_candidate_recommendation_links_are_not_exported_as_verified_incident_ids():
    documents = [{
        "document_id": "CSB-CANDIDATE", "title": "Proposed incident report",
        "investigation_id": None, "candidate_investigation_id": "2010.02.I.PR",
        "recommendation_match_key": None, "candidate_recommendation_match_key": "2010-2-I-PR",
        "incident_ids": [], "candidate_incident_ids": ["CAPECO_2009_10_23"],
        "corpus_role": "primary_incident_evidence", "mapping_status": "automatic_candidate_mapping",
        "retrieval_enabled": True, "text_source_exists": True,
    }]
    recommendations = [{
        "recommendation_id": "2010-2-I-PR-R1", "recommendation_match_key": "2010-2-I-PR", "incident_ids": []
    }]
    row = build_incident_catalog(documents, recommendations, snapshot={})[0]
    assert row["recommendation_ids"] == ["2010-2-I-PR-R1"]
    assert row["verified_recommendation_ids"] == []
    assert row["candidate_recommendation_ids"] == ["2010-2-I-PR-R1"]
    assert recommendations[0]["incident_ids"] == []
    assert recommendations[0]["candidate_incident_ids"] == ["CAPECO_2009_10_23"]
    assert recommendations[0]["incident_link_status"] == "candidate_only"


def test_shared_appendix_is_excluded_for_every_event_in_multi_event_investigation():
    documents = [
        {
            "document_id": "CSB-EVENT-1", "title": "Event 1 report",
            "investigation_id": "2003-13-I-LA", "recommendation_match_key": "2003-13-I-LA",
            "incident_ids": ["HONEYWELL_EVENT_1"], "candidate_incident_ids": [],
            "mapping_status": "explicit_override", "corpus_role": "primary_incident_evidence",
            "retrieval_enabled": True, "text_source_exists": True,
        },
        {
            "document_id": "CSB-EVENT-2", "title": "Event 2 report",
            "investigation_id": "2003-13-I-LA", "recommendation_match_key": "2003-13-I-LA",
            "incident_ids": ["HONEYWELL_EVENT_2"], "candidate_incident_ids": [],
            "mapping_status": "explicit_override", "corpus_role": "primary_incident_evidence",
            "retrieval_enabled": True, "text_source_exists": True,
        },
        {
            "document_id": "CSB-SHARED-APPENDIX", "title": "Shared appendix",
            "investigation_id": "2003-13-I-LA", "recommendation_match_key": "2003-13-I-LA",
            "incident_ids": [], "candidate_incident_ids": [],
            "mapping_status": "source_metadata_candidate", "corpus_role": "supplementary_incident_evidence",
            "retrieval_enabled": False,
        },
    ]
    rows = build_incident_catalog(documents, [], snapshot={})
    by_id = {row["incident_id"]: row for row in rows}
    assert set(documents[2]["inferred_candidate_incident_ids"]) == {"HONEYWELL_EVENT_1", "HONEYWELL_EVENT_2"}
    for incident_id in ("HONEYWELL_EVENT_1", "HONEYWELL_EVENT_2"):
        assert "CSB-SHARED-APPENDIX" in by_id[incident_id]["source_case_exclusion"]["document_ids"]
        assert by_id[incident_id]["source_case_exclusion_ready"] is True
        assert by_id[incident_id]["source_case_exclusion_status"] == "whole_investigation_candidate_scope"


def test_nonincident_investigation_numbers_do_not_form_recommendation_keys():
    assert recommendation_match_key("2006-H-1") is None
    assert recommendation_match_key("2001-01-H") is None
