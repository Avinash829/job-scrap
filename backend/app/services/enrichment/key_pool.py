"""Round-robin API key pool with per-key cooldown.

Purpose is resilience, not quota multiplication: a key that returns 429 or
403 is benched for a cooldown window and the next one is tried, so a
transient limit on one key doesn't fail the run. If every key is benched the
caller is told to fall back to rules-only rather than blocking.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class _KeyState:
    key: str
    cooldown_until: float = 0.0
    failures: int = 0
    successes: int = 0
    consecutive_failures: int = 0

    @property
    def available(self) -> bool:
        return time.monotonic() >= self.cooldown_until

    def masked(self) -> str:
        return f"{self.key[:6]}...{self.key[-4:]}" if len(self.key) > 12 else "***"


class AllKeysExhausted(RuntimeError):
    """Every key is in cooldown. Caller should degrade, not retry."""


class KeyPool:
    BASE_COOLDOWN_S = 60.0
    MAX_COOLDOWN_S = 900.0

    def __init__(self, keys: list[str]) -> None:
        deduped = list(dict.fromkeys(k for k in keys if k))
        self._states = [_KeyState(k) for k in deduped]
        self._idx = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._states)

    @property
    def has_keys(self) -> bool:
        return bool(self._states)

    def acquire(self) -> _KeyState:
        """Next available key, round-robin."""
        with self._lock:
            if not self._states:
                raise AllKeysExhausted("no Gemini keys configured")
            for _ in range(len(self._states)):
                state = self._states[self._idx % len(self._states)]
                self._idx += 1
                if state.available:
                    return state
            soonest = min(s.cooldown_until for s in self._states) - time.monotonic()
            raise AllKeysExhausted(
                f"all {len(self._states)} keys cooling down "
                f"(~{max(0, int(soonest))}s remaining)"
            )

    def report_success(self, state: _KeyState) -> None:
        with self._lock:
            state.successes += 1
            state.consecutive_failures = 0

    def report_failure(self, state: _KeyState, *, rate_limited: bool) -> None:
        """Exponential bench time, so a dead key stops being retried."""
        with self._lock:
            state.failures += 1
            state.consecutive_failures += 1
            if rate_limited:
                backoff = min(
                    self.BASE_COOLDOWN_S * (2 ** (state.consecutive_failures - 1)),
                    self.MAX_COOLDOWN_S,
                )
                state.cooldown_until = time.monotonic() + backoff
                log.warning(
                    "key %s rate-limited, benched %.0fs", state.masked(), backoff
                )

    def stats(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "key": s.masked(),
                    "ok": s.successes,
                    "fail": s.failures,
                    "available": s.available,
                }
                for s in self._states
            ]
