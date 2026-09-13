"""Slug -> YC company metadata, written by `cli yc` and read at ingest time.

Kept in its own file rather than in companies.yaml because validation
rewrites that file, and because the mapping is one-to-many: several slug
variants can point at the same company.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.core.config import BACKEND_ROOT

INDEX_PATH = BACKEND_ROOT / "yc_index.json"


@lru_cache(maxsize=1)
def load() -> dict[str, dict]:
    if not INDEX_PATH.exists():
        return {}
    try:
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def lookup(slug: str) -> dict | None:
    """YC metadata for an ATS board slug, if that slug is a YC company."""
    return load().get(slug.lower())


def save(index: dict[str, dict]) -> int:
    INDEX_PATH.write_text(
        json.dumps(index, indent=1, sort_keys=True), encoding="utf-8"
    )
    load.cache_clear()
    return len(index)
