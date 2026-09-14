"""Typed application settings.

Everything environment-driven lands here once, validated at import, so no
module ever reaches for os.getenv() directly.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import dotenv_values
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # backend/.env only. The frontend has its own .env for VITE_* values;
    # nothing secret is ever shared between the two.
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- storage ---
    database_url: str = Field(
        default="sqlite:///./jobscrap.db", alias="DATABASE_URL"
    )

    # --- LLM ---
    # Comma-separated lists are read as plain strings and split in the
    # properties below. pydantic-settings JSON-decodes any list[str] field
    # coming from a dotenv file, so `k1,k2,k3` would raise before validation -
    # keeping these as str is the version-proof fix.
    gemini_api_keys_raw: str = Field(default="", alias="GEMINI_API_KEYS")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    gemini_fallback_models_raw: str = Field(
        default="gemini-flash-latest,gemini-2.5-flash-lite,gemini-2.0-flash",
        alias="GEMINI_FALLBACK_MODELS",
    )
    llm_batch_size: int = 12
    llm_max_batches_per_run: int = 40  # hard ceiling so a run can't burn quota

    # --- http ---
    http_timeout: float = Field(default=30.0, alias="HTTP_TIMEOUT")
    max_concurrency: int = Field(default=8, alias="MAX_CONCURRENCY")
    user_agent: str = Field(
        default="jobscrap/0.1 (personal job search tool)", alias="USER_AGENT"
    )

    # --- alerts ---
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, alias="TELEGRAM_CHAT_ID")

    # --- api ---
    cors_origins_raw: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )
    # Vercel gives every preview deploy its own random hostname, so an exact
    # allow-list can never cover them. This regex does, without opening the
    # API to the whole internet.
    cors_origin_regex: str = Field(
        default=r"https://.*\.vercel\.app",
        alias="CORS_ORIGIN_REGEX",
    )

    @staticmethod
    def _csv(value: str) -> list[str]:
        """Tolerant of trailing commas, stray quotes and padding - all of
        which are easy to introduce when pasting keys into a .env by hand."""
        return [
            item.strip().strip("\"'")
            for item in value.split(",")
            if item.strip().strip("\"'")
        ]

    @property
    def gemini_api_keys(self) -> list[str]:
        """Accept either style, because both are natural to write:

            GEMINI_API_KEYS=key1,key2,key3        (one CSV line)
            GEMINI_API_KEY_1=key1                 (numbered, one per line)
            GEMINI_API_KEY_2=key2

        Numbered vars aren't declared fields, so they're read from the dotenv
        file and the process environment directly. Order is stable: CSV first,
        then numbered ascending. Duplicates are dropped by the pool.
        """
        keys = self._csv(self.gemini_api_keys_raw)

        env: dict[str, str | None] = {}
        env_path = BACKEND_ROOT / ".env"
        if env_path.exists():
            env.update(dotenv_values(env_path))
        env.update(os.environ)  # real env wins over the file (CI secrets)

        numbered: list[tuple[int, str]] = []
        for name, value in env.items():
            if not value:
                continue
            m = re.fullmatch(r"GEMINI_API_KEY_(\d+)", name)
            if m:
                cleaned = value.strip().strip("\"'")
                if cleaned:
                    numbered.append((int(m.group(1)), cleaned))

        if single := (env.get("GEMINI_API_KEY") or "").strip().strip("\"'"):
            keys.append(single)

        keys.extend(v for _, v in sorted(numbered))
        return list(dict.fromkeys(keys))

    @property
    def gemini_fallback_models(self) -> list[str]:
        return self._csv(self.gemini_fallback_models_raw)

    @property
    def cors_origins(self) -> list[str]:
        return self._csv(self.cors_origins_raw)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.gemini_api_keys)


class SearchProfile:
    """config.yaml - the user's search preferences, hot-reloadable."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (BACKEND_ROOT / "config.yaml")
        self._data: dict = yaml.safe_load(self.path.read_text(encoding="utf-8"))

    @property
    def experience(self) -> dict:
        return self._data["experience"]

    @property
    def roles(self) -> dict:
        return self._data["roles"]

    @property
    def location(self) -> dict:
        return self._data["location"]

    @property
    def stack(self) -> dict:
        return self._data.get("stack", {"strong": [], "learning": []})

    @property
    def profile(self) -> dict:
        return self._data.get("profile", {})

    @property
    def freshness(self) -> dict:
        return self._data.get("freshness", {"max_posting_age_days": 2})

    @property
    def employment(self) -> dict:
        return self._data.get(
            "employment", {"allowed": [], "exclude_title_patterns": []}
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_profile() -> SearchProfile:
    return SearchProfile()
