# Refresh local PDF availability

The catalog CSV/JSONL files included here use the current automatic-classification schema and current source mapping logic. Their `file_exists` and `text_source_exists` flags are inherited from the previous catalog snapshot (`2026-11-13T00:13:33Z`), because the uploaded ZIP does not contain the complete `data/raw/reports/` directory. These flags are preserved as the last known local inventory, not freshly verified in this extracted environment.

After extracting this update into the original project directory that still contains the downloaded CSB reports, refresh the availability checks and catalogs with:

```bash
uv run python scripts/build_document_catalog.py
uv run pytest -q
```

Preserve your existing `data/raw/reports/` folder. The normal builder recomputes local file availability from the actual files.
