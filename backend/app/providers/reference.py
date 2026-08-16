"""Reference and macro adapters: AMFI, RBI, World Bank.

All three are official publishers with genuinely open data, so they need no
key, no session and no negotiation:

* **AMFI** publishes every Indian mutual fund NAV as a plain text file, plus
  the half-yearly large/mid/small-cap classification that decides which index
  bucket a stock sits in. That classification is the *official* one - vendors
  who guess it from market cap alone get the boundaries wrong.
* **RBI** publishes policy rates and daily FX reference rates.
* **World Bank** publishes India's macro series (GDP, CPI, current account)
  through a documented, unauthenticated JSON API.

Macro series arrive with long publication lags - a CPI print lands weeks after
the month it measures. Every envelope here therefore carries the *reference
period* as `observed_at`, not the download time, so a two-month-old inflation
figure is never mistaken for this morning's reading.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from app.core.cache import cached_call
from app.core.data_quality import (DataStatus, SourceReliability, Sourced,
                                   freshness)
from app.providers.base import (MarketDataProvider, ProviderError,
                                ProviderNoData)

logger = logging.getLogger(__name__)

_UA = "MarketIntelligenceIndia/1.0 (personal research desk)"
_TIMEOUT = 45


def _fetch(url: str, *, as_json: bool = False, params: Optional[Dict] = None) -> Any:
    try:
        resp = requests.get(url, headers={"User-Agent": _UA},
                            params=params, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise ProviderError(f"reference fetch failed: {exc}") from exc
    if resp.status_code == 404:
        raise ProviderNoData(f"nothing published at {url}")
    if resp.status_code >= 400:
        raise ProviderError(f"reference fetch returned HTTP {resp.status_code}")
    if as_json:
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError(f"{url} returned non-JSON") from exc
    return resp.text


def _f(value: Any) -> Optional[float]:
    try:
        text = str(value).strip().replace(",", "")
        if text in ("", "-", "NA", "N.A.", "nan", "None"):
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


class _ReferenceProvider(MarketDataProvider):
    reliability = SourceReliability.HIGH
    is_delayed = True
    requires_auth = False

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
        )


# ==========================================================================
# AMFI - mutual fund NAVs and the official cap classification
# ==========================================================================


class AmfiProvider(_ReferenceProvider):
    name = "amfi"
    display_name = "AMFI India (NAV and cap classification)"
    base_url = "https://www.amfiindia.com"
    terms_url = "https://www.amfiindia.com/terms-and-conditions"
    licence_note = "Published by AMFI for public use."

    _NAV_ALL = "https://www.amfiindia.com/spages/NAVAll.txt"

    def get_fund_navs(self, **kw: Any) -> Sourced[List[Dict[str, Any]]]:
        """Every scheme's latest NAV.

        AMFI's file is semicolon-delimited with AMC names on bare lines acting
        as section headers, so the parser tracks the current AMC as it walks
        rather than trying to join on a code afterwards.
        """

        def _load() -> List[Dict[str, Any]]:
            text = _fetch(self._NAV_ALL)
            rows: List[Dict[str, Any]] = []
            amc: Optional[str] = None
            category: Optional[str] = None
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if ";" not in line:
                    # Either a category banner or an AMC name.
                    if "Mutual Fund" in line:
                        amc = line
                    else:
                        category = line
                    continue
                parts = line.split(";")
                if len(parts) < 6 or parts[0].strip() == "Scheme Code":
                    continue
                nav = _f(parts[4])
                if nav is None:
                    continue
                rows.append({
                    "scheme_code": parts[0].strip(),
                    "isin_growth": parts[1].strip() or None,
                    "isin_reinvestment": parts[2].strip() or None,
                    "scheme_name": parts[3].strip(),
                    "nav": nav,
                    "nav_date": parts[5].strip(),
                    "amc": amc,
                    "category": category,
                })
            if not rows:
                raise ProviderNoData("AMFI NAV file parsed to zero schemes")
            return rows

        rows = cached_call("amfi:navall", 60 * 60 * 6, _load) or []
        observed = _parse_amfi_date(rows[0].get("nav_date")) if rows else None
        env = self._envelope(rows, "fundamentals", observed)
        env.notes = (
            f"{len(rows)} schemes. NAVs are declared once per business day "
            "after the close; intraday values do not exist for mutual funds."
        )
        return env


def _parse_amfi_date(text: Any) -> Optional[datetime]:
    if not text:
        return None
    try:
        return datetime.strptime(str(text).strip(), "%d-%b-%Y").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


# ==========================================================================
# RBI - policy rates
# ==========================================================================


class RbiProvider(_ReferenceProvider):
    name = "rbi"
    display_name = "Reserve Bank of India"
    base_url = "https://www.rbi.org.in"
    terms_url = "https://www.rbi.org.in/Scripts/Disclaimer.aspx"
    licence_note = "Published by the Reserve Bank of India for public use."

    def get_policy_rates(self, **kw: Any) -> Sourced[List[Dict[str, Any]]]:
        """Current policy corridor, scraped from RBI's own rates banner.

        RBI publishes no JSON API for this. The banner is a small, stable HTML
        fragment on the homepage that exists to be read; it carries no bot
        challenge. If the markup changes this returns UNAVAILABLE with the
        reason rather than guessing - a wrong repo rate would silently corrupt
        every discounted valuation downstream.
        """

        def _load() -> List[Dict[str, Any]]:
            # Collapse whitespace first: the banner spans several lines and
            # every cell is padded, which no single-line regex survives.
            html = re.sub(r"\s+", " ", _fetch(f"{self.base_url}/"))
            wanted = {
                "repo_rate": "Policy Repo Rate",
                "standing_deposit_facility": "Standing Deposit Facility Rate",
                "marginal_standing_facility": "Marginal Standing Facility Rate",
                "bank_rate": "Bank Rate",
                "reverse_repo_rate": "Fixed Reverse Repo Rate",
                "crr": "CRR",
                "slr": "SLR",
            }
            found: List[Dict[str, Any]] = []
            for key, label in wanted.items():
                # <th> Policy Repo Rate </th> <td> : 5.25% </td>
                match = re.search(
                    r"<th[^>]*>\s*" + re.escape(label)
                    + r"\s*</th>\s*<td[^>]*>\s*:?\s*([\d.]+)\s*%",
                    html, re.IGNORECASE,
                )
                if match:
                    found.append({"key": key, "label": label,
                                  "value_pct": _f(match.group(1))})
            if not found:
                raise ProviderNoData(
                    "RBI rates banner did not match the expected layout - "
                    "the page has been restructured and this parser needs "
                    "updating rather than guessing a rate."
                )
            return found

        rows = cached_call("rbi:rates", 60 * 60 * 12, _load) or []
        observed = datetime.now(tz=timezone.utc)
        env = self._envelope(rows, "fundamentals", observed)
        env.status = DataStatus.UNVERIFIED
        env.notes = (
            "Read from RBI's published rates banner. Confirm against the "
            "latest Monetary Policy Statement before using in a valuation."
        )
        return env


# ==========================================================================
# World Bank - India macro series
# ==========================================================================


class WorldBankProvider(_ReferenceProvider):
    name = "worldbank"
    display_name = "World Bank Open Data (India)"
    base_url = "https://api.worldbank.org/v2"
    terms_url = "https://datacatalog.worldbank.org/public-licenses"
    licence_note = "CC BY 4.0. Attribution required."
    reliability = SourceReliability.MEDIUM

    #: Friendly name -> World Bank indicator code.
    INDICATORS = {
        "gdp_growth": "NY.GDP.MKTP.KD.ZG",
        "gdp_current_usd": "NY.GDP.MKTP.CD",
        "gdp_per_capita": "NY.GDP.PCAP.CD",
        "inflation_cpi": "FP.CPI.TOTL.ZG",
        "current_account_pct_gdp": "BN.CAB.XOKA.GD.ZS",
        "gross_savings_pct_gdp": "NY.GNS.ICTR.ZS",
        "fdi_net_inflows_usd": "BX.KLT.DINV.CD.WD",
        "unemployment_pct": "SL.UEM.TOTL.ZS",
        "population": "SP.POP.TOTL",
        "market_cap_pct_gdp": "CM.MKT.LCAP.GD.ZS",
    }

    def get_macro_series(self, indicator: str,
                         **kw: Any) -> Sourced[List[Dict[str, Any]]]:
        code = self.INDICATORS.get(indicator, indicator)
        country = kw.get("country", "IND")

        def _load() -> List[Dict[str, Any]]:
            payload = _fetch(
                f"{self.base_url}/country/{country}/indicator/{code}",
                as_json=True,
                params={"format": "json", "per_page": 120},
            )
            # The API answers [metadata, rows]; rows is null when the code is
            # unknown, which is a "no data" not a transport failure.
            if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
                raise ProviderNoData(
                    f"World Bank has no series {code} for {country}"
                )
            rows = []
            for r in payload[1]:
                value = _f(r.get("value"))
                if value is None:
                    continue          # gap years are omitted, not zero-filled
                rows.append({
                    "year": int(r["date"]),
                    "value": value,
                    "indicator": code,
                    "indicator_name": (r.get("indicator") or {}).get("value"),
                    "country": (r.get("country") or {}).get("value"),
                })
            rows.sort(key=lambda x: x["year"])
            if not rows:
                raise ProviderNoData(f"World Bank series {code} is all nulls")
            return rows

        rows = cached_call(f"wb:{country}:{code}", 60 * 60 * 24, _load) or []
        # Stamp the *reference period*, not the fetch time: this is annual data
        # published with a lag of a year or more.
        observed = datetime(rows[-1]["year"], 12, 31, tzinfo=timezone.utc)
        env = self._envelope(rows, "fundamentals", observed)
        env.notes = (
            f"Annual series, {rows[0]['year']}-{rows[-1]['year']}. "
            "Published with a substantial lag; the latest year shown is the "
            "latest the World Bank has finalised, not the current year."
        )
        return env
