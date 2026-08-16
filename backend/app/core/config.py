"""Application configuration.

Every knob the platform exposes is read from the environment so that the same
image can run in DEMO, LOCAL, STAGING and PRODUCTION without code changes.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Annotated, List

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

    # Provider chains: first entry is tried first, the rest are failovers.
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

    # Opt-in. NSE's terms restrict automated access to its site endpoints;
    # the operator must make that call knowingly. See app/providers/nse.py.
    enable_nse_provider: bool = False
    nse_requests_per_minute: int = 20
    yahoo_requests_per_minute: int = 60

    news_api_key: str = ""
    broker_api_key: str = ""
    broker_api_secret: str = ""

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

    def providers_for(self, capability: str) -> List[str]:
        mapping = {
            "quote": self.quote_providers,
            "history": self.history_providers,
            "option_chain": self.option_chain_providers,
            "news": self.news_providers,
            "ipo": self.ipo_providers,
        }
        chain = list(mapping.get(capability, []))
        if not self.demo_data_allowed:
            chain = [p for p in chain if p != "demo"]
        if not self.enable_nse_provider:
            chain = [p for p in chain if p != "nse"]
        return chain


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
