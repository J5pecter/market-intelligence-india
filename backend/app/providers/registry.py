"""Provider registry: ordered failover, retry with backoff, health tracking.

Call sites use `registry.fetch("quote", "HDFCBANK")` and receive a `Sourced`
envelope plus, on the envelope's `notes`, the trail of providers that were
tried and why they were skipped. When every provider fails the registry returns
`Sourced.unavailable(...)` - it never invents a value and never silently
substitutes a stale one for a live one.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.core.cache import breaker_snapshot, get_breaker
from app.core.config import settings
from app.core.data_quality import DataStatus, Sourced
from app.providers.base import (MarketDataProvider, ProviderError,
                                ProviderNoData, ProviderUnsupported)
from app.providers.brokers import BROKER_CLASSES
from app.providers.db_backed import DemoProvider, ManualProvider
from app.providers.news_rss import GoogleNewsRssProvider
from app.providers.nse import NseProvider
from app.providers.nse_archives import (BseArchivesProvider,
                                        NseArchivesProvider)
from app.providers.reference import (AmfiProvider, RbiProvider,
                                     WorldBankProvider)
from app.providers.yahoo import YahooProvider

logger = logging.getLogger(__name__)


@dataclass
class ProviderHealth:
    name: str
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    last_error: Optional[str] = None
    success_count: int = 0
    failure_count: int = 0
    call_times: List[float] = field(default_factory=list)

    def calls_last_hour(self) -> int:
        cutoff = time.time() - 3600
        self.call_times = [t for t in self.call_times if t >= cutoff]
        return len(self.call_times)


@dataclass
class FetchAttempt:
    provider: str
    outcome: str          # OK | UNSUPPORTED | ERROR | SKIPPED
    detail: Optional[str] = None
    duration_ms: Optional[float] = None


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: Dict[str, MarketDataProvider] = {}
        self._health: Dict[str, ProviderHealth] = {}
        self._register_defaults()

    # -- registration ------------------------------------------------------

    def _register_defaults(self) -> None:
        providers: List[MarketDataProvider] = [
            YahooProvider(),
            NseProvider(),
            NseArchivesProvider(),
            BseArchivesProvider(),
            AmfiProvider(),
            RbiProvider(),
            WorldBankProvider(),
            GoogleNewsRssProvider(),
            ManualProvider(),
            DemoProvider(),
        ]
        # Brokers are always registered so the health endpoint can report that
        # they exist but are unconfigured. `providers_for()` keeps unconfigured
        # ones out of every chain, so registering them costs nothing.
        providers.extend(cls() for cls in BROKER_CLASSES.values())
        for provider in providers:
            self.register(provider)

    def register(self, provider: MarketDataProvider) -> None:
        self._providers[provider.name] = provider
        self._health.setdefault(provider.name, ProviderHealth(name=provider.name))

    def get(self, name: str) -> Optional[MarketDataProvider]:
        return self._providers.get(name)

    def all(self) -> List[MarketDataProvider]:
        return list(self._providers.values())

    # -- the failover loop -------------------------------------------------

    def fetch(
        self,
        capability: str,
        *args: Any,
        chain: Optional[List[str]] = None,
        retries: int = 1,
        **kwargs: Any,
    ) -> Sourced[Any]:
        """Walk the configured chain until one provider returns a usable value.

        `capability` maps to `get_<capability>` on the adapter. The chain comes
        from settings unless overridden (tests and admin previews override it).
        """
        chain = chain if chain is not None else settings.providers_for(
            _chain_key(capability)
        )
        attempts: List[FetchAttempt] = []

        for name in chain:
            provider = self._providers.get(name)
            if provider is None:
                attempts.append(FetchAttempt(name, "SKIPPED", "not registered"))
                continue
            method: Optional[Callable[..., Sourced[Any]]] = getattr(
                provider, f"get_{capability}", None
            )
            if method is None or not provider.supports(capability):
                attempts.append(FetchAttempt(name, "UNSUPPORTED",
                                             f"no get_{capability}"))
                continue

            breaker = get_breaker(name)
            if not breaker.allows():
                attempts.append(FetchAttempt(name, "SKIPPED",
                                             f"circuit {breaker.state}"))
                continue

            result = self._call_with_retry(provider, method, name, attempts,
                                           retries, *args, **kwargs)
            if result is not None:
                result.notes = _merge_notes(result.notes, attempts)
                return result

        env: Sourced[Any] = Sourced.unavailable(
            capability,
            reason="; ".join(
                f"{a.provider}: {a.outcome}"
                + (f" ({a.detail})" if a.detail else "")
                for a in attempts
            ) or "no providers configured for this capability",
        )
        # Keep the reason: with an empty chain there is no trail to append, and
        # overwriting it would leave the caller with no explanation at all.
        env.notes = _merge_notes(env.notes, attempts)
        return env

    def _call_with_retry(
        self,
        provider: MarketDataProvider,
        method: Callable[..., Sourced[Any]],
        name: str,
        attempts: List[FetchAttempt],
        retries: int,
        *args: Any,
        **kwargs: Any,
    ) -> Optional[Sourced[Any]]:
        breaker = get_breaker(name)
        health = self._health[name]

        for attempt_no in range(retries + 1):
            started = time.perf_counter()
            health.call_times.append(time.time())
            try:
                result = method(*args, **kwargs)
            except ProviderUnsupported as exc:
                attempts.append(FetchAttempt(name, "UNSUPPORTED", str(exc)))
                return None
            except ProviderNoData as exc:
                # The provider is healthy, it simply holds nothing for this key.
                # Fail over without penalising its health or breaker: otherwise
                # a handful of obscure symbols would blind us to every symbol
                # the provider *does* carry.
                duration = (time.perf_counter() - started) * 1000
                attempts.append(FetchAttempt(name, "NO_DATA", str(exc), duration))
                return None
            except ProviderError as exc:
                duration = (time.perf_counter() - started) * 1000
                health.failure_count += 1
                health.last_failure_at = datetime.now(tz=timezone.utc)
                health.last_error = str(exc)
                breaker.record_failure()
                attempts.append(FetchAttempt(name, "ERROR", str(exc), duration))
                if attempt_no < retries:
                    # Exponential backoff, capped - we are not hammering anyone.
                    time.sleep(min(0.25 * (2 ** attempt_no), 2.0))
                    continue
                return None
            except Exception as exc:  # noqa: BLE001 - an adapter bug must not 500
                duration = (time.perf_counter() - started) * 1000
                health.failure_count += 1
                health.last_failure_at = datetime.now(tz=timezone.utc)
                health.last_error = f"{type(exc).__name__}: {exc}"
                breaker.record_failure()
                logger.exception("provider %s raised unexpectedly", name)
                attempts.append(FetchAttempt(name, "ERROR", health.last_error,
                                             duration))
                return None

            duration = (time.perf_counter() - started) * 1000
            if result is None or not result.is_usable:
                attempts.append(FetchAttempt(name, "ERROR", "empty result",
                                             duration))
                breaker.record_failure()
                return None

            health.success_count += 1
            health.last_success_at = datetime.now(tz=timezone.utc)
            breaker.record_success()
            attempts.append(FetchAttempt(name, "OK", None, duration))
            return result
        return None

    # -- health ------------------------------------------------------------

    def health_report(self) -> List[Dict[str, Any]]:
        breakers = breaker_snapshot()
        out: List[Dict[str, Any]] = []
        for name, provider in self._providers.items():
            health = self._health[name]
            enabled = _provider_enabled(name)
            out.append({
                **provider.describe(),
                "enabled": enabled,
                "circuit_state": breakers.get(name, "CLOSED"),
                "status": _derive_status(enabled, health, breakers.get(name)),
                "last_success_at": health.last_success_at.isoformat()
                if health.last_success_at else None,
                "last_failure_at": health.last_failure_at.isoformat()
                if health.last_failure_at else None,
                "last_error": health.last_error,
                "success_count": health.success_count,
                "failure_count": health.failure_count,
                "calls_last_hour": health.calls_last_hour(),
                "in_chains": [
                    key for key in ("quote", "history", "option_chain", "news",
                                    "ipo", "eod", "macro")
                    if name in settings.providers_for(key)
                ],
            })
        return out


def _provider_enabled(name: str) -> bool:
    if name == "nse":
        return settings.enable_nse_provider
    if name == "demo":
        return settings.demo_data_allowed
    if name.endswith("_archives"):
        return settings.enable_exchange_archives
    if name in BROKER_CLASSES:
        return settings.broker_is_configured(name)
    return True


def _derive_status(enabled: bool, health: ProviderHealth,
                   circuit: Optional[str]) -> str:
    if not enabled:
        return "DISABLED"
    if circuit == "OPEN":
        return "DOWN"
    if health.last_failure_at and (
        health.last_success_at is None
        or health.last_failure_at > health.last_success_at
    ):
        return "DEGRADED"
    if health.last_success_at:
        return "OK"
    return "UNKNOWN"


def _chain_key(capability: str) -> str:
    """Several capabilities share one configured chain."""
    return {
        "quote": "quote",
        "quotes": "quote",
        "indices": "quote",
        "instruments": "quote",
        "corporate_actions": "quote",
        "fundamentals": "quote",
        "history": "history",
        "option_chain": "option_chain",
        "futures_chain": "option_chain",
        "news": "news",
        "ipos": "ipo",
        "bhavcopy": "eod",
        "delivery": "eod",
        "bulk_deals": "eod",
        "block_deals": "eod",
        "fii_dii": "eod",
        "macro_series": "macro",
        "policy_rates": "macro",
        "fund_navs": "macro",
    }.get(capability, "quote")


def _merge_notes(existing: Optional[str],
                 attempts: List[FetchAttempt]) -> Optional[str]:
    trail = " -> ".join(
        f"{a.provider}:{a.outcome}" for a in attempts
    )
    if not trail:
        return existing
    return f"{existing} | provider chain: {trail}" if existing else \
        f"provider chain: {trail}"


registry = ProviderRegistry()
