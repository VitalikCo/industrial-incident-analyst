from scripts.collect_csb import select_primary_report


def test_selects_main_report_not_appendix_or_update():
    links = [
        ("FINAL REPORT: Appendix E: 2025 CSB Testing Data Reports", "https://example.test/6338"),
        ("FINAL REPORT: Appendix G - Consultant Report", "https://example.test/6336"),
        ("FINAL REPORT: Final Report: Chemical Decomposition at Bio-Lab", "https://example.test/6332"),
        ("FINAL REPORT: Update II: Chemical Decomposition at Bio-Lab", "https://example.test/6292"),
    ]
    primary, supplementary = select_primary_report(links)
    assert primary == "https://example.test/6332"
    assert len(supplementary) == 3


def test_urgent_recommendation_is_not_primary_investigation_report():
    links = [("FINAL REPORT: Urgent Recommendation", "https://example.test/5928")]
    primary, supplementary = select_primary_report(links)
    assert primary is None
    assert supplementary[0]["reason"] == "supplementary_or_update"


def test_regular_final_report_is_selected():
    links = [("FINAL REPORT: Chemical Reaction, Decomposition, and Toxic Gas Release at Bio-Lab", "https://example.test/6330")]
    primary, supplementary = select_primary_report(links)
    assert primary == "https://example.test/6330"
    assert supplementary == []
