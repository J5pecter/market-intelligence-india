"""Cache + rate limiting + circuit breaker.

Uses Redis when REDIS_URL is set, otherwise an in-process TTL dictionary. The
in-process fallback keeps the MVP free to run (no Redis instance required) but
is per-worker, so run a single uvicorn worker or set REDIS_URL when scaling.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from app.core.config import settings

try:  # pragma: no cover - optional dependency
    import redis as _redis
except Exception:  # noqa: BLE001
    _redis = None


class _MemoryBackend:
    def __init__(self) -> None:
        self._data: Dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if expires_at and expires_at < time.time():
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: str, value: str, ttl: int) -> None:
        with self._lock:
            self._data[key] = (time.time() + ttl if ttl else 0.0, value)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def incr_window(self, key: str, window_seconds: int) -> int:
        """Fixed-window counter used by the rate limiter."""
        bucket = f"{key}:{int(time.time() // window_seconds)}"
        with self._lock:
            entry = self._data.get(bucket)
            count = int(entry[1]) + 1 if entry else 1
            self._data[bucket] = (time.time() + window_seconds, str(count))
        return count

    def ping(self) -> bool:
        return True


class _RedisBackend:  # pragma: no cover - requires a live Redis
    def __init__(self, url: str) -> None:
        self._client = _redis.from_url(url, decode_responses=True)

    def get(self, key: str) -> Optional[str]:
        return self._client.get(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        if ttl:
            self._client.setex(key, ttl, value)
        else:
            self._client.set(key, value)

    def delete(self, key: str) -> None:
        self._client.delete(key)

    def incr_window(self, key: str, window_seconds: int) -> int:
        bucket = f"{key}:{int(time.time() // window_seconds)}"
        pipe = self._client.pipeline()
        pipe.incr(bucket)
        pipe.expire(bucket, window_seconds)
        return int(pipe.execute()[0])

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:  # noqa: BLE001
            return False


def _build_backend():
    if settings.redis_url and _redis is not None:
        try:
            backend = _RedisBackend(settings.redis_url)
            if backend.ping():
                return backend, "redis"
        except Exception:  # noqa: BLE001
            pass
    return _MemoryBackend(), "memory"


_backend, BACKEND_KIND = _build_backend()


def cache_get(key: str) -> Optional[Any]:
    raw = _backend.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    _backend.set(key, json.dumps(value, default=str), ttl_seconds)


def cache_delete(key: str) -> None:
    _backend.delete(key)


def cache_backend_healthy() -> bool:
    return _backend.ping()


def cached_call(key: str, ttl_seconds: int, producer: Callable[[], Any]) -> Any:
    hit = cache_get(key)
    if hit is not None:
        return hit
    value = producer()
    if value is not None:
        cache_set(key, value, ttl_seconds)
    return value


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


def rate_limit_ok(bucket: str, limit: int, window_seconds: int = 60) -> bool:
    """Fixed-window limiter. Returns False when the caller must back off."""
    if limit <= 0:
        return True
    return _backend.incr_window(f"rl:{bucket}", window_seconds) <= limit


# --------------------------------------------------------------------------
# Circuit breaker
# --------------------------------------------------------------------------


@dataclass
class CircuitBreaker:
    """Trips after `failure_threshold` consecutive failures, then half-opens
    after `reset_seconds` to probe whether the provider recovered."""

    name: str
    failure_threshold: int = 4
    reset_seconds: int = 120
    _failures: int = field(default=0, init=False)
    _opened_at: float = field(default=0.0, init=False)

    @property
    def state(self) -> str:
        if self._opened_at == 0.0:
            return "CLOSED"
        if time.time() - self._opened_at >= self.reset_seconds:
            return "HALF_OPEN"
        return "OPEN"

    def allows(self) -> bool:
        return self.state in ("CLOSED", "HALF_OPEN")

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = 0.0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = time.time()


_breakers: Dict[str, CircuitBreaker] = {}
_breaker_lock = threading.Lock()


def get_breaker(name: str) -> CircuitBreaker:
    with _breaker_lock:
        if name not in _breakers:
            _breakers[name] = CircuitBreaker(name=name)
        return _breakers[name]


def breaker_snapshot() -> Dict[str, str]:
    with _breaker_lock:
        return {name: cb.state for name, cb in _breakers.items()}
