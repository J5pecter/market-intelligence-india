"""Daily ingestion of the exchange's published end-of-day record.

Runs after the close, stores the bhavcopy, the delivery report and the deal
registers, and writes an `IngestionRun` row for each so a gap in the data can
always be explained: either the exchange published nothing, or our job did not
run. Those look identical in the data and demand opposite responses, which is
why the audit row is written even when nothing is stored.

Everything here is idempotent. Re-running a day that is already stored updates
those rows in place rather than appending a second copy, so a backfill can be
run repeatedly without corrupting the history it is meant to build.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.data_quality import DataStatus, Sourced
from app.core.market_calendar import IST, is_trading_day
from app.models.exchange_data import (DealRecord, DeliveryRecord, EodBar,
                                      IngestionRun)
from app.providers.base import ProviderError
from app.providers.registry import registry

logger = logging.getLogger(__name__)


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _record_run(session: Session, dataset: str, session_date: Optional[date],
                status: str, *, rows_seen: int = 0, rows_written: int = 0,
                provider: Optional[str] = None, duration_ms: Optional[float] = None,
                message: Optional[str] = None) -> IngestionRun:
    run = IngestionRun(
        dataset=dataset, session_date=session_date, status=status,
        rows_seen=rows_seen, rows_written=rows_written, provider=provider,
        duration_ms=duration_ms, message=(message or "")[:500] or None,
    )
    session.add(run)
    return run


def _provenance(env: Sourced[Any]) -> Dict[str, Any]:
    """Provenance columns copied onto every ingested row."""
    return {
        "provider": env.provider,
        "source_name": env.source_name,
        "source_url": env.source_url,
        "data_status": env.status.value,
        "observed_at": env.observed_at,
        "is_demo": env.is_demo,
    }


# --------------------------------------------------------------------------
# bhavcopy
# --------------------------------------------------------------------------


def ingest_bhavcopy(session: Session, on: Optional[date] = None,
                    exchange: str = "NSE") -> IngestionRun:
    started = time.perf_counter()
    provider_name = "nse_archives" if exchange == "NSE" else "bse_archives"
    provider = registry.get(provider_name)
    if provider is None:
        return _record_run(session, f"bhavcopy_{exchange}", on, "FAILED",
                           message=f"{provider_name} is not registered")
    try:
        env = provider.get_bhavcopy(on=on)
    except ProviderError as exc:
        return _record_run(session, f"bhavcopy_{exchange}", on, "FAILED",
                           provider=provider_name, message=str(exc),
                           duration_ms=(time.perf_counter() - started) * 1000)

    rows = env.value or []
    if not rows:
        return _record_run(session, f"bhavcopy_{exchange}", on, "EMPTY",
                           provider=provider_name,
                           message="provider returned no rows")

    session_date = _as_date(rows[0].get("session_date")) or on or date.today()
    existing = {
        (r.symbol, r.exchange): r
        for r in session.scalars(
            select(EodBar).where(EodBar.session_date == session_date,
                                 EodBar.exchange == exchange)
        )
    }
    prov = _provenance(env)
    written = 0
    for row in rows:
        symbol = (row.get("symbol") or "").upper()
        if not symbol:
            continue
        close, prev = row.get("close"), row.get("previous_close")
        change_pct = (
            round((close - prev) / prev * 100, 4)
            if close is not None and prev else None
        )
        target = existing.get((symbol, exchange))
        values = dict(
            symbol=symbol, exchange=exchange, series=row.get("series"),
            isin=row.get("isin"), session_date=session_date,
            open=row.get("open"), high=row.get("high"), low=row.get("low"),
            close=close, previous_close=prev, vwap=row.get("vwap"),
            volume=row.get("volume"), turnover=row.get("turnover"),
            trades=row.get("trades"),
            settlement_price=row.get("settlement_price"),
            change_pct=change_pct, **prov,
        )
        if target is None:
            session.add(EodBar(**values))
        else:
            for key, value in values.items():
                setattr(target, key, value)
        written += 1

    return _record_run(
        session, f"bhavcopy_{exchange}", session_date, "OK",
        rows_seen=len(rows), rows_written=written, provider=provider_name,
        duration_ms=(time.perf_counter() - started) * 1000,
    )


# --------------------------------------------------------------------------
# delivery
# --------------------------------------------------------------------------


def ingest_delivery(session: Session, on: Optional[date] = None) -> IngestionRun:
    started = time.perf_counter()
    provider = registry.get("nse_archives")
    if provider is None:
        return _record_run(session, "delivery", on, "FAILED",
                           message="nse_archives is not registered")
    try:
        env = provider.get_delivery(on=on)
    except ProviderError as exc:
        return _record_run(session, "delivery", on, "FAILED",
                           provider="nse_archives", message=str(exc),
                           duration_ms=(time.perf_counter() - started) * 1000)

    rows = env.value or []
    if not rows:
        return _record_run(session, "delivery", on, "EMPTY",
                           provider="nse_archives",
                           message="provider returned no rows")

    session_date = _as_date(rows[0].get("session_date")) or on or date.today()
    existing = {
        (r.symbol, r.series): r
        for r in session.scalars(
            select(DeliveryRecord).where(
                DeliveryRecord.session_date == session_date)
        )
    }
    prov = _provenance(env)
    written = 0
    for row in rows:
        symbol = (row.get("symbol") or "").upper()
        series = (row.get("series") or "EQ").upper()
        if not symbol:
            continue
        values = dict(
            symbol=symbol, series=series, session_date=session_date,
            traded_quantity=row.get("traded_quantity"),
            deliverable_quantity=row.get("deliverable_quantity"),
            delivery_pct=row.get("delivery_pct"), **prov,
        )
        target = existing.get((symbol, series))
        if target is None:
            session.add(DeliveryRecord(**values))
        else:
            for key, value in values.items():
                setattr(target, key, value)
        written += 1

    return _record_run(
        session, "delivery", session_date, "OK", rows_seen=len(rows),
        rows_written=written, provider="nse_archives",
        duration_ms=(time.perf_counter() - started) * 1000,
    )


# --------------------------------------------------------------------------
# deals
# --------------------------------------------------------------------------


def ingest_deals(session: Session, kind: str = "bulk",
                 on: Optional[date] = None) -> IngestionRun:
    started = time.perf_counter()
    provider = registry.get("nse_archives")
    dataset = f"{kind}_deals"
    if provider is None:
        return _record_run(session, dataset, on, "FAILED",
                           message="nse_archives is not registered")
    method = getattr(provider, f"get_{kind}_deals")
    try:
        env = method(on=on)
    except ProviderError as exc:
        # An empty deal register is a legitimate market outcome, not a fault.
        status = "EMPTY" if "empty" in str(exc).lower() or "no " in str(exc).lower() \
            else "FAILED"
        return _record_run(session, dataset, on, status, provider="nse_archives",
                           message=str(exc),
                           duration_ms=(time.perf_counter() - started) * 1000)

    rows = env.value or []
    if not rows:
        return _record_run(session, dataset, on, "EMPTY", provider="nse_archives",
                           message="no deals reported")

    session_date = _as_date(rows[0].get("date")) or on or date.today()
    # Deals have no natural key, so a re-run replaces the day wholesale.
    for stale in session.scalars(
        select(DealRecord).where(DealRecord.session_date == session_date,
                                 DealRecord.deal_type == kind.upper())
    ):
        session.delete(stale)

    prov = _provenance(env)
    written = 0
    for row in rows:
        symbol = (row.get("symbol") or "").upper()
        if not symbol:
            continue
        session.add(DealRecord(
            symbol=symbol, security_name=row.get("security_name"),
            session_date=_as_date(row.get("date")) or session_date,
            deal_type=kind.upper(), client_name=row.get("client_name"),
            buy_sell=row.get("buy_sell"), quantity=row.get("quantity"),
            price=row.get("price"), value=row.get("value"),
            remarks=row.get("remarks"), **prov,
        ))
        written += 1

    return _record_run(
        session, dataset, session_date, "OK", rows_seen=len(rows),
        rows_written=written, provider="nse_archives",
        duration_ms=(time.perf_counter() - started) * 1000,
    )


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def ingest_session(session: Session, on: Optional[date] = None) -> List[IngestionRun]:
    """Ingest every EOD dataset for one session."""
    runs = [
        ingest_bhavcopy(session, on, exchange="NSE"),
        ingest_bhavcopy(session, on, exchange="BSE"),
        ingest_delivery(session, on),
        ingest_deals(session, "bulk", on),
        ingest_deals(session, "block", on),
    ]
    session.commit()
    return runs


def backfill(session: Session, days: int = 30,
             end: Optional[date] = None) -> List[IngestionRun]:
    """Walk backwards over trading days, ingesting each.

    Exchanges keep these files for a limited window, so a backfill run today
    cannot reach back years - it collects what is still published. The delivery
    percentile needs about 20 sessions before it says anything, so a month is
    the practical minimum worth running.
    """
    runs: List[IngestionRun] = []
    day = end or datetime.now(tz=IST).date()
    collected = 0
    while collected < days:
        if is_trading_day(day):
            runs.extend(ingest_session(session, day))
            collected += 1
        day -= timedelta(days=1)
    return runs


def delivery_history(session: Session, symbol: str, series: str = "EQ",
                     limit: int = 250) -> List[float]:
    """Stored delivery percentages for one symbol, oldest first."""
    rows = session.scalars(
        select(DeliveryRecord)
        .where(DeliveryRecord.symbol == symbol.upper(),
               DeliveryRecord.series == series.upper(),
               DeliveryRecord.delivery_pct.is_not(None))
        .order_by(DeliveryRecord.session_date.desc())
        .limit(limit)
    ).all()
    return [r.delivery_pct for r in reversed(rows)]
