from scripts.refresh_english_report_candidates import refresh_candidate_rows


def test_candidate_rows_are_rekeyed_to_english_documents_and_keep_incident_ids():
    rows = [
        {
            "document_id": "CSB-6323", "title": "Cuisine Solutions Spanish",
            "classification_profile": "primary_translation",
            "candidate_incident_ids": "CUISINE_SOLUTIONS_2024_07_31",
        },
        {
            "document_id": "CSB-6269", "title": "Foundation Food Group Spanish",
            "classification_profile": "primary_translation",
            "candidate_incident_ids": "FOUNDATION_FOOD_GROUP_2021_01_28",
        },
    ]
    manifest = [
        {
            "final_report_url": "https://www.csb.gov/file.aspx?DocumentId=6304",
            "title": "Cuisine Solutions Ammonia Release",
            "local_pdf_path": "data/raw/reports/cuisine-solutions--csb-6304.pdf",
            "pdf_sha256": "sha-cuisine",
        },
        {
            "final_report_url": "https://www.csb.gov/file.aspx?DocumentId=6268",
            "title": "Foundation Food Group Fatal Chemical Release",
            "local_pdf_path": "data/raw/reports/foundation-food--csb-6268.pdf",
            "pdf_sha256": "sha-foundation",
        },
    ]
    audit = {
        "data/raw/reports/cuisine-solutions--csb-6304.pdf": {
            "audit_status": "opened_and_text_extracted", "page_count": 96,
            "flags": [], "first_page_preview": "Hazardous Ammonia Release", "sha256": "sha-cuisine",
        },
        "data/raw/reports/foundation-food--csb-6268.pdf": {
            "audit_status": "opened_and_text_extracted", "page_count": 116,
            "flags": [], "first_page_preview": "Fatal Liquid Nitrogen Release", "sha256": "sha-foundation",
        },
    }
    # Replace filesystem checks for the helper function with existing temp files.
    import tempfile
    from pathlib import Path
    from scripts import refresh_english_report_candidates as module

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monkey_manifest = []
        for item in manifest:
            path = root / item["local_pdf_path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"%PDF-test")
            copied = dict(item)
            monkey_manifest.append(copied)
        old_root = module.ROOT
        try:
            module.ROOT = root
            count = refresh_candidate_rows(rows, monkey_manifest, audit)
        finally:
            module.ROOT = old_root

    assert count == 2
    assert [row["document_id"] for row in rows] == ["CSB-6304", "CSB-6268"]
    assert [row["classification_profile"] for row in rows] == ["primary_report", "primary_report"]
    assert rows[0]["candidate_incident_ids"] == "CUISINE_SOLUTIONS_2024_07_31"
    assert rows[1]["candidate_incident_ids"] == "FOUNDATION_FOOD_GROUP_2021_01_28"
    assert rows[0]["page_count"] == "96"
    assert rows[1]["source_url"].endswith("DocumentId=6268")
