"""Connector contract shared by all ~40 sources.

Two HTTP paths, chosen per-connector:
  httpx      - default, fast, async
  curl_cffi  - TLS/JA3 impersonation, for endpoints that 403 a plain client
               (several of the undocumented FAANG career APIs do this)
"""
from __future__ import annotations

import abc
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.domain.entities import ConnectorRun, RawJob

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """Carries the HTTP status so callers can tell 404 from 502.

    That distinction is the whole point: a 404 means the slug is wrong, while
    a 502 means retry later. Collapsing them is how coverage silently rots.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _retry_transient(retry_state) -> bool:
    """Retry network blips and 5xx, never a 404.

    A definitive 404 means the slug is wrong; retrying it three times just
    triples the request count for no new information.
    """
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if exc is None:
        return False
    if isinstance(exc, FetchError):
        code = exc.status_code
        return code is None or code >= 500 or code == 429
    return isinstance(exc, httpx.HTTPError)


class Connector(abc.ABC):
    """Subclass and implement `fetch`.

    name        stable id, stored on every job row
    tier        refresh tier: 1 = every 6h, 2 = daily, 3 = weekly
    impersonate None -> httpx; e.g. "chrome124" -> curl_cffi
    """

    name: str
    tier: int = 2
    impersonate: str | None = None
    rate_limit_delay: float = 0.0   # seconds between requests, be polite

    # One pooled client per connector, created lazily and reused for every
    # request. An earlier version opened a fresh AsyncClient per call, which
    # at concurrency 30 meant 30 simultaneous TLS handshakes and surfaced as
    # "ConnectError: All connection attempts failed" on endpoints that answer
    # a pooled client perfectly well.
    _client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            settings = get_settings()
            self._client = httpx.AsyncClient(
                timeout=settings.http_timeout,
                follow_redirects=True,
                http2=True,
                limits=httpx.Limits(
                    max_connections=max(20, self.concurrency_hint),
                    max_keepalive_connections=10,
                    keepalive_expiry=30.0,
                ),
                headers={"User-Agent": settings.user_agent},
            )
        return self._client

    @property
    def concurrency_hint(self) -> int:
        return int(getattr(self, "concurrency", 10))

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # ---------------------------------------------------------------- fetching

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=_retry_transient,
        reraise=True,
    )
    async def get_json(self, url: str, **kw) -> Any:
        return await self._request("GET", url, expect="json", **kw)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type((httpx.HTTPError, FetchError)),
        retry_error_callback=None,
        reraise=True,
    )
    async def get_text(self, url: str, **kw) -> str:
        return await self._request("GET", url, expect="text", **kw)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type((httpx.HTTPError, FetchError)),
        retry_error_callback=None,
        reraise=True,
    )
    async def post_json(self, url: str, json: dict, **kw) -> Any:
        return await self._request("POST", url, expect="json", json=json, **kw)

    async def _request(self, method: str, url: str, *, expect: str, **kw) -> Any:
        settings = get_settings()
        headers = {"User-Agent": settings.user_agent, "Accept": "application/json, text/*"}
        headers.update(kw.pop("headers", {}) or {})

        if self.rate_limit_delay:
            await asyncio.sleep(self.rate_limit_delay)

        if self.impersonate:
            return await self._request_curl(
                method, url, expect, headers, settings.http_timeout, **kw
            )

        client = self._get_client()
        r = await client.request(method, url, headers=headers, **kw)
        if r.status_code >= 400:
            raise FetchError(
                f"{self.name}: {url} -> HTTP {r.status_code}", r.status_code
            )
        return r.json() if expect == "json" else r.text

    async def _request_curl(
        self, method: str, url: str, expect: str, headers: dict, timeout: float, **kw
    ) -> Any:
        """curl_cffi is sync-only; push it to a thread so we stay async."""
        from curl_cffi import requests as cffi

        def _do():
            r = cffi.request(
                method,
                url,
                headers=headers,
                timeout=timeout,
                impersonate=self.impersonate,
                **kw,
            )
            if r.status_code >= 400:
                raise FetchError(
                    f"{self.name}: {url} -> HTTP {r.status_code}", r.status_code
                )
            return r.json() if expect == "json" else r.text

        return await asyncio.to_thread(_do)

    # ---------------------------------------------------------------- contract

    @abc.abstractmethod
    async def fetch(self) -> Iterable[RawJob]:
        """Return every currently-open posting this source exposes."""
        raise NotImplementedError

    async def run(self) -> tuple[list[RawJob], ConnectorRun]:
        """Wrap fetch with health tracking.

        A connector that returns 200 OK with zero jobs is the dangerous
        failure mode - it looks fine and silently rots. `ok` is False on an
        empty result so the run record flags it.
        """
        rec = ConnectorRun(connector=self.name, started_at=datetime.now(timezone.utc))
        jobs: list[RawJob] = []
        try:
            jobs = list(await self.fetch())
            rec.jobs_found = len(jobs)
            rec.ok = len(jobs) > 0
            if not jobs:
                rec.error = "returned zero jobs (possible silent breakage)"
        except Exception as exc:  # noqa: BLE001 - one bad source must not kill the run
            rec.ok = False
            rec.error = f"{type(exc).__name__}: {exc}"
            log.warning("connector %s failed: %s", self.name, rec.error)
        finally:
            rec.finished_at = datetime.now(timezone.utc)
            await self.aclose()
        return jobs, rec
