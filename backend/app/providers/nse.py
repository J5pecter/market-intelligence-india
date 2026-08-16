"""NSE India public JSON endpoints.

Read this before enabling the adapter
-------------------------------------
NSE publishes JSON endpoints that its own website consumes. They are not a
documented public API, and NSE's terms of use restrict automated access. This
adapter therefore:

* is **disabled by default** (`ENABLE_NSE_PROVIDER=false`);
* sends one honest, identifiable User-Agent and never rotates it;
* obtains a session cookie the ordinary way (a single GET to the site root)
  and does not attempt to defeat any challenge, CAPTCHA or bot check;
* treats HTTP 401/403 as "the operator has declined access" - it opens the
  circuit breaker and stops calling, rather than retrying with new identities;
* honours a conservative request budget (`NSE_REQUESTS_PER_MINUTE`).

If NSE blocks you, the correct response is to obtain a licensed feed or a
broker API - not to work around the block. The platform degrades gracefully:
option-chain panels report UNAVAILABLE with the reason shown in the UI.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from app.core.cache import cached_call, get_breaker, rate_limit_ok
from app.core.config import settings
from app.core.data_quality import SourceReliability, Sourced, freshness
from app.providers.base import (FuturesData, InstrumentRecord,
                                MarketDataProvider, OptionChainData, OptionLeg,
                                ProviderError, QuoteData)

logger = logging.getLogger(__name__)

_ROOT = "https://www.nseindia.com"
_UA = (
    "MarketIntelligenceIndia/1.0 (research platform; "
    "+https://github.com/your-org/market-intelligence-india)"
)

_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNEXT50"}


class AccessDeclined(ProviderError):
    """The provider returned 401/403. We stop; we do not evade."""


class NseProvider(MarketDataProvider):
    name = "nse"
    display_name = "NSE India (public site endpoints)"
    base_url = _ROOT
    reliability = SourceReliability.HIGH
    is_delayed = False
    requires_auth = False
    terms_url = "https://www.nseindia.com/terms-conditions"
    licence_note = (
        "Undocumented public endpoints belonging to NSE. Automated access is "
        "restricted by NSE's terms of use. Disabled unless the operator opts in."
    )

    def __init__(self) -> None:
        self.rate_limit_per_minute = settings.nse_requests_per_minute
        self._session: Optional[requests.Session] = None
        self._lock = threading.Lock()
        self._declined = False

    # -- transport ---------------------------------------------------------

    def _get_session(self) -> requests.Session:
        with self._lock:
            if self._session is None:
                session = requests.Session()
                session.headers.update({
                    "User-Agent": _UA,
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "en-IN,en;q=0.9",
                })
                try:
                    # Ordinary session establishment: one GET to the site root.
                    session.get(_ROOT, timeout=10)
                except requests.RequestException as exc:
                    raise ProviderError(f"could not reach nseindia.com: {exc}") from exc
                self._session = session
            return self._session

    def _fetch_json(self, path: str, ttl: int = 60) -> Dict[str, Any]:
        if not settings.enable_nse_provider:
            raise ProviderError(
                "NSE provider is disabled. Set ENABLE_NSE_PROVIDER=true only "
                "after reviewing NSE's terms of use."
            )
        if self._declined:
            raise AccessDeclined(
                "NSE previously returned 403/401. This adapter does not retry "
                "with a different identity. Configure a licensed feed instead."
            )
        breaker = get_breaker("nse")
        if not breaker.allows():
            raise ProviderError(f"nse circuit breaker is {breaker.state}")
        if not rate_limit_ok("nse", self.rate_limit_per_minute):
            raise ProviderError("nse request budget exhausted for this minute")

        def _do() -> Dict[str, Any]:
            session = self._get_session()
            try:
                response = session.get(f"{_ROOT}{path}", timeout=12)
            except requests.RequestException as exc:
                breaker.record_failure()
                raise ProviderError(f"nse request failed: {exc}") from exc

            if response.status_code in (401, 403):
                self._declined = True
                breaker.record_failure()
                raise AccessDeclined(
                    f"NSE returned HTTP {response.status_code} for {path}. "
                    "Access declined; the adapter has stopped calling."
                )
            if response.status_code == 429:
                breaker.record_failure()
                raise ProviderError("nse rate-limited this client (HTTP 429)")
            if response.status_code >= 400:
                breaker.record_failure()
                raise ProviderError(f"nse returned HTTP {response.status_code}")
            try:
                payload = response.json()
            except ValueError as exc:
                breaker.record_failure()
                raise ProviderError("nse returned a non-JSON body") from exc
            breaker.record_success()
            return payload

        return cached_call(f"nse:{path}", ttl, _do)

    def _envelope(self, value: Any, capability: str,
                  observed_at: Optional[datetime]) -> Sourced[Any]:
        return Sourced(
            value=value,
            provider=self.name,
            source_name=self.display_name,
            status=freshness(observed_at, capability, provider_is_delayed=False),
            observed_at=observed_at,
            reliability=self.reliability,
            source_url=_ROOT,
            license_note=self.licence_note,
        )

    # -- capabilities ------------------------------------------------------

    def get_quote(self, symbol: str, **kw: Any) -> Sourced[QuoteData]:
        payload = self._fetch_json(f"/api/quote-equity?symbol={symbol.upper()}", ttl=45)
        price = payload.get("priceInfo") or {}
        if not price:
            raise ProviderError(f"nse returned no priceInfo for {symbol}")
        intraday = price.get("intraDayHighLow") or {}
        week52 = price.get("weekHighLow") or {}
        meta = payload.get("securityInfo") or {}
        observed = _parse_nse_time(
            (payload.get("metadata") or {}).get("lastUpdateTime")
        ) or datetime.now(tz=timezone.utc)

        quote = QuoteData(
            symbol=symbol.upper(),
            ltp=_f(price.get("lastPrice")),
            open=_f(price.get("open")),
            high=_f(intraday.get("max")),
            low=_f(intraday.get("min")),
            previous_close=_f(price.get("previousClose")),
            change=_f(price.get("change")),
            change_pct=_f(price.get("pChange")),
            vwap=_f(price.get("vwap")),
            week52_high=_f(week52.get("max")),
            week52_low=_f(week52.get("min")),
            observed_at=observed,
        )
        env = self._envelope(quote, "quote", observed)
        env.notes = f"Board lot / surveillance flags: {meta.get('surveillance', {})}" \
            if meta.get("surveillance") else None
        return env

    def get_option_chain(
        self, symbol: str, expiry: Optional[date] = None, **kw: Any
    ) -> Sourced[OptionChainData]:
        upper = symbol.upper()
        endpoint = (
            "option-chain-indices" if upper in _INDEX_SYMBOLS
            else "option-chain-equities"
        )
        payload = self._fetch_json(f"/api/{endpoint}?symbol={upper}", ttl=120)
        records = payload.get("records") or {}
        rows = records.get("data") or []
        if not rows:
            raise ProviderError(f"nse returned an empty chain for {symbol}")

        expiries = sorted({
            d for d in (_parse_nse_date(e) for e in records.get("expiryDates", []))
            if d
        })
        chosen = expiry or (expiries[0] if expiries else None)
        if chosen is None:
            raise ProviderError("nse chain has no parseable expiry dates")

        underlying = _f(records.get("underlyingValue"))
        observed = _parse_nse_time(records.get("timestamp")) or datetime.now(
            tz=timezone.utc
        )

        legs: List[OptionLeg] = []
        for row in rows:
            row_expiry = _parse_nse_date(row.get("expiryDate"))
            if row_expiry != chosen:
                continue
            strike = _f(row.get("strikePrice"))
            if strike is None:
                continue
            for side in ("CE", "PE"):
                leg = row.get(side)
                if not leg:
                    continue
                legs.append(OptionLeg(
                    strike=strike,
                    option_type=side,
                    ltp=_f(leg.get("lastPrice")),
                    change=_f(leg.get("change")),
                    change_pct=_f(leg.get("pChange")),
                    open_interest=_i(leg.get("openInterest")),
                    oi_change=_i(leg.get("changeinOpenInterest")),
                    volume=_i(leg.get("totalTradedVolume")),
                    implied_volatility=_f(leg.get("impliedVolatility")),
                    bid=_f(leg.get("bidprice")),
                    ask=_f(leg.get("askPrice")),
                    bid_qty=_i(leg.get("bidQty")),
                    ask_qty=_i(leg.get("askQty")),
                ))

        chain = OptionChainData(
            underlying_symbol=upper,
            expiry=chosen,
            captured_at=observed,
            underlying_value=underlying,
            legs=sorted(legs, key=lambda leg: (leg.strike, leg.option_type)),
            available_expiries=expiries,
        )
        return self._envelope(chain, "option_chain", observed)

    def get_indices(self, **kw: Any) -> Sourced[List[QuoteData]]:
        payload = self._fetch_json("/api/allIndices", ttl=60)
        rows = payload.get("data") or []
        if not rows:
            raise ProviderError("nse returned no indices")
        observed = _parse_nse_time(payload.get("timestamp")) or datetime.now(
            tz=timezone.utc
        )
        quotes = [
            QuoteData(
                symbol=(row.get("index") or "").strip(),
                ltp=_f(row.get("last")),
                open=_f(row.get("open")),
                high=_f(row.get("high")),
                low=_f(row.get("low")),
                previous_close=_f(row.get("previousClose")),
                change=_f(row.get("variation")),
                change_pct=_f(row.get("percentChange")),
                week52_high=_f(row.get("yearHigh")),
                week52_low=_f(row.get("yearLow")),
                observed_at=observed,
            )
            for row in rows
            if row.get("index")
        ]
        return self._envelope(quotes, "index", observed)

    def get_instruments(self, **kw: Any) -> Sourced[List[InstrumentRecord]]:
        """Equity master from the NIFTY 500 constituent list plus the F&O
        eligible list. Nothing is hard-coded; if the endpoints are unavailable
        the registry falls back to the next provider."""
        records: Dict[str, InstrumentRecord] = {}
        observed = datetime.now(tz=timezone.utc)

        for index_name in ("NIFTY%20500", "SECURITIES%20IN%20F%26O"):
            try:
                payload = self._fetch_json(
                    f"/api/equity-stockIndices?index={index_name}", ttl=60 * 60 * 6
                )
            except ProviderError:
                continue
            for row in payload.get("data") or []:
                symbol = (row.get("symbol") or "").strip().upper()
                if not symbol or symbol.startswith("NIFTY"):
                    continue
                existing = records.get(symbol)
                record = existing or InstrumentRecord(
                    symbol=symbol,
                    name=(row.get("meta") or {}).get("companyName") or symbol,
                    exchange_code="NSE",
                    segment="EQUITY",
                    isin=(row.get("meta") or {}).get("isin"),
                    industry=row.get("industry") or (row.get("meta") or {}).get("industry"),
                    series=((row.get("meta") or {}).get("series") or ["EQ"])[0]
                    if isinstance((row.get("meta") or {}).get("series"), list)
                    else (row.get("meta") or {}).get("series"),
                )
                if "F%26O" in index_name:
                    record.is_fno_eligible = True
                records[symbol] = record

        if not records:
            raise ProviderError("nse returned no instrument records")
        return self._envelope(list(records.values()), "quote", observed)

    def get_corporate_actions(self, symbol: str, **kw: Any) -> Sourced[List[Dict[str, Any]]]:
        payload = self._fetch_json(
            f"/api/corporates-corporateActions?index=equities&symbol={symbol.upper()}",
            ttl=60 * 60 * 6,
        )
        rows = payload if isinstance(payload, list) else payload.get("data", [])
        observed = datetime.now(tz=timezone.utc)
        actions = [
            {
                "symbol": symbol.upper(),
                "description": row.get("subject") or row.get("purpose") or "",
                "ex_date": _parse_nse_date(row.get("exDate")),
                "record_date": _parse_nse_date(row.get("recDate")),
                "announcement_date": _parse_nse_date(row.get("bcStartDate")),
            }
            for row in rows or []
        ]
        return self._envelope(actions, "corporate_actions", observed)


# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------


def _f(value: Any) -> Optional[float]:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> Optional[int]:
    f = _f(value)
    return int(f) if f is not None else None


def _parse_nse_date(value: Any) -> Optional[date]:
    """NSE mixes '28-Aug-2026' and '28-Aug-26'."""
    if not value or not isinstance(value, str):
        return None
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_nse_time(value: Any) -> Optional[datetime]:
    """'14-Aug-2026 15:30:00' in IST."""
    if not value or not isinstance(value, str):
        return None
    from app.core.market_calendar import IST

    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%m-%Y %H:%M:%S"):
        try:
            naive = datetime.strptime(value.strip(), fmt)
            return naive.replace(tzinfo=IST).astimezone(timezone.utc)
        except ValueError:
            continue
    return None
