"""F&O: option chain, greeks, futures, and the derivatives dashboard."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, rate_limit
from app.core.compliance import disclaimers, statistical_claims
from app.core.config import settings
from app.models.derivatives import FuturesSnapshot, OptionChainSnapshot
from app.models.instrument import Instrument
from app.providers.registry import registry
from app.services import greeks as gk
from app.services.options_analysis import options_analysis_service
from app.services.risk import risk_service

router = APIRouter(prefix="/fno", tags=["derivatives"])


@router.get("/summary", dependencies=[Depends(rate_limit("fno", 120))])
def fno_summary(db: Session = Depends(db_session)) -> Dict[str, Any]:
    """Landing payload for the F&O dashboard."""
    chains = db.execute(
        select(OptionChainSnapshot)
        .order_by(OptionChainSnapshot.captured_at.desc()).limit(12)
    ).scalars().all()
    futures = db.execute(
        select(FuturesSnapshot)
        .order_by(FuturesSnapshot.captured_at.desc()).limit(20)
    ).scalars().all()

    fno_universe = db.execute(
        select(Instrument.symbol, Instrument.name, Instrument.lot_size)
        .where(Instrument.is_fno_eligible.is_(True))
        .order_by(Instrument.symbol).limit(300)
    ).all()

    return {
        "risk_disclosure": {
            "text": disclaimers()["derivatives"],
            "statistical_claims": statistical_claims(settings.app_env.value),
            "note": (
                "Any statistic shown here carries its study period. Figures "
                "without a verified current source are withheld in PRODUCTION."
            ),
        },
        "option_chains": [
            {
                "underlying": c.underlying_symbol,
                "expiry": c.expiry.isoformat(),
                "underlying_value": c.underlying_value,
                "pcr_oi": c.pcr_oi, "pcr_volume": c.pcr_volume,
                "max_pain": c.max_pain, "atm_strike": c.atm_strike,
                "total_call_oi": c.total_call_oi,
                "total_put_oi": c.total_put_oi,
                "captured_at": c.captured_at.isoformat(),
                "source": c.source_name, "status": c.data_status,
                "is_demo": c.is_demo,
            }
            for c in chains
        ],
        "futures": [
            {
                "underlying": f.underlying_symbol,
                "expiry": f.expiry.isoformat(),
                "spot": f.spot, "ltp": f.ltp, "change_pct": f.change_pct,
                "basis": f.basis, "basis_pct": f.basis_pct,
                "annualised_basis_pct": f.annualised_basis_pct,
                "open_interest": f.open_interest, "oi_change": f.oi_change,
                "volume": f.volume, "lot_size": f.lot_size,
                "buildup": f.buildup, "captured_at": f.captured_at.isoformat(),
                "source": f.source_name, "is_demo": f.is_demo,
            }
            for f in futures
        ],
        "fno_universe": [
            {"symbol": s, "name": n, "lot_size": lot}
            for s, n, lot in fno_universe
        ],
        "availability_note": (
            None if (chains or futures) else
            "No derivatives data is stored. The NSE adapter is disabled by "
            "default; enable ENABLE_NSE_PROVIDER after reviewing NSE's terms, "
            "or wire a licensed provider / broker API."
        ),
    }


@router.get("/options/{symbol}")
def option_chain(
    symbol: str,
    expiry: Optional[str] = Query(default=None,
                                  description="ISO date, e.g. 2026-08-27"),
    strikes: int = Query(default=15, le=40,
                         description="strikes each side of ATM"),
    greeks: bool = True,
    risk_free_rate: float = Query(default=gk.DEFAULT_RISK_FREE_RATE),
    dividend_yield: float = Query(default=gk.DEFAULT_DIVIDEND_YIELD),
    db: Session = Depends(db_session),
) -> Dict[str, Any]:
    parsed_expiry = None
    if expiry:
        try:
            parsed_expiry = date.fromisoformat(expiry)
        except ValueError as exc:
            raise HTTPException(400, "expiry must be an ISO date "
                                     "(YYYY-MM-DD).") from exc

    env = registry.fetch("option_chain", symbol.upper(), expiry=parsed_expiry,
                         db=db)
    if not env.is_usable or env.value is None:
        return {
            "symbol": symbol.upper(), "available": False,
            "reason": env.notes or "No option-chain provider returned data.",
            "provenance": env.to_dict(),
            "how_to_fix": [
                "Enable the NSE adapter (ENABLE_NSE_PROVIDER=true) after "
                "reviewing NSE's terms of use, or",
                "Configure a licensed market-data provider / broker API, or",
                "Enter a chain manually through the admin panel.",
            ],
        }

    view = options_analysis_service.analyse(
        env, risk_free_rate=risk_free_rate, dividend_yield=dividend_yield,
        compute_greeks=greeks, strikes_around_atm=strikes,
    )
    if view is None:
        return {"symbol": symbol.upper(), "available": False,
                "reason": "The chain returned no strikes.",
                "provenance": env.to_dict()}

    return {
        "available": True,
        **view.to_dict(),
        "provenance": env.to_dict(),
        "risk_disclosure": disclaimers()["derivatives"],
    }


@router.get("/options/{symbol}/greeks")
def option_greeks(
    symbol: str,
    strike: float = Query(...),
    expiry: str = Query(...),
    option_type: str = Query(..., pattern="^(CE|PE|ce|pe)$"),
    premium: Optional[float] = Query(default=None),
    volatility: Optional[float] = Query(
        default=None, description="annualised, as a percentage e.g. 28.5"
    ),
    risk_free_rate: float = Query(default=gk.DEFAULT_RISK_FREE_RATE),
    dividend_yield: float = Query(default=gk.DEFAULT_DIVIDEND_YIELD),
    time_basis: str = Query(default="calendar", pattern="^(calendar|trading)$"),
    db: Session = Depends(db_session),
) -> Dict[str, Any]:
    """Greeks for a single contract, with every assumption returned."""
    try:
        parsed_expiry = date.fromisoformat(expiry)
    except ValueError as exc:
        raise HTTPException(400, "expiry must be an ISO date.") from exc

    quote_env = registry.fetch("quote", symbol.upper(), db=db)
    spot = quote_env.value.ltp if quote_env.value else None

    result = gk.compute_greeks(
        spot=spot, strike=strike, expiry=parsed_expiry,
        option_type=option_type.upper(), market_price=premium,
        volatility=(volatility / 100.0) if volatility is not None else None,
        risk_free_rate=risk_free_rate, dividend_yield=dividend_yield,
        time_basis=time_basis,
    )

    return {
        "symbol": symbol.upper(),
        "strike": strike,
        "expiry": parsed_expiry.isoformat(),
        "option_type": option_type.upper(),
        "spot": spot,
        "spot_provenance": quote_env.to_dict(),
        "greeks": result.to_dict(),
        "model_note": (
            "Black-Scholes-Merton with continuous dividend yield. Indian index "
            "and stock options are European-style, which this model assumes. "
            "Every Greek scales with the risk-free rate and volatility inputs "
            "shown above - change them and the numbers change."
        ),
    }


@router.get("/futures/{symbol}")
def futures_chain(symbol: str,
                  db: Session = Depends(db_session)) -> Dict[str, Any]:
    env = registry.fetch("futures_chain", symbol.upper(), db=db)
    rows: List[Dict[str, Any]] = []
    provenance = env.to_dict()

    if env.is_usable and env.value:
        for contract in env.value:
            rows.append(_futures_row(
                contract.underlying_symbol, contract.expiry, contract.spot,
                contract.ltp, contract.change, contract.change_pct,
                contract.open_interest, contract.oi_change, contract.volume,
                contract.lot_size, contract.captured_at, env.source_name,
                env.status.value, False,
            ))
    else:
        stored = db.execute(
            select(FuturesSnapshot)
            .where(FuturesSnapshot.underlying_symbol == symbol.upper())
            .order_by(FuturesSnapshot.expiry)
        ).scalars().all()
        for snapshot in stored:
            rows.append(_futures_row(
                snapshot.underlying_symbol, snapshot.expiry, snapshot.spot,
                snapshot.ltp, snapshot.change, snapshot.change_pct,
                snapshot.open_interest, snapshot.oi_change, snapshot.volume,
                snapshot.lot_size, snapshot.captured_at, snapshot.source_name,
                snapshot.data_status, snapshot.is_demo,
            ))

    if not rows:
        return {
            "symbol": symbol.upper(), "available": False,
            "reason": env.notes or "No futures data is available.",
            "provenance": provenance,
        }

    return {
        "symbol": symbol.upper(), "available": True, "contracts": rows,
        "provenance": provenance,
        "basis_note": (
            "Basis is futures minus spot. A positive basis (premium) usually "
            "reflects the cost of carry; a persistent discount often signals "
            "borrowing demand or dividend expectations rather than a directional "
            "view."
        ),
        "risk_disclosure": disclaimers()["derivatives"],
    }


def _futures_row(underlying, expiry, spot, ltp, change, change_pct, oi,
                 oi_change, volume, lot_size, captured_at, source, status,
                 is_demo) -> Dict[str, Any]:
    basis = (ltp - spot) if (ltp is not None and spot is not None) else None
    basis_pct = (basis / spot * 100.0) if (basis is not None and spot) else None
    days = (expiry - date.today()).days if expiry else None
    annualised = (
        basis_pct * 365.0 / days if basis_pct is not None and days and days > 0
        else None
    )
    from app.services.options_analysis import classify_buildup

    return {
        "underlying": underlying,
        "expiry": expiry.isoformat() if expiry else None,
        "days_to_expiry": days,
        "spot": spot, "ltp": ltp, "change": change, "change_pct": change_pct,
        "basis": round(basis, 2) if basis is not None else None,
        "basis_pct": round(basis_pct, 3) if basis_pct is not None else None,
        "annualised_basis_pct": round(annualised, 2)
        if annualised is not None else None,
        "premium_or_discount": (
            None if basis is None else ("PREMIUM" if basis > 0 else "DISCOUNT")
        ),
        "open_interest": oi, "oi_change": oi_change, "volume": volume,
        "lot_size": lot_size,
        "contract_value": round(ltp * lot_size, 2)
        if ltp and lot_size else None,
        "buildup": classify_buildup(change, oi_change),
        "captured_at": captured_at.isoformat() if captured_at else None,
        "source": source, "data_status": status, "is_demo": is_demo,
    }


@router.get("/setup/{symbol}")
def option_setup_analysis(
    symbol: str,
    strike: float = Query(...),
    expiry: str = Query(...),
    option_type: str = Query(..., pattern="^(CE|PE|ce|pe)$"),
    entry: float = Query(...),
    stop_loss: Optional[float] = Query(default=None),
    target: Optional[float] = Query(default=None),
    lot_size: Optional[int] = Query(default=None),
    db: Session = Depends(db_session),
) -> Dict[str, Any]:
    """Everything an F&O research card needs: greeks, break-even, risk."""
    try:
        parsed_expiry = date.fromisoformat(expiry)
    except ValueError as exc:
        raise HTTPException(400, "expiry must be an ISO date.") from exc

    quote_env = registry.fetch("quote", symbol.upper(), db=db)
    spot = quote_env.value.ltp if quote_env.value else None

    instrument = db.execute(
        select(Instrument).where(Instrument.symbol == symbol.upper()).limit(1)
    ).scalars().first()
    lot = lot_size or (instrument.lot_size if instrument else None) or 1

    chain_env = registry.fetch("option_chain", symbol.upper(),
                               expiry=parsed_expiry, db=db)
    leg = None
    chain_context = None
    if chain_env.is_usable and chain_env.value:
        for candidate in chain_env.value.legs:
            if (abs(candidate.strike - strike) < 1e-6
                    and candidate.option_type == option_type.upper()):
                leg = candidate
                break
        view = options_analysis_service.analyse(chain_env, compute_greeks=False)
        if view:
            chain_context = {
                "pcr_oi": view.totals.get("pcr_oi"),
                "max_pain": view.totals.get("max_pain"),
                "key_levels": view.key_levels,
                "iv_structure": view.iv_structure,
                "explanation": view.chain.explain(),
            }

    premium = leg.ltp if leg else entry
    greeks_result = gk.compute_greeks(
        spot=spot, strike=strike, expiry=parsed_expiry,
        option_type=option_type.upper(), market_price=premium,
        volatility=(leg.implied_volatility / 100.0
                    if leg and leg.implied_volatility else None),
    )

    is_call = option_type.upper() == "CE"
    break_even = strike + entry if is_call else strike - entry
    days = (parsed_expiry - date.today()).days

    risk = risk_service.assess(
        symbol=symbol.upper(), segment="OPTION",
        risk_reward=(
            abs(target - entry) / abs(entry - stop_loss)
            if target is not None and stop_loss is not None
            and entry != stop_loss else None
        ),
        open_interest=leg.open_interest if leg else None,
        bid_ask_spread_pct=(
            round((leg.ask - leg.bid) / leg.ask * 100.0, 2)
            if leg and leg.bid and leg.ask and leg.ask > 0 else None
        ),
        implied_volatility=(
            greeks_result.implied_volatility * 100.0
            if greeks_result.implied_volatility else None
        ),
        days_to_expiry=days,
        theta_per_day=greeks_result.theta,
        option_premium=premium,
        data_quality=70.0 if leg else 40.0,
    )

    return {
        "contract": {
            "symbol": symbol.upper(), "strike": strike,
            "expiry": parsed_expiry.isoformat(),
            "option_type": option_type.upper(), "lot_size": lot,
            "days_to_expiry": days,
        },
        "spot": spot,
        "market": {
            "ltp": leg.ltp if leg else None,
            "change": leg.change if leg else None,
            "change_pct": leg.change_pct if leg else None,
            "open_interest": leg.open_interest if leg else None,
            "oi_change": leg.oi_change if leg else None,
            "volume": leg.volume if leg else None,
            "implied_volatility": leg.implied_volatility if leg else None,
            "bid": leg.bid if leg else None, "ask": leg.ask if leg else None,
        } if leg else {"available": False,
                       "reason": chain_env.notes or "chain unavailable"},
        "setup": {
            "entry": entry, "stop_loss": stop_loss, "target": target,
            "break_even_underlying": round(break_even, 2),
            "break_even_note": (
                f"The underlying must be {'above' if is_call else 'below'} "
                f"{break_even:.2f} at expiry for the option to have intrinsic "
                f"value equal to the premium paid."
            ),
            "contract_value": round(entry * lot, 2),
            "max_theoretical_loss": round(entry * lot, 2),
            "max_theoretical_loss_note": (
                "For a bought option the premium paid is the maximum loss. "
                "A sold option carries a materially larger and, for a naked "
                "call, an unbounded loss."
            ),
            "risk_reward": (
                round(abs(target - entry) / abs(entry - stop_loss), 2)
                if target is not None and stop_loss is not None
                and entry != stop_loss else None
            ),
            "potential_profit_per_lot": (
                round((target - entry) * lot, 2) if target is not None else None
            ),
            "potential_loss_per_lot": (
                round((entry - stop_loss) * lot, 2)
                if stop_loss is not None else None
            ),
        },
        "greeks": greeks_result.to_dict(),
        "theta_burn": {
            "per_day_per_lot": round(greeks_result.theta * lot, 2)
            if greeks_result.theta else None,
            "pct_of_premium_per_day": round(
                abs(greeks_result.theta) / entry * 100.0, 2
            ) if greeks_result.theta and entry else None,
        },
        "chain_context": chain_context,
        "risk": risk.to_dict(),
        "risk_disclosure": disclaimers()["derivatives"],
        "provenance": {
            "quote": quote_env.to_dict(),
            "chain": chain_env.to_dict(),
        },
    }
