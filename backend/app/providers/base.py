"""Provider abstraction.

Business logic never imports a concrete provider. It asks the registry for a
capability and gets back a `Sourced[...]` envelope, so swapping Yahoo for a
licensed feed is a configuration change, not a code change.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.core.data_quality import SourceReliability, Sourced


# --------------------------------------------------------------------------
# Transport-neutral payloads
# --------------------------------------------------------------------------


@dataclass
class QuoteData:
    symbol: str
    ltp: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    previous_close: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    volume: Optional[int] = None
    average_volume_20d: Optional[int] = None
    vwap: Optional[float] = None
    turnover: Optional[float] = None
    week52_high: Optional[float] = None
    week52_low: Optional[float] = None
    market_cap: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_qty: Optional[int] = None
    ask_qty: Optional[int] = None
    open_interest: Optional[int] = None
    oi_change: Optional[int] = None
    observed_at: Optional[datetime] = None
    currency: str = "INR"


@dataclass
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[int] = None
    raw_close: Optional[float] = None


@dataclass
class OptionLeg:
    strike: float
    option_type: str  # CE | PE
    ltp: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    open_interest: Optional[int] = None
    oi_change: Optional[int] = None
    volume: Optional[int] = None
    implied_volatility: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_qty: Optional[int] = None
    ask_qty: Optional[int] = None


@dataclass
class OptionChainData:
    underlying_symbol: str
    expiry: date
    captured_at: datetime
    underlying_value: Optional[float]
    legs: List[OptionLeg] = field(default_factory=list)
    available_expiries: List[date] = field(default_factory=list)


@dataclass
class FuturesData:
    underlying_symbol: str
    expiry: date
    captured_at: datetime
    spot: Optional[float] = None
    ltp: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    open_interest: Optional[int] = None
    oi_change: Optional[int] = None
    volume: Optional[int] = None
    lot_size: Optional[int] = None


@dataclass
class NewsItem:
    headline: str
    url: str
    publisher: str
    published_at: Optional[datetime]
    summary: Optional[str] = None
    primary_symbol: Optional[str] = None
    related_symbols: List[str] = field(default_factory=list)


@dataclass
class InstrumentRecord:
    symbol: str
    name: str
    exchange_code: str
    segment: str = "EQUITY"
    isin: Optional[str] = None
    series: Optional[str] = None
    industry: Optional[str] = None
    sector: Optional[str] = None
    lot_size: Optional[int] = None
    expiry: Optional[date] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None
    underlying_symbol: Optional[str] = None
    is_fno_eligible: bool = False


@dataclass
class FundamentalsData:
    symbol: str
    as_of: Optional[date] = None
    ratios: Dict[str, Optional[float]] = field(default_factory=dict)
    statements: List[Dict[str, Any]] = field(default_factory=list)
    profile: Dict[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    """The provider itself failed: transport, auth, rate limit, bad payload.

    These count against the provider's health and trip its circuit breaker.
    """


class ProviderUnsupported(ProviderError):
    """This adapter does not implement the requested capability."""


class ProviderNoData(ProviderError):
    """The provider is healthy but holds nothing for this particular key.

    Deliberately distinct from ProviderError: a stored-data adapter that has no
    row for one obscure symbol is working perfectly. Counting that as a failure
    would trip its circuit breaker and blind the platform to every *other*
    symbol it does have - which is exactly the kind of silent degradation this
    platform is supposed to make impossible.
    """


# --------------------------------------------------------------------------
# The interface
# --------------------------------------------------------------------------


class MarketDataProvider(abc.ABC):
    """Adapters implement whichever methods they can and raise
    ProviderUnsupported for the rest. The registry skips unsupported ones
    without counting them as failures."""

    name: str = "abstract"
    display_name: str = "Abstract provider"
    base_url: Optional[str] = None
    reliability: SourceReliability = SourceReliability.UNKNOWN
    is_delayed: bool = True
    requires_auth: bool = False
    rate_limit_per_minute: int = 60
    terms_url: Optional[str] = None
    licence_note: Optional[str] = None

    # -- capabilities ------------------------------------------------------

    def get_quote(self, symbol: str, **kw: Any) -> Sourced[QuoteData]:
        raise ProviderUnsupported(f"{self.name} has no get_quote")

    def get_quotes(self, symbols: List[str], **kw: Any) -> Dict[str, Sourced[QuoteData]]:
        """Default: loop. Adapters with a batch endpoint should override."""
        out: Dict[str, Sourced[QuoteData]] = {}
        for sym in symbols:
            try:
                out[sym] = self.get_quote(sym, **kw)
            except ProviderError:
                continue
        return out

    def get_history(
        self,
        symbol: str,
        interval: str = "1d",
        start: Optional[date] = None,
        end: Optional[date] = None,
        **kw: Any,
    ) -> Sourced[List[Bar]]:
        raise ProviderUnsupported(f"{self.name} has no get_history")

    def get_instruments(self, **kw: Any) -> Sourced[List[InstrumentRecord]]:
        raise ProviderUnsupported(f"{self.name} has no get_instruments")

    def get_option_chain(
        self, symbol: str, expiry: Optional[date] = None, **kw: Any
    ) -> Sourced[OptionChainData]:
        raise ProviderUnsupported(f"{self.name} has no get_option_chain")

    def get_futures_chain(
        self, symbol: str, **kw: Any
    ) -> Sourced[List[FuturesData]]:
        raise ProviderUnsupported(f"{self.name} has no get_futures_chain")

    def get_indices(self, **kw: Any) -> Sourced[List[QuoteData]]:
        raise ProviderUnsupported(f"{self.name} has no get_indices")

    def get_corporate_actions(self, symbol: str, **kw: Any) -> Sourced[List[Dict[str, Any]]]:
        raise ProviderUnsupported(f"{self.name} has no get_corporate_actions")

    def get_fundamentals(self, symbol: str, **kw: Any) -> Sourced[FundamentalsData]:
        raise ProviderUnsupported(f"{self.name} has no get_fundamentals")

    def get_news(
        self, symbol: Optional[str] = None, query: Optional[str] = None,
        limit: int = 25, **kw: Any
    ) -> Sourced[List[NewsItem]]:
        raise ProviderUnsupported(f"{self.name} has no get_news")

    def get_ipos(self, **kw: Any) -> Sourced[List[Dict[str, Any]]]:
        raise ProviderUnsupported(f"{self.name} has no get_ipos")

    # -- introspection -----------------------------------------------------

    def supports(self, capability: str) -> bool:
        method = getattr(self, f"get_{capability}", None)
        if method is None:
            return False
        # An adapter "supports" a capability when it overrides the base method.
        base_method = getattr(MarketDataProvider, f"get_{capability}", None)
        return getattr(method, "__func__", None) is not base_method

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "base_url": self.base_url,
            "reliability": self.reliability.value,
            "is_delayed": self.is_delayed,
            "requires_auth": self.requires_auth,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "terms_url": self.terms_url,
            "licence": self.licence_note,
            "capabilities": [
                cap
                for cap in (
                    "quote", "history", "instruments", "option_chain",
                    "futures_chain", "indices", "corporate_actions",
                    "fundamentals", "news", "ipos",
                )
                if self.supports(cap)
            ],
        }
