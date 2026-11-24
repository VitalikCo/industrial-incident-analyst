"""Lightweight tokenizer used by the BM25 retriever and recommendation ranker."""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    """Return lowercase alphanumeric tokens from *text*.

    Non-alphanumeric characters act as separators. Empty or whitespace-only
    input yields an empty list.
    """
    if not text:
        return []
    return [token.lower() for token in _TOKEN_RE.findall(text)]
