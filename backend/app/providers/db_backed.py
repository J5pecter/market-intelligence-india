"""Manual and demo providers - both read from the database.

`ManualProvider` serves rows an admin entered (data_status=MANUAL).
`DemoProvider` serves seeded sample rows (data_status=DEMO, is_demo=True) and
refuses to return anything when APP_ENV=PRODUCTION or STAGING.

Both are last-resort links in the failover chain, and both stamp their status
honestly so the UI can badge them.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.data_quality import DataStatus, SourceReliability, Sourced
from app.db.session import SessionLocal
from app.providers.base import (Bar, MarketDataProvider, NewsItem,
                                OptionChainData, OptionLeg, ProviderError,
                                ProviderNoData, QuoteData)


class _DbProvider(MarketDataProvider):
    """Shared plumbing. Subclasses set `demo_only`."""

    demo_only = False
    status = DataStatus.MANUAL

    def _session(self, db: Optional[Session] = None) -> tuple[Session, bool]:
        if db is not None:
            return db, False
        return SessionLocal(), True

    def _check_allowed(self) -> None:
        if self.demo_only and not settings.demo_data_allowed:
            raise ProviderError(
                f"demo data is not served when APP_ENV={settings.app_env.value}"
            )

    def _envelope(self, value: Any, observed_at: Optional[datetime],
                  note: Optional[str] = None) -> Sourced[Any]:
        return Sourced(
            value=value,
            provider=self.name,
            source_name=self.display_name,
            status=self.status,
            observed_at=observed_at,
            reliability=self.reliability,
            notes=note,
        )

    # -- capabilities ------------------------------------------------------

    def get_quote(self, symbol: str, db: Optional[Session] = None,
                  **kw: Any) -> Sourced[QuoteData]:
        self._check_allowed()
        from app.models.market import Quote

        session, owned = self._session(db)
        try:
            stmt = select(Quote).where(Quote.symbol == symbol.upper())
            stmt = stmt.where(Quote.is_demo.is_(self.demo_only))
            row = session.execute(stmt).scalars().first()
            if row is None:
                raise ProviderNoData(f"{self.name}: no stored quote for {symbol}")
            quote = QuoteData(
                symbol=row.symbol, ltp=row.ltp, open=row.open, high=row.high,
                low=row.low, previous_close=row.previous_close,
                change=row.change, change_pct=row.change_pct, volume=row.volume,
                average_volume_20d=row.average_volume_20d, vwap=row.vwap,
                week52_high=row.week52_high, week52_low=row.week52_low,
                market_cap=row.market_cap, bid=row.bid, ask=row.ask,
                open_interest=row.open_interest, oi_change=row.oi_change,
                observed_at=row.observed_at,
            )
            return self._envelope(quote, row.observed_at)
        finally:
            if owned:
                session.close()

    def get_history(self, symbol: str, interval: str = "1d",
                    start: Optional[date] = None, end: Optional[date] = None,
                    db: Optional[Session] = None, **kw: Any) -> Sourced[List[Bar]]:
        self._check_allowed()
        from app.models.market import HistoricalPrice

        session, owned = self._session(db)
        try:
            stmt = (
                select(HistoricalPrice)
                .where(HistoricalPrice.symbol == symbol.upper())
                .where(HistoricalPrice.interval == interval)
                .where(HistoricalPrice.is_demo.is_(self.demo_only))
                .order_by(HistoricalPrice.bar_time)
            )
            rows = session.execute(stmt).scalars().all()
            if not rows:
                raise ProviderNoData(f"{self.name}: no stored history for {symbol}")
            bars = [
                Bar(time=r.bar_time, open=r.open, high=r.high, low=r.low,
                    close=r.close, volume=r.volume, raw_close=r.raw_close)
                for r in rows
                if (start is None or r.bar_time.date() >= start)
                and (end is None or r.bar_time.date() <= end)
            ]
            if not bars:
                raise ProviderNoData(f"{self.name}: no bars in the requested window")
            return self._envelope(bars, bars[-1].time)
        finally:
            if owned:
                session.close()

    def get_option_chain(self, symbol: str, expiry: Optional[date] = None,
                         db: Optional[Session] = None,
                         **kw: Any) -> Sourced[OptionChainData]:
        self._check_allowed()
        from app.models.derivatives import OptionChainSnapshot, OptionSnapshot

        session, owned = self._session(db)
        try:
            stmt = (
                select(OptionChainSnapshot)
                .where(OptionChainSnapshot.underlying_symbol == symbol.upper())
                .where(OptionChainSnapshot.is_demo.is_(self.demo_only))
                .order_by(OptionChainSnapshot.captured_at.desc())
            )
            if expiry is not None:
                stmt = stmt.where(OptionChainSnapshot.expiry == expiry)
            snapshot = session.execute(stmt).scalars().first()
            if snapshot is None:
                raise ProviderNoData(f"{self.name}: no stored chain for {symbol}")

            legs_rows = session.execute(
                select(OptionSnapshot)
                .where(OptionSnapshot.snapshot_id == snapshot.id)
                .order_by(OptionSnapshot.strike, OptionSnapshot.option_type)
            ).scalars().all()

            expiries = session.execute(
                select(OptionChainSnapshot.expiry)
                .where(OptionChainSnapshot.underlying_symbol == symbol.upper())
                .where(OptionChainSnapshot.is_demo.is_(self.demo_only))
                .distinct()
            ).scalars().all()

            chain = OptionChainData(
                underlying_symbol=snapshot.underlying_symbol,
                expiry=snapshot.expiry,
                captured_at=snapshot.captured_at,
                underlying_value=snapshot.underlying_value,
                legs=[
                    OptionLeg(
                        strike=r.strike, option_type=r.option_type, ltp=r.ltp,
                        change=r.change, change_pct=r.change_pct,
                        open_interest=r.open_interest, oi_change=r.oi_change,
                        volume=r.volume,
                        implied_volatility=r.implied_volatility,
                        bid=r.bid, ask=r.ask, bid_qty=r.bid_qty, ask_qty=r.ask_qty,
                    )
                    for r in legs_rows
                ],
                available_expiries=sorted(e for e in expiries if e),
            )
            return self._envelope(chain, snapshot.captured_at)
        finally:
            if owned:
                session.close()

    def get_news(self, symbol: Optional[str] = None, query: Optional[str] = None,
                 limit: int = 25, db: Optional[Session] = None,
                 **kw: Any) -> Sourced[List[NewsItem]]:
        self._check_allowed()
        from app.models.news import NewsArticle

        session, owned = self._session(db)
        try:
            stmt = (
                select(NewsArticle)
                .where(NewsArticle.is_demo.is_(self.demo_only))
                .where(NewsArticle.is_suppressed.is_(False))
                .order_by(NewsArticle.published_at.desc())
                .limit(limit)
            )
            if symbol:
                stmt = stmt.where(NewsArticle.primary_symbol == symbol.upper())
            rows = session.execute(stmt).scalars().all()
            if not rows:
                raise ProviderNoData(f"{self.name}: no stored news")
            items = [
                NewsItem(
                    headline=r.headline, url=r.url, publisher=r.publisher,
                    published_at=r.published_at, summary=r.summary,
                    primary_symbol=r.primary_symbol,
                )
                for r in rows
            ]
            return self._envelope(items, rows[0].published_at)
        finally:
            if owned:
                session.close()

    def get_ipos(self, db: Optional[Session] = None,
                 **kw: Any) -> Sourced[List[Dict[str, Any]]]:
        self._check_allowed()
        from app.models.ipo import Ipo

        session, owned = self._session(db)
        try:
            rows = session.execute(
                select(Ipo).where(Ipo.is_demo.is_(self.demo_only))
                .order_by(Ipo.open_date.desc())
            ).scalars().all()
            if not rows:
                raise ProviderNoData(f"{self.name}: no stored IPOs")
            return self._envelope(
                [{"id": r.id, "slug": r.slug, "company_name": r.company_name}
                 for r in rows],
                datetime.now(tz=timezone.utc),
            )
        finally:
            if owned:
                session.close()


class ManualProvider(_DbProvider):
    name = "manual"
    display_name = "Operator-entered data"
    reliability = SourceReliability.MEDIUM
    is_delayed = True
    demo_only = False
    status = DataStatus.MANUAL
    licence_note = "Entered through the admin panel by an authorised operator."


class DemoProvider(_DbProvider):
    name = "demo"
    display_name = "Seeded demonstration data"
    reliability = SourceReliability.UNKNOWN
    is_delayed = True
    demo_only = True
    status = DataStatus.DEMO
    licence_note = (
        "Illustrative sample rows shipped with the repository. Not market data. "
        "Never served when APP_ENV is STAGING or PRODUCTION."
    )
