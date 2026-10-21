"""Error classification, back-off with jitter and circuit breakers for every external call.

Usage:
    kind = classify_error(exc)                       # RETRYABLE or NON_RETRYABLE
    result = await retry_async(call, max_retries=2, base_delay=1.0, max_delay=8.0, label="tool x")
    breaker = get_circuit_breaker("tools:portfolio", failure_threshold=5, cooldown_seconds=60)

"No data" is not an error: a call that succeeds with an empty result returns normally and is never retried.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

# Indirection so tests can skip the waits without patching asyncio for the whole process.
_sleep = asyncio.sleep


class ErrorKind(str, Enum):
    RETRYABLE = "retryable"  # timeouts, connection drops, HTTP 5xx, 429: usually clear on their own
    NON_RETRYABLE = "non_retryable"  # entitlement denials, invalid input, other 4xx: retrying cannot help


# error_kind recorded when a call was skipped because its dependency's breaker is open
CIRCUIT_OPEN = "circuit_open"


class CircuitOpenError(RuntimeError):
    """A dependency failed repeatedly and is skipped until its cooldown ends."""


_RETRYABLE_STATUS = frozenset({408, 425, 429})
_STATUS_IN_MESSAGE = re.compile(r"(?:status(?:\s*code)?|http|error\s*code)\D{0,3}([1-5]\d\d)\b", re.IGNORECASE)
_RETRYABLE_TYPE_NAMES = frozenset(
    {
        "RateLimitError", "APITimeoutError", "APIConnectionError", "InternalServerError", "ServiceUnavailableError",
        "TimeoutException", "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
        "ReadError", "WriteError", "RemoteProtocolError", "ServerDisconnectedError", "ClientConnectionError",
    }
)
_NON_RETRYABLE_TYPE_NAMES = frozenset(
    {
        "AuthenticationError", "PermissionDeniedError", "BadRequestError", "NotFoundError",
        "UnprocessableEntityError", "ConflictError",
    }
)
_NON_RETRYABLE_HINTS = (
    "forbidden", "unauthorized", "unauthorised", "not authorized", "not entitled", "entitlement",
    "permission denied", "access denied", "invalid", "bad request", "missing required", "not configured",
)
_RETRYABLE_HINTS = (
    "timed out", "timeout", "temporarily", "unavailable", "connection reset", "connection refused",
    "connection aborted", "connection closed", "rate limit", "too many requests", "try again", "bad gateway",
    "gateway timeout", "server error", "session expired", "session closed",
)


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "http_status", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    value = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(value, int):
        return value
    match = _STATUS_IN_MESSAGE.search(str(exc))
    return int(match.group(1)) if match else None


def _classify_one(exc: BaseException) -> ErrorKind | None:
    if isinstance(exc, CircuitOpenError):
        return ErrorKind.NON_RETRYABLE
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return ErrorKind.RETRYABLE
    if isinstance(exc, PermissionError):
        return ErrorKind.NON_RETRYABLE
    status = _status_code(exc)
    if status is not None:
        if status in _RETRYABLE_STATUS or status >= 500:
            return ErrorKind.RETRYABLE
        if 400 <= status < 500:
            return ErrorKind.NON_RETRYABLE
    names = {cls.__name__ for cls in type(exc).__mro__}
    if names & _RETRYABLE_TYPE_NAMES:
        return ErrorKind.RETRYABLE
    if names & _NON_RETRYABLE_TYPE_NAMES:
        return ErrorKind.NON_RETRYABLE
    if isinstance(exc, (ValueError, TypeError, KeyError)):  # includes schema validation errors
        return ErrorKind.NON_RETRYABLE
    message = str(exc).lower()
    if any(hint in message for hint in _NON_RETRYABLE_HINTS):
        return ErrorKind.NON_RETRYABLE
    if any(hint in message for hint in _RETRYABLE_HINTS):
        return ErrorKind.RETRYABLE
    return None


def classify_error(exc: BaseException, *, default: ErrorKind = ErrorKind.RETRYABLE) -> ErrorKind:
    """Classify a failure; wrapped causes (for example a tool error around an HTTP error) are inspected too."""
    for candidate in (exc, exc.__cause__, exc.__context__):
        if candidate is not None:
            kind = _classify_one(candidate)
            if kind is not None:
                return kind
    return default


def is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timed out" in str(exc).lower()


def backoff_delay(
    attempt: int, *, base_delay: float, max_delay: float, multiplier: float = 2.0, jitter_ratio: float = 0.25
) -> float:
    """Exponential back-off (base, 2x base, 4x base...) plus random jitter so parallel retries do not sync up."""
    delay = min(base_delay * (multiplier ** attempt), max_delay)
    return min(max_delay, delay + random.uniform(0, delay * max(0.0, jitter_ratio)))


@dataclass
class RetryStats:
    attempts: int = 0


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    base_delay: float,
    max_delay: float,
    label: str,
    multiplier: float = 2.0,
    jitter_ratio: float = 0.25,
    default_kind: ErrorKind = ErrorKind.RETRYABLE,
    retry_on_timeout: bool = True,
    stats: RetryStats | None = None,
) -> T:
    """Run fn, retrying only retryable failures with back-off and jitter; non-retryable ones fail fast."""
    stats = stats or RetryStats()
    for attempt in range(max(0, max_retries) + 1):
        stats.attempts = attempt + 1
        try:
            return await fn()
        except Exception as exc:
            kind = classify_error(exc, default=default_kind)
            retryable = kind is ErrorKind.RETRYABLE and (retry_on_timeout or not is_timeout(exc))
            if not retryable or attempt >= max_retries:
                raise
            delay = backoff_delay(
                attempt, base_delay=base_delay, max_delay=max_delay, multiplier=multiplier, jitter_ratio=jitter_ratio
            )
            log.warning(
                "[RETRY] %s attempt=%s/%s kind=%s error=%s: %s; retrying in %.1fs",
                label, attempt + 1, max_retries + 1, kind.value, type(exc).__name__, exc, delay,
            )
            await _sleep(delay)
    raise RuntimeError(f"{label}: retry loop ended without a result")  # unreachable


class CircuitBreaker:
    """Opens after repeated technical failures of one dependency; lets a trial call through after the cooldown."""

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int,
        cooldown_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self._clock = clock
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None and self._clock() - self._opened_at < self.cooldown_seconds

    def allow(self) -> bool:
        return not self.is_open

    def record_success(self) -> None:
        if self._opened_at is not None:
            log.info("[CIRCUIT] closed name=%s (dependency answered again)", self.name)
        self._failures, self._opened_at = 0, None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            if not self.is_open:
                log.error(
                    "[CIRCUIT] opened name=%s failures=%s cooldown_s=%s; calls are skipped until the cooldown ends",
                    self.name, self._failures, self.cooldown_seconds,
                )
            self._opened_at = self._clock()

    def unavailable_error(self) -> CircuitOpenError:
        return CircuitOpenError(f"{self.name} is temporarily unavailable after repeated failures; skipped for now.")


_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(name: str, *, failure_threshold: int, cooldown_seconds: float) -> CircuitBreaker:
    """One breaker per dependency (for example one MCP server), shared by all turns in the process."""
    breaker = _breakers.get(name)
    if breaker is None:
        breaker = _breakers[name] = CircuitBreaker(
            name, failure_threshold=failure_threshold, cooldown_seconds=cooldown_seconds
        )
    return breaker


def reset_circuit_breakers() -> None:
    _breakers.clear()
