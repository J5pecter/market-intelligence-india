"""Scheduled jobs.

Each job is a plain callable so it can run under APScheduler, be triggered from
the admin panel, or be called from a test. Every run writes a `JobRunLog` row
with counters, so the system-health page shows facts rather than assurances.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.data_quality import DataStatus
from app.core.logging import job_logger
from app.core.market_calendar import is_market_open
from app.db.session import SessionLocal
from app.models.fundamental import Fundamental
from app.models.instrument import Instrument, InstrumentSyncRun, MarketHoliday
from app.models.market import HistoricalPrice, Quote, TechnicalIndicatorSnapshot
from app.models.news import NewsArticle, NewsScore
from app.models.research import ResearchCall
from app.models.system import JobRunLog
from app.models.user_data import Alert
from app.providers.registry import registry
from app.services import indicators as ind
from app.services.alerts import alert_service
from app.services.news_analysis import news_analysis_service, url_hash
from app.services.research_calls import research_call_service
from app.services.technical_analysis import bars_to_frame

logger = logging.getLogger(__name__)


def _job(name: str):
    """Decorator: wrap a job so it logs, times and never raises into the
    scheduler."""
    def decorator(func: Callable[[Session, Any], Dict[str, Any]]):
        def wrapper(**kwargs: Any) -> Dict[str, Any]:
            db = SessionLocal()
            record = JobRunLog(job_name=name,
                               started_at=datetime.now(tz=timezone.utc))
            db.add(record)
            db.commit()
            started = time.perf_counter()
            try:
                with job_logger(logger, name) as run:
                    result = func(db, run, **kwargs)
                record.status = "SUCCESS"
                record.provider = run.get("provider")
                record.records_received = run.get("records_received", 0)
                record.records_saved = run.get("records_saved", 0)
                record.records_rejected = run.get("records_rejected", 0)
                if run.get("errors"):
                    record.error = "; ".join(run["errors"])[:4000]
                    record.status = "PARTIAL"
            except Exception as exc:  # noqa: BLE001
                logger.exception("job %s failed", name)
                record.status = "FAILED"
                record.error = f"{type(exc).__name__}: {exc}"[:4000]
                result = {"status": "FAILED", "error": str(exc)}
            finally:
                record.finished_at = datetime.now(tz=timezone.utc)
                record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
                db.commit()
                summary = {
                    "job": name, "status": record.status,
                    "duration_ms": record.duration_ms,
                    "records_saved": record.records_saved,
                    "records_rejected": record.records_rejected,
                    "error": record.error,
                }
                db.close()
            return {**summary, **(result if isinstance(result, dict) else {})}

        wrapper.__name__ = func.__name__
        return wrapper
    return decorator


# --------------------------------------------------------------------------
# Instrument master
# --------------------------------------------------------------------------


@_job("instrument_sync")
def instrument_sync(db: Session, run) -> Dict[str, Any]:
    """Import the instrument universe. Never hard-codes a stock list."""
    sync = InstrumentSyncRun(provider="chain",
                             started_at=datetime.now(tz=timezone.utc))
    db.add(sync)

    env = registry.fetch("instruments", db=db)
    run["provider"] = env.provider
    if not env.is_usable or not env.value:
        sync.status = "FAILED"
        sync.error = env.notes or "no provider returned instruments"
        sync.finished_at = datetime.now(tz=timezone.utc)
        db.commit()
        run.error(sync.error)
        return {"imported": 0, "reason": sync.error}

    records = env.value
    run["records_received"] = len(records)
    saved = rejected = 0

    for record in records:
        if not record.symbol or not record.symbol.strip():
            rejected += 1
            continue
        existing = db.execute(
            select(Instrument)
            .where(Instrument.symbol == record.symbol.upper())
            .where(Instrument.segment == record.segment)
            .where(Instrument.exchange_code == record.exchange_code)
        ).scalars().first()

        target = existing or Instrument(
            symbol=record.symbol.upper(), segment=record.segment,
            exchange_code=record.exchange_code,
        )
        target.name = record.name or target.name
        target.isin = record.isin or target.isin
        target.series = record.series or target.series
        target.industry = record.industry or target.industry
        target.sector = record.sector or target.sector or record.industry
        target.lot_size = record.lot_size or target.lot_size
        target.underlying_symbol = record.underlying_symbol
        target.expiry = record.expiry
        target.strike = record.strike
        target.option_type = record.option_type
        target.is_fno_eligible = record.is_fno_eligible or target.is_fno_eligible
        target.provider = env.provider
        target.source_name = env.source_name
        target.data_status = env.status.value
        target.observed_at = env.observed_at
        if existing is None:
            db.add(target)
        saved += 1

    sync.records_received = len(records)
    sync.records_saved = saved
    sync.records_rejected = rejected
    sync.status = "SUCCESS"
    sync.finished_at = datetime.now(tz=timezone.utc)
    run["records_saved"] = saved
    run["records_rejected"] = rejected
    db.commit()
    return {"imported": saved, "rejected": rejected, "provider": env.provider}


# --------------------------------------------------------------------------
# Quotes
# --------------------------------------------------------------------------


@_job("quote_refresh")
def quote_refresh(db: Session, run, limit: int = 120,
                  symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """Refresh quotes for the most relevant instruments.

    Priority order, so a small provider budget is spent where it matters:
    watchlisted symbols, symbols with a live research call, then the rest.
    """
    targets = _priority_symbols(db, limit, symbols)
    saved = rejected = 0

    for instrument in targets:
        env = registry.fetch("quote", instrument.symbol, db=db)
        run["records_received"] += 1
        if not env.is_usable or env.value is None or env.value.ltp is None:
            rejected += 1
            continue

        data = env.value
        if not _validate_quote(data):
            rejected += 1
            run.error(f"{instrument.symbol}: failed validation")
            continue

        quote = db.execute(
            select(Quote).where(Quote.instrument_id == instrument.id)
        ).scalars().first() or Quote(instrument_id=instrument.id,
                                     symbol=instrument.symbol)

        for field_name in ("ltp", "open", "high", "low", "previous_close",
                           "change", "change_pct", "volume", "vwap",
                           "week52_high", "week52_low", "market_cap", "bid",
                           "ask", "open_interest", "oi_change"):
            value = getattr(data, field_name, None)
            if value is not None:
                setattr(quote, field_name, value)

        quote.provider = env.provider
        quote.source_name = env.source_name
        quote.data_status = env.status.value
        quote.observed_at = env.observed_at
        quote.is_demo = env.is_demo
        db.add(quote)
        saved += 1

    run["records_saved"] = saved
    run["records_rejected"] = rejected
    db.commit()
    return {"refreshed": saved, "skipped": rejected,
            "market_open": is_market_open()}


def _priority_symbols(db: Session, limit: int,
                      symbols: Optional[List[str]]) -> List[Instrument]:
    if symbols:
        return list(db.execute(
            select(Instrument).where(Instrument.symbol.in_(
                [s.upper() for s in symbols]
            ))
        ).scalars().all())

    from app.models.user_data import WatchlistItem

    watched = set(db.execute(select(WatchlistItem.symbol)).scalars().all())
    called = set(db.execute(
        select(ResearchCall.symbol).where(ResearchCall.is_published.is_(True))
    ).scalars().all())
    priority = watched | called

    ordered: List[Instrument] = []
    if priority:
        ordered.extend(db.execute(
            select(Instrument)
            .where(Instrument.symbol.in_(priority))
            .where(Instrument.is_active.is_(True))
        ).scalars().all())

    remaining = limit - len(ordered)
    if remaining > 0:
        ordered.extend(db.execute(
            select(Instrument)
            .where(Instrument.segment == "EQUITY")
            .where(Instrument.is_active.is_(True))
            .where(Instrument.symbol.notin_(priority or {""}))
            .order_by(Instrument.symbol)
            .limit(remaining)
        ).scalars().all())
    return ordered[:limit]


def _validate_quote(data) -> bool:
    """Reject corrupt payloads before they reach the database."""
    if data.ltp is None or data.ltp < 0:
        return False
    if data.volume is not None and data.volume < 0:
        return False
    if data.open_interest is not None and data.open_interest < 0:
        return False
    if data.high is not None and data.low is not None and data.high < data.low:
        return False
    if data.high is not None and data.ltp > data.high * 1.5:
        return False  # implausible relative to the day's range
    return True


# --------------------------------------------------------------------------
# History and indicators
# --------------------------------------------------------------------------


@_job("history_refresh")
def history_refresh(db: Session, run, limit: int = 60,
                    lookback_days: int = 420) -> Dict[str, Any]:
    targets = _priority_symbols(db, limit, None)
    saved = 0
    end = date.today()
    start = end - timedelta(days=lookback_days)

    for instrument in targets:
        env = registry.fetch("history", instrument.symbol, interval="1d",
                             start=start, end=end, db=db)
        if not env.is_usable or not env.value:
            continue
        run["records_received"] += len(env.value)

        existing = {
            row[0] for row in db.execute(
                select(HistoricalPrice.bar_time)
                .where(HistoricalPrice.instrument_id == instrument.id)
                .where(HistoricalPrice.interval == "1d")
            ).all()
        }
        for bar in env.value:
            if bar.time in existing:
                continue
            if bar.high < bar.low or bar.close < 0:
                run["records_rejected"] += 1
                continue
            db.add(HistoricalPrice(
                instrument_id=instrument.id, symbol=instrument.symbol,
                interval="1d", bar_time=bar.time, open=bar.open,
                high=bar.high, low=bar.low, close=bar.close,
                raw_close=bar.raw_close, volume=bar.volume,
                provider=env.provider, source_name=env.source_name,
                data_status=env.status.value, observed_at=bar.time,
                is_demo=env.is_demo,
            ))
            saved += 1
        db.commit()

    run["records_saved"] = saved
    return {"bars_saved": saved}


@_job("indicator_refresh")
def indicator_refresh(db: Session, run, limit: int = 200) -> Dict[str, Any]:
    """Recompute the indicator snapshot scanners query."""
    symbols = db.execute(
        select(HistoricalPrice.instrument_id, HistoricalPrice.symbol)
        .where(HistoricalPrice.interval == "1d")
        .distinct().limit(limit)
    ).all()

    saved = 0
    today = date.today()

    for instrument_id, symbol in symbols:
        rows = db.execute(
            select(HistoricalPrice)
            .where(HistoricalPrice.instrument_id == instrument_id)
            .where(HistoricalPrice.interval == "1d")
            .order_by(HistoricalPrice.bar_time)
        ).scalars().all()
        if len(rows) < 60:
            run["records_rejected"] += 1
            continue

        from app.providers.base import Bar

        frame = bars_to_frame([
            Bar(time=r.bar_time, open=r.open, high=r.high, low=r.low,
                close=r.close, volume=r.volume)
            for r in rows
        ])
        enriched = ind.compute_all(frame)
        last = enriched.iloc[-1]
        as_of = enriched.index[-1].date()

        snapshot = db.execute(
            select(TechnicalIndicatorSnapshot)
            .where(TechnicalIndicatorSnapshot.instrument_id == instrument_id)
            .where(TechnicalIndicatorSnapshot.interval == "1d")
            .where(TechnicalIndicatorSnapshot.as_of == as_of)
        ).scalars().first() or TechnicalIndicatorSnapshot(
            instrument_id=instrument_id, symbol=symbol, interval="1d",
            as_of=as_of,
        )

        mapping = {
            "close": "close", "sma_20": "sma_20", "sma_50": "sma_50",
            "sma_100": "sma_100", "sma_200": "sma_200", "ema_9": "ema_9",
            "ema_20": "ema_20", "ema_50": "ema_50", "rsi_14": "rsi_14",
            "macd": "macd", "macd_signal": "macd_signal",
            "macd_hist": "macd_hist", "atr_14": "atr_14", "atr_pct": "atr_pct",
            "adx_14": "adx_14", "bb_upper": "bb_upper", "bb_lower": "bb_lower",
            "bb_width": "bb_width", "stoch_k": "stoch_k", "stoch_d": "stoch_d",
            "supertrend": "supertrend", "vwap": "vwap",
            "volume_ratio_20d": "volume_ratio_20",
            "distance_from_52w_high_pct": "pct_from_52w_high",
            "distance_from_52w_low_pct": "pct_from_52w_low",
        }
        for column, source_key in mapping.items():
            value = last.get(source_key)
            setattr(snapshot, column,
                    None if value is None or value != value else float(value))

        direction = last.get("supertrend_dir")
        snapshot.supertrend_dir = (
            None if direction is None or direction != direction
            else int(direction)
        )
        snapshot.trend_score = _trend_score(last)
        snapshot.momentum_score = _momentum_score(last)
        snapshot.provider = rows[-1].provider
        snapshot.source_name = rows[-1].source_name
        snapshot.data_status = rows[-1].data_status
        snapshot.observed_at = rows[-1].bar_time
        snapshot.is_demo = rows[-1].is_demo
        db.add(snapshot)
        saved += 1

    run["records_saved"] = saved
    run["records_received"] = len(symbols)
    db.commit()
    return {"snapshots": saved, "as_of": today.isoformat()}


def _trend_score(last) -> Optional[float]:
    """0-100 from moving-average stacking. Documented in /methodology."""
    close = last.get("close")
    if close is None or close != close:
        return None
    score = 50.0
    for key, weight in (("sma_20", 10), ("sma_50", 12), ("sma_200", 16)):
        value = last.get(key)
        if value is None or value != value:
            continue
        score += weight if close > value else -weight
    direction = last.get("supertrend_dir")
    if direction is not None and direction == direction:
        score += 12 if direction > 0 else -12
    return round(max(0.0, min(100.0, score)), 1)


def _momentum_score(last) -> Optional[float]:
    rsi = last.get("rsi_14")
    macd_hist = last.get("macd_hist")
    if (rsi is None or rsi != rsi) and (macd_hist is None or macd_hist != macd_hist):
        return None
    score = 50.0
    if rsi is not None and rsi == rsi:
        score += (rsi - 50.0) * 0.8
    if macd_hist is not None and macd_hist == macd_hist:
        score += 10 if macd_hist > 0 else -10
    return round(max(0.0, min(100.0, score)), 1)


# --------------------------------------------------------------------------
# News
# --------------------------------------------------------------------------


@_job("news_refresh")
def news_refresh(db: Session, run, limit_symbols: int = 40) -> Dict[str, Any]:
    targets = _priority_symbols(db, limit_symbols, None)
    saved = 0

    for instrument in targets:
        env = registry.fetch("news", symbol=instrument.symbol,
                             company_name=instrument.name, limit=10, db=db)
        if not env.is_usable or not env.value:
            continue
        run["provider"] = env.provider
        run["records_received"] += len(env.value)

        for item in env.value:
            digest = url_hash(item.url)
            existing = db.execute(
                select(NewsArticle).where(NewsArticle.url_hash == digest)
            ).scalars().first()
            if existing:
                continue

            article = NewsArticle(
                headline=item.headline[:600], summary=item.summary,
                url=item.url[:1200], url_hash=digest,
                publisher=item.publisher[:200], published_at=item.published_at,
                primary_symbol=instrument.symbol,
                related_symbols=json.dumps(item.related_symbols or []),
                sector=instrument.sector,
                provider=env.provider, source_name=env.source_name,
                source_url=item.url[:1000],
                data_status=env.status.value, observed_at=item.published_at,
                is_demo=env.is_demo,
            )
            assessment = news_analysis_service.assess(
                headline=item.headline, publisher=item.publisher,
                url=item.url, published_at=item.published_at,
                symbol=instrument.symbol, company_name=instrument.name,
                sector=instrument.sector,
            )
            article.event_category = assessment.event_category
            db.add(article)
            db.flush()

            db.add(NewsScore(
                article_id=article.id,
                model_version=news_analysis_service.MODEL_VERSION,
                sentiment=assessment.sentiment,
                sentiment_score=assessment.sentiment_score,
                headline_sentiment=assessment.components["headline_sentiment"],
                event_importance=assessment.components["event_importance"],
                historical_reaction=assessment.components["historical_reaction"],
                sector_relevance=assessment.components["sector_relevance"],
                company_relevance=assessment.components["company_relevance"],
                source_credibility=assessment.components["source_credibility"],
                impact_score=assessment.impact_score,
                explanation=json.dumps({
                    "text": assessment.explanation,
                    "components": assessment.components,
                    "limitations": assessment.limitations,
                }),
                matched_terms=json.dumps(assessment.matched_terms),
            ))
            saved += 1
        db.commit()

    run["records_saved"] = saved
    return {"articles_saved": saved}


# --------------------------------------------------------------------------
# Research status and alerts
# --------------------------------------------------------------------------


@_job("research_status_update")
def research_status_update(db: Session, run) -> Dict[str, Any]:
    calls = db.execute(
        select(ResearchCall).where(ResearchCall.is_published.is_(True))
    ).scalars().all()
    changed = 0
    for call in calls:
        before = call.status
        research_call_service.refresh_status(db, call)
        if call.status != before:
            changed += 1
    run["records_received"] = len(calls)
    run["records_saved"] = changed
    db.commit()
    return {"evaluated": len(calls), "status_changes": changed}


@_job("alert_engine")
def alert_engine(db: Session, run) -> Dict[str, Any]:
    alerts = db.execute(
        select(Alert).where(Alert.is_active.is_(True))
    ).scalars().all()
    run["records_received"] = len(alerts)
    fired = 0

    latest_snapshot = db.execute(
        select(TechnicalIndicatorSnapshot.as_of)
        .order_by(TechnicalIndicatorSnapshot.as_of.desc()).limit(1)
    ).scalar_one_or_none()

    for alert in alerts:
        alert.last_evaluated_at = datetime.now(tz=timezone.utc)
        if not alert_service.should_fire(alert):
            alert.last_evaluation_note = "Skipped: cooldown or already triggered."
            continue

        context = _alert_context(db, alert, latest_snapshot)
        outcome = alert_service.evaluate(alert, context)
        alert.last_evaluation_note = (
            outcome.body[:600] if outcome.body else "Condition not met."
        )
        if outcome.fired:
            alert_service.fire(db, alert, outcome)
            fired += 1
    run["records_saved"] = fired
    db.commit()
    return {"evaluated": len(alerts), "fired": fired}


def _alert_context(db: Session, alert: Alert,
                   snapshot_date) -> Dict[str, Any]:
    context: Dict[str, Any] = {}
    if alert.symbol:
        env = registry.fetch("quote", alert.symbol, db=db)
        if env.value:
            context.update({
                "ltp": env.value.ltp, "change_pct": env.value.change_pct,
                "volume": env.value.volume,
                "oi_change_pct": None,
                "source": env.source_name,
                "data_status": env.status.value,
                "observed_at": env.observed_at.isoformat()
                if env.observed_at else None,
            })
        if snapshot_date:
            tech = db.execute(
                select(TechnicalIndicatorSnapshot)
                .where(TechnicalIndicatorSnapshot.symbol == alert.symbol)
                .where(TechnicalIndicatorSnapshot.as_of == snapshot_date)
            ).scalars().first()
            if tech:
                context.update({
                    "rsi_14": tech.rsi_14, "ema_50": tech.ema_50,
                    "ema_20": tech.ema_20, "ema_9": tech.ema_9,
                    "volume_ratio_20": tech.volume_ratio_20d,
                })

    if alert.research_call_id:
        call = db.execute(
            select(ResearchCall).where(ResearchCall.id == alert.research_call_id)
        ).scalars().first()
        if call:
            context["call_status"] = call.status
            context["call_status_reason"] = call.status_reason

    if alert.alert_type in ("NEWS_IMPACT", "NEWS_KEYWORD") and alert.symbol:
        rows = db.execute(
            select(NewsArticle, NewsScore)
            .outerjoin(NewsScore, NewsScore.article_id == NewsArticle.id)
            .where(NewsArticle.primary_symbol == alert.symbol)
            .where(NewsArticle.published_at >= datetime.now(tz=timezone.utc)
                   - timedelta(hours=24))
            .order_by(NewsArticle.published_at.desc()).limit(20)
        ).all()
        context["news"] = [
            {
                "headline": a.headline, "publisher": a.publisher,
                "impact_score": s.impact_score if s else 0,
                "sentiment": s.sentiment if s else "NEUTRAL",
                "explanation": json.loads(s.explanation).get("text")
                if s and s.explanation else "",
            }
            for a, s in rows
        ]

    if alert.ipo_id:
        from app.models.ipo import IpoGmpHistory, IpoSubscription

        gmp_rows = db.execute(
            select(IpoGmpHistory).where(IpoGmpHistory.ipo_id == alert.ipo_id)
            .order_by(IpoGmpHistory.observed_on.desc()).limit(2)
        ).scalars().all()
        if gmp_rows:
            context["gmp"] = gmp_rows[0].gmp
            context["previous_gmp"] = gmp_rows[1].gmp if len(gmp_rows) > 1 else None
        subscription = db.execute(
            select(IpoSubscription).where(IpoSubscription.ipo_id == alert.ipo_id)
            .order_by(IpoSubscription.observed_at.desc()).limit(1)
        ).scalars().first()
        if subscription:
            context["total_times"] = subscription.total_times

    return context


# --------------------------------------------------------------------------
# End of day
# --------------------------------------------------------------------------


@_job("end_of_day_snapshot")
def end_of_day_snapshot(db: Session, run) -> Dict[str, Any]:
    """Roll the day's quotes into sector performance rows."""
    from app.models.market import SectorPerformance

    rows = db.execute(
        select(Instrument.sector, Quote.change_pct)
        .join(Quote, Quote.instrument_id == Instrument.id)
        .where(Instrument.sector.isnot(None))
        .where(Quote.change_pct.isnot(None))
    ).all()

    buckets: Dict[str, List[float]] = {}
    for sector, change in rows:
        buckets.setdefault(sector, []).append(change)

    today = date.today()
    saved = 0
    for sector, changes in buckets.items():
        existing = db.execute(
            select(SectorPerformance)
            .where(SectorPerformance.sector == sector)
            .where(SectorPerformance.as_of == today)
        ).scalars().first() or SectorPerformance(sector=sector, as_of=today)
        existing.change_pct = round(sum(changes) / len(changes), 3)
        existing.advancing = sum(1 for c in changes if c > 0)
        existing.declining = sum(1 for c in changes if c < 0)
        existing.constituents = len(changes)
        existing.provider = "computed"
        existing.source_name = "Average of stored quote changes"
        existing.data_status = DataStatus.ESTIMATED.value
        existing.observed_at = datetime.now(tz=timezone.utc)
        db.add(existing)
        saved += 1

    run["records_saved"] = saved
    db.commit()
    return {"sectors": saved,
            "note": "Equal-weighted average, not market-cap weighted."}


@_job("exchange_eod_ingest")
def exchange_eod_ingest(db: Session, run) -> Dict[str, Any]:
    """Store the exchange's published EOD files for the latest session.

    Runs after the archives are posted. Each dataset records its own audit row,
    so a day missing from the history can always be attributed either to the
    exchange not publishing or to this job not running.
    """
    from app.services.eod_ingest import ingest_session

    runs = ingest_session(db)
    received = sum(r.rows_seen for r in runs)
    saved = sum(r.rows_written for r in runs)
    failures = [f"{r.dataset}: {r.message}" for r in runs if r.status == "FAILED"]

    run["provider"] = "nse_archives"
    run["records_received"] = received
    run["records_saved"] = saved
    if failures:
        run["errors"] = failures

    return {
        "datasets": {r.dataset: r.status for r in runs},
        "rows_written": saved,
        # An empty block-deal register is a normal market outcome, so it is
        # reported rather than treated as a failure.
        "empty": [r.dataset for r in runs if r.status == "EMPTY"],
        "failed": [r.dataset for r in runs if r.status == "FAILED"],
    }


JOB_REGISTRY: Dict[str, Callable[..., Dict[str, Any]]] = {
    "instrument_sync": instrument_sync,
    "quote_refresh": quote_refresh,
    "history_refresh": history_refresh,
    "indicator_refresh": indicator_refresh,
    "news_refresh": news_refresh,
    "research_status_update": research_status_update,
    "alert_engine": alert_engine,
    "end_of_day_snapshot": end_of_day_snapshot,
    "exchange_eod_ingest": exchange_eod_ingest,
}
