"""Provider abstraction: failover, health accounting and data provenance."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.cache import get_breaker
from app.core.data_quality import (DataStatus, SourceReliability, Sourced,
                                   data_quality_score, freshness, stale_banner)
from app.providers.base import (MarketDataProvider, ProviderError,
                                ProviderNoData, QuoteData)
from app.providers.registry import ProviderRegistry


class _Healthy(MarketDataProvider):
    name = "healthy"
    display_name = "Healthy provider"
    reliability = SourceReliability.HIGH
    is_delayed = False

    def get_quote(self, symbol: str, **kw) -> Sourced[QuoteData]:
        return Sourced(
            value=QuoteData(symbol=symbol, ltp=100.0),
            provider=self.name, source_name=self.display_name,
            status=DataStatus.LIVE,
            observed_at=datetime.now(tz=timezone.utc),
            reliability=self.reliability,
        )


class _Broken(MarketDataProvider):
    name = "broken"
    display_name = "Broken provider"

    def get_quote(self, symbol: str, **kw) -> Sourced[QuoteData]:
        raise ProviderError("upstream returned HTTP 500")


class _Empty(MarketDataProvider):
    name = "empty"
    display_name = "Provider with no row for this key"

    def get_quote(self, symbol: str, **kw) -> Sourced[QuoteData]:
        raise ProviderNoData("no stored quote for this symbol")


class _Silent(MarketDataProvider):
    """Implements nothing - must be skipped, not counted as a failure."""

    name = "silent"
    display_name = "Silent provider"


@pytest.fixture
def registry() -> ProviderRegistry:
    reg = ProviderRegistry()
    for provider in (_Healthy(), _Broken(), _Empty(), _Silent()):
        reg.register(provider)
    return reg


# --------------------------------------------------------------------------
# Failover
# --------------------------------------------------------------------------


def test_a_broken_provider_falls_through_to_a_healthy_one(registry):
    result = registry.fetch("quote", "TEST", chain=["broken", "healthy"])
    assert result.value.ltp == 100.0
    assert result.provider == "healthy"


def test_the_provider_trail_is_recorded_on_the_envelope(registry):
    result = registry.fetch("quote", "TEST", chain=["broken", "healthy"])
    assert "broken:ERROR" in result.notes
    assert "healthy:OK" in result.notes


def test_an_unsupported_capability_is_skipped_not_failed(registry):
    result = registry.fetch("quote", "TEST", chain=["silent", "healthy"])
    assert result.provider == "healthy"
    assert "silent:UNSUPPORTED" in result.notes


def test_every_provider_failing_returns_unavailable_not_a_guess(registry):
    result = registry.fetch("quote", "TEST", chain=["broken", "empty"])
    assert result.status is DataStatus.UNAVAILABLE
    assert result.value is None
    assert result.is_usable is False


def test_an_empty_chain_is_reported_clearly(registry):
    result = registry.fetch("quote", "TEST", chain=[])
    assert result.status is DataStatus.UNAVAILABLE
    assert "no providers configured" in result.notes.lower() or \
        result.value is None


# --------------------------------------------------------------------------
# Health accounting - the regression this file exists for
# --------------------------------------------------------------------------


def test_no_data_does_not_count_against_provider_health(registry):
    """A provider with no row for one symbol is working perfectly.

    Counting that as a failure would trip its circuit breaker and blind the
    platform to every other symbol it does carry.
    """
    for _ in range(10):
        registry.fetch("quote", "OBSCURE", chain=["empty"])

    health = {row["name"]: row for row in registry.health_report()}["empty"]
    assert health["failure_count"] == 0
    assert health["circuit_state"] == "CLOSED"


def test_no_data_is_labelled_distinctly_in_the_trail(registry):
    result = registry.fetch("quote", "TEST", chain=["empty", "healthy"])
    assert "empty:NO_DATA" in result.notes


def test_a_real_failure_does_count_against_health(registry):
    breaker = get_breaker("broken")
    breaker.record_success()  # reset from any earlier test
    for _ in range(3):
        registry.fetch("quote", "TEST", chain=["broken"])

    health = {row["name"]: row for row in registry.health_report()}["broken"]
    assert health["failure_count"] >= 3
    assert health["last_error"]


def test_health_report_describes_every_registered_provider(registry):
    names = {row["name"] for row in registry.health_report()}
    assert {"healthy", "broken", "empty", "silent"} <= names
    for row in registry.health_report():
        assert "capabilities" in row
        assert "status" in row


# --------------------------------------------------------------------------
# Data quality envelope
# --------------------------------------------------------------------------


def test_a_recent_observation_is_live_during_market_hours():
    # Freshness is market-aware, so assert the classification is at worst
    # DELAYED - never STALE - for an observation seconds old.
    status = freshness(datetime.now(tz=timezone.utc), "quote")
    assert status in (DataStatus.LIVE, DataStatus.DELAYED)


def test_a_week_old_quote_is_stale():
    old = datetime.now(tz=timezone.utc) - timedelta(days=7)
    assert freshness(old, "quote") is DataStatus.STALE


def test_a_missing_observation_is_unavailable_not_stale():
    assert freshness(None, "quote") is DataStatus.UNAVAILABLE


def test_a_delayed_provider_is_never_reported_as_live():
    status = freshness(datetime.now(tz=timezone.utc), "quote",
                       provider_is_delayed=True)
    assert status is not DataStatus.LIVE


def test_data_quality_rewards_fresh_reliable_sources():
    strong = Sourced(value=1, provider="p", source_name="s",
                     status=DataStatus.LIVE,
                     reliability=SourceReliability.HIGH)
    weak = Sourced(value=1, provider="p", source_name="s",
                   status=DataStatus.DEMO,
                   reliability=SourceReliability.UNKNOWN)
    assert data_quality_score([strong]) > data_quality_score([weak])


def test_missing_inputs_drag_the_quality_score_down():
    good = Sourced(value=1, provider="p", source_name="s",
                   status=DataStatus.LIVE, reliability=SourceReliability.HIGH)
    missing = Sourced.unavailable("quote")
    assert data_quality_score([good, missing]) < data_quality_score([good])


def test_stale_inputs_produce_a_banner_naming_them():
    stale = Sourced(value=1, provider="p", source_name="Yahoo Finance",
                    status=DataStatus.STALE)
    banner = stale_banner([stale])
    assert banner is not None
    assert "Yahoo Finance" in banner
    assert "not live data" in banner


def test_no_banner_when_everything_is_fresh():
    fresh = Sourced(value=1, provider="p", source_name="s",
                    status=DataStatus.LIVE)
    assert stale_banner([fresh]) is None


def test_the_envelope_always_serialises_its_provenance():
    payload = Sourced(
        value=1, provider="yahoo", source_name="Yahoo Finance",
        status=DataStatus.DELAYED,
        observed_at=datetime.now(tz=timezone.utc),
        reliability=SourceReliability.MEDIUM,
    ).to_dict()
    for key in ("provider", "source", "status", "observed_at", "retrieved_at",
                "reliability", "is_demo"):
        assert key in payload


# --------------------------------------------------------------------------
# Adapter descriptions
# --------------------------------------------------------------------------


def test_adapters_declare_only_the_capabilities_they_implement():
    assert _Healthy().supports("quote") is True
    assert _Healthy().supports("option_chain") is False
    assert _Silent().supports("quote") is False


def test_nse_adapter_is_off_by_default_and_says_why():
    from app.core.config import settings
    from app.providers.nse import NseProvider

    provider = NseProvider()
    described = provider.describe()
    assert described["terms_url"]
    assert "terms of use" in (described["licence"] or "").lower()
    if not settings.enable_nse_provider:
        with pytest.raises(ProviderError, match="disabled"):
            provider._fetch_json("/api/anything")
