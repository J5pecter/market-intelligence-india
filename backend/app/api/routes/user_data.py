"""Watchlists, alerts, paper trading and portfolio."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.filters import hide_demo
from app.api.deps import current_user, db_session
from app.models.instrument import Instrument
from app.models.market import Quote, TechnicalIndicatorSnapshot
from app.models.news import NewsArticle, NewsScore
from app.models.research import Catalyst, ResearchCall
from app.models.user import User
from app.models.user_data import (Alert, AlertEvent, PaperPosition,
                                  PortfolioHolding, PortfolioTransaction,
                                  Watchlist, WatchlistItem)
from app.providers.registry import registry
from app.services import audit
from app.services.alerts import ALERT_TYPES, alert_service
from app.services.portfolio import portfolio_service

router = APIRouter(tags=["user"])


# --------------------------------------------------------------------------
# Watchlists
# --------------------------------------------------------------------------


class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class WatchlistItemCreate(BaseModel):
    symbol: str
    segment: str = "EQUITY"
    expiry: Optional[date] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None
    note: Optional[str] = None


@router.get("/watchlists")
def list_watchlists(user: User = Depends(current_user),
                    db: Session = Depends(db_session)) -> Dict[str, Any]:
    lists = db.execute(
        select(Watchlist).where(Watchlist.user_id == user.id)
        .order_by(Watchlist.sort_order, Watchlist.name)
    ).scalars().all()
    return {
        "watchlists": [
            {"id": w.id, "name": w.name, "description": w.description,
             "item_count": len(w.items)}
            for w in lists
        ]
    }


@router.post("/watchlists", status_code=201)
def create_watchlist(payload: WatchlistCreate,
                     user: User = Depends(current_user),
                     db: Session = Depends(db_session)) -> Dict[str, Any]:
    existing = db.execute(
        select(Watchlist).where(Watchlist.user_id == user.id)
        .where(Watchlist.name == payload.name)
    ).scalars().first()
    if existing:
        raise HTTPException(400, "A watchlist with that name already exists.")
    watchlist = Watchlist(user_id=user.id, name=payload.name,
                          description=payload.description)
    db.add(watchlist)
    db.commit()
    return {"id": watchlist.id, "name": watchlist.name}


@router.delete("/watchlists/{watchlist_id}", status_code=204, response_class=Response,
                response_model=None)
def delete_watchlist(watchlist_id: str, user: User = Depends(current_user),
                     db: Session = Depends(db_session)) -> None:
    watchlist = _owned_watchlist(db, watchlist_id, user)
    db.delete(watchlist)
    db.commit()


@router.get("/watchlists/{watchlist_id}")
def watchlist_detail(watchlist_id: str, user: User = Depends(current_user),
                     db: Session = Depends(db_session)) -> Dict[str, Any]:
    watchlist = _owned_watchlist(db, watchlist_id, user)
    latest_snapshot = db.execute(
        select(TechnicalIndicatorSnapshot.as_of)
        .order_by(TechnicalIndicatorSnapshot.as_of.desc()).limit(1)
    ).scalar_one_or_none()

    rows = []
    for item in watchlist.items:
        env = registry.fetch("quote", item.symbol, db=db)
        quote = env.value

        tech = None
        if latest_snapshot:
            tech = db.execute(
                select(TechnicalIndicatorSnapshot)
                .where(TechnicalIndicatorSnapshot.symbol == item.symbol)
                .where(TechnicalIndicatorSnapshot.as_of == latest_snapshot)
            ).scalars().first()

        call = db.execute(
            hide_demo(select(ResearchCall), ResearchCall)
            .where(ResearchCall.symbol == item.symbol)
            .where(ResearchCall.is_published.is_(True))
            .order_by(ResearchCall.published_at.desc()).limit(1)
        ).scalars().first()

        news = db.execute(
            select(NewsArticle, NewsScore)
            .outerjoin(NewsScore, NewsScore.article_id == NewsArticle.id)
            .where(NewsArticle.primary_symbol == item.symbol)
            .order_by(NewsArticle.published_at.desc()).limit(1)
        ).first()

        catalyst = db.execute(
            select(Catalyst).where(Catalyst.symbol == item.symbol)
            .where(Catalyst.event_date >= date.today())
            .order_by(Catalyst.event_date).limit(1)
        ).scalars().first()

        rows.append({
            "id": item.id,
            "symbol": item.symbol, "segment": item.segment,
            "expiry": item.expiry.isoformat() if item.expiry else None,
            "strike": item.strike, "option_type": item.option_type,
            "note": item.note,
            "ltp": quote.ltp if quote else None,
            "change": quote.change if quote else None,
            "change_pct": quote.change_pct if quote else None,
            "rsi_14": tech.rsi_14 if tech else None,
            "volume_ratio": tech.volume_ratio_20d if tech else None,
            "trend": (
                "UP" if tech and tech.supertrend_dir and tech.supertrend_dir > 0
                else "DOWN" if tech and tech.supertrend_dir else None
            ),
            "research_score": tech.trend_score if tech else None,
            "signal": call.side if call else None,
            "signal_status": call.status if call else None,
            "risk": call.risk_rating if call else None,
            "latest_news": {
                "headline": news[0].headline,
                "impact": news[1].impact_score if news[1] else None,
                "published_at": news[0].published_at.isoformat()
                if news[0].published_at else None,
            } if news else None,
            "upcoming_event": {
                "title": catalyst.title,
                "date": catalyst.event_date.isoformat()
                if catalyst.event_date else None,
                "impact": catalyst.expected_impact,
            } if catalyst else None,
            "data_status": env.status.value,
            "is_demo": env.is_demo,
        })

    return {"id": watchlist.id, "name": watchlist.name,
            "description": watchlist.description, "items": rows,
            "indicator_snapshot_date": latest_snapshot.isoformat()
            if latest_snapshot else None}


@router.post("/watchlists/{watchlist_id}/items", status_code=201)
def add_watchlist_item(watchlist_id: str, payload: WatchlistItemCreate,
                       user: User = Depends(current_user),
                       db: Session = Depends(db_session)) -> Dict[str, Any]:
    watchlist = _owned_watchlist(db, watchlist_id, user)
    item = WatchlistItem(
        watchlist_id=watchlist.id, symbol=payload.symbol.upper(),
        segment=payload.segment.upper(), expiry=payload.expiry,
        strike=payload.strike, option_type=payload.option_type,
        note=payload.note,
    )
    db.add(item)
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001 - unique constraint
        db.rollback()
        raise HTTPException(400, "That instrument is already on this watchlist.") \
            from exc
    return {"id": item.id, "symbol": item.symbol}


@router.delete("/watchlists/{watchlist_id}/items/{item_id}", status_code=204, response_class=Response,
                response_model=None)
def remove_watchlist_item(watchlist_id: str, item_id: str,
                          user: User = Depends(current_user),
                          db: Session = Depends(db_session)) -> None:
    watchlist = _owned_watchlist(db, watchlist_id, user)
    item = db.execute(
        select(WatchlistItem).where(WatchlistItem.id == item_id)
        .where(WatchlistItem.watchlist_id == watchlist.id)
    ).scalars().first()
    if item is None:
        raise HTTPException(404, "Item not found on this watchlist.")
    db.delete(item)
    db.commit()


def _owned_watchlist(db: Session, watchlist_id: str, user: User) -> Watchlist:
    watchlist = db.execute(
        select(Watchlist).where(Watchlist.id == watchlist_id)
    ).scalars().first()
    if watchlist is None or watchlist.user_id != user.id:
        raise HTTPException(404, "Watchlist not found.")
    return watchlist


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------


class AlertCreate(BaseModel):
    name: str = ""
    symbol: Optional[str] = None
    segment: str = "EQUITY"
    alert_type: str
    condition: Dict[str, Any] = Field(default_factory=dict)
    research_call_id: Optional[str] = None
    ipo_id: Optional[str] = None
    channels: List[str] = Field(default_factory=lambda: ["in_app"])
    trigger_once: bool = True
    cooldown_minutes: int = Field(default=60, ge=1, le=10080)


@router.get("/alerts/types")
def alert_types() -> Dict[str, Any]:
    return {
        "types": [
            {"key": key, **spec} for key, spec in ALERT_TYPES.items()
        ],
        "channels": alert_service.available_channels(),
        "note": "Channels that report available=false need configuration in "
                "the environment before they will deliver.",
    }


@router.get("/alerts")
def list_alerts(user: User = Depends(current_user),
                db: Session = Depends(db_session)) -> Dict[str, Any]:
    rows = db.execute(
        select(Alert).where(Alert.user_id == user.id)
        .order_by(Alert.created_at.desc())
    ).scalars().all()
    return {
        "alerts": [
            {
                "id": a.id, "name": a.name, "symbol": a.symbol,
                "segment": a.segment, "alert_type": a.alert_type,
                "condition": json.loads(a.condition_json or "{}"),
                "channels": a.channels.split(","),
                "is_active": a.is_active, "trigger_once": a.trigger_once,
                "cooldown_minutes": a.cooldown_minutes,
                "trigger_count": a.trigger_count,
                "last_triggered_at": a.last_triggered_at.isoformat()
                if a.last_triggered_at else None,
                "last_evaluated_at": a.last_evaluated_at.isoformat()
                if a.last_evaluated_at else None,
                "last_evaluation_note": a.last_evaluation_note,
            }
            for a in rows
        ]
    }


@router.post("/alerts", status_code=201)
def create_alert(payload: AlertCreate, user: User = Depends(current_user),
                 db: Session = Depends(db_session)) -> Dict[str, Any]:
    if payload.alert_type not in ALERT_TYPES:
        raise HTTPException(
            400,
            f"Unknown alert type. Valid types: {', '.join(sorted(ALERT_TYPES))}",
        )
    required = ALERT_TYPES[payload.alert_type]["fields"]
    missing = [f for f in required if f not in payload.condition
               and f not in ("research_call_id",)]
    if missing:
        raise HTTPException(
            400, f"This alert type needs: {', '.join(missing)}."
        )

    alert = Alert(
        user_id=user.id,
        name=payload.name or f"{payload.symbol or 'Market'} "
                             f"{ALERT_TYPES[payload.alert_type]['label']}",
        symbol=payload.symbol.upper() if payload.symbol else None,
        segment=payload.segment.upper(),
        alert_type=payload.alert_type,
        condition_json=json.dumps(payload.condition),
        research_call_id=payload.research_call_id,
        ipo_id=payload.ipo_id,
        channels=",".join(payload.channels),
        trigger_once=payload.trigger_once,
        cooldown_minutes=payload.cooldown_minutes,
    )
    db.add(alert)
    audit.record(db, action="ALERT_CREATED", entity_type="alert",
                 entity_id=alert.id, actor_id=user.id, actor_email=user.email,
                 new_value={"type": alert.alert_type,
                            "condition": payload.condition})
    db.commit()
    return {"id": alert.id, "name": alert.name}


@router.delete("/alerts/{alert_id}", status_code=204, response_class=Response,
                response_model=None)
def delete_alert(alert_id: str, user: User = Depends(current_user),
                 db: Session = Depends(db_session)) -> None:
    alert = db.execute(
        select(Alert).where(Alert.id == alert_id)
        .where(Alert.user_id == user.id)
    ).scalars().first()
    if alert is None:
        raise HTTPException(404, "Alert not found.")
    db.delete(alert)
    db.commit()


@router.get("/alerts/events")
def alert_events(unread_only: bool = False, limit: int = Query(50, le=200),
                 user: User = Depends(current_user),
                 db: Session = Depends(db_session)) -> Dict[str, Any]:
    stmt = (
        select(AlertEvent).where(AlertEvent.user_id == user.id)
        .order_by(AlertEvent.created_at.desc()).limit(limit)
    )
    if unread_only:
        stmt = stmt.where(AlertEvent.is_read.is_(False))
    rows = db.execute(stmt).scalars().all()
    return {
        "events": [
            {
                "id": e.id, "alert_id": e.alert_id, "title": e.title,
                "body": e.body, "triggered_value": e.triggered_value,
                "evidence": json.loads(e.evidence_json or "{}"),
                "delivery_status": e.delivery_status,
                "is_read": e.is_read,
                "created_at": e.created_at.isoformat(),
            }
            for e in rows
        ],
        "unread_count": db.execute(
            select(AlertEvent).where(AlertEvent.user_id == user.id)
            .where(AlertEvent.is_read.is_(False))
        ).scalars().all().__len__(),
    }


@router.post("/alerts/events/{event_id}/read")
def mark_event_read(event_id: str, user: User = Depends(current_user),
                    db: Session = Depends(db_session)) -> Dict[str, Any]:
    event = db.execute(
        select(AlertEvent).where(AlertEvent.id == event_id)
        .where(AlertEvent.user_id == user.id)
    ).scalars().first()
    if event is None:
        raise HTTPException(404, "Alert event not found.")
    event.is_read = True
    db.commit()
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Paper trading
# --------------------------------------------------------------------------


class PaperPositionCreate(BaseModel):
    symbol: str
    segment: str = "EQUITY"
    side: str = "LONG"
    quantity: int = Field(gt=0)
    lot_size: int = Field(default=1, ge=1)
    entry_price: float = Field(gt=0)
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    expiry: Optional[date] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None
    linked_call_id: Optional[str] = None
    note: Optional[str] = None


@router.get("/paper")
def paper_portfolio(user: User = Depends(current_user),
                    db: Session = Depends(db_session)) -> Dict[str, Any]:
    return portfolio_service.paper_snapshot(db, user.id)


@router.post("/paper/positions", status_code=201)
def open_paper_position(payload: PaperPositionCreate,
                        user: User = Depends(current_user),
                        db: Session = Depends(db_session)) -> Dict[str, Any]:
    position = PaperPosition(
        user_id=user.id, symbol=payload.symbol.upper(),
        segment=payload.segment.upper(), side=payload.side.upper(),
        quantity=payload.quantity, lot_size=payload.lot_size,
        entry_price=payload.entry_price, entry_at=datetime.now(tz=timezone.utc),
        stop_loss=payload.stop_loss, target=payload.target,
        expiry=payload.expiry, strike=payload.strike,
        option_type=payload.option_type, linked_call_id=payload.linked_call_id,
        note=payload.note,
    )
    db.add(position)
    db.commit()
    return {"id": position.id, "symbol": position.symbol,
            "notice": "Paper position only. No order was placed anywhere."}


class PaperClose(BaseModel):
    exit_price: float = Field(gt=0)


@router.post("/paper/positions/{position_id}/close")
def close_paper_position(position_id: str, payload: PaperClose,
                         user: User = Depends(current_user),
                         db: Session = Depends(db_session)) -> Dict[str, Any]:
    position = db.execute(
        select(PaperPosition).where(PaperPosition.id == position_id)
        .where(PaperPosition.user_id == user.id)
    ).scalars().first()
    if position is None:
        raise HTTPException(404, "Paper position not found.")
    if position.status == "CLOSED":
        raise HTTPException(400, "That position is already closed.")
    portfolio_service.close_paper_position(db, position, payload.exit_price)
    db.commit()
    return {"id": position.id, "realised_pnl": position.realised_pnl,
            "charges": position.charges}


# --------------------------------------------------------------------------
# Portfolio
# --------------------------------------------------------------------------


class HoldingCreate(BaseModel):
    symbol: str
    segment: str = "EQUITY"
    quantity: float = Field(gt=0)
    average_cost: float = Field(ge=0)
    sector: Optional[str] = None
    note: Optional[str] = None


class TransactionCreate(BaseModel):
    symbol: str
    segment: str = "EQUITY"
    txn_type: str
    quantity: float = 0.0
    price: float = 0.0
    amount: Optional[float] = None
    charges: float = 0.0
    traded_on: date
    note: Optional[str] = None


@router.get("/portfolio")
def portfolio(user: User = Depends(current_user),
              db: Session = Depends(db_session)) -> Dict[str, Any]:
    return portfolio_service.snapshot(db, user.id)


@router.post("/portfolio/holdings", status_code=201)
def upsert_holding(payload: HoldingCreate, user: User = Depends(current_user),
                   db: Session = Depends(db_session)) -> Dict[str, Any]:
    existing = db.execute(
        select(PortfolioHolding).where(PortfolioHolding.user_id == user.id)
        .where(PortfolioHolding.symbol == payload.symbol.upper())
        .where(PortfolioHolding.segment == payload.segment.upper())
    ).scalars().first()

    if existing:
        existing.quantity = payload.quantity
        existing.average_cost = payload.average_cost
        existing.sector = payload.sector
        existing.note = payload.note
        holding = existing
    else:
        holding = PortfolioHolding(
            user_id=user.id, symbol=payload.symbol.upper(),
            segment=payload.segment.upper(), quantity=payload.quantity,
            average_cost=payload.average_cost, sector=payload.sector,
            note=payload.note,
        )
        db.add(holding)
    db.commit()
    return {"id": holding.id, "symbol": holding.symbol}


@router.delete("/portfolio/holdings/{holding_id}", status_code=204, response_class=Response,
                response_model=None)
def delete_holding(holding_id: str, user: User = Depends(current_user),
                   db: Session = Depends(db_session)) -> None:
    holding = db.execute(
        select(PortfolioHolding).where(PortfolioHolding.id == holding_id)
        .where(PortfolioHolding.user_id == user.id)
    ).scalars().first()
    if holding is None:
        raise HTTPException(404, "Holding not found.")
    db.delete(holding)
    db.commit()


@router.post("/portfolio/transactions", status_code=201)
def add_transaction(payload: TransactionCreate,
                    user: User = Depends(current_user),
                    db: Session = Depends(db_session)) -> Dict[str, Any]:
    if payload.txn_type not in ("BUY", "SELL", "DIVIDEND", "BONUS", "SPLIT",
                               "CHARGE"):
        raise HTTPException(400, "Unsupported transaction type.")
    txn = PortfolioTransaction(
        user_id=user.id, symbol=payload.symbol.upper(),
        segment=payload.segment.upper(), txn_type=payload.txn_type,
        quantity=payload.quantity, price=payload.price, amount=payload.amount,
        charges=payload.charges, traded_on=payload.traded_on, note=payload.note,
    )
    db.add(txn)
    db.commit()
    return {"id": txn.id}
