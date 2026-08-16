"""Stock endpoints: list, overview, chart, technicals, fundamentals, news,
research, corporate actions — plus the index pages."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import db_session, rate_limit
from app.core.market_calendar import market_state
from app.models.fundamental import (CompanyProfile, CorporateAction,
                                    EarningsEvent, FinancialStatement,
                                    Fundamental, Shareholding)
from app.models.instrument import IndexConstituent, Instrument
from app.models.market import IndexSnapshot, Quote
from app.models.news import NewsArticle, NewsPriceReaction, NewsScore
from app.models.research import ResearchCall, ResearchSource
from app.providers.registry import registry
from app.services import indicators as ind
from app.services.news_analysis import news_analysis_service
from app.services.research import research_service
from app.services.research_calls import research_call_service
from app.services.technical_analysis import (bars_to_frame,
                                             technical_analysis_service)

router = APIRouter(tags=["stocks"])

VALID_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"}


def _instrument_or_404(db: Session, symbol: str,
                       segment: Optional[str] = None) -> Instrument:
    stmt = select(Instrument).where(Instrument.symbol == symbol.upper())
    if segment:
        stmt = stmt.where(Instrument.segment == segment)
    instrument = db.execute(stmt.limit(1)).scalars().first()
    if instrument is None:
        raise HTTPException(
            404,
            f"{symbol.upper()} is not in the instrument master for this "
            f"deployment. Run the instrument sync job or add it through the "
            f"admin panel.",
        )
    return instrument


@router.get("/stocks", dependencies=[Depends(rate_limit("stocks", 120))])
def list_stocks(
    q: Optional[str] = None,
    sector: Optional[str] = None,
    exchange: Optional[str] = None,
    fno_only: bool = False,
    sort: str = Query(default="symbol",
                      pattern="^(symbol|change_pct|volume|market_cap)$"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, le=200),
    db: Session = Depends(db_session),
) -> Dict[str, Any]:
    stmt = (
        select(Instrument, Quote, Fundamental)
        .outerjoin(Quote, Quote.instrument_id == Instrument.id)
        .outerjoin(Fundamental, Fundamental.instrument_id == Instrument.id)
        .where(Instrument.segment == "EQUITY")
        .where(Instrument.is_active.is_(True))
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Instrument.symbol.ilike(like),
                              Instrument.name.ilike(like)))
    if sector:
        stmt = stmt.where(Instrument.sector == sector)
    if exchange:
        stmt = stmt.where(Instrument.exchange_code == exchange.upper())
    if fno_only:
        stmt = stmt.where(Instrument.is_fno_eligible.is_(True))

    total = db.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()

    sort_column = {
        "symbol": Instrument.symbol,
        "change_pct": Quote.change_pct,
        "volume": Quote.volume,
        "market_cap": Fundamental.market_cap,
    }[sort]
    stmt = stmt.order_by(
        sort_column.desc() if order == "desc" else sort_column.asc()
    ).offset((page - 1) * page_size).limit(page_size)

    rows = []
    for instrument, quote, fundamental in db.execute(stmt).all():
        rows.append({
            "symbol": instrument.symbol,
            "name": instrument.name,
            "exchange": instrument.exchange_code,
            "sector": instrument.sector,
            "industry": instrument.industry,
            "isin": instrument.isin,
            "is_fno_eligible": instrument.is_fno_eligible,
            "ltp": quote.ltp if quote else None,
            "change": quote.change if quote else None,
            "change_pct": quote.change_pct if quote else None,
            "volume": quote.volume if quote else None,
            "week52_high": quote.week52_high if quote else None,
            "week52_low": quote.week52_low if quote else None,
            "market_cap": fundamental.market_cap if fundamental else None,
            "pe": fundamental.pe if fundamental else None,
            "data_status": quote.data_status if quote else "UNAVAILABLE",
            "observed_at": quote.observed_at.isoformat()
            if quote and quote.observed_at else None,
            "is_demo": instrument.is_demo,
        })

    return {
        "rows": rows, "total": total, "page": page, "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
        "note": "Rows without a quote show null - the instrument exists in the "
                "master but no provider has priced it yet.",
    }


@router.get("/stocks/{symbol}")
def stock_overview(symbol: str,
                   db: Session = Depends(db_session)) -> Dict[str, Any]:
    instrument = _instrument_or_404(db, symbol)
    env = registry.fetch("quote", instrument.symbol, db=db)
    quote = env.value

    fundamental = db.execute(
        select(Fundamental).where(Fundamental.instrument_id == instrument.id)
    ).scalars().first()
    profile = db.execute(
        select(CompanyProfile).where(CompanyProfile.instrument_id == instrument.id)
    ).scalars().first()

    state = market_state()

    return {
        "instrument": {
            "symbol": instrument.symbol,
            "name": instrument.name,
            "display_name": instrument.display_name,
            "exchange": instrument.exchange_code,
            "segment": instrument.segment,
            "isin": instrument.isin,
            "nse_code": instrument.nse_code or instrument.symbol,
            "bse_code": instrument.bse_code,
            "series": instrument.series,
            "sector": instrument.sector,
            "industry": instrument.industry,
            "lot_size": instrument.lot_size,
            "is_fno_eligible": instrument.is_fno_eligible,
            "is_demo": instrument.is_demo,
        },
        "market_status": {
            "status": state.status.value,
            "timezone": state.timezone,
            "note": state.note,
        },
        "quote": {
            "available": quote is not None,
            "ltp": quote.ltp if quote else None,
            "change": quote.change if quote else None,
            "change_pct": quote.change_pct if quote else None,
            "open": quote.open if quote else None,
            "high": quote.high if quote else None,
            "low": quote.low if quote else None,
            "previous_close": quote.previous_close if quote else None,
            "volume": quote.volume if quote else None,
            "average_volume_20d": quote.average_volume_20d if quote else None,
            "vwap": quote.vwap if quote else None,
            "week52_high": quote.week52_high if quote else None,
            "week52_low": quote.week52_low if quote else None,
            "market_cap": quote.market_cap if quote
            else (fundamental.market_cap if fundamental else None),
            "bid": quote.bid if quote else None,
            "ask": quote.ask if quote else None,
        } if quote else {"available": False,
                         "reason": env.notes or "No provider returned a quote."},
        "key_ratios": {
            "pe": fundamental.pe if fundamental else None,
            "pb": fundamental.pb if fundamental else None,
            "eps_ttm": fundamental.eps_ttm if fundamental else None,
            "roe": fundamental.roe if fundamental else None,
            "roce": fundamental.roce if fundamental else None,
            "debt_to_equity": fundamental.debt_to_equity if fundamental else None,
            "dividend_yield": fundamental.dividend_yield if fundamental else None,
            "beta": fundamental.beta if fundamental else None,
            "promoter_holding": fundamental.promoter_holding if fundamental else None,
            "fii_holding": fundamental.fii_holding if fundamental else None,
            "dii_holding": fundamental.dii_holding if fundamental else None,
        } if fundamental else {"available": False,
                               "reason": "No fundamentals stored for this symbol."},
        "profile": {
            "description": profile.description if profile else None,
            "website": profile.website if profile else None,
            "employees": profile.employees if profile else None,
        } if profile else None,
        "provenance": env.to_dict(),
    }


@router.get("/stocks/{symbol}/chart")
def stock_chart(
    symbol: str,
    interval: str = Query(default="1d"),
    lookback_days: int = Query(default=400, le=3650),
    indicators: str = Query(default="", description="comma-separated columns"),
    db: Session = Depends(db_session),
) -> Dict[str, Any]:
    if interval not in VALID_INTERVALS:
        raise HTTPException(
            400, f"interval must be one of {sorted(VALID_INTERVALS)}"
        )
    instrument = _instrument_or_404(db, symbol)
    end = date.today()
    start = end - timedelta(days=lookback_days)

    env = registry.fetch("history", instrument.symbol, interval=interval,
                         start=start, end=end, db=db)
    bars = env.value or []
    if not bars:
        return {
            "symbol": instrument.symbol, "interval": interval,
            "candles": [], "indicators": {}, "available": False,
            "reason": env.notes or "No history provider returned bars.",
            "provenance": env.to_dict(),
        }

    frame = bars_to_frame(bars)
    enriched = ind.compute_all(frame) if len(frame) >= 30 else frame

    candles = [
        {
            "time": int(ts.timestamp()),
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": int(row["volume"]) if row.get("volume") == row.get("volume")
            and row.get("volume") is not None else None,
        }
        for ts, row in enriched.iterrows()
    ]

    requested = [c.strip() for c in indicators.split(",") if c.strip()]
    series: Dict[str, List[Dict[str, Any]]] = {}
    for column in requested:
        if column not in enriched.columns:
            continue
        series[column] = [
            {"time": int(ts.timestamp()), "value": float(value)}
            for ts, value in enriched[column].items()
            if value == value and value is not None  # drop NaN warm-up
        ]

    volume_profile = None
    if "volume" in enriched and enriched["volume"].notna().any():
        profile = ind.volume_profile(enriched["close"], enriched["volume"])
        if not profile.empty:
            volume_profile = profile.to_dict(orient="records")

    return {
        "symbol": instrument.symbol,
        "interval": interval,
        "available": True,
        "candles": candles,
        "indicators": series,
        "available_indicators": sorted(
            c for c in enriched.columns
            if c not in ("open", "high", "low", "close", "volume")
        ),
        "volume_profile": volume_profile,
        "volume_profile_note": (
            "Volume profile buckets each bar's volume at its close price. "
            "Without tick data the distribution inside the bar is unknown."
        ),
        "provenance": env.to_dict(),
    }


@router.get("/stocks/{symbol}/technicals")
def stock_technicals(symbol: str, interval: str = Query(default="1d"),
                     db: Session = Depends(db_session)) -> Dict[str, Any]:
    instrument = _instrument_or_404(db, symbol)
    env = registry.fetch("history", instrument.symbol, interval=interval, db=db)
    view = technical_analysis_service.analyse(instrument.symbol, env, interval)
    return {**view.to_dict(), "provenance": env.to_dict()}


@router.get("/stocks/{symbol}/fundamentals")
def stock_fundamentals(symbol: str,
                       db: Session = Depends(db_session)) -> Dict[str, Any]:
    instrument = _instrument_or_404(db, symbol)
    bundle = research_service.build_instrument_research(
        db, instrument.symbol, include_options=False, include_analogues=False
    )
    statements = db.execute(
        select(FinancialStatement)
        .where(FinancialStatement.symbol == instrument.symbol)
        .order_by(FinancialStatement.period_end.desc())
    ).scalars().all()
    shareholding = db.execute(
        select(Shareholding)
        .where(Shareholding.symbol == instrument.symbol)
        .order_by(Shareholding.as_of.desc()).limit(8)
    ).scalars().all()

    return {
        "symbol": instrument.symbol,
        "fundamental": bundle.fundamental,
        "statements": [
            {
                "period_label": s.period_label,
                "period_type": s.period_type,
                "period_end": s.period_end.isoformat(),
                "statement_type": s.statement_type,
                "revenue": s.revenue, "ebitda": s.ebitda,
                "ebitda_margin": s.ebitda_margin, "ebit": s.ebit,
                "pat": s.pat, "eps": s.eps,
                "operating_cash_flow": s.operating_cash_flow,
                "free_cash_flow": s.free_cash_flow,
                "total_assets": s.total_assets, "total_debt": s.total_debt,
                "net_worth": s.net_worth,
                "source": s.source_name, "is_demo": s.is_demo,
                "published_at": s.published_at.isoformat()
                if s.published_at else None,
            }
            for s in statements
        ],
        "shareholding": [
            {"as_of": s.as_of.isoformat(), "promoter": s.promoter,
             "promoter_pledged": s.promoter_pledged, "fii": s.fii,
             "dii": s.dii, "public": s.public, "source": s.source_name,
             "is_demo": s.is_demo}
            for s in shareholding
        ],
    }


@router.get("/stocks/{symbol}/news")
def stock_news(symbol: str, limit: int = Query(default=25, le=100),
               db: Session = Depends(db_session)) -> Dict[str, Any]:
    instrument = _instrument_or_404(db, symbol)
    env = registry.fetch("news", symbol=instrument.symbol,
                         company_name=instrument.name, limit=limit, db=db)
    items = env.value or []

    assessments = [
        news_analysis_service.assess(
            headline=item.headline, publisher=item.publisher, url=item.url,
            published_at=item.published_at, symbol=instrument.symbol,
            company_name=instrument.name, sector=instrument.sector,
        ).to_dict()
        for item in items
    ]

    stored_reactions = db.execute(
        select(NewsArticle, NewsPriceReaction)
        .join(NewsPriceReaction, NewsPriceReaction.article_id == NewsArticle.id)
        .where(NewsArticle.primary_symbol == instrument.symbol)
        .order_by(NewsArticle.published_at.desc()).limit(20)
    ).all()

    return {
        "symbol": instrument.symbol,
        "available": bool(assessments),
        "reason": None if assessments else (env.notes or "No news returned."),
        "articles": sorted(assessments,
                           key=lambda a: a["impact_score"], reverse=True),
        "price_reactions": [
            {
                "headline": article.headline,
                "published_at": article.published_at.isoformat()
                if article.published_at else None,
                "price_before": reaction.price_before,
                "price_5m": reaction.price_5m,
                "price_15m": reaction.price_15m,
                "price_1h": reaction.price_1h,
                "price_eod": reaction.price_eod,
                "return_eod_pct": reaction.return_eod_pct,
                "volume_ratio": reaction.volume_ratio,
                "note": reaction.resolution_note,
            }
            for article, reaction in stored_reactions
        ],
        "provenance": env.to_dict(),
    }


@router.get("/stocks/{symbol}/research")
def stock_research(
    symbol: str,
    interval: str = Query(default="1d"),
    include_options: bool = True,
    include_analogues: bool = True,
    db: Session = Depends(db_session),
) -> Dict[str, Any]:
    """The full evidence chain for one instrument."""
    instrument = _instrument_or_404(db, symbol)
    bundle = research_service.build_instrument_research(
        db, instrument.symbol, segment=instrument.segment, interval=interval,
        include_options=include_options, include_analogues=include_analogues,
    )
    return bundle.to_dict()


@router.get("/stocks/{symbol}/calls")
def stock_calls(symbol: str,
                db: Session = Depends(db_session)) -> Dict[str, Any]:
    instrument = _instrument_or_404(db, symbol)
    calls = db.execute(
        select(ResearchCall)
        .where(ResearchCall.symbol == instrument.symbol)
        .order_by(ResearchCall.published_at.desc())
    ).scalars().all()

    rows = []
    for call in calls:
        source = db.execute(
            select(ResearchSource).where(ResearchSource.id == call.source_id)
        ).scalars().first() if call.source_id else None
        evaluation = research_call_service.refresh_status(db, call)
        rows.append(research_call_service.to_card(call, evaluation, source))
    db.commit()
    return {"symbol": instrument.symbol, "count": len(rows), "calls": rows}


@router.get("/stocks/{symbol}/corporate-actions")
def stock_corporate_actions(symbol: str,
                            db: Session = Depends(db_session)) -> Dict[str, Any]:
    instrument = _instrument_or_404(db, symbol)
    actions = db.execute(
        select(CorporateAction)
        .where(CorporateAction.symbol == instrument.symbol)
        .order_by(CorporateAction.ex_date.desc())
    ).scalars().all()
    earnings = db.execute(
        select(EarningsEvent)
        .where(EarningsEvent.symbol == instrument.symbol)
        .order_by(EarningsEvent.expected_date.desc())
    ).scalars().all()

    return {
        "symbol": instrument.symbol,
        "corporate_actions": [
            {
                "type": a.action_type, "description": a.description,
                "announcement_date": a.announcement_date.isoformat()
                if a.announcement_date else None,
                "ex_date": a.ex_date.isoformat() if a.ex_date else None,
                "record_date": a.record_date.isoformat()
                if a.record_date else None,
                "payment_date": a.payment_date.isoformat()
                if a.payment_date else None,
                "value": a.value, "ratio_from": a.ratio_from,
                "ratio_to": a.ratio_to,
                "price_adjustment_factor": a.price_adjustment_factor,
                "source": a.source_name, "is_demo": a.is_demo,
            }
            for a in actions
        ],
        "results": [
            {
                "quarter": e.quarter_label,
                "expected_date": e.expected_date.isoformat()
                if e.expected_date else None,
                "reported_date": e.reported_date.isoformat()
                if e.reported_date else None,
                "revenue": e.revenue, "pat": e.pat, "eps": e.eps,
                "revenue_yoy_pct": e.revenue_yoy_pct,
                "pat_yoy_pct": e.pat_yoy_pct,
                "consensus_revenue": e.consensus_revenue,
                "consensus_pat": e.consensus_pat,
                "consensus_source": e.consensus_source,
                "price_reaction_1d_pct": e.price_reaction_1d_pct,
                "management_commentary": e.management_commentary,
                "status": e.status, "is_demo": e.is_demo,
            }
            for e in earnings
        ],
    }


# --------------------------------------------------------------------------
# Indices
# --------------------------------------------------------------------------


@router.get("/indices")
def list_indices(db: Session = Depends(db_session)) -> Dict[str, Any]:
    env = registry.fetch("indices", db=db)
    if env.is_usable and env.value:
        return {
            "available": True,
            "rows": [
                {"symbol": q.symbol, "ltp": q.ltp, "change": q.change,
                 "change_pct": q.change_pct, "open": q.open, "high": q.high,
                 "low": q.low, "previous_close": q.previous_close,
                 "week52_high": q.week52_high, "week52_low": q.week52_low}
                for q in env.value
            ],
            "provenance": env.to_dict(),
        }
    stored = db.execute(
        select(IndexSnapshot).order_by(IndexSnapshot.as_of.desc()).limit(30)
    ).scalars().all()
    return {
        "available": bool(stored),
        "reason": None if stored else (env.notes or "No index provider available."),
        "rows": [
            {"symbol": s.index_symbol, "ltp": s.ltp, "change_pct": s.change_pct,
             "regime": s.regime, "pcr": s.pcr, "max_pain": s.max_pain,
             "as_of": s.as_of.isoformat(), "is_demo": s.is_demo}
            for s in stored
        ],
        "provenance": env.to_dict(),
    }


@router.get("/indices/{symbol}")
def index_detail(symbol: str, interval: str = Query(default="1d"),
                 db: Session = Depends(db_session)) -> Dict[str, Any]:
    name = symbol.upper().replace("-", " ")
    env = registry.fetch("history", name, interval=interval, db=db)
    view = technical_analysis_service.analyse(name, env, interval)

    snapshot = db.execute(
        select(IndexSnapshot)
        .where(IndexSnapshot.index_symbol == name)
        .order_by(IndexSnapshot.as_of.desc()).limit(1)
    ).scalars().first()

    constituents = db.execute(
        select(IndexConstituent)
        .where(IndexConstituent.index_symbol == name)
        .order_by(IndexConstituent.weight_pct.desc()).limit(50)
    ).scalars().all()

    contributions: List[Dict[str, Any]] = []
    for member in constituents:
        quote = db.execute(
            select(Quote).where(Quote.symbol == member.constituent_symbol)
        ).scalars().first()
        if quote and quote.change_pct is not None and member.weight_pct:
            contributions.append({
                "symbol": member.constituent_symbol,
                "weight_pct": member.weight_pct,
                "change_pct": quote.change_pct,
                "contribution_pct": round(
                    member.weight_pct * quote.change_pct / 100.0, 4
                ),
            })
    contributions.sort(key=lambda c: c["contribution_pct"], reverse=True)

    chain_env = registry.fetch("option_chain", name.replace(" ", ""), db=db)
    options_summary = None
    if chain_env.is_usable and chain_env.value:
        from app.services.options_analysis import options_analysis_service

        view_options = options_analysis_service.analyse(chain_env)
        if view_options:
            options_summary = {
                "expiry": view_options.expiry.isoformat(),
                "pcr_oi": view_options.totals.get("pcr_oi"),
                "max_pain": view_options.totals.get("max_pain"),
                "key_levels": view_options.key_levels,
                "explanation": view_options.chain.explain(),
            }

    return {
        "symbol": name,
        "technical": view.to_dict(),
        "regime": {
            **view.regime,
            "stored_regime": snapshot.regime if snapshot else None,
            "stored_rationale": snapshot.regime_rationale if snapshot else None,
        },
        "breadth": {
            "advances": snapshot.advances if snapshot else None,
            "declines": snapshot.declines if snapshot else None,
            "new_highs": snapshot.new_highs if snapshot else None,
            "new_lows": snapshot.new_lows if snapshot else None,
            "as_of": snapshot.as_of.isoformat() if snapshot else None,
        } if snapshot else {"available": False,
                            "reason": "No breadth snapshot stored for this index."},
        "sector_contribution": contributions[:20],
        "worst_contributors": contributions[-10:][::-1] if contributions else [],
        "options": options_summary,
        "india_vix": snapshot.india_vix if snapshot else None,
        "provenance": env.to_dict(),
    }
