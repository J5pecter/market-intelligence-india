"""Market overview, indices, search, calendar."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import db_session, rate_limit
from app.core.config import settings
from app.db.filters import hide_demo
from app.core.data_quality import Sourced, data_quality_score
from app.core.market_calendar import IST, market_state, now_ist
from app.models.fundamental import CorporateAction, EarningsEvent
from app.models.instrument import Instrument, MarketHoliday
from app.models.market import (FlowSnapshot, IndexSnapshot, Quote,
                               SectorPerformance, TechnicalIndicatorSnapshot)
from app.models.news import NewsArticle, NewsScore
from app.models.research import Catalyst, ResearchCall
from app.providers.registry import registry

router = APIRouter(tags=["market"])

DEFAULT_INDICES = ["NIFTY 50", "NIFTY BANK", "SENSEX", "INDIA VIX",
                   "NIFTY FIN SERVICE", "NIFTY MIDCAP 100"]


def _holidays(db: Session) -> List[date]:
    return list(db.execute(select(MarketHoliday.holiday_date)).scalars().all())


@router.get("/market/status")
def market_status(db: Session = Depends(db_session)) -> Dict[str, Any]:
    state = market_state(holidays=_holidays(db))
    return {
        "status": state.status.value,
        "as_of": state.as_of.isoformat(),
        "timezone": state.timezone,
        "current_time_ist": now_ist().strftime("%d %b %Y, %I:%M %p"),
        "next_open": state.next_open.isoformat() if state.next_open else None,
        "next_close": state.next_close.isoformat() if state.next_close else None,
        "holiday_source": state.holiday_source,
        "note": state.note,
        "app_env": settings.app_env.value,
    }


@router.get("/market/overview", dependencies=[Depends(rate_limit("overview", 120))])
def market_overview(db: Session = Depends(db_session)) -> Dict[str, Any]:
    """The dashboard payload. Every block reports its own availability."""
    envelopes: List[Sourced[Any]] = []
    state = market_state(holidays=_holidays(db))

    indices = _indices(db, envelopes)
    movers = _movers(db)
    breadth = _breadth(db, movers)
    sectors = _sectors(db)
    flows = _flows(db)
    events = _today_events(db)
    news = _top_news(db)
    calls = _active_calls(db)
    derivatives = _derivatives_summary(db)
    ipos = _ipo_summary(db)

    return {
        "market_status": {
            "status": state.status.value,
            "current_time_ist": now_ist().strftime("%d %b %Y, %I:%M %p"),
            "next_open": state.next_open.isoformat() if state.next_open else None,
            "holiday_source": state.holiday_source,
        },
        "indices": indices,
        "breadth": breadth,
        "top_gainers": movers["gainers"],
        "top_losers": movers["losers"],
        "volume_shockers": movers["volume"],
        "breakouts": movers["breakouts"],
        "breakdowns": movers["breakdowns"],
        "new_52w_highs": movers["highs"],
        "new_52w_lows": movers["lows"],
        "sector_performance": sectors,
        "flows": flows,
        "derivatives": derivatives,
        "ipo": ipos,
        "news": news,
        "today": events,
        "active_research": calls,
        "data_quality": {
            "score": data_quality_score(envelopes) if envelopes else None,
            "app_env": settings.app_env.value,
            "demo_data_visible": settings.demo_data_allowed,
        },
    }


def _indices(db: Session, envelopes: List[Sourced[Any]]) -> Dict[str, Any]:
    env = registry.fetch("indices", db=db)
    if env.is_usable and env.value:
        envelopes.append(env)
        wanted = {name.upper() for name in DEFAULT_INDICES}
        rows = [
            {
                "symbol": q.symbol, "ltp": q.ltp, "change": q.change,
                "change_pct": q.change_pct, "open": q.open, "high": q.high,
                "low": q.low, "previous_close": q.previous_close,
                "week52_high": q.week52_high, "week52_low": q.week52_low,
            }
            for q in env.value
        ]
        preferred = [r for r in rows if r["symbol"].upper() in wanted]
        return {
            "available": True,
            "rows": preferred or rows[:12],
            "all_count": len(rows),
            "provenance": env.to_dict(),
        }

    stored = db.execute(
        hide_demo(select(IndexSnapshot), IndexSnapshot)
        .order_by(IndexSnapshot.as_of.desc()).limit(12)
    ).scalars().all()
    if stored:
        return {
            "available": True,
            "rows": [
                {"symbol": s.index_symbol, "ltp": s.ltp,
                 "change_pct": s.change_pct, "pcr": s.pcr,
                 "max_pain": s.max_pain, "regime": s.regime,
                 "is_demo": s.is_demo}
                for s in stored
            ],
            "provenance": {"source": "stored index snapshots",
                           "status": "STALE" if stored else "UNAVAILABLE"},
            "note": "Live index feed unavailable; showing the last stored snapshot.",
        }
    return {"available": False,
            "reason": env.notes or "No index provider returned data.",
            "provenance": env.to_dict()}


def _movers(db: Session) -> Dict[str, List[Dict[str, Any]]]:
    base = hide_demo(
        select(Quote, Instrument.name, Instrument.sector)
        .join(Instrument, Instrument.id == Quote.instrument_id)
        .where(Instrument.segment == "EQUITY"),
        Quote,
    )

    def _rows(stmt, extra=None) -> List[Dict[str, Any]]:
        out = []
        for quote, name, sector in db.execute(stmt).all():
            row = {
                "symbol": quote.symbol, "name": name, "sector": sector,
                "ltp": quote.ltp, "change": quote.change,
                "change_pct": quote.change_pct, "volume": quote.volume,
                "data_status": quote.data_status, "is_demo": quote.is_demo,
                "observed_at": quote.observed_at.isoformat()
                if quote.observed_at else None,
            }
            if extra:
                row.update(extra(quote))
            out.append(row)
        return out

    gainers = _rows(
        base.where(Quote.change_pct.isnot(None))
        .order_by(Quote.change_pct.desc()).limit(10)
    )
    losers = _rows(
        base.where(Quote.change_pct.isnot(None))
        .order_by(Quote.change_pct.asc()).limit(10)
    )
    volume = _rows(
        base.where(Quote.average_volume_20d.isnot(None))
        .where(Quote.volume.isnot(None))
        .where(Quote.volume > Quote.average_volume_20d * 2)
        .order_by((Quote.volume / func.nullif(Quote.average_volume_20d, 0)).desc())
        .limit(10),
        extra=lambda q: {
            "volume_ratio": round(q.volume / q.average_volume_20d, 2)
            if q.average_volume_20d else None
        },
    )
    highs = _rows(
        base.where(Quote.week52_high.isnot(None))
        .where(Quote.ltp >= Quote.week52_high * 0.999).limit(15)
    )
    lows = _rows(
        base.where(Quote.week52_low.isnot(None))
        .where(Quote.ltp <= Quote.week52_low * 1.001).limit(15)
    )

    latest_snapshot = db.execute(
        select(func.max(TechnicalIndicatorSnapshot.as_of))
    ).scalar_one_or_none()

    breakouts: List[Dict[str, Any]] = []
    breakdowns: List[Dict[str, Any]] = []
    if latest_snapshot:
        tech_base = (
            select(TechnicalIndicatorSnapshot, Instrument.name)
            .join(Instrument,
                  Instrument.id == TechnicalIndicatorSnapshot.instrument_id)
            .where(TechnicalIndicatorSnapshot.as_of == latest_snapshot)
        )
        for row, name in db.execute(
            tech_base.where(TechnicalIndicatorSnapshot.close >
                            TechnicalIndicatorSnapshot.bb_upper)
            .where(TechnicalIndicatorSnapshot.volume_ratio_20d > 1.3)
            .limit(10)
        ).all():
            breakouts.append({
                "symbol": row.symbol, "name": name, "close": row.close,
                "bb_upper": row.bb_upper, "rsi_14": row.rsi_14,
                "volume_ratio": row.volume_ratio_20d,
                "reason": "Closed above the upper Bollinger band on "
                          "above-average volume.",
                "is_demo": row.is_demo,
            })
        for row, name in db.execute(
            tech_base.where(TechnicalIndicatorSnapshot.close <
                            TechnicalIndicatorSnapshot.bb_lower)
            .where(TechnicalIndicatorSnapshot.volume_ratio_20d > 1.3)
            .limit(10)
        ).all():
            breakdowns.append({
                "symbol": row.symbol, "name": name, "close": row.close,
                "bb_lower": row.bb_lower, "rsi_14": row.rsi_14,
                "volume_ratio": row.volume_ratio_20d,
                "reason": "Closed below the lower Bollinger band on "
                          "above-average volume.",
                "is_demo": row.is_demo,
            })

    return {"gainers": gainers, "losers": losers, "volume": volume,
            "highs": highs, "lows": lows, "breakouts": breakouts,
            "breakdowns": breakdowns}


def _breadth(db: Session, movers: Dict[str, Any]) -> Dict[str, Any]:
    total = db.execute(
        select(func.count(Quote.id))
        .join(Instrument, Instrument.id == Quote.instrument_id)
        .where(Instrument.segment == "EQUITY")
        .where(Quote.change_pct.isnot(None))
    ).scalar_one()
    if not total:
        return {"available": False,
                "reason": "No quotes are stored yet - run the quote refresh job."}

    advances = db.execute(
        select(func.count(Quote.id))
        .join(Instrument, Instrument.id == Quote.instrument_id)
        .where(Instrument.segment == "EQUITY").where(Quote.change_pct > 0)
    ).scalar_one()
    declines = db.execute(
        select(func.count(Quote.id))
        .join(Instrument, Instrument.id == Quote.instrument_id)
        .where(Instrument.segment == "EQUITY").where(Quote.change_pct < 0)
    ).scalar_one()

    return {
        "available": True,
        "advances": advances,
        "declines": declines,
        "unchanged": total - advances - declines,
        "universe": total,
        "advance_decline_ratio": round(advances / declines, 2) if declines else None,
        "new_52w_highs": len(movers["highs"]),
        "new_52w_lows": len(movers["lows"]),
        "note": f"Breadth covers the {total} instruments with a stored quote, "
                f"not the full exchange universe.",
    }


def _sectors(db: Session) -> Dict[str, Any]:
    latest = db.execute(
        select(func.max(SectorPerformance.as_of))
    ).scalar_one_or_none()
    if latest is None:
        rows = db.execute(
            select(Instrument.sector,
                   func.avg(Quote.change_pct),
                   func.count(Quote.id))
            .join(Quote, Quote.instrument_id == Instrument.id)
            .where(Instrument.sector.isnot(None))
            .where(Quote.change_pct.isnot(None))
            .group_by(Instrument.sector)
        ).all()
        if not rows:
            return {"available": False,
                    "reason": "No sector data has been computed yet."}
        return {
            "available": True,
            "computed_live": True,
            "rows": sorted(
                [{"sector": s, "change_pct": round(float(avg), 2),
                  "constituents": count} for s, avg, count in rows],
                key=lambda r: r["change_pct"], reverse=True,
            ),
            "note": "Simple average of stored quote changes per sector - not "
                    "market-cap weighted.",
        }

    rows = db.execute(
        select(SectorPerformance).where(SectorPerformance.as_of == latest)
    ).scalars().all()
    return {
        "available": True,
        "as_of": latest.isoformat(),
        "rows": sorted(
            [{"sector": r.sector, "change_pct": r.change_pct,
              "advancing": r.advancing, "declining": r.declining,
              "constituents": r.constituents, "is_demo": r.is_demo}
             for r in rows],
            key=lambda r: (r["change_pct"] or 0), reverse=True,
        ),
    }


def _flows(db: Session) -> Dict[str, Any]:
    row = db.execute(
        select(FlowSnapshot).order_by(FlowSnapshot.as_of.desc()).limit(1)
    ).scalars().first()
    if row is None:
        return {
            "available": False,
            "reason": "No FII/DII flow data is configured. These figures are "
                      "published by the exchanges and are not available from "
                      "the free providers wired up by default.",
        }
    return {
        "available": True,
        "as_of": row.as_of.isoformat(),
        "fii_net": row.fii_net, "dii_net": row.dii_net,
        "fii_buy": row.fii_buy, "fii_sell": row.fii_sell,
        "dii_buy": row.dii_buy, "dii_sell": row.dii_sell,
        "segment": row.segment, "source": row.source_name,
        "is_demo": row.is_demo,
    }


def _today_events(db: Session) -> Dict[str, Any]:
    today = date.today()
    horizon = today + timedelta(days=7)

    actions = db.execute(
        select(CorporateAction)
        .where(CorporateAction.ex_date >= today)
        .where(CorporateAction.ex_date <= horizon)
        .order_by(CorporateAction.ex_date).limit(20)
    ).scalars().all()

    results = db.execute(
        select(EarningsEvent)
        .where(EarningsEvent.expected_date >= today)
        .where(EarningsEvent.expected_date <= horizon)
        .order_by(EarningsEvent.expected_date).limit(20)
    ).scalars().all()

    catalysts = db.execute(
        select(Catalyst)
        .where(Catalyst.event_date >= today)
        .where(Catalyst.event_date <= horizon)
        .order_by(Catalyst.event_date).limit(20)
    ).scalars().all()

    return {
        "corporate_actions": [
            {"symbol": a.symbol, "type": a.action_type,
             "description": a.description,
             "ex_date": a.ex_date.isoformat() if a.ex_date else None,
             "record_date": a.record_date.isoformat() if a.record_date else None,
             "value": a.value, "is_demo": a.is_demo}
            for a in actions
        ],
        "results": [
            {"symbol": r.symbol, "quarter": r.quarter_label,
             "expected_date": r.expected_date.isoformat()
             if r.expected_date else None,
             "status": r.status, "is_demo": r.is_demo}
            for r in results
        ],
        "catalysts": [
            {"symbol": c.symbol, "title": c.title, "category": c.category,
             "event_date": c.event_date.isoformat() if c.event_date else None,
             "expected_impact": c.expected_impact, "risk_level": c.risk_level,
             "is_demo": c.is_demo}
            for c in catalysts
        ],
        "high_risk_events": [
            {"symbol": c.symbol, "title": c.title,
             "event_date": c.event_date.isoformat() if c.event_date else None,
             "risk_level": c.risk_level}
            for c in catalysts if (c.risk_level or "").upper() in ("HIGH", "VERY_HIGH")
        ],
    }


def _top_news(db: Session) -> List[Dict[str, Any]]:
    rows = db.execute(
        select(NewsArticle, NewsScore)
        .outerjoin(NewsScore, NewsScore.article_id == NewsArticle.id)
        .where(NewsArticle.is_suppressed.is_(False))
        .order_by(NewsArticle.published_at.desc())
        .limit(15)
    ).all()
    return [
        {
            "id": article.id, "headline": article.headline,
            "publisher": article.publisher, "url": article.url,
            "published_at": article.published_at.isoformat()
            if article.published_at else None,
            "symbol": article.primary_symbol,
            "category": article.event_category,
            "sentiment": score.sentiment if score else None,
            "impact_score": score.impact_score if score else None,
            "is_demo": article.is_demo,
        }
        for article, score in rows
    ]


def _active_calls(db: Session) -> List[Dict[str, Any]]:
    rows = db.execute(
        hide_demo(select(ResearchCall), ResearchCall)
        .where(ResearchCall.is_published.is_(True))
        .where(ResearchCall.status.notin_(
            ["EXPIRED", "INVALIDATED", "TARGET_ACHIEVED", "STOP_LOSS_TRIGGERED"]
        ))
        .order_by(ResearchCall.updated_at.desc())
        .limit(12)
    ).scalars().all()
    return [
        {
            "id": c.id, "symbol": c.symbol, "company": c.company_name,
            "segment": c.segment, "side": c.side, "status": c.status,
            "source_type": c.source_type, "source": c.source_name,
            "ltp": c.reference_price, "entry_min": c.entry_min,
            "entry_max": c.entry_max, "stop_loss": c.stop_loss,
            "target": c.target_1, "achieved_pct": c.achieved_pct,
            "potential_pct": c.potential_pct, "risk_reward": c.risk_reward,
            "confidence": c.confidence, "risk_rating": c.risk_rating,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            "is_demo": c.is_demo,
        }
        for c in rows
    ]


def _derivatives_summary(db: Session) -> Dict[str, Any]:
    from app.models.derivatives import FuturesSnapshot, OptionChainSnapshot

    chains = db.execute(
        select(OptionChainSnapshot)
        .order_by(OptionChainSnapshot.captured_at.desc()).limit(6)
    ).scalars().all()
    futures = db.execute(
        select(FuturesSnapshot)
        .order_by(FuturesSnapshot.captured_at.desc()).limit(6)
    ).scalars().all()

    if not chains and not futures:
        return {
            "available": False,
            "reason": (
                "No derivatives snapshots are stored. The NSE adapter is off by "
                "default; enable it after reviewing NSE's terms, or configure a "
                "licensed provider."
            ),
        }
    return {
        "available": True,
        "options": [
            {"underlying": c.underlying_symbol,
             "expiry": c.expiry.isoformat(),
             "pcr_oi": c.pcr_oi, "max_pain": c.max_pain,
             "underlying_value": c.underlying_value,
             "captured_at": c.captured_at.isoformat(), "is_demo": c.is_demo}
            for c in chains
        ],
        "futures": [
            {"underlying": f.underlying_symbol, "expiry": f.expiry.isoformat(),
             "ltp": f.ltp, "spot": f.spot, "basis": f.basis,
             "basis_pct": f.basis_pct, "open_interest": f.open_interest,
             "oi_change": f.oi_change, "buildup": f.buildup,
             "is_demo": f.is_demo}
            for f in futures
        ],
    }


def _ipo_summary(db: Session) -> Dict[str, Any]:
    from app.models.ipo import Ipo, IpoGmpHistory

    rows = db.execute(
        hide_demo(select(Ipo).where(Ipo.status.in_(["OPEN", "UPCOMING"])), Ipo)
        .order_by(Ipo.open_date).limit(8)
    ).scalars().all()
    out = []
    for ipo in rows:
        gmp = db.execute(
            select(IpoGmpHistory).where(IpoGmpHistory.ipo_id == ipo.id)
            .order_by(IpoGmpHistory.observed_on.desc()).limit(1)
        ).scalars().first()
        out.append({
            "id": ipo.id, "slug": ipo.slug, "company": ipo.company_name,
            "status": ipo.status, "type": ipo.ipo_type,
            "open_date": ipo.open_date.isoformat() if ipo.open_date else None,
            "close_date": ipo.close_date.isoformat() if ipo.close_date else None,
            "price_band": [ipo.price_band_low, ipo.price_band_high],
            "lot_size": ipo.lot_size,
            "gmp": gmp.gmp if gmp else None,
            "gmp_pct": gmp.gmp_pct if gmp else None,
            "gmp_observed_on": gmp.observed_on.isoformat() if gmp else None,
            "gmp_source": gmp.source_name if gmp else None,
            "is_demo": ipo.is_demo,
        })
    return {"available": bool(out), "rows": out,
            "gmp_notice": "Grey Market Premium is an unofficial dealer quote, "
                          "not an exchange price."}


@router.get("/search", dependencies=[Depends(rate_limit("search", 240))])
def search(q: str = Query(min_length=1, max_length=60),
           limit: int = Query(default=12, le=50),
           db: Session = Depends(db_session)) -> Dict[str, Any]:
    """Symbol / name / ISIN / exchange-code search with quick actions."""
    term = q.strip().upper()
    like = f"%{term}%"

    rows = db.execute(
        select(Instrument)
        .where(Instrument.is_active.is_(True))
        .where(or_(
            Instrument.symbol.ilike(like),
            Instrument.name.ilike(like),
            Instrument.isin.ilike(like),
            Instrument.nse_code.ilike(like),
            Instrument.bse_code.ilike(like),
            Instrument.underlying_symbol.ilike(like),
        ))
        .order_by(
            (Instrument.symbol == term).desc(),
            func.length(Instrument.symbol),
        )
        .limit(limit)
    ).scalars().all()

    results = []
    for instrument in rows:
        quote = db.execute(
            hide_demo(
                select(Quote).where(Quote.instrument_id == instrument.id), Quote
            )
        ).scalars().first()
        results.append({
            "symbol": instrument.symbol,
            "display_name": instrument.display_name,
            "name": instrument.name,
            "segment": instrument.segment,
            "exchange": instrument.exchange_code,
            "isin": instrument.isin,
            "nse_code": instrument.nse_code,
            "bse_code": instrument.bse_code,
            "sector": instrument.sector,
            "expiry": instrument.expiry.isoformat() if instrument.expiry else None,
            "strike": instrument.strike,
            "option_type": instrument.option_type,
            "lot_size": instrument.lot_size,
            "ltp": quote.ltp if quote else None,
            "change_pct": quote.change_pct if quote else None,
            "is_demo": instrument.is_demo,
            "actions": _quick_actions(instrument),
        })

    return {
        "query": q,
        "count": len(results),
        "results": results,
        "note": "Search covers the instrument master stored in this deployment. "
                "Run the instrument sync job to widen it."
        if len(results) < limit else None,
    }


def _quick_actions(instrument: Instrument) -> List[Dict[str, str]]:
    base = f"/stocks/{instrument.symbol}"
    if instrument.segment == "INDEX":
        base = f"/indices/{instrument.symbol}"
    actions = [
        {"label": "Overview", "href": base},
        {"label": "Technical", "href": f"{base}?tab=technical"},
        {"label": "Research", "href": f"{base}?tab=research"},
        {"label": "News", "href": f"{base}?tab=news"},
    ]
    if instrument.segment == "EQUITY":
        actions.append({"label": "Fundamentals", "href": f"{base}?tab=fundamentals"})
    if instrument.is_fno_eligible or instrument.segment in ("INDEX", "OPTION",
                                                            "FUTURE"):
        underlying = instrument.underlying_symbol or instrument.symbol
        actions.append({"label": "Options", "href": f"/fno/options?symbol={underlying}"})
        actions.append({"label": "Futures", "href": f"/fno/futures?symbol={underlying}"})
    return actions


@router.get("/calendar")
def calendar(days: int = Query(default=30, le=180),
             db: Session = Depends(db_session)) -> Dict[str, Any]:
    today = date.today()
    horizon = today + timedelta(days=days)

    holidays = db.execute(
        select(MarketHoliday)
        .where(MarketHoliday.holiday_date >= today)
        .where(MarketHoliday.holiday_date <= horizon)
        .order_by(MarketHoliday.holiday_date)
    ).scalars().all()

    earnings = db.execute(
        select(EarningsEvent)
        .where(EarningsEvent.expected_date >= today)
        .where(EarningsEvent.expected_date <= horizon)
        .order_by(EarningsEvent.expected_date)
    ).scalars().all()

    actions = db.execute(
        select(CorporateAction)
        .where(CorporateAction.ex_date >= today)
        .where(CorporateAction.ex_date <= horizon)
        .order_by(CorporateAction.ex_date)
    ).scalars().all()

    catalysts = db.execute(
        select(Catalyst)
        .where(Catalyst.event_date >= today)
        .where(Catalyst.event_date <= horizon)
        .order_by(Catalyst.event_date)
    ).scalars().all()

    return {
        "from": today.isoformat(),
        "to": horizon.isoformat(),
        "holidays": [
            {"date": h.holiday_date.isoformat(), "description": h.description,
             "exchange": h.exchange_code, "source": h.source_name}
            for h in holidays
        ],
        "holidays_note": (
            "Exchange holidays are loaded from the market_holidays table. If it "
            "is empty the platform falls back to an unverified bootstrap seed "
            "and says so on the market-status endpoint."
        ),
        "earnings": [
            {"symbol": e.symbol, "quarter": e.quarter_label,
             "date": e.expected_date.isoformat() if e.expected_date else None,
             "status": e.status, "is_demo": e.is_demo}
            for e in earnings
        ],
        "corporate_actions": [
            {"symbol": a.symbol, "type": a.action_type,
             "description": a.description,
             "ex_date": a.ex_date.isoformat() if a.ex_date else None,
             "record_date": a.record_date.isoformat() if a.record_date else None,
             "payment_date": a.payment_date.isoformat()
             if a.payment_date else None,
             "value": a.value, "is_demo": a.is_demo}
            for a in actions
        ],
        "catalysts": [
            {"symbol": c.symbol, "scope": c.scope, "title": c.title,
             "category": c.category,
             "date": c.event_date.isoformat() if c.event_date else None,
             "expected_impact": c.expected_impact, "risk_level": c.risk_level,
             "confirmed": c.is_confirmed, "is_demo": c.is_demo}
            for c in catalysts
        ],
    }
