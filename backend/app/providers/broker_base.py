"""Shared machinery for broker feeds.

Why brokers matter here
-----------------------
A broker API is the only source in this platform that is genuinely real-time
*and* licensed. Everything else is delayed (Yahoo), end-of-day (exchange
archives) or restricted (NSE's site JSON). So when a broker is configured its
adapter leads every market-data chain, and it is the only adapter permitted to
stamp an envelope `LIVE`.

What this base class does not do
--------------------------------
It does not store credentials, log them, or return them from any endpoint.
`describe()` reports *which* credential fields are missing, never their values.
Sessions live in memory only; on restart the adapter re-authenticates.

Instrument tokens
-----------------
Every Indian broker keys its API on a numeric instrument token rather than a
symbol, and each publishes a full instrument dump. The base class caches that
dump for a trading day and resolves `HDFCBANK` -> token once, because getting
this wrong silently returns another company's price - the single most dangerous
failure mode in the whole platform.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import requests

from app.core.cache import cached_call, rate_limit_ok
from app.core.config import settings
from app.core.data_quality import (DataStatus, SourceReliability, Sourced,
                                   freshness)
from app.providers.base import (Bar, MarketDataProvider, ProviderError,
                                ProviderNoData, QuoteData)

logger = logging.getLogger(__name__)

_TIMEOUT = 12


class BrokerAuthError(ProviderError):
    """Credentials are missing, wrong, or the session has expired.

    Kept distinct so the health endpoint can tell "you never configured this"
    apart from "the exchange feed is down", which need different fixes.
    """


class BrokerNotConfigured(BrokerAuthError):
    """No credentials supplied. Not an outage - the operator opted out."""


class BrokerProvider(MarketDataProvider):
    """Base for Angel One, Dhan, Kite and Upstox.

    Subclasses implement `_login`, `_fetch_quote`, `_instrument_dump` and
    whichever extras they support. Rate limiting, session refresh, token
    resolution and envelope stamping are handled here so the concrete adapters
    stay small enough to audit against the broker's documentation.
    """

    broker_key: str = ""
    reliability = SourceReliability.HIGH
    is_delayed = False          # a broker feed is the real thing
    requires_auth = True
    exchange_segment_default = "NSE"

    #: Credential fields the adapter needs, in the order a human would supply
    #: them. Used by `describe()` to report what is missing.
    credential_fields: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.rate_limit_per_minute = settings.broker_requests_per_minute
        self._session: Optional[Dict[str, Any]] = None
        self._session_expires: Optional[datetime] = None
        self._lock = threading.Lock()
        self._http = requests.Session()
        self._http.headers.update({
            "User-Agent": "MarketIntelligenceIndia/1.0 (personal research desk)",
            "Accept": "application/json",
        })

    # -- credentials -------------------------------------------------------

    @property
    def credentials(self) -> Dict[str, str]:
        return settings.broker_credentials(self.broker_key)

    @property
    def is_configured(self) -> bool:
        return settings.broker_is_configured(self.broker_key)

    def missing_credentials(self) -> List[str]:
        have = set(self.credentials)
        return [f for f in self.credential_fields if f not in have]

    def _guard(self) -> None:
        if not self.is_configured:
            raise BrokerNotConfigured(
                f"{self.broker_key} is not configured "
                f"(missing: {', '.join(self.missing_credentials()) or 'credentials'})"
            )
        if not rate_limit_ok(self.name, self.rate_limit_per_minute):
            raise ProviderError(f"{self.name} rate limit reached - backing off")

    # -- session -----------------------------------------------------------

    def session(self) -> Dict[str, Any]:
        """Return a live session, logging in or refreshing if needed."""
        with self._lock:
            now = datetime.now(tz=timezone.utc)
            fresh = (
                self._session is not None
                and (self._session_expires is None or self._session_expires > now)
            )
            if not fresh:
                self._session = self._login()
                # Most Indian broker tokens are valid until ~06:00 next day.
                # Expiring ours earlier costs one extra login and avoids
                # serving a request with a token that dies mid-flight.
                self._session_expires = now + timedelta(hours=6)
            return self._session or {}

    def invalidate_session(self) -> None:
        with self._lock:
            self._session = None
            self._session_expires = None

    def _login(self) -> Dict[str, Any]:
        raise NotImplementedError

    # -- HTTP --------------------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        retry_on_auth: bool = True,
        **kwargs: Any,
    ) -> Any:
        merged = {**self._auth_headers(), **(headers or {})}
        try:
            resp = self._http.request(method, url, headers=merged,
                                      timeout=_TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"{self.name} transport error: {exc}") from exc

        if resp.status_code in (401, 403):
            # One retry with a fresh session covers ordinary token expiry.
            # A second failure means the credentials are wrong, and retrying
            # further would just lock the account.
            if retry_on_auth:
                self.invalidate_session()
                return self._request(method, url, headers=headers,
                                     retry_on_auth=False, **kwargs)
            raise BrokerAuthError(
                f"{self.name} rejected the session (HTTP {resp.status_code}). "
                "Re-issue the access token."
            )
        if resp.status_code == 429:
            raise ProviderError(f"{self.name} rate-limited this client (HTTP 429)")
        if resp.status_code >= 500:
            raise ProviderError(f"{self.name} upstream error (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise ProviderError(
                f"{self.name} rejected the request (HTTP {resp.status_code}): "
                f"{resp.text[:200]}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError(f"{self.name} returned non-JSON") from exc

    def _auth_headers(self) -> Dict[str, str]:
        return {}

    # -- instrument tokens -------------------------------------------------

    def _instrument_dump(self) -> List[Dict[str, Any]]:
        """Return the broker's full instrument list as plain dicts.

        Each row must carry at least `token`, `symbol`, `exchange`; option and
        future rows should also carry `expiry`, `strike`, `option_type`.
        """
        raise NotImplementedError

    def instruments_index(self) -> Dict[str, Dict[str, Any]]:
        """`EXCHANGE:SYMBOL` -> instrument row, cached for the trading day."""

        def _build() -> Dict[str, Dict[str, Any]]:
            index: Dict[str, Dict[str, Any]] = {}
            for row in self._instrument_dump():
                symbol = (row.get("symbol") or "").upper()
                exchange = (row.get("exchange") or "NSE").upper()
                if symbol:
                    index.setdefault(f"{exchange}:{symbol}", row)
            return index

        # Instrument masters are republished once a day, before the open.
        return cached_call(
            f"{self.name}:instruments:{date.today().isoformat()}",
            60 * 60 * 8,
            _build,
        ) or {}

    def resolve_token(self, symbol: str, exchange: str = "NSE") -> str:
        key = f"{exchange.upper()}:{symbol.upper()}"
        row = self.instruments_index().get(key)
        if row is None:
            raise ProviderNoData(
                f"{self.name} has no instrument named {key}. "
                "Check the trading symbol against the broker's instrument master."
            )
        token = row.get("token")
        if token in (None, ""):
            raise ProviderNoData(f"{self.name} instrument {key} carries no token")
        return str(token)

    # -- envelopes ---------------------------------------------------------

    def _envelope(self, value: Any, capability: str,
                  observed_at: Optional[datetime]) -> Sourced[Any]:
        """Stamp a broker payload.

        A broker feed is the one source allowed to read LIVE, and only when the
        observation really is recent - `freshness()` still decides, so a
        reconnect that replays a stale tick cannot masquerade as live.
        """
        return Sourced(
            value=value,
            provider=self.name,
            source_name=self.display_name,
            status=freshness(observed_at, capability, provider_is_delayed=False),
            observed_at=observed_at,
            reliability=self.reliability,
            source_url=self.base_url,
            license_note=self.licence_note,
            notes=(
                "Exchange feed via your own broker account. Subject to the "
                "broker's terms; for personal use only."
            ),
        )

    # -- introspection -----------------------------------------------------

    def describe(self) -> Dict[str, Any]:
        """Public description. Carries no credential values *or names*.

        `describe()` is surfaced on the unauthenticated `/api/health`, so it
        reports only how many fields are still outstanding. `missing_credentials()`
        returns the actual field names for admin tooling and log messages.
        """
        base = super().describe()
        base.update({
            "broker": self.broker_key,
            "configured": self.is_configured,
            "credentials_outstanding": len(self.missing_credentials()),
            "session_active": self._session is not None,
            "session_expires_at": self._session_expires.isoformat()
            if self._session_expires else None,
        })
        return base


# --------------------------------------------------------------------------
# helpers shared by the concrete adapters
# --------------------------------------------------------------------------


def totp_now(secret: str) -> str:
    """RFC 6238 TOTP, standard 6-digit / 30-second parameters.

    Angel One requires a TOTP on every login. Implemented from the stdlib
    rather than pulling in `pyotp` for thirty lines of HMAC.
    """
    import base64
    import hashlib
    import hmac
    import struct

    padded = secret.strip().replace(" ", "").upper()
    padded += "=" * (-len(padded) % 8)
    try:
        key = base64.b32decode(padded, casefold=True)
    except Exception as exc:  # noqa: BLE001
        raise BrokerAuthError("TOTP secret is not valid base32") from exc

    counter = int(datetime.now(tz=timezone.utc).timestamp()) // 30
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


def to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return None
        out = float(value)
        return None if out != out else out
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> Optional[int]:
    f = to_float(value)
    return int(f) if f is not None else None


def parse_dt(value: Any) -> Optional[datetime]:
    """Broker timestamps arrive as epoch seconds, epoch millis or ISO text."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:          # milliseconds
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = (datetime.fromisoformat(text) if fmt is None
                  else datetime.strptime(text, fmt))
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None
