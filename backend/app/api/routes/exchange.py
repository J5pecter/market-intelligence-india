"""Official exchange record: EOD bars, delivery, deals, breadth, macro.

Everything served here comes from a published exchange or regulator file. The
routes deliberately expose the *session date* of what they return rather than
implying "now": an end-of-day record is authoritative and stale at the same
time, and hiding which session it belongs to is how a research tool starts
lying by omission.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api.deps import db_session, rate_limit, require_admin
from app.core.config import settings
from app.core.data_quality import Sourced
from app.models.exchange_data import (DealRecord, DeliveryRecord, EodBar,
                                      IngestionRun)
from app.models.user import User
from app.providers.base import ProviderError
from app.providers.registry import registry
from app.services.eod_ingest import backfill, delivery_history, ingest_session
from app.services.market_flows import (analyse_delivery, deal_flow,
                                       delivery_evidence, market_breadth)
from app.services.reconciliation import (reconciliation_evidence,
                                         verify_close_against_exchange,
                                         verify_quote)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["exchange"])

# rate_limit is a factory; one shared bucket for every route in this module.
_limit = rate_limit("exchange", 240)


def _envelope(env: Sourced[Any]) -> Dict[str, Any]:
    """Provenance block attached to every response in this module."""
    return {
        "provider": env.provider,
        "source": env.source_name,
        "source_url": env.source_url,
        "status": env.status.value,
        "observed_at": env.observed_at.isoformat() if env.observed_at else None,
        "retrieved_at": env.retrieved_at.isoformat(),
        "age_seconds": env.age_seconds,
        "reliability": env.reliability.value,
        "licence": env.license_note,
        "notes": env.notes,
        "is_demo": env.is_demo,
    }


# --------------------------------------------------------------------------
# breadth
# --------------------------------------------------------------------------


@router.get("/exchange/breadth")
def breadth(
    on: Optional[date] = Query(None, description="Session date; defaults to the latest published"),
    _: None = Depends(_limit),
) -> Dict[str, Any]:
    """Advance/decline and the distribution of moves, from the bhavcopy."""
    try:
        stats, env = market_breadth(on)
    except ProviderError as exc:
        raise HTTPException(503, f"Exchange breadth unavailable: {exc}") from exc
    if not stats:
        raise HTTPException(
            503,
            "No bhavcopy is available for that session. The exchange publishes "
            "it after the close; try the previous trading day.",
        )
    return {
        "breadth": stats,
        "provenance": _envelope(env),
        "disclaimer": (
            "Breadth describes the session that has already settled. It is not "
            "a forecast and carries no probability about the next session."
        ),
    }


# --------------------------------------------------------------------------
# delivery
# --------------------------------------------------------------------------


@router.get("/exchange/delivery/{symbol}")
def delivery_for_symbol(
    symbol: str,
    on: Optional[date] = None,
    db: Session = Depends(db_session),
    _: None = Depends(_limit),
) -> Dict[str, Any]:
    """One symbol's delivery percentage against its own stored history."""
    history = delivery_history(db, symbol)
    try:
        reading, env = analyse_delivery(symbol, on, history=history)
    except ProviderError as exc:
        raise HTTPException(503, f"Delivery data unavailable: {exc}") from exc
    if reading is None:
        raise HTTPException(
            404,
            f"The exchange delivery report for that session does not list "
            f"{symbol.upper()}. Illiquid scrips and non-EQ series are often absent.",
        )
    chain = delivery_evidence(reading, env)
    return {
        "symbol": symbol.upper(),
        "delivery": reading.to_dict(),
        "evidence_chain": chain.to_dict(),
        "provenance": _envelope(env),
        "history_sessions_stored": len(history),
    }


@router.get("/exchange/delivery")
def delivery_leaders(
    on: Optional[date] = None,
    min_pct: float = Query(0.0, ge=0, le=100),
    min_turnover: float = Query(0.0, ge=0,
                                description="Filter out illiquid scrips"),
    limit: int = Query(50, ge=1, le=500),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    db: Session = Depends(db_session),
    _: None = Depends(_limit),
) -> Dict[str, Any]:
    """Rank the market by delivery percentage for one session.

    Joined against the bhavcopy so a turnover floor can exclude the illiquid
    counters that otherwise dominate any delivery ranking - a scrip that traded
    200 shares, all delivered, is 100% delivery and means nothing.
    """
    latest = db.scalar(select(func.max(DeliveryRecord.session_date)))
    session_date = on or latest
    if session_date is None:
        raise HTTPException(
            503,
            "No delivery data has been ingested yet. Run the EOD ingestion "
            "job (POST /api/exchange/ingest) to populate it.",
        )

    stmt = (
        select(DeliveryRecord, EodBar.turnover, EodBar.close, EodBar.change_pct)
        .join(
            EodBar,
            (EodBar.symbol == DeliveryRecord.symbol)
            & (EodBar.session_date == DeliveryRecord.session_date)
            & (EodBar.exchange == "NSE"),
            isouter=True,
        )
        .where(DeliveryRecord.session_date == session_date,
               DeliveryRecord.series == "EQ",
               DeliveryRecord.delivery_pct.is_not(None),
               DeliveryRecord.delivery_pct >= min_pct)
    )
    if min_turnover > 0:
        stmt = stmt.where(EodBar.turnover >= min_turnover)
    stmt = stmt.order_by(
        DeliveryRecord.delivery_pct.asc() if order == "asc"
        else DeliveryRecord.delivery_pct.desc()
    ).limit(limit)

    rows = db.execute(stmt).all()
    return {
        "session_date": session_date.isoformat(),
        "count": len(rows),
        "filters": {"min_pct": min_pct, "min_turnover": min_turnover,
                    "order": order},
        "rows": [
            {
                "symbol": rec.symbol,
                "delivery_pct": rec.delivery_pct,
                "traded_quantity": rec.traded_quantity,
                "deliverable_quantity": rec.deliverable_quantity,
                "close": close,
                "change_pct": change_pct,
                "turnover": turnover,
                "provider": rec.provider,
                "data_status": rec.data_status,
            }
            for rec, turnover, close, change_pct in rows
        ],
        "note": (
            "Delivery percentage is settlement, not intent, and it is only "
            "interpretable against a stock's own history - sector norms differ "
            "enormously. Use /exchange/delivery/{symbol} for that comparison."
        ),
    }


# --------------------------------------------------------------------------
# deals
# --------------------------------------------------------------------------


@router.get("/exchange/deals")
def deals(
    kind: str = Query("bulk", pattern="^(bulk|block)$"),
    on: Optional[date] = None,
    symbol: Optional[str] = None,
    _: None = Depends(_limit),
) -> Dict[str, Any]:
    """Bulk or block deals, netted per symbol."""
    try:
        flows, env = deal_flow(on, kind=kind)
    except ProviderError as exc:
        raise HTTPException(503, f"{kind} deals unavailable: {exc}") from exc
    if symbol:
        flows = [f for f in flows if f["symbol"] == symbol.upper()]
    return {
        "kind": kind.upper(),
        "count": len(flows),
        "flows": flows,
        "provenance": _envelope(env),
        "note": (
            "Both legs of every deal are disclosed, so gross quantity "
            "double-counts; only the net figure per symbol is meaningful. A "
            "disclosed deal is a transaction that happened, not a "
            "recommendation by the party that made it."
        ),
    }


# --------------------------------------------------------------------------
# EOD bars
# --------------------------------------------------------------------------


@router.get("/exchange/eod/{symbol}")
def eod_series(
    symbol: str,
    exchange: str = Query("NSE", pattern="^(NSE|BSE)$"),
    days: int = Query(90, ge=1, le=2000),
    db: Session = Depends(db_session),
    _: None = Depends(_limit),
) -> Dict[str, Any]:
    """The exchange's own settled series for one symbol, from local storage."""
    rows = db.scalars(
        select(EodBar)
        .where(EodBar.symbol == symbol.upper(), EodBar.exchange == exchange)
        .order_by(EodBar.session_date.desc())
        .limit(days)
    ).all()
    if not rows:
        raise HTTPException(
            404,
            f"No stored sessions for {symbol.upper()} on {exchange}. This "
            "endpoint reads the local archive; run the ingestion job to fill it.",
        )
    rows = list(reversed(rows))
    return {
        "symbol": symbol.upper(),
        "exchange": exchange,
        "sessions": len(rows),
        "first_session": rows[0].session_date.isoformat(),
        "last_session": rows[-1].session_date.isoformat(),
        "bars": [
            {
                "date": r.session_date.isoformat(),
                "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                "previous_close": r.previous_close, "vwap": r.vwap,
                "volume": r.volume, "turnover": r.turnover, "trades": r.trades,
                "change_pct": r.change_pct,
            }
            for r in rows
        ],
        "provenance": {
            "provider": rows[-1].provider,
            "source": rows[-1].source_name,
            "data_status": rows[-1].data_status,
            "is_demo": rows[-1].is_demo,
        },
        "note": (
            "Settled exchange closes, NOT adjusted for splits or bonuses. "
            "Do not compute long-horizon returns from this series without "
            "applying corporate actions first."
        ),
    }


# --------------------------------------------------------------------------
# reconciliation
# --------------------------------------------------------------------------


@router.get("/exchange/verify/{symbol}")
def verify_symbol(
    symbol: str,
    exchange: str = Query("NSE", pattern="^(NSE|BSE)$"),
    _: None = Depends(_limit),
) -> Dict[str, Any]:
    """Cross-check one symbol's price fields across every available source.

    This is the endpoint to hit before trusting a number in your own research:
    it asks every source independently instead of stopping at the first that
    answers, and it publishes a consensus only when they actually agree.
    """
    recs = verify_quote(symbol, exchange=exchange)
    close_check = verify_close_against_exchange(symbol)
    recs_with_close = {**recs, "close_vs_exchange": close_check}
    chain = reconciliation_evidence(recs_with_close)
    disputed = [k for k, r in recs_with_close.items() if not r.is_trustworthy]
    return {
        "symbol": symbol.upper(),
        "exchange": exchange,
        "checks": {k: r.to_dict() for k, r in recs_with_close.items()},
        "evidence_chain": chain.to_dict(),
        "fields_confirmed": [k for k, r in recs_with_close.items() if r.is_trustworthy],
        "fields_needing_attention": disputed,
        "verdict": (
            "Every field is corroborated by two or more independent sources."
            if not disputed else
            f"{len(disputed)} field(s) are single-sourced or disputed: "
            f"{', '.join(disputed)}. Check these before using them."
        ),
    }


# --------------------------------------------------------------------------
# macro
# --------------------------------------------------------------------------


@router.get("/macro/rates")
def policy_rates(_: None = Depends(_limit)) -> Dict[str, Any]:
    """RBI's current policy corridor."""
    env = registry.fetch("policy_rates")
    if not env.is_usable:
        raise HTTPException(503, f"Policy rates unavailable: {env.notes}")
    return {"rates": env.value, "provenance": _envelope(env)}


@router.get("/macro/series/{indicator}")
def macro_series(indicator: str, _: None = Depends(_limit)) -> Dict[str, Any]:
    """One World Bank macro series for India."""
    from app.providers.reference import WorldBankProvider

    env = registry.fetch("macro_series", indicator)
    if not env.is_usable:
        raise HTTPException(
            404,
            f"No series '{indicator}'. Known keys: "
            f"{', '.join(sorted(WorldBankProvider.INDICATORS))}",
        )
    return {
        "indicator": indicator,
        "available_indicators": sorted(WorldBankProvider.INDICATORS),
        "series": env.value,
        "provenance": _envelope(env),
    }


@router.get("/macro/funds")
def fund_navs(
    q: Optional[str] = Query(None, min_length=2,
                             description="Filter on scheme name"),
    limit: int = Query(100, ge=1, le=1000),
    _: None = Depends(_limit),
) -> Dict[str, Any]:
    """AMFI mutual fund NAVs."""
    env = registry.fetch("fund_navs")
    if not env.is_usable:
        raise HTTPException(503, f"AMFI NAVs unavailable: {env.notes}")
    rows = env.value or []
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in (r.get("scheme_name") or "").lower()]
    return {
        "count": len(rows),
        "schemes": rows[:limit],
        "truncated": len(rows) > limit,
        "provenance": _envelope(env),
    }


# --------------------------------------------------------------------------
# ingestion status
# --------------------------------------------------------------------------


@router.get("/exchange/ingest/status")
def ingest_status(
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(db_session),
    _: None = Depends(_limit),
) -> Dict[str, Any]:
    """Recent ingestion runs.

    Exposed because a gap in the stored history has two very different causes -
    the exchange published nothing, or the job did not run - and only this
    audit trail distinguishes them.
    """
    runs = db.scalars(
        select(IngestionRun).order_by(desc(IngestionRun.created_at)).limit(limit)
    ).all()
    coverage = db.execute(
        select(func.min(EodBar.session_date), func.max(EodBar.session_date),
               func.count(func.distinct(EodBar.session_date)))
    ).one()
    delivery_sessions = db.scalar(
        select(func.count(func.distinct(DeliveryRecord.session_date)))
    ) or 0
    return {
        "coverage": {
            "eod_first_session": coverage[0].isoformat() if coverage[0] else None,
            "eod_last_session": coverage[1].isoformat() if coverage[1] else None,
            "eod_sessions_stored": coverage[2] or 0,
            "delivery_sessions_stored": delivery_sessions,
            "delivery_percentile_ready": delivery_sessions >= 20,
        },
        "runs": [
            {
                "dataset": r.dataset,
                "session_date": r.session_date.isoformat() if r.session_date else None,
                "status": r.status,
                "rows_seen": r.rows_seen,
                "rows_written": r.rows_written,
                "provider": r.provider,
                "duration_ms": round(r.duration_ms, 1) if r.duration_ms else None,
                "message": r.message,
                "at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in runs
        ],
        "note": (
            "A delivery percentile needs at least 20 stored sessions before it "
            "means anything; until then the delivery endpoint reports UNKNOWN "
            "rather than guessing."
        ),
    }


@router.post("/exchange/ingest")
def run_ingestion(
    on: Optional[date] = Query(None, description="Session to ingest; latest if omitted"),
    days: int = Query(1, ge=1, le=90,
                      description="Backfill this many trading days ending at `on`"),
    db: Session = Depends(db_session),
    admin: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Pull the exchange's published files into local storage.

    Admin-only because it makes a burst of outbound requests to the exchange;
    the rate limiter in the archive adapter still applies, so a large backfill
    takes minutes rather than seconds. It is idempotent - re-ingesting a stored
    session corrects those rows instead of duplicating them.
    """
    runs = (backfill(db, days=days, end=on) if days > 1
            else ingest_session(db, on))
    summary: Dict[str, Dict[str, int]] = {}
    for r in runs:
        bucket = summary.setdefault(r.dataset, {"OK": 0, "EMPTY": 0, "FAILED": 0,
                                                "rows": 0})
        bucket[r.status] = bucket.get(r.status, 0) + 1
        bucket["rows"] += r.rows_written
    return {
        "requested_by": admin.email,
        "sessions_requested": days,
        "runs": len(runs),
        "summary": summary,
        "detail": [
            {"dataset": r.dataset,
             "session_date": r.session_date.isoformat() if r.session_date else None,
             "status": r.status, "rows_written": r.rows_written,
             "message": r.message}
            for r in runs
        ],
    }
