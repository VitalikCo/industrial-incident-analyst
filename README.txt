Follow-up patch for Industrial Incident Analyst

Files:
- scripts/collect_csb.py: collector with safer primary-final-report link selection and a targeted --only-detail-url option.
- scripts/audit_pdf_contents.py: read-only PDF integrity/text audit using PyMuPDF.
- tests/test_primary_report_selection.py: regression tests for Appendix/Update/Urgent Recommendation selection.

To apply in the existing project:
1. Back up scripts/collect_csb.py.
2. Replace it with this version.
3. Copy audit_pdf_contents.py into scripts/.
4. Add PyMuPDF: uv add 'PyMuPDF>=1.24,<2'
5. Run tests: uv run pytest -q
6. Run targeted Bio-Lab 2024 refresh:
   uv run python scripts/collect_csb.py --only-detail-url https://www.csb.gov/bio-lab-inc-conyers-fire-and-chemical-release-/ --timeout 90 --delay 2
7. Run PDF content audit:
   uv run python scripts/audit_pdf_contents.py

The targeted refresh will re-fetch only one detail page. It detects the changed primary PDF URL and replaces the previously downloaded first-linked appendix with the main investigation report. The previous report file is replaced in place; make a copy first if you want to preserve the appendix PDF.
