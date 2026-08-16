"""Research calls, reports, source performance, scanners, backtests."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user_optional, db_session, rate_limit
from app.core.compliance import disclaimers
from app.models.research import (ResearchCall, ResearchCallVersion,
                                 ResearchReport, ResearchSource)
from app.models.user import User
from app.models.user_data import Backtest, BacktestTrade
from app.providers.registry import registry
from app.services.backtest import (BrokerageModel, StrategySpec,
                                   backtest_service)
from app.services.research import research_service
from app.services.research_calls import research_call_service
from app.services.scanner import Filter, scanner_service
from app.services.technical_analysis import bars_to_frame
from app.services.trade_status import position_size, simulate_pnl

router = APIRouter(tags=["research"])


# --------------------------------------------------------------------------
# Research calls
# --------------------------------------------------------------------------


@router.get("/research/calls", dependencies=[Depends(rate_limit("calls", 120))])
def list_calls(
    segment: Optional[str] = None,
    status: Optional[str] = None,
    source_type: Optional[str] = None,
    symbol: Optional[str] = None,
    limit: int = Query(default=60, le=200),
    refresh: bool = Query(default=True,
                          description="re-evaluate status from live price"),
    db: Session = Depends(db_session),
) -> Dict[str, Any]:
    stmt = (
        select(ResearchCall)
        .where(ResearchCall.is_published.is_(True))
        .order_by(ResearchCall.published_at.desc())
        .limit(limit)
    )
    if segment:
        stmt = stmt.where(ResearchCall.segment == segment.upper())
    if status:
        stmt = stmt.where(ResearchCall.status == status.upper())
    if source_type:
        stmt = stmt.where(ResearchCall.source_type == source_type.upper())
    if symbol:
        stmt = stmt.where(ResearchCall.symbol == symbol.upper())

    calls = db.execute(stmt).scalars().all()
    rows = []
    for call in calls:
        source = db.execute(
            select(ResearchSource).where(ResearchSource.id == call.source_id)
        ).scalars().first() if call.source_id else None
        evaluation = (
            research_call_service.refresh_status(db, call) if refresh else None
        )
        rows.append(research_call_service.to_card(call, evaluation, source))
    if refresh:
        db.commit()

    return {
        "count": len(rows),
        "calls": rows,
        "legend": {
            "EXTERNAL_RESEARCH": "Published by a third party and reproduced "
                                 "with attribution. Not originated here.",
            "PLATFORM_GENERATED": "Calculated by this platform's engines from "
                                  "the evidence shown. Not advice.",
        },
        "disclaimers": disclaimers(),
    }


@router.get("/research/calls/{call_id}")
def call_detail(call_id: str,
                db: Session = Depends(db_session)) -> Dict[str, Any]:
    call = db.execute(
        select(ResearchCall).where(ResearchCall.id == call_id)
    ).scalars().first()
    if call is None:
        raise HTTPException(404, "Research call not found.")

    source = db.execute(
        select(ResearchSource).where(ResearchSource.id == call.source_id)
    ).scalars().first() if call.source_id else None

    evaluation = research_call_service.refresh_status(db, call)
    versions = db.execute(
        select(ResearchCallVersion)
        .where(ResearchCallVersion.call_id == call.id)
        .order_by(ResearchCallVersion.version.desc())
    ).scalars().all()
    db.commit()

    return {
        **research_call_service.to_card(call, evaluation, source),
        "evidence": _json(call.evidence_json),
        "catalysts": _json(call.catalysts_json),
        "versions": [
            {
                "version": v.version,
                "changed_by": v.changed_by,
                "change_reason": v.change_reason,
                "changed_fields": _json(v.changed_fields),
                "created_at": v.created_at.isoformat(),
            }
            for v in versions
        ],
        "version_note": (
            "Published records are never overwritten. Each change writes a new "
            "version with the fields that moved and the reason given."
        ),
    }


@router.get("/research/sources/performance")
def source_performance(source: Optional[str] = None,
                       db: Session = Depends(db_session)) -> Dict[str, Any]:
    return {
        "rows": research_call_service.source_performance(db, source),
        "note": (
            "Performance is computed from stored price history after each "
            "call's publication timestamp. Records are never edited after "
            "publication; every change is in the audit log."
        ),
    }


# --------------------------------------------------------------------------
# Research reports
# --------------------------------------------------------------------------


@router.get("/research/report/{symbol}")
def research_report(symbol: str, interval: str = "1d",
                    db: Session = Depends(db_session)) -> Dict[str, Any]:
    """A full company research report assembled from the evidence chain."""
    bundle = research_service.build_instrument_research(
        db, symbol.upper(), interval=interval
    )
    payload = bundle.to_dict()

    sections = {
        "executive_summary": _executive_summary(bundle),
        "business": (payload.get("fundamental") or {}).get("profile"),
        "financial_performance": {
            "statements": (payload.get("fundamental") or {}).get("statements"),
            "quality_score": (payload.get("fundamental") or {}).get("quality_score"),
        },
        "valuation": (payload.get("fundamental") or {}).get("peer_context"),
        "technical_view": payload.get("technical"),
        "news": payload.get("news"),
        "institutional_activity": {
            "note": "FII/DII holding is shown in the fundamentals block when a "
                    "provider supplies it. Daily flow data is not available "
                    "from the free providers wired up by default.",
            "holdings": {
                k: v for k, v in
                ((payload.get("fundamental") or {}).get("ratios") or {}).items()
                if k in ("fii_holding", "dii_holding", "promoter_holding",
                         "promoter_pledge")
            },
        },
        "risks": payload.get("risk"),
        "catalysts": payload.get("catalysts"),
        "bull_base_bear": _scenarios(bundle),
        "what_could_go_wrong": payload.get("why_not"),
        "historical_similar_setups": payload.get("historical_analogues"),
        "conclusion": _conclusion(bundle),
    }

    stored = db.execute(
        select(ResearchReport).where(ResearchReport.symbol == symbol.upper())
        .order_by(ResearchReport.version.desc()).limit(1)
    ).scalars().first()

    return {
        "symbol": symbol.upper(),
        "company_name": bundle.company_name,
        "generated_at": bundle.generated_at.isoformat(),
        "sections": sections,
        "scorecard": payload["scorecard"],
        "confidence": payload["confidence"],
        "sources": payload["sources"],
        "warnings": payload["warnings"],
        "stored_version": stored.version if stored else None,
        "disclaimers": disclaimers(),
        "methodology": "/methodology",
    }


def _executive_summary(bundle) -> Dict[str, Any]:
    confidence = bundle.confidence or {}
    return {
        "state": confidence.get("state"),
        "confidence": confidence.get("overall"),
        "technical": (bundle.technical or {}).get("explanation"),
        "fundamental": (
            ((bundle.fundamental or {}).get("evidence_chain") or {}).get("summary")
            if bundle.fundamental and bundle.fundamental.get("available")
            else "Fundamental data unavailable."
        ),
        "options": (bundle.options or {}).get("explanation"),
        "risk": (bundle.risk or {}).get("explanation"),
        "conflict": confidence.get("conflict", {}).get("message"),
        "caveat": (
            "This summary restates the evidence panels below. It introduces no "
            "conclusion the panels do not support."
        ),
    }


def _scenarios(bundle) -> Dict[str, Any]:
    setup = bundle.trade_setup or {}
    analogues = (bundle.analogues or {}).get("statistics", {})
    if not setup.get("available"):
        return {
            "available": False,
            "reason": setup.get("reason", "No directional setup was derived."),
        }
    targets = setup.get("targets", [])
    return {
        "available": True,
        "bull": {
            "description": "Price reaches the furthest target derived from the "
                           "current ATR.",
            "level": targets[-1]["price"] if targets else None,
            "return_pct": targets[-1]["return_from_entry_pct"] if targets else None,
            "historical_reference": analogues.get("best_return_pct"),
        },
        "base": {
            "description": "Price oscillates around the entry zone and the "
                           "structure neither confirms nor breaks.",
            "level": setup.get("entry_zone", [None, None])[1],
            "return_pct": 0.0,
            "historical_reference": analogues.get("median_return_pct"),
        },
        "bear": {
            "description": "Price reaches the stop derived from 1.5x ATR.",
            "level": setup.get("stop_loss"),
            "return_pct": (
                -abs(setup["status"]["downside_to_stop_pct"])
                if setup.get("status", {}).get("downside_to_stop_pct") is not None
                else None
            ),
            "historical_reference": analogues.get("worst_return_pct"),
        },
        "note": (
            "These are arithmetic levels, not probability-weighted forecasts. "
            "The historical references describe past similar configurations in "
            "this instrument only."
        ),
    }


def _conclusion(bundle) -> Dict[str, Any]:
    confidence = bundle.confidence or {}
    state = confidence.get("state")
    text = {
        "EVIDENCE_ALIGNED": (
            "The dimensions that could be scored point the same way. That is "
            "agreement between measurements, not a forecast."
        ),
        "EVIDENCE_LEANING": (
            "The evidence leans one way but not decisively."
        ),
        "MIXED_WAIT_FOR_CONFIRMATION": (
            "The evidence conflicts across dimensions. No directional "
            "conclusion is offered; that is the honest reading."
        ),
        "EVIDENCE_WEAK": (
            "The scored evidence is weak across the board."
        ),
        "INSUFFICIENT_EVIDENCE": (
            "Too little of the evidence base could be computed to characterise "
            "this instrument."
        ),
    }.get(state, "No conclusion could be drawn.")
    return {
        "state": state,
        "text": text,
        "confidence": confidence.get("overall"),
        "explanation": confidence.get("explanation"),
        "disclaimer": disclaimers()["generated_signal"],
    }


# --------------------------------------------------------------------------
# Calculators
# --------------------------------------------------------------------------


class PositionSizeRequest(BaseModel):
    capital: float = Field(gt=0)
    max_loss_pct: float = Field(gt=0, le=100)
    entry: float = Field(gt=0)
    stop_loss: float = Field(ge=0)
    lot_size: int = Field(default=1, ge=1)


@router.post("/calculators/position-size")
def calculate_position_size(payload: PositionSizeRequest) -> Dict[str, Any]:
    return position_size(
        capital=payload.capital, max_loss_pct=payload.max_loss_pct,
        entry=payload.entry, stop_loss=payload.stop_loss,
        lot_size=payload.lot_size,
    )


class PnlRequest(BaseModel):
    capital: float = Field(gt=0)
    entry: float = Field(gt=0)
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    quantity: int = Field(gt=0)
    lot_size: int = Field(default=1, ge=1)
    segment: str = Field(default="EQUITY_DELIVERY")
    side: str = Field(default="BUY")
    brokerage_per_order: Optional[float] = None


@router.post("/calculators/pnl")
def calculate_pnl(payload: PnlRequest) -> Dict[str, Any]:
    model = BrokerageModel()
    if payload.brokerage_per_order is not None:
        model.brokerage_per_order = payload.brokerage_per_order
    return simulate_pnl(
        capital=payload.capital, entry=payload.entry,
        stop_loss=payload.stop_loss, target=payload.target,
        quantity=payload.quantity, lot_size=payload.lot_size,
        segment=payload.segment, side=payload.side, model=model,
    )


# --------------------------------------------------------------------------
# Scanners
# --------------------------------------------------------------------------


@router.get("/scanners")
def list_scanners() -> Dict[str, Any]:
    return {
        "scanners": scanner_service.list_scanners(),
        "available_fields": sorted(
            __import__("app.services.scanner", fromlist=["FIELD_MAP"]).FIELD_MAP
        ),
        "operators": [">", ">=", "<", "<=", "==", "!=", "between"],
        "note": "Scans run against the latest stored indicator snapshot, not "
                "live prices. The snapshot date is returned with every result.",
    }


class ScanRequest(BaseModel):
    scanner_key: Optional[str] = None
    filters: List[Dict[str, Any]] = Field(default_factory=list)
    logic: str = "AND"
    limit: int = Field(default=200, le=500)
    segment: str = "EQUITY"


@router.post("/scanners/run", dependencies=[Depends(rate_limit("scan", 60))])
def run_scanner(payload: ScanRequest,
                db: Session = Depends(db_session)) -> Dict[str, Any]:
    try:
        filters = [Filter(**f) for f in payload.filters]
    except TypeError as exc:
        raise HTTPException(
            400,
            "Each filter needs field, op and value (or compare_to_field).",
        ) from exc

    result = scanner_service.run(
        db, scanner_key=payload.scanner_key, filters=filters,
        logic=payload.logic, limit=payload.limit, segment=payload.segment,
    )
    return result.to_dict()


# --------------------------------------------------------------------------
# Backtesting
# --------------------------------------------------------------------------


class BacktestRequest(BaseModel):
    name: str = "Untitled strategy"
    symbols: List[str] = Field(min_length=1, max_length=25)
    strategy: Dict[str, Any]
    start_date: date
    end_date: date
    in_sample_end: Optional[date] = None
    interval: str = "1d"
    initial_capital: float = Field(default=100_000.0, gt=0)
    walk_forward_folds: int = Field(default=0, ge=0, le=10)


@router.post("/backtests", dependencies=[Depends(rate_limit("backtest", 20))])
def create_backtest(payload: BacktestRequest,
                    user: Optional[User] = Depends(current_user_optional),
                    db: Session = Depends(db_session)) -> Dict[str, Any]:
    if payload.end_date <= payload.start_date:
        raise HTTPException(400, "end_date must be after start_date.")

    record = Backtest(
        user_id=user.id if user else None,
        name=payload.name,
        strategy_json=json.dumps(payload.strategy),
        universe_json=json.dumps([s.upper() for s in payload.symbols]),
        start_date=payload.start_date, end_date=payload.end_date,
        in_sample_end=payload.in_sample_end, interval=payload.interval,
        status="RUNNING",
    )
    db.add(record)
    db.flush()

    data: Dict[str, pd.DataFrame] = {}
    fetch_warnings: List[str] = []
    for symbol in payload.symbols:
        env = registry.fetch("history", symbol.upper(),
                             interval=payload.interval,
                             start=payload.start_date, end=payload.end_date,
                             db=db)
        if not env.is_usable or not env.value:
            fetch_warnings.append(
                f"{symbol.upper()}: no bars ({env.notes or 'provider returned nothing'})"
            )
            continue
        frame = bars_to_frame(env.value)
        # Trim strictly to the requested window - a provider may return more.
        frame = frame[
            (frame.index.date >= payload.start_date)
            & (frame.index.date <= payload.end_date)
        ]
        data[symbol.upper()] = frame

    if not data:
        record.status = "FAILED"
        record.error = "; ".join(fetch_warnings) or "No price data available."
        db.commit()
        raise HTTPException(
            422,
            {"message": "No price history could be loaded for the requested "
                        "symbols and window.", "details": fetch_warnings},
        )

    try:
        spec = StrategySpec.from_dict(payload.strategy)
    except (TypeError, ValueError) as exc:
        record.status = "FAILED"
        record.error = str(exc)
        db.commit()
        raise HTTPException(400, f"Invalid strategy: {exc}") from exc

    result = backtest_service.run(
        spec, data, initial_capital=payload.initial_capital,
        in_sample_end=payload.in_sample_end,
        walk_forward_folds=payload.walk_forward_folds,
    )

    if "error" in result:
        record.status = "FAILED"
        record.error = result["error"]
        db.commit()
        raise HTTPException(400, result["error"])

    record.status = "COMPLETED"
    record.metrics_json = json.dumps(result["metrics"], default=str)
    record.in_sample_metrics_json = json.dumps(
        result.get("in_sample_metrics"), default=str
    ) if result.get("in_sample_metrics") else None
    record.out_of_sample_metrics_json = json.dumps(
        result.get("out_of_sample_metrics"), default=str
    ) if result.get("out_of_sample_metrics") else None
    record.walk_forward_json = json.dumps(
        result.get("walk_forward"), default=str
    ) if result.get("walk_forward") else None
    record.equity_curve_json = json.dumps(result["equity_curve"], default=str)
    record.assumptions_json = json.dumps(result["assumptions"], default=str)
    record.bars_used = result["bars_used"]
    record.data_warnings = json.dumps(
        (result.get("warnings") or []) + fetch_warnings
    )

    for trade in result["trades"]:
        db.add(BacktestTrade(
            backtest_id=record.id, symbol=trade["symbol"],
            direction=trade["direction"],
            entry_date=date.fromisoformat(trade["entry_date"]),
            entry_price=trade["entry_price"],
            exit_date=date.fromisoformat(trade["exit_date"])
            if trade["exit_date"] else None,
            exit_price=trade["exit_price"], exit_reason=trade["exit_reason"],
            quantity=trade["quantity"], gross_pnl=trade["gross_pnl"],
            costs=trade["costs"], net_pnl=trade["net_pnl"],
            return_pct=trade["return_pct"], holding_days=trade["holding_bars"],
            sample=trade["sample"],
        ))
    db.commit()

    return {
        "id": record.id,
        **result,
        "warnings": (result.get("warnings") or []) + fetch_warnings,
        "integrity_note": (
            "Entries and rule-based exits fill at the next bar's open. When one "
            "bar touches both the stop and the target, the stop is taken. Costs "
            "are deducted on both legs."
        ),
    }


@router.get("/backtests")
def list_backtests(user: Optional[User] = Depends(current_user_optional),
                   db: Session = Depends(db_session)) -> Dict[str, Any]:
    stmt = select(Backtest).order_by(Backtest.created_at.desc()).limit(50)
    if user:
        stmt = stmt.where(Backtest.user_id == user.id)
    rows = db.execute(stmt).scalars().all()
    return {
        "rows": [
            {
                "id": b.id, "name": b.name, "status": b.status,
                "start_date": b.start_date.isoformat(),
                "end_date": b.end_date.isoformat(),
                "interval": b.interval,
                "universe": _json(b.universe_json),
                "metrics": _json(b.metrics_json),
                "created_at": b.created_at.isoformat(),
                "error": b.error,
            }
            for b in rows
        ]
    }


@router.get("/backtests/{backtest_id}")
def backtest_detail(backtest_id: str,
                    db: Session = Depends(db_session)) -> Dict[str, Any]:
    record = db.execute(
        select(Backtest).where(Backtest.id == backtest_id)
    ).scalars().first()
    if record is None:
        raise HTTPException(404, "Backtest not found.")
    trades = db.execute(
        select(BacktestTrade).where(BacktestTrade.backtest_id == record.id)
        .order_by(BacktestTrade.entry_date)
    ).scalars().all()

    return {
        "id": record.id, "name": record.name, "status": record.status,
        "strategy": _json(record.strategy_json),
        "universe": _json(record.universe_json),
        "start_date": record.start_date.isoformat(),
        "end_date": record.end_date.isoformat(),
        "in_sample_end": record.in_sample_end.isoformat()
        if record.in_sample_end else None,
        "metrics": _json(record.metrics_json),
        "in_sample_metrics": _json(record.in_sample_metrics_json),
        "out_of_sample_metrics": _json(record.out_of_sample_metrics_json),
        "walk_forward": _json(record.walk_forward_json),
        "equity_curve": _json(record.equity_curve_json),
        "assumptions": _json(record.assumptions_json),
        "warnings": _json(record.data_warnings),
        "bars_used": record.bars_used,
        "trades": [
            {
                "symbol": t.symbol, "direction": t.direction,
                "entry_date": t.entry_date.isoformat(),
                "entry_price": t.entry_price,
                "exit_date": t.exit_date.isoformat() if t.exit_date else None,
                "exit_price": t.exit_price, "exit_reason": t.exit_reason,
                "net_pnl": t.net_pnl, "return_pct": t.return_pct,
                "holding_days": t.holding_days, "sample": t.sample,
            }
            for t in trades
        ],
    }


def _json(raw: Optional[str]):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
