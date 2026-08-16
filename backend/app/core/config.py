"""Application configuration.

Every knob the platform exposes is read from the environment so that the same
image can run in DEMO, LOCAL, STAGING and PRODUCTION without code changes.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Annotated, Dict, List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# pydantic-settings JSON-decodes complex types straight from the environment.
# NoDecode hands us the raw string so a plain comma-separated list works, which
# is what anyone editing a .env actually expects to be able to write.
CsvList = Annotated[List[str], NoDecode]


class AppEnv(str, Enum):
    DEMO = "DEMO"
    LOCAL = "LOCAL"
    STAGING = "STAGING"
    PRODUCTION = "PRODUCTION"


def _csv(value: str | List[str]) -> List[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Market Intelligence India"
    app_env: AppEnv = AppEnv.DEMO
    api_prefix: str = "/api"

    secret_key: str = "insecure-development-key-change-me"
    access_token_expire_minutes: int = 720

    database_url: str = "sqlite:///./market_intel.db"
    redis_url: str = ""

    cors_origins: CsvList = Field(default_factory=lambda: ["http://localhost:3000"])

    # Single-operator deployment. The platform is a private research desk for
    # one analyst rather than a service offered to the public, so it drops the
    # "distributing research to others" framing from the disclaimers. It does
    # NOT relax any provenance rule - the honesty of the data is the whole
    # point of the tool, and matters more when nobody else is checking it.
    personal_use_mode: bool = False
    operator_name: str = ""

    # Provider chains: first entry is tried first, the rest are failovers.
    # Brokers come first because they are the only genuinely real-time source;
    # everything after them is delayed or end-of-day and is labelled as such.
    quote_providers: CsvList = Field(
        default_factory=lambda: ["yahoo", "nse", "manual", "demo"]
    )
    history_providers: CsvList = Field(
        default_factory=lambda: ["yahoo", "manual", "demo"]
    )
    option_chain_providers: CsvList = Field(
        default_factory=lambda: ["nse", "manual", "demo"]
    )
    news_providers: CsvList = Field(
        default_factory=lambda: ["google_news_rss", "manual", "demo"]
    )
    ipo_providers: CsvList = Field(default_factory=lambda: ["manual", "demo"])
    eod_providers: CsvList = Field(
        default_factory=lambda: ["nse_archives", "bse_archives"]
    )
    macro_providers: CsvList = Field(default_factory=lambda: ["worldbank", "rbi"])

    # Opt-in. NSE's terms restrict automated access to its site endpoints;
    # the operator must make that call knowingly. See app/providers/nse.py.
    enable_nse_provider: bool = False
    nse_requests_per_minute: int = 20
    yahoo_requests_per_minute: int = 60

    # Published bhavcopy/archive files are a separate, documented download path
    # from the site's internal JSON. They carry no bot challenge, so they are
    # enabled by default. See app/providers/nse_archives.py.
    enable_exchange_archives: bool = True
    archive_requests_per_minute: int = 12

    news_api_key: str = ""

    # -- broker feeds ------------------------------------------------------
    # The only genuinely real-time, licensed source available to a retail
    # operator. Each is off unless its credentials are present; none of them
    # ships with a default key.
    active_broker: str = ""             # angelone | dhan | kite | upstox | ""

    angelone_api_key: str = ""
    angelone_client_code: str = ""
    angelone_password: str = ""         # MPIN
    angelone_totp_secret: str = ""

    dhan_client_id: str = ""
    dhan_access_token: str = ""

    kite_api_key: str = ""
    kite_access_token: str = ""

    upstox_access_token: str = ""

    broker_requests_per_minute: int = 180
    enable_tick_stream: bool = False
    tick_stream_symbols: CsvList = Field(default_factory=list)

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    enable_scheduler: bool = True
    quote_refresh_seconds_market_hours: int = 60
    quote_refresh_seconds_off_hours: int = 900

    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = ""

    @field_validator("database_url", mode="after")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        """Accept the URL shape managed Postgres providers actually hand out.

        Render, Heroku, Railway and Supabase all export `postgres://…`, which
        SQLAlchemy 2.0 refuses outright, and `postgresql://…`, which resolves
        to psycopg2 rather than the psycopg 3 driver this project installs.
        Normalising here means a deployment works with the connection string
        pasted straight from the dashboard.
        """
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        return value

    @field_validator(
        "cors_origins",
        "quote_providers",
        "history_providers",
        "option_chain_providers",
        "news_providers",
        "ipo_providers",
        "eod_providers",
        "macro_providers",
        "tick_stream_symbols",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value):  # noqa: ANN001 - pydantic validator
        return _csv(value)

    # -- derived helpers ---------------------------------------------------

    @property
    def demo_data_allowed(self) -> bool:
        """Seeded sample rows are never served in STAGING or PRODUCTION."""
        return self.app_env in (AppEnv.DEMO, AppEnv.LOCAL)

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.PRODUCTION

    def broker_credentials(self, broker: str) -> Dict[str, str]:
        """Credentials for one broker, empty strings stripped.

        Returning a dict (rather than reading attributes at the call site)
        keeps every secret name in one place, so the health endpoint can report
        *which* fields are missing without ever reading their values.
        """
        fields = {
            "angelone": {
                "api_key": self.angelone_api_key,
                "client_code": self.angelone_client_code,
                "password": self.angelone_password,
                "totp_secret": self.angelone_totp_secret,
            },
            "dhan": {
                "client_id": self.dhan_client_id,
                "access_token": self.dhan_access_token,
            },
            "kite": {
                "api_key": self.kite_api_key,
                "access_token": self.kite_access_token,
            },
            "upstox": {
                "access_token": self.upstox_access_token,
            },
        }.get(broker, {})
        return {k: v for k, v in fields.items() if v}

    def broker_is_configured(self, broker: str) -> bool:
        required = {
            "angelone": 4, "dhan": 2, "kite": 2, "upstox": 1,
        }.get(broker, 0)
        return required > 0 and len(self.broker_credentials(broker)) == required

    @property
    def configured_brokers(self) -> List[str]:
        return [b for b in ("angelone", "dhan", "kite", "upstox")
                if self.broker_is_configured(b)]

    def providers_for(self, capability: str) -> List[str]:
        mapping = {
            "quote": self.quote_providers,
            "history": self.history_providers,
            "option_chain": self.option_chain_providers,
            "news": self.news_providers,
            "ipo": self.ipo_providers,
            "eod": self.eod_providers,
            "macro": self.macro_providers,
        }
        chain = list(mapping.get(capability, []))

        # A configured broker is the only real-time source, so it leads every
        # market-data chain whether or not the operator remembered to list it.
        if capability in ("quote", "history", "option_chain"):
            for broker in reversed(self.configured_brokers):
                if broker in chain:
                    chain.remove(broker)
                chain.insert(0, broker)

        if not self.demo_data_allowed:
            chain = [p for p in chain if p != "demo"]
        if not self.enable_nse_provider:
            chain = [p for p in chain if p != "nse"]
        if not self.enable_exchange_archives:
            chain = [p for p in chain if not p.endswith("_archives")]
        # An unconfigured broker must never sit in a chain: it would burn a
        # failover slot and open its breaker on every single call.
        chain = [p for p in chain
                 if p not in ("angelone", "dhan", "kite", "upstox")
                 or self.broker_is_configured(p)]
        return chain


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
