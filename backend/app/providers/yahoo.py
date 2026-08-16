"""Yahoo Finance adapter (via yfinance).

Free, no API key, and explicitly *delayed*. Every envelope this adapter returns
is marked DELAYED unless the observation is genuinely recent - the platform
never dresses a 15-minute-old quote up as live.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.cache import cached_call, rate_limit_ok
from app.core.config import settings
from app.core.data_quality import DataStatus, SourceReliability, Sourced, freshness
from app.providers.base import (Bar, FundamentalsData, InstrumentRecord,
                                MarketDataProvider, ProviderError, QuoteData)

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import guarded so the app boots without yfinance
    import yfinance as yf
except Exception:  # noqa: BLE001
    yf = None


_INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "60m", "4h": "60m",  # 4h is resampled from 60m downstream
    "1d": "1d", "1w": "1wk", "1M": "1mo",
}

# Yahoo caps intraday history; asking for more silently returns nothing.
_MAX_LOOKBACK_DAYS = {
    "1m": 7, "5m": 59, "15m": 59, "30m": 59, "60m": 729,
    "1d": 3650, "1wk": 3650, "1mo": 3650,
}


def _yahoo_ticker(symbol: str, exchange: str = "NSE") -> str:
    if symbol.startswith("^") or "." in symbol:
        return symbol
    return f"{symbol}{'.BO' if exchange == 'BSE' else '.NS'}"


class YahooProvider(MarketDataProvider):
    name = "yahoo"
    display_name = "Yahoo Finance (yfinance)"
    base_url = "https://query1.finance.yahoo.com"
    reliability = SourceReliability.MEDIUM
    is_delayed = True
    requires_auth = False
    terms_url = "https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html"
    licence_note = (
        "Free, delayed data intended for personal, non-commercial use. Review "
        "Yahoo's terms before using it in a commercial deployment."
    )

    def __init__(self) -> None:
        self.rate_limit_per_minute = settings.yahoo_requests_per_minute
        if yf is None:
            logger.warning("yfinance is not installed - YahooProvider disabled")

    # -- helpers -----------------------------------------------------------

    def _guard(self) -> None:
        if yf is None:
            raise ProviderError("yfinance is not installed")
        if not rate_limit_ok("yahoo", self.rate_limit_per_minute):
            raise ProviderError("yahoo rate limit reached - backing off")

    def _envelope(self, value: Any, capability: str,
                  observed_at: Optional[datetime]) -> Sourced[Any]:
        return Sourced(
            value=value,
            provider=self.name,
            source_name=self.display_name,
            status=freshness(observed_at, capability, provider_is_delayed=True),
            observed_at=observed_at,
            reliability=self.reliability,
            source_url=self.base_url,
            license_note=self.licence_note,
            notes="Delayed data. Not an exchange feed.",
        )

    # -- capabilities ------------------------------------------------------

    def get_quote(self, symbol: str, exchange: str = "NSE",
                  **kw: Any) -> Sourced[QuoteData]:
        self._guard()
        ticker_symbol = _yahoo_ticker(symbol, exchange)

        def _fetch() -> Optional[Dict[str, Any]]:
            try:
                tk = yf.Ticker(ticker_symbol)
                info = tk.fast_info
                # fast_info is a lazy mapping; materialise the fields we need.
                data = {
                    "ltp": _f(info.get("last_price")),
                    "open": _f(info.get("open")),
                    "high": _f(info.get("day_high")),
                    "low": _f(info.get("day_low")),
                    "previous_close": _f(info.get("previous_close")),
                    "volume": _i(info.get("last_volume")),
                    "week52_high": _f(info.get("year_high")),
                    "week52_low": _f(info.get("year_low")),
                    "market_cap": _f(info.get("market_cap")),
                }
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(f"yahoo quote failed for {symbol}: {exc}") from exc
            if data["ltp"] is None:
                raise ProviderError(f"yahoo returned no price for {symbol}")
            return data

        cache_key = f"yahoo:quote:{ticker_symbol}"
        raw = cached_call(cache_key, 45, _fetch)
        if not raw:
            raise ProviderError(f"yahoo returned nothing for {symbol}")

        prev = raw.get("previous_close")
        ltp = raw.get("ltp")
        change = (ltp - prev) if (ltp is not None and prev) else None
        change_pct = (change / prev * 100.0) if (change is not None and prev) else None

        observed_at = datetime.now(tz=timezone.utc)
        quote = QuoteData(
            symbol=symbol,
            ltp=ltp,
            open=raw.get("open"),
            high=raw.get("high"),
            low=raw.get("low"),
            previous_close=prev,
            change=_round(change),
            change_pct=_round(change_pct),
            volume=raw.get("volume"),
            week52_high=raw.get("week52_high"),
            week52_low=raw.get("week52_low"),
            market_cap=raw.get("market_cap"),
            observed_at=observed_at,
        )
        return self._envelope(quote, "quote", observed_at)

    def get_history(
        self,
        symbol: str,
        interval: str = "1d",
        start: Optional[date] = None,
        end: Optional[date] = None,
        exchange: str = "NSE",
        **kw: Any,
    ) -> Sourced[List[Bar]]:
        self._guard()
        yf_interval = _INTERVAL_MAP.get(interval, "1d")
        max_days = _MAX_LOOKBACK_DAYS.get(yf_interval, 3650)

        end = end or date.today()
        if start is None:
            start = end - timedelta(days=min(max_days, 400 if yf_interval == "1d" else 55))
        if (end - start).days > max_days:
            start = end - timedelta(days=max_days)

        cache_key = f"yahoo:hist:{symbol}:{exchange}:{yf_interval}:{start}:{end}"

        def _fetch() -> List[Dict[str, Any]]:
            try:
                frame = yf.Ticker(_yahoo_ticker(symbol, exchange)).history(
                    start=start.isoformat(),
                    end=(end + timedelta(days=1)).isoformat(),
                    interval=yf_interval,
                    auto_adjust=True,      # corporate-action adjusted close
                    actions=False,
                    raise_errors=False,
                )
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(f"yahoo history failed for {symbol}: {exc}") from exc
            if frame is None or frame.empty:
                raise ProviderError(f"yahoo returned no bars for {symbol}")
            rows: List[Dict[str, Any]] = []
            for idx, row in frame.iterrows():
                ts = idx.to_pydatetime()
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                rows.append({
                    "t": ts.isoformat(),
                    "o": _f(row.get("Open")), "h": _f(row.get("High")),
                    "l": _f(row.get("Low")), "c": _f(row.get("Close")),
                    "v": _i(row.get("Volume")),
                })
            return rows

        ttl = 60 if yf_interval in ("1m", "5m", "15m") else 900
        raw = cached_call(cache_key, ttl, _fetch) or []
        bars = [
            Bar(
                time=datetime.fromisoformat(r["t"]),
                open=r["o"], high=r["h"], low=r["l"], close=r["c"],
                volume=r["v"],
            )
            for r in raw
            if None not in (r["o"], r["h"], r["l"], r["c"])
        ]
        if interval == "4h":
            bars = _resample_4h(bars)
        observed = bars[-1].time if bars else None
        env = self._envelope(bars, "quote", observed)
        env.notes = (
            "Close is corporate-action adjusted (auto_adjust=True). Intraday "
            "history is capped by Yahoo: 1m ~7 days, 5m-30m ~60 days."
        )
        return env

    def get_fundamentals(self, symbol: str, exchange: str = "NSE",
                         **kw: Any) -> Sourced[FundamentalsData]:
        self._guard()
        cache_key = f"yahoo:fund:{symbol}:{exchange}"

        def _fetch() -> Dict[str, Any]:
            try:
                tk = yf.Ticker(_yahoo_ticker(symbol, exchange))
                info = tk.info or {}
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(f"yahoo fundamentals failed: {exc}") from exc
            return {
                "ratios": {
                    "market_cap": _f(info.get("marketCap")),
                    "enterprise_value": _f(info.get("enterpriseValue")),
                    "pe": _f(info.get("trailingPE")),
                    "forward_pe": _f(info.get("forwardPE")),
                    "pb": _f(info.get("priceToBook")),
                    "ev_ebitda": _f(info.get("enterpriseToEbitda")),
                    "ev_sales": _f(info.get("enterpriseToRevenue")),
                    "peg": _f(info.get("pegRatio")),
                    "eps_ttm": _f(info.get("trailingEps")),
                    "book_value": _f(info.get("bookValue")),
                    "dividend_yield": _pct(info.get("dividendYield")),
                    "roe": _pct(info.get("returnOnEquity")),
                    "roa": _pct(info.get("returnOnAssets")),
                    "debt_to_equity": _f(info.get("debtToEquity")),
                    "current_ratio": _f(info.get("currentRatio")),
                    "ebitda_margin": _pct(info.get("ebitdaMargins")),
                    "net_margin": _pct(info.get("profitMargins")),
                    "beta": _f(info.get("beta")),
                },
                "profile": {
                    "description": info.get("longBusinessSummary"),
                    "industry": info.get("industry"),
                    "sector": info.get("sector"),
                    "website": info.get("website"),
                    "employees": info.get("fullTimeEmployees"),
                    "name": info.get("longName") or info.get("shortName"),
                },
            }

        raw = cached_call(cache_key, 60 * 60 * 6, _fetch) or {}
        observed = datetime.now(tz=timezone.utc)
        data = FundamentalsData(
            symbol=symbol,
            as_of=date.today(),
            ratios=raw.get("ratios", {}),
            profile=raw.get("profile", {}),
        )
        env = self._envelope(data, "fundamentals", observed)
        env.status = DataStatus.UNVERIFIED
        env.notes = (
            "Ratios are as reported by Yahoo Finance and are not cross-checked "
            "against the company's filings. Treat as indicative."
        )
        return env

    def get_indices(self, **kw: Any) -> Sourced[List[QuoteData]]:
        """Yahoo carries the major Indian index tickers."""
        self._guard()
        tickers = {
            "^NSEI": "NIFTY 50",
            "^NSEBANK": "NIFTY BANK",
            "^BSESN": "SENSEX",
            "^CNXIT": "NIFTY IT",
        }
        out: List[QuoteData] = []
        for ticker, label in tickers.items():
            try:
                env = self.get_quote(ticker)
                q = env.value
                q.symbol = label
                out.append(q)
            except ProviderError:
                continue
        if not out:
            raise ProviderError("yahoo returned no index quotes")
        return self._envelope(out, "index", datetime.now(tz=timezone.utc))


# --------------------------------------------------------------------------
# small coercion helpers - Yahoo mixes None, NaN and numpy scalars
# --------------------------------------------------------------------------


def _f(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        out = float(value)
        return None if out != out else out  # NaN check
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> Optional[int]:
    f = _f(value)
    return int(f) if f is not None else None


def _pct(value: Any) -> Optional[float]:
    """Yahoo returns ratios as fractions; the platform stores percentages."""
    f = _f(value)
    return round(f * 100.0, 4) if f is not None else None


def _round(value: Optional[float], places: int = 2) -> Optional[float]:
    return round(value, places) if value is not None else None


def _resample_4h(bars: List[Bar]) -> List[Bar]:
    """Fold 60m bars into 4h buckets. Indian equities trade 09:15-15:30, so a
    session yields two full 4h buckets plus a stub - we keep the stub rather
    than dropping the close."""
    out: List[Bar] = []
    bucket: List[Bar] = []
    for bar in bars:
        bucket.append(bar)
        if len(bucket) == 4:
            out.append(_fold(bucket))
            bucket = []
    if bucket:
        out.append(_fold(bucket))
    return out


def _fold(bucket: List[Bar]) -> Bar:
    return Bar(
        time=bucket[0].time,
        open=bucket[0].open,
        high=max(b.high for b in bucket),
        low=min(b.low for b in bucket),
        close=bucket[-1].close,
        volume=sum(b.volume or 0 for b in bucket) or None,
    )
