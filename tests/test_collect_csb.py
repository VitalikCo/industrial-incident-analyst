from bs4 import BeautifulSoup

from scripts.collect_csb import (
    csb_document_id,
    discover_detail_links,
    extract_date_after_label,
    is_translation_label,
    select_primary_report,
    slugify,
)


def test_discover_detail_links():
    html = '''
    <div class="card">
      <a href="/example-incident/"><h3>Example Incident</h3></a>
      <a href="/example-incident/">full Investigation details</a>
    </div>
    '''
    soup = BeautifulSoup(html, "html.parser")
    links = discover_detail_links(soup, "https://www.csb.gov/investigations/completed-investigations/")
    assert links == [("https://www.csb.gov/example-incident/", "Example Incident")]


def test_extract_date_after_label():
    assert extract_date_after_label(
        "Accident Occurred On: 06/04/2025 | Final Report Released On: 09/16/2026",
        "Accident Occurred On",
    ) == "06/04/2025"


def test_slugify():
    assert slugify("Shell Polymers Furnace Explosion and Fire") == "shell-polymers-furnace-explosion-and-fire"


def test_csb_document_id_is_case_insensitive():
    assert csb_document_id("https://www.csb.gov/file.aspx?DocumentId=6304") == "6304"
    assert csb_document_id("https://www.csb.gov/file.aspx?documentid=6268") == "6268"


def test_cuisine_solutions_prefers_english_final_report_and_retains_spanish_translation():
    primary, supplements = select_primary_report([
        ("FINAL REPORT: Cuisine Solutions - Spanish Version", "https://www.csb.gov/file.aspx?DocumentId=6323"),
        ("FINAL REPORT: Hazardous Ammonia Release at Cuisine Solutions, Inc. Facility", "https://www.csb.gov/file.aspx?DocumentId=6304"),
        ("FINAL REPORT: Appendix D of Cuisine Solutions Report", "https://www.csb.gov/file.aspx?DocumentId=6310"),
    ])
    assert primary.endswith("DocumentId=6304")
    assert any(item["reason"] == "language_translation" and item["url"].endswith("DocumentId=6323") for item in supplements)
    assert any(item["reason"] == "supplementary_or_update" for item in supplements)


def test_foundation_food_group_prefers_english_final_report_and_retains_spanish_translation():
    primary, supplements = select_primary_report([
        ("FINAL REPORT: Fuga fatal de nitrógeno líquido en Foundation Food Group", "https://www.csb.gov/file.aspx?DocumentId=6269"),
        ("FINAL REPORT: Fatal Liquid Nitrogen Release at Foundation Food Group", "https://www.csb.gov/file.aspx?DocumentId=6268"),
    ])
    assert primary.endswith("DocumentId=6268")
    assert any(item["reason"] == "language_translation" and item["url"].endswith("DocumentId=6269") for item in supplements)


def test_translation_is_fallback_only_when_no_english_final_report_exists():
    primary, supplements = select_primary_report([
        ("FINAL REPORT: Fuga fatal de nitrógeno líquido en Foundation Food Group", "https://www.csb.gov/file.aspx?DocumentId=6269"),
    ])
    assert primary.endswith("DocumentId=6269")
    assert supplements == []


def test_appendix_only_page_does_not_become_primary_final_report():
    primary, supplements = select_primary_report([
        ("FINAL REPORT: Appendix D of Example Report", "https://www.csb.gov/file.aspx?DocumentId=9001"),
    ])
    assert primary is None
    assert supplements[0]["reason"] == "supplementary_or_update"
