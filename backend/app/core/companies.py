"""companies.yaml loader.

Keeps verified slugs (confirmed by a live call) separate from candidates
(guesses). Validation promotes candidates that answer and prunes only those
that return a definitive 404 - never those that merely had zero openings or
timed out.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.config import BACKEND_ROOT


@dataclass(frozen=True, slots=True)
class Company:
    slug: str
    platform: str
    tier: int = 2
    note: str | None = None
    verified: bool = True
    # Platform-specific settings from companies.yaml: a Workday tenant needs
    # its `wd` shard and career `site`, an Avature portal its `base` URL.
    meta: dict = field(default_factory=dict, compare=False, hash=False)

    def get(self, key: str, default=None):
        return self.meta.get(key, default)

    @property
    def key(self) -> tuple[str, str]:
        return (self.platform, self.slug)


class CompanyRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (BACKEND_ROOT / "companies.yaml")
        self._data: dict = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}

    @property
    def platforms(self) -> list[str]:
        return sorted(self._data)

    def companies(
        self, platform: str, include_candidates: bool = False
    ) -> list[Company]:
        block = self._data.get(platform) or {}
        out: list[Company] = []

        for entry in block.get("verified") or []:
            if isinstance(entry, str):
                out.append(Company(entry, platform))
            else:
                out.append(
                    Company(
                        slug=entry["slug"],
                        platform=platform,
                        tier=int(entry.get("tier", 2)),
                        note=entry.get("note"),
                        meta={
                            k: v
                            for k, v in entry.items()
                            if k not in ("slug", "tier", "note")
                        },
                    )
                )

        if include_candidates:
            known = {c.slug for c in out}
            for slug in block.get("candidates") or []:
                if slug not in known:
                    out.append(Company(slug, platform, tier=2, verified=False))
        return out

    def all_companies(self, include_candidates: bool = False) -> list[Company]:
        return [
            c
            for p in self.platforms
            for c in self.companies(p, include_candidates)
        ]

    # ------------------------------------------------------------------ writing

    def promote(self, found: dict[tuple[str, str], str | None]) -> int:
        """Move candidates that answered into `verified`, keeping notes."""
        changed = 0
        for (platform, slug), note in found.items():
            block = self._data.setdefault(platform, {})
            verified = block.setdefault("verified", [])
            if any(
                (e["slug"] if isinstance(e, dict) else e) == slug for e in verified
            ):
                continue
            entry = {"slug": slug, "tier": 2}
            if note:
                entry["note"] = note
            verified.append(entry)
            cands = block.get("candidates") or []
            if slug in cands:
                cands.remove(slug)
            changed += 1
        return changed

    def prune(self, missing: list[tuple[str, str]]) -> int:
        """Drop candidates that were tested and ruled out - and remember them.

        Without the `rejected` memory, every discovery run re-added the same
        ~2,300 YC names on all four platforms and re-tested ~9,000 slugs that
        had already come back 404, making each run ~5 minutes of redundant
        requests.
        """
        removed = 0
        for platform, slug in missing:
            block = self._data.setdefault(platform, {})
            cands = block.get("candidates") or []
            rejected = block.setdefault("rejected", [])
            if slug in cands:
                cands.remove(slug)
                removed += 1
            if slug not in rejected:
                rejected.append(slug)
        for block in self._data.values():
            if isinstance(block, dict) and block.get("rejected"):
                block["rejected"] = sorted(set(block["rejected"]))
        return removed

    def add_discovered(self, platform: str, slugs: list[str]) -> int:
        """Append newly discovered slugs as candidates, skipping known ones."""
        block = self._data.setdefault(platform, {})
        verified = {
            (e["slug"] if isinstance(e, dict) else e)
            for e in (block.get("verified") or [])
        }
        cands = block.setdefault("candidates", [])
        existing = set(cands) | verified | set(block.get("rejected") or [])
        added = [s for s in slugs if s not in existing]
        cands.extend(sorted(added))
        return len(added)

    def save(self) -> None:
        self.path.write_text(
            yaml.safe_dump(self._data, sort_keys=False, allow_unicode=True, width=100),
            encoding="utf-8",
        )


@lru_cache(maxsize=1)
def get_registry() -> CompanyRegistry:
    return CompanyRegistry()


def companies_for(platform: str, tier: int | None = None) -> list[Company]:
    cs = get_registry().companies(platform)
    return [c for c in cs if tier is None or c.tier <= tier]
