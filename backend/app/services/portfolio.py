"""Portfolio and paper-trading analytics.

No order ever reaches a broker from this module. Paper positions are marked to
whatever price the provider chain returns, and every aggregate reports how many
of its constituents could actually be priced - a portfolio value computed from
half the holdings is not a portfolio value.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.instrument import Instrument
from app.models.user_data import (PaperPosition, PortfolioHolding,
                                  PortfolioTransaction)
from app.providers.registry import registry
from app.services.trade_status import estimate_charges


class PortfolioService:

    # -- holdings ----------------------------------------------------------

    def snapshot(self, db: Session, user_id: str) -> Dict[str, Any]:
        holdings = db.execute(
            select(PortfolioHolding).where(PortfolioHolding.user_id == user_id)
        ).scalars().all()

        if not holdings:
            return {
                "holdings": [], "totals": _empty_totals(),
                "allocation": {}, "note": "No holdings have been entered.",
            }

        rows: List[Dict[str, Any]] = []
        priced = 0
        invested = market_value = 0.0

        for holding in holdings:
            env = registry.fetch("quote", holding.symbol, db=db)
            ltp = env.value.ltp if env.value else None
            cost = holding.quantity * holding.average_cost
            invested += cost
            value = None
            if ltp is not None:
                value = holding.quantity * ltp
                market_value += value
                priced += 1

            sector = holding.sector or self._sector(db, holding.symbol)
            rows.append({
                "symbol": holding.symbol,
                "segment": holding.segment,
                "quantity": holding.quantity,
                "average_cost": holding.average_cost,
                "invested": round(cost, 2),
                "ltp": ltp,
                "market_value": round(value, 2) if value is not None else None,
                "unrealised_pnl": round(value - cost, 2) if value is not None else None,
                "unrealised_pnl_pct": round((value / cost - 1) * 100.0, 2)
                if value is not None and cost else None,
                "sector": sector,
                "priced": ltp is not None,
                "provenance": env.to_dict(),
            })

        coverage = priced / len(holdings) * 100.0
        unrealised = market_value - sum(
            r["invested"] for r in rows if r["priced"]
        )

        realised = self._realised_pnl(db, user_id)
        dividends = self._dividend_income(db, user_id)

        return {
            "holdings": sorted(rows, key=lambda r: (r["market_value"] or 0),
                               reverse=True),
            "totals": {
                "invested": round(invested, 2),
                "market_value": round(market_value, 2),
                "unrealised_pnl": round(unrealised, 2),
                "unrealised_pnl_pct": round(
                    unrealised / sum(r["invested"] for r in rows if r["priced"])
                    * 100.0, 2
                ) if any(r["priced"] for r in rows) else None,
                "realised_pnl": realised,
                "dividend_income": dividends,
                "priced_holdings": priced,
                "total_holdings": len(holdings),
                "pricing_coverage_pct": round(coverage, 1),
            },
            "allocation": self._allocation(rows),
            "concentration": self._concentration(rows, market_value),
            "top_winners": sorted(
                [r for r in rows if r["unrealised_pnl_pct"] is not None],
                key=lambda r: r["unrealised_pnl_pct"], reverse=True
            )[:5],
            "top_losers": sorted(
                [r for r in rows if r["unrealised_pnl_pct"] is not None],
                key=lambda r: r["unrealised_pnl_pct"]
            )[:5],
            "xirr": self._xirr(db, user_id, market_value),
            "warnings": (
                [f"Only {priced} of {len(holdings)} holdings could be priced; "
                 f"totals cover those holdings only."]
                if priced < len(holdings) else []
            ),
        }

    # -- paper trading -----------------------------------------------------

    def paper_snapshot(self, db: Session, user_id: str) -> Dict[str, Any]:
        positions = db.execute(
            select(PaperPosition).where(PaperPosition.user_id == user_id)
        ).scalars().all()

        open_rows: List[Dict[str, Any]] = []
        closed_rows: List[Dict[str, Any]] = []
        unrealised = realised = 0.0

        for position in positions:
            units = position.quantity * (position.lot_size or 1)
            if position.status == "OPEN":
                env = registry.fetch("quote", position.symbol, db=db)
                ltp = env.value.ltp if env.value else None
                pnl = None
                if ltp is not None:
                    pnl = (ltp - position.entry_price) * units
                    if position.side == "SHORT":
                        pnl = -pnl
                    unrealised += pnl
                open_rows.append({
                    "id": position.id, "symbol": position.symbol,
                    "segment": position.segment, "side": position.side,
                    "quantity": position.quantity, "lot_size": position.lot_size,
                    "entry_price": position.entry_price,
                    "entry_at": position.entry_at.isoformat(),
                    "stop_loss": position.stop_loss, "target": position.target,
                    "ltp": ltp,
                    "unrealised_pnl": round(pnl, 2) if pnl is not None else None,
                    "unrealised_pnl_pct": round(
                        pnl / (position.entry_price * units) * 100.0, 2
                    ) if pnl is not None and position.entry_price else None,
                    "exposure": round(position.entry_price * units, 2),
                    "provenance": env.to_dict(),
                })
            else:
                realised += position.realised_pnl or 0.0
                closed_rows.append({
                    "id": position.id, "symbol": position.symbol,
                    "segment": position.segment, "side": position.side,
                    "entry_price": position.entry_price,
                    "exit_price": position.exit_price,
                    "entry_at": position.entry_at.isoformat(),
                    "exit_at": position.exit_at.isoformat()
                    if position.exit_at else None,
                    "realised_pnl": position.realised_pnl,
                    "charges": position.charges,
                })

        wins = [r for r in closed_rows if (r["realised_pnl"] or 0) > 0]
        exposure_by_segment: Dict[str, float] = defaultdict(float)
        for row in open_rows:
            exposure_by_segment[row["segment"]] += row["exposure"]

        return {
            "open_positions": open_rows,
            "closed_positions": sorted(
                closed_rows, key=lambda r: r["exit_at"] or "", reverse=True
            )[:100],
            "summary": {
                "open_count": len(open_rows),
                "closed_count": len(closed_rows),
                "unrealised_pnl": round(unrealised, 2),
                "realised_pnl": round(realised, 2),
                "total_pnl": round(unrealised + realised, 2),
                "win_rate_pct": round(len(wins) / len(closed_rows) * 100.0, 1)
                if closed_rows else None,
                "total_exposure": round(sum(r["exposure"] for r in open_rows), 2),
                "exposure_by_segment": {
                    k: round(v, 2) for k, v in exposure_by_segment.items()
                },
                "max_drawdown_note": (
                    "Drawdown on paper positions is measured from closed-trade "
                    "P&L only; intra-position excursions are not tracked here."
                ),
            },
            "notice": (
                "Paper trading is a simulation. No order is placed with any "
                "broker, fills are assumed at the prices you enter, and real "
                "execution would differ."
            ),
        }

    def close_paper_position(self, db: Session, position: PaperPosition,
                             exit_price: float) -> PaperPosition:
        units = position.quantity * (position.lot_size or 1)
        gross = (exit_price - position.entry_price) * units
        if position.side == "SHORT":
            gross = -gross
        segment = (
            "OPTION" if position.segment == "OPTION"
            else "FUTURE" if position.segment == "FUTURE"
            else "EQUITY_DELIVERY"
        )
        buy_value = units * min(position.entry_price, exit_price)
        sell_value = units * max(position.entry_price, exit_price)
        charges = estimate_charges(buy_value, sell_value, segment)["total"]

        position.exit_price = exit_price
        position.exit_at = datetime.now(tz=timezone.utc)
        position.status = "CLOSED"
        position.charges = round(charges, 2)
        position.realised_pnl = round(gross - charges, 2)
        db.flush()
        return position

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _sector(db: Session, symbol: str) -> Optional[str]:
        return db.execute(
            select(Instrument.sector).where(Instrument.symbol == symbol).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def _allocation(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        priced = [r for r in rows if r["market_value"] is not None]
        total = sum(r["market_value"] for r in priced)
        if not total:
            return {}

        by_sector: Dict[str, float] = defaultdict(float)
        by_segment: Dict[str, float] = defaultdict(float)
        for row in priced:
            by_sector[row["sector"] or "Unclassified"] += row["market_value"]
            by_segment[row["segment"]] += row["market_value"]

        return {
            "by_sector": [
                {"name": k, "value": round(v, 2),
                 "weight_pct": round(v / total * 100.0, 2)}
                for k, v in sorted(by_sector.items(), key=lambda kv: -kv[1])
            ],
            "by_segment": [
                {"name": k, "value": round(v, 2),
                 "weight_pct": round(v / total * 100.0, 2)}
                for k, v in sorted(by_segment.items(), key=lambda kv: -kv[1])
            ],
            "basis": f"{len(priced)} priced holdings totalling {total:,.0f}",
        }

    @staticmethod
    def _concentration(rows: List[Dict[str, Any]], total: float) -> Dict[str, Any]:
        priced = [r for r in rows if r["market_value"] is not None]
        if not priced or not total:
            return {}
        weights = sorted(
            ((r["symbol"], r["market_value"] / total * 100.0) for r in priced),
            key=lambda kv: -kv[1],
        )
        hhi = sum((w / 100.0) ** 2 for _, w in weights)
        return {
            "largest_position": {"symbol": weights[0][0],
                                 "weight_pct": round(weights[0][1], 2)},
            "top_3_weight_pct": round(sum(w for _, w in weights[:3]), 2),
            "top_5_weight_pct": round(sum(w for _, w in weights[:5]), 2),
            "herfindahl_index": round(hhi, 4),
            "effective_positions": round(1 / hhi, 1) if hhi else None,
            "interpretation": (
                f"The Herfindahl index of {hhi:.3f} implies roughly "
                f"{1 / hhi:.1f} effective positions against "
                f"{len(priced)} nominal ones."
                if hhi else ""
            ),
        }

    @staticmethod
    def _realised_pnl(db: Session, user_id: str) -> Optional[float]:
        rows = db.execute(
            select(PortfolioTransaction)
            .where(PortfolioTransaction.user_id == user_id)
        ).scalars().all()
        if not rows:
            return None
        # FIFO would need lot tracking; this is a cash-basis approximation and
        # is labelled as such wherever it is displayed.
        buys = sum(r.quantity * r.price + r.charges
                   for r in rows if r.txn_type == "BUY")
        sells = sum(r.quantity * r.price - r.charges
                    for r in rows if r.txn_type == "SELL")
        return round(sells - buys, 2) if (buys or sells) else None

    @staticmethod
    def _dividend_income(db: Session, user_id: str) -> Optional[float]:
        rows = db.execute(
            select(PortfolioTransaction.amount)
            .where(PortfolioTransaction.user_id == user_id)
            .where(PortfolioTransaction.txn_type == "DIVIDEND")
        ).scalars().all()
        return round(sum(r for r in rows if r), 2) if rows else None

    @staticmethod
    def _xirr(db: Session, user_id: str,
              current_value: float) -> Dict[str, Any]:
        """Money-weighted return via bisection on NPV.

        Needs at least one inflow and one outflow; otherwise it is undefined
        and we say so rather than printing a misleading number.
        """
        rows = db.execute(
            select(PortfolioTransaction)
            .where(PortfolioTransaction.user_id == user_id)
            .order_by(PortfolioTransaction.traded_on)
        ).scalars().all()
        if not rows:
            return {"value": None,
                    "note": "No transactions recorded, so XIRR is undefined."}

        flows: List[tuple[date, float]] = []
        for row in rows:
            amount = row.quantity * row.price
            if row.txn_type == "BUY":
                flows.append((row.traded_on, -(amount + row.charges)))
            elif row.txn_type == "SELL":
                flows.append((row.traded_on, amount - row.charges))
            elif row.txn_type == "DIVIDEND":
                flows.append((row.traded_on, row.amount or 0.0))
        if current_value:
            flows.append((date.today(), current_value))

        if not any(f < 0 for _, f in flows) or not any(f > 0 for _, f in flows):
            return {"value": None,
                    "note": "XIRR needs both an outflow and an inflow."}

        start = flows[0][0]

        def npv(rate: float) -> float:
            return sum(
                amount / ((1 + rate) ** ((day - start).days / 365.0))
                for day, amount in flows
            )

        low, high = -0.99, 10.0
        if npv(low) * npv(high) > 0:
            return {"value": None,
                    "note": "XIRR did not bracket a root in -99% to +1000%."}
        for _ in range(200):
            mid = (low + high) / 2
            if npv(mid) > 0:
                low = mid
            else:
                high = mid
        return {
            "value": round((low + high) / 2 * 100.0, 2),
            "unit": "%",
            "note": "Money-weighted return across recorded transactions plus "
                    "the current market value as a terminal inflow.",
            "cash_flows": len(flows),
        }


def _empty_totals() -> Dict[str, Any]:
    return {
        "invested": 0.0, "market_value": 0.0, "unrealised_pnl": 0.0,
        "unrealised_pnl_pct": None, "realised_pnl": None,
        "priced_holdings": 0, "total_holdings": 0, "pricing_coverage_pct": 0.0,
    }


portfolio_service = PortfolioService()
