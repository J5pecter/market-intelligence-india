"""Official NSE and BSE end-of-day archive files.

Why this is a separate adapter from `nse.py`
--------------------------------------------
`nse.py` talks to the JSON endpoints that power NSE's *website*. Those are
undocumented, bot-challenged, and restricted by NSE's terms - which is why that
adapter is off by default.

This adapter downloads the **published archive files** instead: the daily
bhavcopy, the securities-wise delivery report, the F&O bhavcopy, and the bulk
and block deal registers. These are static files that NSE and BSE publish for
download precisely so that market participants can consume them. There is no
challenge to defeat and no session to forge - the request is a plain GET for a
file that is meant to be fetched.

That distinction matters for research quality as much as for terms compliance:
these files *are* the exchange's official record. Settlement prices here are
the numbers the exchange itself settles on, not a vendor's reconstruction. When
this adapter and a broker feed disagree, the archive is right.

What you get here and nowhere else
----------------------------------
**Delivery percentage.** The share of traded volume that actually settled into
demat accounts. A stock up 6% on 80% delivery is being accumulated; the same
move on 18% delivery is intraday churn that often round-trips. No price or
volume series can distinguish those two, and no free vendor publishes it.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import requests

from app.core.cache import cached_call, rate_limit_ok
from app.core.config import settings
from app.core.data_quality import (DataStatus, SourceReliability, Sourced,
                                   freshness)
from app.core.market_calendar import IST, is_trading_day
from app.providers.base import (MarketDataProvider, ProviderError,
                                ProviderNoData)

logger = logging.getLogger(__name__)

_UA = (
    "MarketIntelligenceIndia/1.0 (personal research desk; "
    "official archive files only)"
)
_TIMEOUT = 45


def _get(url: str, *, referer: Optional[str] = None) -> bytes:
    """Fetch one archive file.

    A 403 here means the exchange has declined the request. We surface that
    plainly and stop; we do not rotate identities or retry with a different
    fingerprint. See the module docstring.
    """
    headers = {"User-Agent": _UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    try:
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise ProviderError(f"archive fetch failed: {exc}") from exc

    if resp.status_code == 404:
        raise ProviderNoData(f"no archive file published at {url}")
    if resp.status_code in (401, 403):
        raise ProviderError(
            f"exchange declined the archive request (HTTP {resp.status_code}). "
            "Not retrying with a different identity."
        )
    if resp.status_code >= 400:
        raise ProviderError(f"archive fetch returned HTTP {resp.status_code}")
    if not resp.content:
        raise ProviderNoData(f"archive file at {url} is empty")
    return resp.content


def _unzip_first(payload: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            raise ProviderNoData("archive zip is empty")
        return zf.read(names[0])


def _rows(payload: bytes | str) -> List[Dict[str, str]]:
    # Accepts str as well as bytes: cached payloads come back decoded.
    text = (payload.decode("utf-8", errors="replace")
            if isinstance(payload, (bytes, bytearray)) else payload)
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for row in reader:
        out.append({(k or "").strip(): (v or "").strip()
                    for k, v in row.items() if k})
    return out


def _f(value: Any) -> Optional[float]:
    try:
        text = str(value).strip().replace(",", "")
        if text in ("", "-", "NA", "nan"):
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> Optional[int]:
    f = _f(value)
    return int(f) if f is not None else None


def _deal_date(value: Any) -> Optional[date]:
    """NSE stamps deal registers `14-AUG-2026`."""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _recent_trading_days(back: int = 7, end: Optional[date] = None) -> List[date]:
    """Walk backwards over the trading calendar.

    Archives are published after the close, so "today" is usually not there
    yet. Callers try each day in turn until one resolves.
    """
    day = end or datetime.now(tz=IST).date()
    out: List[date] = []
    while len(out) < back:
        if is_trading_day(day):
            out.append(day)
        day -= timedelta(days=1)
    return out


class _ArchiveProvider(MarketDataProvider):
    """Shared behaviour: rate limiting, envelope stamping, day resolution."""

    reliability = SourceReliability.HIGH
    is_delayed = True          # end-of-day by definition
    requires_auth = False

    def _guard(self) -> None:
        if not settings.enable_exchange_archives:
            raise ProviderError(f"{self.name} is disabled by configuration")
        if not rate_limit_ok(self.name, settings.archive_requests_per_minute):
            raise ProviderError(f"{self.name} rate limit reached - backing off")

    def _envelope(self, value: Any, capability: str,
                  observed_at: Optional[datetime]) -> Sourced[Any]:
        env = Sourced(
            value=value,
            provider=self.name,
            source_name=self.display_name,
            status=freshness(observed_at, capability, provider_is_delayed=True),
            observed_at=observed_at,
            reliability=self.reliability,
            source_url=self.base_url,
            license_note=self.licence_note,
            notes="Official end-of-day exchange record.",
        )
        return env

    def _try_days(
        self,
        loader: Callable[[date], Any],
        on: Optional[date],
        capability: str,
        label: str,
    ) -> Sourced[Any]:
        """Resolve the most recent session that actually has a file.

        Returning the requested day's *absence* as UNAVAILABLE would be less
        useful than returning the last published session - but only if the
        envelope says which session it is, which `observed_at` does.
        """
        self._guard()
        days = [on] if on else _recent_trading_days(back=6)
        errors: List[str] = []
        for day in days:
            try:
                value = loader(day)
            except ProviderNoData as exc:
                errors.append(f"{day}: {exc}")
                continue
            if not value:
                errors.append(f"{day}: empty")
                continue
            observed = datetime.combine(
                day, datetime.min.time(), tzinfo=IST
            ).replace(hour=18) .astimezone(timezone.utc)
            env = self._envelope(value, capability, observed)
            env.notes = f"{label} for the session dated {day.isoformat()}."
            return env
        raise ProviderNoData(
            f"{self.name}: no {label} published for "
            f"{', '.join(d.isoformat() for d in days)} ({'; '.join(errors[:3])})"
        )


class NseArchivesProvider(_ArchiveProvider):
    name = "nse_archives"
    display_name = "NSE published archives (bhavcopy, delivery, deals)"
    base_url = "https://nsearchives.nseindia.com"
    terms_url = "https://www.nseindia.com/terms-conditions"
    licence_note = (
        "Official NSE end-of-day files published for download. Free to use "
        "for personal analysis; redistribution is governed by NSE's terms."
    )

    _REFERER = "https://www.nseindia.com/all-reports"

    # -- bhavcopy ---------------------------------------------------------

    def get_bhavcopy(self, on: Optional[date] = None,
                     **kw: Any) -> Sourced[List[Dict[str, Any]]]:
        def _load(day: date) -> List[Dict[str, Any]]:
            return cached_call(
                f"nse_arch:bhav:{day.isoformat()}", 60 * 60 * 20,
                lambda: self._bhavcopy_for(day),
            ) or []

        return self._try_days(_load, on, "corporate_actions", "Bhavcopy")

    def _bhavcopy_for(self, day: date) -> List[Dict[str, Any]]:
        # NSE moved to the UDiFF format in 2024; the old PR/bhavcopy path was
        # retired. This is the current published location.
        stamp = day.strftime("%Y%m%d")
        url = (f"{self.base_url}/content/cm/"
               f"BhavCopy_NSE_CM_0_0_0_{stamp}_F_0000.csv.zip")
        payload = _unzip_first(_get(url, referer=self._REFERER))
        out: List[Dict[str, Any]] = []
        for r in _rows(payload):
            if (r.get("SctySrs") or "").upper() not in ("EQ", "BE", "BZ", "SM"):
                continue
            out.append({
                "symbol": r.get("TckrSymb"),
                "series": r.get("SctySrs"),
                "isin": r.get("ISIN"),
                "open": _f(r.get("OpnPric")),
                "high": _f(r.get("HghPric")),
                "low": _f(r.get("LwPric")),
                "close": _f(r.get("ClsPric")),
                "last": _f(r.get("LastPric")),
                "previous_close": _f(r.get("PrvsClsgPric")),
                "vwap": _f(r.get("AvgPric")),
                "volume": _i(r.get("TtlTradgVol")),
                "turnover": _f(r.get("TtlTrfVal")),
                "trades": _i(r.get("TtlNbOfTxsExctd")),
                "settlement_price": _f(r.get("SttlmPric")),
                "session_date": day.isoformat(),
            })
        return out

    # -- delivery ---------------------------------------------------------

    def get_delivery(self, on: Optional[date] = None,
                     **kw: Any) -> Sourced[List[Dict[str, Any]]]:
        def _load(day: date) -> List[Dict[str, Any]]:
            return cached_call(
                f"nse_arch:deliv:{day.isoformat()}", 60 * 60 * 20,
                lambda: self._delivery_for(day),
            ) or []

        env = self._try_days(_load, on, "corporate_actions",
                             "Securities-wise delivery report")
        env.notes = (
            (env.notes or "") +
            " Delivery percentage is deliverable quantity over traded quantity: "
            "how much of the day's volume actually settled rather than being "
            "squared off intraday."
        )
        return env

    def _delivery_for(self, day: date) -> List[Dict[str, Any]]:
        # MTO = "market trade to occurrence"; NSE's securities-wise delivery file.
        url = f"{self.base_url}/archives/equities/mto/MTO_{day.strftime('%d%m%Y')}.DAT"
        raw = _get(url, referer=self._REFERER).decode("utf-8", errors="replace")
        out: List[Dict[str, Any]] = []
        for line in raw.splitlines():
            parts = [p.strip() for p in line.split(",")]
            # Data rows carry a leading record-type marker of 20:
            #   20, sr_no, symbol, series, traded_qty, deliverable_qty, pct
            # The four header lines above them have no such marker. Anchoring
            # on it (rather than "the first field is numeric") is what keeps
            # the columns aligned - reading the serial number as the symbol
            # silently attributes every delivery figure to the wrong stock.
            if len(parts) < 7 or parts[0] != "20":
                continue
            traded, delivered = _i(parts[4]), _i(parts[5])
            pct = _f(parts[6])
            if pct is None and traded and delivered is not None:
                pct = round(delivered / traded * 100, 2)
            out.append({
                "symbol": parts[2],
                "series": parts[3],
                "traded_quantity": traded,
                "deliverable_quantity": delivered,
                "delivery_pct": pct,
                "session_date": day.isoformat(),
            })
        if not out:
            raise ProviderNoData(f"MTO file for {day} parsed to zero rows")
        return out

    # -- deal registers ---------------------------------------------------

    def get_bulk_deals(self, on: Optional[date] = None,
                       **kw: Any) -> Sourced[List[Dict[str, Any]]]:
        env = self._deals("bulk", on)
        env.notes = (
            (env.notes or "") +
            " A bulk deal is any single-client trade above 0.5% of listed equity, "
            "disclosed to the exchange the same day. Buy and sell legs of the "
            "same trade both appear, so summing raw quantity double-counts."
        )
        return env

    def get_block_deals(self, on: Optional[date] = None,
                        **kw: Any) -> Sourced[List[Dict[str, Any]]]:
        env = self._deals("block", on)
        env.notes = (
            (env.notes or "") +
            " Block deals execute in a separate window at a negotiated price, "
            "so the print need not sit inside the day's regular range."
        )
        return env

    def _deals(self, kind: str, on: Optional[date]) -> Sourced[List[Dict[str, Any]]]:
        """Read a deal register.

        Unlike the bhavcopy, NSE publishes these as a single rolling file
        covering the recent past rather than one file per session. So we fetch
        once, parse every row, and select a day from what is actually in it -
        walking backwards over the calendar re-downloading the same file would
        just be the same answer six times.
        """
        self._guard()
        # Cache the *parsed* rows, not the raw response. The cache serialises
        # through JSON, so bytes would come back as a str on every hit after
        # the first and blow up the decode.
        parsed = cached_call(
            f"nse_arch:{kind}", 60 * 60 * 4,
            lambda: _rows(_get(f"{self.base_url}/content/equities/{kind}.csv",
                               referer=self._REFERER)),
        ) or []
        rows: List[Dict[str, Any]] = []
        for r in parsed:
            row_date = _deal_date(r.get("Date") or r.get("DATE"))
            qty = _i(r.get("Quantity Traded") or r.get("QUANTITY TRADED"))
            price = _f(r.get("Trade Price / Wght. Avg. Price")
                       or r.get("TRADE PRICE / WGHT. AVG. PRICE"))
            if row_date is None:
                continue
            rows.append({
                "date": row_date.isoformat(),
                "symbol": r.get("Symbol") or r.get("SYMBOL"),
                "security_name": r.get("Security Name") or r.get("SECURITY NAME"),
                "client_name": r.get("Client Name") or r.get("CLIENT NAME"),
                "buy_sell": (r.get("Buy/Sell") or r.get("Buy / Sell")
                             or r.get("BUY/SELL") or "").upper(),
                "quantity": qty,
                "price": price,
                "value": round(qty * price, 2) if (qty and price) else None,
                "remarks": r.get("Remarks") or None,
                "deal_type": kind.upper(),
            })
        if not rows:
            raise ProviderNoData(
                f"NSE's {kind} deal register is currently empty - no {kind} "
                "deals have been reported in the published window."
            )

        available = sorted({r["date"] for r in rows}, reverse=True)
        target = on.isoformat() if on else available[0]
        selected = [r for r in rows if r["date"] == target]
        if not selected:
            raise ProviderNoData(
                f"no {kind} deals on {target}; the register covers "
                f"{available[-1]} to {available[0]}"
            )
        observed = datetime.fromisoformat(target).replace(hour=18, tzinfo=IST) \
            .astimezone(timezone.utc)
        env = self._envelope(selected, "corporate_actions", observed)
        env.notes = (
            f"{kind.title()} deal register for {target} "
            f"({len(selected)} of {len(rows)} rows in the published window "
            f"{available[-1]} to {available[0]})."
        )
        return env


class BseArchivesProvider(_ArchiveProvider):
    name = "bse_archives"
    display_name = "BSE published archives (bhavcopy)"
    base_url = "https://www.bseindia.com"
    terms_url = "https://www.bseindia.com/aboutus/disclaimer.html"
    licence_note = (
        "Official BSE end-of-day bhavcopy published for download. Free for "
        "personal analysis; redistribution is governed by BSE's terms."
    )

    def get_bhavcopy(self, on: Optional[date] = None,
                     **kw: Any) -> Sourced[List[Dict[str, Any]]]:
        def _load(day: date) -> List[Dict[str, Any]]:
            return cached_call(
                f"bse_arch:bhav:{day.isoformat()}", 60 * 60 * 20,
                lambda: self._bhavcopy_for(day),
            ) or []

        return self._try_days(_load, on, "corporate_actions", "BSE bhavcopy")

    def _bhavcopy_for(self, day: date) -> List[Dict[str, Any]]:
        stamp = day.strftime("%Y%m%d")
        url = (f"https://www.bseindia.com/download/BhavCopy/Equity/"
               f"BhavCopy_BSE_CM_0_0_0_{stamp}_F_0000.CSV")
        payload = _get(url, referer="https://www.bseindia.com/markets.html")
        out: List[Dict[str, Any]] = []
        for r in _rows(payload):
            if (r.get("SctySrs") or "").upper() not in ("A", "B", "T", "X", "EQ"):
                continue
            out.append({
                "symbol": r.get("TckrSymb"),
                "scrip_code": r.get("FinInstrmId"),
                "isin": r.get("ISIN"),
                "series": r.get("SctySrs"),
                "open": _f(r.get("OpnPric")),
                "high": _f(r.get("HghPric")),
                "low": _f(r.get("LwPric")),
                "close": _f(r.get("ClsPric")),
                "previous_close": _f(r.get("PrvsClsgPric")),
                "volume": _i(r.get("TtlTradgVol")),
                "turnover": _f(r.get("TtlTrfVal")),
                "trades": _i(r.get("TtlNbOfTxsExctd")),
                "session_date": day.isoformat(),
            })
        return out
