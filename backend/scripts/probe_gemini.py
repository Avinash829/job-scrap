"""Ask the API which models your keys can actually use, then smoke-test one.

    python -m scripts.probe_gemini

Prints masked keys only - never the secrets themselves.
"""
from __future__ import annotations

import asyncio
import sys

from app.core.config import get_settings
from app.services.enrichment.provider import GeminiProvider, LLMUnavailable

PREFERRED_HINTS = ("flash-lite", "flash", "pro")


async def main() -> int:
    s = get_settings()
    if not s.gemini_api_keys:
        print("FAIL: no GEMINI_API_KEYS in .env")
        print("      add: GEMINI_API_KEYS=key1,key2,key3")
        return 1

    print(f"keys configured: {len(s.gemini_api_keys)}")
    provider = GeminiProvider()
    for st in provider.pool.stats():
        print(f"  - {st['key']}")
    print(f"configured model: {s.gemini_model}")
    print(f"fallbacks:        {', '.join(s.gemini_fallback_models)}\n")

    try:
        models = await provider.list_models()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: could not list models: {type(exc).__name__}: {exc}")
        return 1

    print(f"{len(models)} models support generateContent. Relevant ones:")
    for name in models:
        if any(h in name for h in PREFERRED_HINTS):
            mark = "  <-- configured" if name == s.gemini_model else ""
            print(f"  {name}{mark}")

    if s.gemini_model not in models:
        print(f"\nNOTE: configured model '{s.gemini_model}' is NOT available to this key.")

    try:
        chosen = await provider.resolve_model()
    except LLMUnavailable as exc:
        print(f"\nFAIL: {exc}")
        return 1
    print(f"\nresolved model -> {chosen}")

    print("smoke-testing structured output...")
    schema = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "idx": {"type": "integer"},
                        "min_yoe": {"type": "integer", "nullable": True},
                        "hiring_regions": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["idx", "hiring_regions"],
                },
            }
        },
        "required": ["results"],
    }
    prompt = (
        "Extract min_yoe and hiring_regions. Use 'unknown' if not stated.\n"
        "### idx: 0\nTITLE: Backend Engineer\nDESCRIPTION: Remote (US only). "
        "Requires 4+ years of Python.\n"
        "### idx: 1\nTITLE: Grad Software Engineer\nDESCRIPTION: Open to "
        "candidates anywhere in the world. No prior experience needed.\n"
    )
    try:
        out = await provider.generate_json(prompt, schema)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: generate failed: {type(exc).__name__}: {exc}")
        return 1

    print(f"response: {out}")
    rows = {r.get("idx"): r for r in out.get("results", [])}
    ok = (
        rows.get(0, {}).get("min_yoe") == 4
        and "us" in str(rows.get(0, {}).get("hiring_regions", [])).lower()
        and rows.get(1, {}).get("min_yoe") in (0, None)
        and "worldwide" in str(rows.get(1, {}).get("hiring_regions", [])).lower()
    )
    print("\nPASS: extraction is sane" if ok else "\nWARN: output parsed but values look off")
    print(f"\nput this in .env:  GEMINI_MODEL={chosen}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
