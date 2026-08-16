"""Concrete broker adapters: Angel One, Dhan, Zerodha Kite, Upstox.

Each is written against the broker's published REST documentation and uses the
operator's own account credentials. None of them ships with a key, none is
enabled unless its credentials are present, and none is a workaround for an
access control - a broker API is the licensed way to obtain real-time Indian
market data, which is precisely why these exist.

Endpoint references
-------------------
* Angel One SmartAPI  https://smartapi.angelone.in/docs
* Dhan API v2         https://dhanhq.co/docs/v2/
* Kite Connect v3     https://kite.trade/docs/connect/v3/
* Upstox API v2       https://upstox.com/developer/api-documentation/

Broker APIs change. Every adapter therefore fails loudly with the HTTP status
and the broker's own message rather than returning a half-parsed payload: a
wrong number is far more damaging to research than a missing one.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from app.core.cache import cached_call
from app.core.config import settings
from app.core.data_quality import SourceReliability, Sourced
from app.core.market_calendar import IST
from app.providers.base import (Bar, OptionChainData, OptionLeg, ProviderError,
                                ProviderNoData, ProviderUnsupported, QuoteData)
from app.providers.broker_base import (BrokerAuthError, BrokerProvider,
                                       parse_dt, to_float, to_int, totp_now)

logger = logging.getLogger(__name__)

_DUMP_TIMEOUT = 60      # instrument masters are several MB


def _download(url: str, *, gz: bool = False) -> bytes:
    try:
        resp = requests.get(
            url, timeout=_DUMP_TIMEOUT,
            headers={"User-Agent": "MarketIntelligenceIndia/1.0 (personal research desk)"},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ProviderError(f"instrument master download failed: {exc}") from exc
    return gzip.decompress(resp.content) if gz else resp.content


def _csv_rows(payload: bytes) -> List[Dict[str, str]]:
    text = payload.decode("utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


# ==========================================================================
# Angel One SmartAPI
# ==========================================================================


class AngelOneProvider(BrokerProvider):
    name = "angelone"
    broker_key = "angelone"
    display_name = "Angel One SmartAPI"
    base_url = "https://apiconnect.angelone.in"
    terms_url = "https://smartapi.angelone.in/terms"
    licence_note = (
        "Real-time data via your own Angel One account. Free with a demat "
        "account; redistribution is not permitted under the broker's terms."
    )
    credential_fields = ("api_key", "client_code", "password", "totp_secret")

    _SCRIP_MASTER = (
        "https://margincalculator.angelbroking.com/OpenAPI_File/files/"
        "OpenAPIScripMaster.json"
    )
    _INTERVALS = {
        "1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE",
        "30m": "THIRTY_MINUTE", "1h": "ONE_HOUR", "1d": "ONE_DAY",
    }
    # Angel caps each candle request; asking for more returns an error.
    _MAX_DAYS = {
        "ONE_MINUTE": 30, "FIVE_MINUTE": 100, "FIFTEEN_MINUTE": 200,
        "THIRTY_MINUTE": 200, "ONE_HOUR": 400, "ONE_DAY": 2000,
    }

    def _login(self) -> Dict[str, Any]:
        creds = self.credentials
        body = {
            "clientcode": creds["client_code"],
            "password": creds["password"],          # MPIN
            "totp": totp_now(creds["totp_secret"]),
        }
        headers = {
            "Content-Type": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress": "00:00:00:00:00:00",
            "X-PrivateKey": creds["api_key"],
        }
        try:
            resp = self._http.post(
                f"{self.base_url}/rest/auth/angelbroking/user/v1/loginByPassword",
                json=body, headers=headers, timeout=15,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"angelone login transport error: {exc}") from exc

        payload = resp.json() if resp.content else {}
        if not payload.get("status"):
            raise BrokerAuthError(
                f"angelone login refused: {payload.get('message') or resp.status_code}"
            )
        data = payload.get("data") or {}
        if not data.get("jwtToken"):
            raise BrokerAuthError("angelone login returned no jwtToken")
        return {
            "jwt": data["jwtToken"],
            "refresh": data.get("refreshToken"),
            "feed_token": data.get("feedToken"),
        }

    def _auth_headers(self) -> Dict[str, str]:
        sess = self.session()
        return {
            "Authorization": f"Bearer {sess['jwt']}",
            "Content-Type": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress": "00:00:00:00:00:00",
            "X-PrivateKey": self.credentials["api_key"],
        }

    def _instrument_dump(self) -> List[Dict[str, Any]]:
        import json
        rows = json.loads(_download(self._SCRIP_MASTER).decode("utf-8"))
        out: List[Dict[str, Any]] = []
        for r in rows:
            seg = (r.get("exch_seg") or "").upper()
            if seg not in ("NSE", "BSE", "NFO", "BFO"):
                continue
            out.append({
                "token": r.get("token"),
                "symbol": (r.get("symbol") or "").upper(),
                "name": r.get("name"),
                "exchange": seg,
                "expiry": r.get("expiry"),
                "strike": to_float(r.get("strike")),
                "lot_size": to_int(r.get("lotsize")),
                "instrument_type": r.get("instrumenttype"),
            })
        return out

    def get_quote(self, symbol: str, exchange: str = "NSE",
                  **kw: Any) -> Sourced[QuoteData]:
        self._guard()
        # Angel's equity symbols carry a series suffix in the master.
        token = self._resolve_equity(symbol, exchange)
        payload = self._request(
            "POST",
            f"{self.base_url}/rest/secure/angelbroking/market/v1/quote/",
            json={"mode": "FULL", "exchangeTokens": {exchange.upper(): [token]}},
        )
        rows = ((payload.get("data") or {}).get("fetched")) or []
        if not rows:
            raise ProviderNoData(f"angelone returned no quote for {symbol}")
        r = rows[0]
        observed = parse_dt(r.get("exchFeedTime")) or datetime.now(tz=timezone.utc)
        depth = r.get("depth") or {}
        buy = (depth.get("buy") or [{}])[0]
        sell = (depth.get("sell") or [{}])[0]
        quote = QuoteData(
            symbol=symbol,
            ltp=to_float(r.get("ltp")),
            open=to_float(r.get("open")),
            high=to_float(r.get("high")),
            low=to_float(r.get("low")),
            previous_close=to_float(r.get("close")),
            change=to_float(r.get("netChange")),
            change_pct=to_float(r.get("percentChange")),
            volume=to_int(r.get("tradeVolume")),
            week52_high=to_float(r.get("52WeekHigh")),
            week52_low=to_float(r.get("52WeekLow")),
            bid=to_float(buy.get("price")),
            ask=to_float(sell.get("price")),
            bid_qty=to_int(buy.get("quantity")),
            ask_qty=to_int(sell.get("quantity")),
            observed_at=observed,
        )
        return self._envelope(quote, "quote", observed)

    def get_history(self, symbol: str, interval: str = "1d",
                    start: Optional[date] = None, end: Optional[date] = None,
                    exchange: str = "NSE", **kw: Any) -> Sourced[List[Bar]]:
        self._guard()
        ang = self._INTERVALS.get(interval)
        if ang is None:
            raise ProviderUnsupported(f"angelone has no {interval} candles")
        token = self._resolve_equity(symbol, exchange)
        end = end or date.today()
        start = start or (end - timedelta(days=min(self._MAX_DAYS[ang], 400)))
        if (end - start).days > self._MAX_DAYS[ang]:
            start = end - timedelta(days=self._MAX_DAYS[ang])

        payload = self._request(
            "POST",
            f"{self.base_url}/rest/secure/angelbroking/historical/v1/getCandleData",
            json={
                "exchange": exchange.upper(),
                "symboltoken": token,
                "interval": ang,
                "fromdate": f"{start.isoformat()} 09:15",
                "todate": f"{end.isoformat()} 15:30",
            },
        )
        rows = payload.get("data") or []
        bars: List[Bar] = []
        for row in rows:
            # [timestamp, open, high, low, close, volume]
            ts = parse_dt(row[0])
            if ts is None:
                continue
            bars.append(Bar(time=ts, open=to_float(row[1]), high=to_float(row[2]),
                            low=to_float(row[3]), close=to_float(row[4]),
                            volume=to_int(row[5])))
        if not bars:
            raise ProviderNoData(f"angelone returned no candles for {symbol}")
        env = self._envelope(bars, "quote", bars[-1].time)
        env.notes = (
            "Exchange candles via Angel One. Close is NOT corporate-action "
            "adjusted - compare against an adjusted series before computing "
            "long-horizon returns."
        )
        return env

    def _resolve_equity(self, symbol: str, exchange: str) -> str:
        """Angel lists cash equities as `SYMBOL-EQ`; indices carry no suffix."""
        index = self.instruments_index()
        for candidate in (f"{symbol.upper()}-EQ", symbol.upper()):
            row = index.get(f"{exchange.upper()}:{candidate}")
            if row is not None:
                return str(row["token"])
        raise ProviderNoData(f"angelone has no instrument for {exchange}:{symbol}")


# ==========================================================================
# Dhan
# ==========================================================================


class DhanProvider(BrokerProvider):
    name = "dhan"
    broker_key = "dhan"
    display_name = "Dhan API v2"
    base_url = "https://api.dhan.co/v2"
    terms_url = "https://dhanhq.co/docs/v2/"
    licence_note = (
        "Real-time data via your own Dhan account. Free with a demat account; "
        "redistribution is not permitted under the broker's terms."
    )
    credential_fields = ("client_id", "access_token")

    _SCRIP_MASTER = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
    _SEGMENTS = {"NSE": "NSE_EQ", "BSE": "BSE_EQ", "NFO": "NSE_FNO", "BFO": "BSE_FNO"}
    _INTRADAY = {"1m": "1", "5m": "5", "15m": "15", "25m": "25", "1h": "60"}

    def _login(self) -> Dict[str, Any]:
        # Dhan issues a long-lived token from its web console; there is no
        # programmatic login step. Validate it once so a bad token surfaces
        # here rather than as a confusing 401 on the first quote.
        creds = self.credentials
        try:
            resp = self._http.get(
                f"{self.base_url}/profile",
                headers={"access-token": creds["access_token"],
                         "client-id": creds["client_id"]},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"dhan transport error: {exc}") from exc
        if resp.status_code in (401, 403):
            raise BrokerAuthError(
                "dhan rejected the access token - regenerate it in the Dhan console"
            )
        return {"access_token": creds["access_token"], "client_id": creds["client_id"]}

    def _auth_headers(self) -> Dict[str, str]:
        sess = self.session()
        return {
            "access-token": sess["access_token"],
            "client-id": sess["client_id"],
            "Content-Type": "application/json",
        }

    def _instrument_dump(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for r in _csv_rows(_download(self._SCRIP_MASTER)):
            seg = (r.get("SEM_EXM_EXCH_ID") or "").upper()
            if seg not in ("NSE", "BSE"):
                continue
            out.append({
                "token": r.get("SEM_SMST_SECURITY_ID"),
                "symbol": (r.get("SEM_TRADING_SYMBOL") or "").upper(),
                "name": r.get("SM_SYMBOL_NAME"),
                "exchange": seg,
                "segment": r.get("SEM_SEGMENT"),
                "expiry": r.get("SEM_EXPIRY_DATE"),
                "strike": to_float(r.get("SEM_STRIKE_PRICE")),
                "option_type": r.get("SEM_OPTION_TYPE"),
                "lot_size": to_int(r.get("SEM_LOT_UNITS")),
                "instrument_type": r.get("SEM_INSTRUMENT_NAME"),
            })
        return out

    def get_quote(self, symbol: str, exchange: str = "NSE",
                  **kw: Any) -> Sourced[QuoteData]:
        self._guard()
        token = self.resolve_token(symbol, exchange)
        seg = self._SEGMENTS.get(exchange.upper(), "NSE_EQ")
        payload = self._request("POST", f"{self.base_url}/marketfeed/quote",
                                json={seg: [int(token)]})
        block = ((payload.get("data") or {}).get(seg) or {}).get(str(token))
        if not block:
            raise ProviderNoData(f"dhan returned no quote for {symbol}")
        ohlc = block.get("ohlc") or {}
        observed = parse_dt(block.get("last_trade_time")) or datetime.now(tz=timezone.utc)
        ltp = to_float(block.get("last_price"))
        prev = to_float(ohlc.get("close"))
        change = (ltp - prev) if (ltp is not None and prev) else None
        quote = QuoteData(
            symbol=symbol,
            ltp=ltp,
            open=to_float(ohlc.get("open")),
            high=to_float(ohlc.get("high")),
            low=to_float(ohlc.get("low")),
            previous_close=prev,
            change=round(change, 2) if change is not None else None,
            change_pct=round(change / prev * 100, 2) if (change is not None and prev) else None,
            volume=to_int(block.get("volume")),
            average_volume_20d=None,
            observed_at=observed,
            open_interest=to_int(block.get("oi")),
        )
        return self._envelope(quote, "quote", observed)

    def get_history(self, symbol: str, interval: str = "1d",
                    start: Optional[date] = None, end: Optional[date] = None,
                    exchange: str = "NSE", **kw: Any) -> Sourced[List[Bar]]:
        self._guard()
        token = self.resolve_token(symbol, exchange)
        seg = self._SEGMENTS.get(exchange.upper(), "NSE_EQ")
        end = end or date.today()
        intraday = interval in self._INTRADAY

        if intraday:
            start = start or (end - timedelta(days=5))
            url, body = f"{self.base_url}/charts/intraday", {
                "securityId": str(token), "exchangeSegment": seg,
                "instrument": "EQUITY", "interval": self._INTRADAY[interval],
                "fromDate": start.isoformat(), "toDate": end.isoformat(),
            }
        else:
            start = start or (end - timedelta(days=400))
            url, body = f"{self.base_url}/charts/historical", {
                "securityId": str(token), "exchangeSegment": seg,
                "instrument": "EQUITY", "expiryCode": 0,
                "fromDate": start.isoformat(), "toDate": end.isoformat(),
            }

        payload = self._request("POST", url, json=body)
        # Dhan returns parallel arrays rather than rows.
        opens = payload.get("open") or []
        if not opens:
            raise ProviderNoData(f"dhan returned no candles for {symbol}")
        highs, lows = payload.get("high") or [], payload.get("low") or []
        closes, vols = payload.get("close") or [], payload.get("volume") or []
        stamps = payload.get("timestamp") or []
        bars: List[Bar] = []
        for i in range(min(len(opens), len(highs), len(lows), len(closes), len(stamps))):
            ts = parse_dt(stamps[i])
            if ts is None:
                continue
            bars.append(Bar(time=ts, open=to_float(opens[i]), high=to_float(highs[i]),
                            low=to_float(lows[i]), close=to_float(closes[i]),
                            volume=to_int(vols[i]) if i < len(vols) else None))
        if not bars:
            raise ProviderNoData(f"dhan returned no usable candles for {symbol}")
        return self._envelope(bars, "quote", bars[-1].time)

    def get_option_chain(self, symbol: str, expiry: Optional[date] = None,
                         **kw: Any) -> Sourced[OptionChainData]:
        self._guard()
        token = self.resolve_token(symbol, kw.get("exchange", "NSE"))
        seg = "IDX_I" if symbol.upper() in (
            "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY") else "NSE_FNO"

        expiries_payload = self._request(
            "POST", f"{self.base_url}/optionchain/expirylist",
            json={"UnderlyingScrip": int(token), "UnderlyingSeg": seg},
        )
        available = [d for d in (
            _as_date(x) for x in (expiries_payload.get("data") or [])
        ) if d]
        if not available:
            raise ProviderNoData(f"dhan lists no expiries for {symbol}")
        target = expiry or available[0]

        payload = self._request(
            "POST", f"{self.base_url}/optionchain",
            json={"UnderlyingScrip": int(token), "UnderlyingSeg": seg,
                  "Expiry": target.isoformat()},
        )
        data = payload.get("data") or {}
        legs: List[OptionLeg] = []
        for strike_text, block in (data.get("oc") or {}).items():
            strike = to_float(strike_text)
            if strike is None:
                continue
            for side, key in (("CE", "ce"), ("PE", "pe")):
                raw = block.get(key) or {}
                if not raw:
                    continue
                legs.append(OptionLeg(
                    strike=strike, option_type=side,
                    ltp=to_float(raw.get("last_price")),
                    open_interest=to_int(raw.get("oi")),
                    oi_change=(to_int(raw.get("oi")) or 0) - (to_int(raw.get("previous_oi")) or 0)
                    if raw.get("previous_oi") is not None else None,
                    volume=to_int(raw.get("volume")),
                    implied_volatility=to_float(raw.get("implied_volatility")),
                    bid=to_float(raw.get("top_bid_price")),
                    ask=to_float(raw.get("top_ask_price")),
                    bid_qty=to_int(raw.get("top_bid_quantity")),
                    ask_qty=to_int(raw.get("top_ask_quantity")),
                ))
        if not legs:
            raise ProviderNoData(f"dhan returned an empty chain for {symbol}")
        observed = datetime.now(tz=timezone.utc)
        chain = OptionChainData(
            underlying_symbol=symbol.upper(), expiry=target, captured_at=observed,
            underlying_value=to_float(data.get("last_price")),
            legs=sorted(legs, key=lambda l: (l.strike, l.option_type)),
            available_expiries=available,
        )
        return self._envelope(chain, "option_chain", observed)


# ==========================================================================
# Zerodha Kite Connect
# ==========================================================================


class KiteProvider(BrokerProvider):
    name = "kite"
    broker_key = "kite"
    display_name = "Zerodha Kite Connect"
    base_url = "https://api.kite.trade"
    terms_url = "https://kite.trade/docs/connect/v3/"
    licence_note = (
        "Real-time data via your own Kite Connect subscription. Paid API; "
        "redistribution is not permitted under Zerodha's terms."
    )
    credential_fields = ("api_key", "access_token")

    _INTERVALS = {
        "1m": "minute", "3m": "3minute", "5m": "5minute", "15m": "15minute",
        "30m": "30minute", "1h": "60minute", "1d": "day",
    }
    _MAX_DAYS = {
        "minute": 60, "3minute": 100, "5minute": 100, "15minute": 200,
        "30minute": 200, "60minute": 400, "day": 2000,
    }

    def _login(self) -> Dict[str, Any]:
        # Kite's access token comes from the interactive request-token flow and
        # expires daily. There is nothing to POST here; validate and move on.
        creds = self.credentials
        return {"api_key": creds["api_key"], "access_token": creds["access_token"]}

    def _auth_headers(self) -> Dict[str, str]:
        sess = self.session()
        return {
            "X-Kite-Version": "3",
            "Authorization": f"token {sess['api_key']}:{sess['access_token']}",
        }

    def _instrument_dump(self) -> List[Dict[str, Any]]:
        # The instruments dump is CSV and unauthenticated.
        out: List[Dict[str, Any]] = []
        for r in _csv_rows(_download(f"{self.base_url}/instruments")):
            exch = (r.get("exchange") or "").upper()
            if exch not in ("NSE", "BSE", "NFO", "BFO"):
                continue
            out.append({
                "token": r.get("instrument_token"),
                "symbol": (r.get("tradingsymbol") or "").upper(),
                "name": r.get("name"),
                "exchange": exch,
                "expiry": r.get("expiry"),
                "strike": to_float(r.get("strike")),
                "option_type": r.get("instrument_type"),
                "lot_size": to_int(r.get("lot_size")),
            })
        return out

    def get_quote(self, symbol: str, exchange: str = "NSE",
                  **kw: Any) -> Sourced[QuoteData]:
        self._guard()
        key = f"{exchange.upper()}:{symbol.upper()}"
        payload = self._request("GET", f"{self.base_url}/quote", params={"i": key})
        block = (payload.get("data") or {}).get(key)
        if not block:
            raise ProviderNoData(f"kite returned no quote for {key}")
        ohlc = block.get("ohlc") or {}
        depth = block.get("depth") or {}
        buy = (depth.get("buy") or [{}])[0]
        sell = (depth.get("sell") or [{}])[0]
        observed = parse_dt(block.get("last_trade_time")) or datetime.now(tz=timezone.utc)
        quote = QuoteData(
            symbol=symbol,
            ltp=to_float(block.get("last_price")),
            open=to_float(ohlc.get("open")),
            high=to_float(ohlc.get("high")),
            low=to_float(ohlc.get("low")),
            previous_close=to_float(ohlc.get("close")),
            change=to_float(block.get("net_change")),
            volume=to_int(block.get("volume")),
            average_volume_20d=None,
            bid=to_float(buy.get("price")), ask=to_float(sell.get("price")),
            bid_qty=to_int(buy.get("quantity")), ask_qty=to_int(sell.get("quantity")),
            open_interest=to_int(block.get("oi")),
            observed_at=observed,
        )
        prev = quote.previous_close
        if quote.change is not None and prev:
            quote.change_pct = round(quote.change / prev * 100, 2)
        return self._envelope(quote, "quote", observed)

    def get_history(self, symbol: str, interval: str = "1d",
                    start: Optional[date] = None, end: Optional[date] = None,
                    exchange: str = "NSE", **kw: Any) -> Sourced[List[Bar]]:
        self._guard()
        kite_interval = self._INTERVALS.get(interval)
        if kite_interval is None:
            raise ProviderUnsupported(f"kite has no {interval} candles")
        token = self.resolve_token(symbol, exchange)
        end = end or date.today()
        cap = self._MAX_DAYS[kite_interval]
        start = start or (end - timedelta(days=min(cap, 400)))
        if (end - start).days > cap:
            start = end - timedelta(days=cap)

        payload = self._request(
            "GET",
            f"{self.base_url}/instruments/historical/{token}/{kite_interval}",
            params={"from": start.isoformat(), "to": end.isoformat()},
        )
        rows = (payload.get("data") or {}).get("candles") or []
        bars: List[Bar] = []
        for row in rows:
            ts = parse_dt(row[0])
            if ts is None:
                continue
            bars.append(Bar(time=ts, open=to_float(row[1]), high=to_float(row[2]),
                            low=to_float(row[3]), close=to_float(row[4]),
                            volume=to_int(row[5]) if len(row) > 5 else None))
        if not bars:
            raise ProviderNoData(f"kite returned no candles for {symbol}")
        return self._envelope(bars, "quote", bars[-1].time)

    def get_option_chain(self, symbol: str, expiry: Optional[date] = None,
                         **kw: Any) -> Sourced[OptionChainData]:
        """Kite has no chain endpoint; assemble one from instruments + quotes."""
        self._guard()
        rows = [
            r for r in self.instruments_index().values()
            if r["exchange"] in ("NFO", "BFO")
            and (r.get("name") or "").upper() == symbol.upper()
            and r.get("option_type") in ("CE", "PE")
        ]
        if not rows:
            raise ProviderNoData(f"kite lists no options on {symbol}")

        expiries = sorted({d for d in (_as_date(r.get("expiry")) for r in rows) if d})
        target = expiry or next((d for d in expiries if d >= date.today()),
                                expiries[0] if expiries else None)
        if target is None:
            raise ProviderNoData(f"kite lists no expiry for {symbol}")
        selected = [r for r in rows if _as_date(r.get("expiry")) == target]

        # Kite accepts up to 500 instruments per quote call.
        legs: List[OptionLeg] = []
        for batch in _chunks(selected, 250):
            keys = [f"NFO:{r['symbol']}" for r in batch]
            payload = self._request("GET", f"{self.base_url}/quote",
                                    params=[("i", k) for k in keys])
            data = payload.get("data") or {}
            for row in batch:
                block = data.get(f"NFO:{row['symbol']}")
                if not block:
                    continue
                legs.append(OptionLeg(
                    strike=row["strike"], option_type=row["option_type"],
                    ltp=to_float(block.get("last_price")),
                    open_interest=to_int(block.get("oi")),
                    oi_change=(to_int(block.get("oi")) or 0)
                    - (to_int(block.get("oi_day_high")) or 0) if block.get("oi") else None,
                    volume=to_int(block.get("volume")),
                ))
        if not legs:
            raise ProviderNoData(f"kite returned no option quotes for {symbol}")
        observed = datetime.now(tz=timezone.utc)
        underlying = None
        try:
            underlying = self.get_quote(symbol).value.ltp
        except ProviderError:
            pass
        chain = OptionChainData(
            underlying_symbol=symbol.upper(), expiry=target, captured_at=observed,
            underlying_value=underlying,
            legs=sorted(legs, key=lambda l: (l.strike, l.option_type)),
            available_expiries=expiries,
        )
        env = self._envelope(chain, "option_chain", observed)
        env.notes = (
            "Assembled from Kite's instrument master plus batch quotes; Kite "
            "publishes no chain endpoint. OI change is approximate."
        )
        return env


# ==========================================================================
# Upstox
# ==========================================================================


class UpstoxProvider(BrokerProvider):
    name = "upstox"
    broker_key = "upstox"
    display_name = "Upstox API v2"
    base_url = "https://api.upstox.com/v2"
    terms_url = "https://upstox.com/developer/api-documentation/"
    licence_note = (
        "Real-time data via your own Upstox account. Free with a demat "
        "account; redistribution is not permitted under the broker's terms."
    )
    credential_fields = ("access_token",)

    _SCRIP_MASTER = (
        "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
    )
    _INTERVALS = {"1m": "1minute", "30m": "30minute", "1d": "day",
                  "1w": "week", "1M": "month"}

    def _login(self) -> Dict[str, Any]:
        return {"access_token": self.credentials["access_token"]}

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.session()['access_token']}",
            "Accept": "application/json",
        }

    def _instrument_dump(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for r in _csv_rows(_download(self._SCRIP_MASTER, gz=True)):
            exch = (r.get("exchange") or "").upper()
            if not exch.startswith(("NSE", "BSE")):
                continue
            out.append({
                "token": r.get("instrument_key"),
                "symbol": (r.get("tradingsymbol") or "").upper(),
                "name": r.get("name"),
                "exchange": exch.split("_")[0],
                "expiry": r.get("expiry"),
                "strike": to_float(r.get("strike_price")),
                "option_type": r.get("option_type"),
                "lot_size": to_int(r.get("lot_size")),
            })
        return out

    def get_quote(self, symbol: str, exchange: str = "NSE",
                  **kw: Any) -> Sourced[QuoteData]:
        self._guard()
        key = self.resolve_token(symbol, exchange)      # an instrument_key
        payload = self._request("GET", f"{self.base_url}/market-quote/quotes",
                                params={"instrument_key": key})
        data = payload.get("data") or {}
        if not data:
            raise ProviderNoData(f"upstox returned no quote for {symbol}")
        block = next(iter(data.values()))
        ohlc = block.get("ohlc") or {}
        depth = ((block.get("depth") or {}).get("buy") or [{}])[0]
        depth_sell = ((block.get("depth") or {}).get("sell") or [{}])[0]
        observed = parse_dt(block.get("last_trade_time")) or datetime.now(tz=timezone.utc)
        ltp = to_float(block.get("last_price"))
        prev = to_float(ohlc.get("close"))
        change = (ltp - prev) if (ltp is not None and prev) else None
        quote = QuoteData(
            symbol=symbol, ltp=ltp,
            open=to_float(ohlc.get("open")), high=to_float(ohlc.get("high")),
            low=to_float(ohlc.get("low")), previous_close=prev,
            change=round(change, 2) if change is not None else None,
            change_pct=round(change / prev * 100, 2) if (change is not None and prev) else None,
            volume=to_int(block.get("volume")),
            bid=to_float(depth.get("price")), ask=to_float(depth_sell.get("price")),
            bid_qty=to_int(depth.get("quantity")), ask_qty=to_int(depth_sell.get("quantity")),
            open_interest=to_int(block.get("oi")),
            observed_at=observed,
        )
        return self._envelope(quote, "quote", observed)

    def get_history(self, symbol: str, interval: str = "1d",
                    start: Optional[date] = None, end: Optional[date] = None,
                    exchange: str = "NSE", **kw: Any) -> Sourced[List[Bar]]:
        self._guard()
        up_interval = self._INTERVALS.get(interval)
        if up_interval is None:
            raise ProviderUnsupported(f"upstox has no {interval} candles")
        key = self.resolve_token(symbol, exchange)
        end = end or date.today()
        start = start or (end - timedelta(days=400))
        payload = self._request(
            "GET",
            f"{self.base_url}/historical-candle/{key}/{up_interval}/"
            f"{end.isoformat()}/{start.isoformat()}",
        )
        rows = ((payload.get("data") or {}).get("candles")) or []
        bars: List[Bar] = []
        for row in rows:
            ts = parse_dt(row[0])
            if ts is None:
                continue
            bars.append(Bar(time=ts, open=to_float(row[1]), high=to_float(row[2]),
                            low=to_float(row[3]), close=to_float(row[4]),
                            volume=to_int(row[5]) if len(row) > 5 else None))
        if not bars:
            raise ProviderNoData(f"upstox returned no candles for {symbol}")
        # Upstox returns newest-first; the whole platform assumes oldest-first.
        bars.sort(key=lambda b: b.time)
        return self._envelope(bars, "quote", bars[-1].time)

    def get_option_chain(self, symbol: str, expiry: Optional[date] = None,
                         **kw: Any) -> Sourced[OptionChainData]:
        self._guard()
        key = self.resolve_token(symbol, kw.get("exchange", "NSE"))
        target = expiry or _next_thursday()
        payload = self._request(
            "GET", f"{self.base_url}/option/chain",
            params={"instrument_key": key, "expiry_date": target.isoformat()},
        )
        rows = payload.get("data") or []
        if not rows:
            raise ProviderNoData(f"upstox returned an empty chain for {symbol}")
        legs: List[OptionLeg] = []
        underlying = None
        for row in rows:
            strike = to_float(row.get("strike_price"))
            underlying = underlying or to_float(row.get("underlying_spot_price"))
            if strike is None:
                continue
            for side, blob_key in (("CE", "call_options"), ("PE", "put_options")):
                blob = row.get(blob_key) or {}
                md = blob.get("market_data") or {}
                greeks = blob.get("option_greeks") or {}
                if not md:
                    continue
                legs.append(OptionLeg(
                    strike=strike, option_type=side,
                    ltp=to_float(md.get("ltp")),
                    open_interest=to_int(md.get("oi")),
                    oi_change=(to_int(md.get("oi")) or 0) - (to_int(md.get("prev_oi")) or 0)
                    if md.get("prev_oi") is not None else None,
                    volume=to_int(md.get("volume")),
                    implied_volatility=to_float(greeks.get("iv")),
                    bid=to_float(md.get("bid_price")), ask=to_float(md.get("ask_price")),
                    bid_qty=to_int(md.get("bid_qty")), ask_qty=to_int(md.get("ask_qty")),
                ))
        observed = datetime.now(tz=timezone.utc)
        chain = OptionChainData(
            underlying_symbol=symbol.upper(), expiry=target, captured_at=observed,
            underlying_value=underlying,
            legs=sorted(legs, key=lambda l: (l.strike, l.option_type)),
            available_expiries=[target],
        )
        env = self._envelope(chain, "option_chain", observed)
        env.notes = "IV and greeks are the broker's own calculation, not ours."
        return env


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    dt = parse_dt(value)
    return dt.date() if dt else None


def _chunks(items: List[Any], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _next_thursday(today: Optional[date] = None) -> date:
    """NSE weekly options expire on a Thursday.

    Only a fallback for when the caller names no expiry - any real chain
    request should pass one explicitly, because NSE has moved expiry days
    before and will again.
    """
    today = today or datetime.now(tz=IST).date()
    return today + timedelta(days=(3 - today.weekday()) % 7)


BROKER_CLASSES = {
    "angelone": AngelOneProvider,
    "dhan": DhanProvider,
    "kite": KiteProvider,
    "upstox": UpstoxProvider,
}
