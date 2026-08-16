"""Research call lifecycle: create, version, re-evaluate, track performance.

Two rules the rest of the app depends on:

1. A published call is never mutated in place without a `ResearchCallVersion`
   snapshot and an audit entry. History is not editable.
2. `status`, `achieved_pct`, `potential_pct`, `risk_reward` and `reference_price`
   are *derived*. They are recomputed from live price on every refresh and are
   rejected if supplied by an operator.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.compliance import assert_no_prohibited_claim, disclaimers
from app.core.data_quality import DataStatus, Sourced
from app.models.research import (ResearchCall, ResearchCallPerformance,
                                 ResearchCallVersion, ResearchSource)
from app.providers.registry import registry
from app.services import audit
from app.services.trade_status import SetupLevels, evaluate_status

DERIVED_FIELDS = {
    "status", "status_reason", "achieved_pct", "potential_pct",
    "risk_reward", "reference_price", "version", "lifecycle_state",
}

EDITABLE_FIELDS = [
    "symbol", "company_name", "segment", "expiry", "strike", "option_type",
    "lot_size", "source_type", "source_id", "source_name", "analyst_name",
    "original_url", "published_at", "valid_until", "was_transformed",
    "transformation_note", "original_recommendation", "side", "entry_min",
    "entry_max", "stop_loss", "target_1", "target_2", "target_3", "horizon",
    "timeframe", "rationale", "invalidation", "why_now", "why_not",
    "confidence", "risk_rating", "is_published",
]


class ResearchCallService:

    # -- creation ----------------------------------------------------------

    def create(self, db: Session, payload: Dict[str, Any],
               actor: Optional[Any] = None) -> ResearchCall:
        self._validate(payload)

        # why_now / why_not are stored as JSON text; accept either a list from
        # a caller or a pre-serialised string.
        normalised = {
            key: (json.dumps(value) if key in ("why_now", "why_not")
                  and isinstance(value, (list, dict)) else value)
            for key, value in payload.items()
        }
        call = ResearchCall(**{
            key: normalised.get(key) for key in EDITABLE_FIELDS
            if key in normalised
        })
        call.version = 1
        call.lifecycle_state = "PUBLISHED" if payload.get("is_published") else "CREATED"
        call.provider = payload.get("provider", "manual")
        call.source_name = payload.get("source_name") or "Operator entry"
        call.data_status = (
            DataStatus.DEMO.value if payload.get("is_demo")
            else DataStatus.MANUAL.value
        )
        call.is_demo = bool(payload.get("is_demo"))
        call.published_at = payload.get("published_at") or datetime.now(tz=timezone.utc)
        if payload.get("evidence"):
            call.evidence_json = json.dumps(payload["evidence"], default=str)

        db.add(call)
        db.flush()

        self._snapshot(db, call, actor, "Created", {})
        audit.record(
            db, action="RESEARCH_CALL_CREATED", entity_type="research_call",
            entity_id=call.id, actor_id=getattr(actor, "id", None),
            actor_email=getattr(actor, "email", None),
            actor_role=getattr(actor, "role", None),
            new_value=self._as_dict(call),
            reason=payload.get("reason", "Initial creation"),
        )

        db.add(ResearchCallPerformance(
            call_id=call.id, symbol=call.symbol,
            source_name=call.source_name, published_at=call.published_at,
        ))
        db.flush()
        return call

    def update(self, db: Session, call: ResearchCall, changes: Dict[str, Any],
               actor: Optional[Any] = None,
               reason: Optional[str] = None) -> ResearchCall:
        illegal = DERIVED_FIELDS & set(changes)
        if illegal:
            raise ValueError(
                f"These fields are derived and cannot be set directly: "
                f"{', '.join(sorted(illegal))}."
            )
        if not reason:
            raise ValueError(
                "A reason is required when changing a published research call."
            )
        self._validate({**self._as_dict(call), **changes}, partial=True)

        before = self._as_dict(call)
        for key, value in changes.items():
            if key in EDITABLE_FIELDS:
                setattr(call, key, value)

        call.version += 1
        call.lifecycle_state = "MODIFIED"
        db.flush()

        after = self._as_dict(call)
        self._snapshot(db, call, actor, reason, audit.diff(before, after))
        audit.record_change(
            db, entity_type="research_call", entity_id=call.id,
            before=before, after=after, actor=actor, reason=reason,
            action="RESEARCH_CALL_UPDATED",
        )
        return call

    # -- evaluation --------------------------------------------------------

    def refresh_status(self, db: Session, call: ResearchCall,
                       quote_env: Optional[Sourced[Any]] = None) -> Dict[str, Any]:
        """Recompute the derived fields from the *contract's own* price.

        The reference price for an option call is the option premium, not the
        underlying's spot. Pricing a 1440 CE off a Rs 1,425 underlying would
        produce a nonsense "achieved" figure, so the option branch resolves the
        leg from the chain and reports honestly when it cannot.
        """
        if quote_env is None:
            quote_env = self._reference_quote(db, call)
        ltp = quote_env.value.ltp if quote_env.value else None

        levels = SetupLevels(
            side=call.side, entry_min=call.entry_min, entry_max=call.entry_max,
            stop_loss=call.stop_loss, target_1=call.target_1,
            target_2=call.target_2, target_3=call.target_3,
            valid_until=call.valid_until,
        )
        high, low = self._path_extremes(db, call)
        evaluation = evaluate_status(
            levels, ltp,
            price_is_stale=quote_env.status in (DataStatus.STALE,
                                                DataStatus.UNAVAILABLE),
            high_since_publication=high, low_since_publication=low,
            manually_invalidated=call.status == "INVALIDATED",
        )

        before = {
            "status": call.status, "achieved_pct": call.achieved_pct,
            "potential_pct": call.potential_pct, "risk_reward": call.risk_reward,
            "reference_price": call.reference_price,
        }

        call.status = evaluation.status.value
        call.status_reason = evaluation.reason
        call.reference_price = evaluation.reference_price
        call.achieved_pct = evaluation.achieved_pct
        # Equity cards show potential from the entry; option cards from the LTP.
        call.potential_pct = (
            evaluation.potential_from_ltp_pct if call.segment == "OPTION"
            else evaluation.potential_from_entry_pct
        )
        call.risk_reward = evaluation.risk_reward

        terminal = {
            "TARGET_ACHIEVED": "TARGET_REACHED",
            "STOP_LOSS_TRIGGERED": "STOP_LOSS",
            "EXPIRED": "EXPIRED",
        }
        if call.status in terminal:
            call.lifecycle_state = terminal[call.status]
        elif call.status in ("WITHIN_ENTRY", "TARGET_IN_PROGRESS"):
            call.lifecycle_state = "ACTIVE"

        after = {
            "status": call.status, "achieved_pct": call.achieved_pct,
            "potential_pct": call.potential_pct, "risk_reward": call.risk_reward,
            "reference_price": call.reference_price,
        }
        if before["status"] != after["status"]:
            audit.record_change(
                db, entity_type="research_call", entity_id=call.id,
                before=before, after=after,
                reason="Automatic status re-evaluation from live price",
                action="RESEARCH_CALL_STATUS_CHANGED",
            )

        self._update_performance(db, call, ltp, high, low)
        db.flush()

        return {
            **evaluation.to_dict(),
            "potential_display_label": (
                "Potential left" if call.segment == "OPTION"
                else "Potential expected"
            ),
            "potential_display_value": call.potential_pct,
            "price_provenance": quote_env.to_dict(),
        }

    @staticmethod
    def _reference_quote(db: Session, call: ResearchCall) -> Sourced[Any]:
        """The price the setup's levels are actually quoted in."""
        from app.providers.base import QuoteData

        if call.segment != "OPTION" or call.strike is None or not call.option_type:
            return registry.fetch("quote", call.symbol, db=db)

        chain_env = registry.fetch("option_chain", call.symbol,
                                   expiry=call.expiry, db=db)
        if chain_env.is_usable and chain_env.value:
            for leg in chain_env.value.legs:
                if (abs(leg.strike - call.strike) < 1e-6
                        and leg.option_type == call.option_type.upper()):
                    return Sourced(
                        value=QuoteData(
                            symbol=f"{call.symbol} {call.strike:g} "
                                   f"{call.option_type}",
                            ltp=leg.ltp, change=leg.change,
                            change_pct=leg.change_pct,
                            volume=leg.volume, bid=leg.bid, ask=leg.ask,
                            open_interest=leg.open_interest,
                            oi_change=leg.oi_change,
                            observed_at=chain_env.observed_at,
                        ),
                        provider=chain_env.provider,
                        source_name=f"{chain_env.source_name} (option leg)",
                        status=chain_env.status,
                        observed_at=chain_env.observed_at,
                        reliability=chain_env.reliability,
                        notes=chain_env.notes,
                    )

        env: Sourced[Any] = Sourced.unavailable(
            "option premium",
            reason=(
                f"No {call.strike:g} {call.option_type} leg for "
                f"{call.symbol} {call.expiry} was returned by the chain "
                f"providers, so this setup cannot be re-evaluated. The "
                f"underlying's spot price is deliberately not substituted."
            ),
        )
        return env

    @staticmethod
    def _path_extremes(db: Session, call: ResearchCall):
        """Highest high / lowest low since publication, from stored bars.

        Without this, a target touched intraday and since retraced would be
        invisible. When no history is stored the caller is told, rather than
        the omission being hidden.
        """
        from app.models.market import HistoricalPrice

        if not call.published_at:
            return None, None
        if call.segment == "OPTION":
            # Option premium history is not stored, and the underlying's high
            # and low say nothing about where the premium traded.
            return None, None
        rows = db.execute(
            select(HistoricalPrice.high, HistoricalPrice.low)
            .where(HistoricalPrice.symbol == call.symbol)
            .where(HistoricalPrice.interval == "1d")
            .where(HistoricalPrice.bar_time >= call.published_at)
        ).all()
        if not rows:
            return None, None
        highs = [r[0] for r in rows if r[0] is not None]
        lows = [r[1] for r in rows if r[1] is not None]
        return (max(highs) if highs else None, min(lows) if lows else None)

    @staticmethod
    def _update_performance(db: Session, call: ResearchCall,
                            ltp: Optional[float], high: Optional[float],
                            low: Optional[float]) -> None:
        performance = db.execute(
            select(ResearchCallPerformance)
            .where(ResearchCallPerformance.call_id == call.id)
        ).scalars().first()
        if performance is None:
            performance = ResearchCallPerformance(
                call_id=call.id, symbol=call.symbol,
                source_name=call.source_name, published_at=call.published_at,
            )
            db.add(performance)

        anchor = performance.price_at_publication
        if anchor is None:
            anchor = call.entry_max or call.entry_min or ltp
            performance.price_at_publication = anchor

        if anchor and ltp:
            direction = 1 if call.side.upper() != "SELL" else -1
            performance.current_return_pct = round(
                direction * (ltp / anchor - 1.0) * 100.0, 3
            )
        if anchor and high:
            performance.max_favourable_excursion_pct = round(
                (high / anchor - 1.0) * 100.0, 3
            )
        if anchor and low:
            performance.max_adverse_excursion_pct = round(
                (low / anchor - 1.0) * 100.0, 3
            )

        performance.target_hit = call.status == "TARGET_ACHIEVED"
        performance.stop_hit = call.status == "STOP_LOSS_TRIGGERED"
        performance.last_evaluated_at = datetime.now(tz=timezone.utc)

        if call.published_at:
            for days, attr in ((1, "return_1d_pct"), (3, "return_3d_pct"),
                               (7, "return_7d_pct"), (30, "return_30d_pct")):
                if getattr(performance, attr) is None:
                    value = ResearchCallService._return_after(
                        db, call, anchor, days
                    )
                    if value is not None:
                        setattr(performance, attr, value)

    @staticmethod
    def _return_after(db: Session, call: ResearchCall,
                      anchor: Optional[float], days: int) -> Optional[float]:
        from app.models.market import HistoricalPrice

        if not anchor or not call.published_at:
            return None
        target_date = call.published_at + timedelta(days=days)
        if target_date > datetime.now(tz=timezone.utc):
            return None
        row = db.execute(
            select(HistoricalPrice.close)
            .where(HistoricalPrice.symbol == call.symbol)
            .where(HistoricalPrice.interval == "1d")
            .where(HistoricalPrice.bar_time >= target_date)
            .order_by(HistoricalPrice.bar_time)
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        direction = 1 if call.side.upper() != "SELL" else -1
        return round(direction * (row / anchor - 1.0) * 100.0, 3)

    # -- source performance -------------------------------------------------

    def source_performance(self, db: Session,
                           source_name: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = db.execute(select(ResearchCallPerformance)).scalars().all()
        buckets: Dict[str, List[ResearchCallPerformance]] = {}
        for row in rows:
            if source_name and row.source_name != source_name:
                continue
            buckets.setdefault(row.source_name or "Unattributed", []).append(row)

        out: List[Dict[str, Any]] = []
        for name, items in buckets.items():
            resolved = [i for i in items
                        if i.target_hit is not None or i.stop_hit is not None]
            hits = sum(1 for i in resolved if i.target_hit)
            stops = sum(1 for i in resolved if i.stop_hit)
            closed = hits + stops
            returns = [i.current_return_pct for i in items
                       if i.current_return_pct is not None]
            out.append({
                "source": name,
                "total_calls": len(items),
                "closed_calls": closed,
                "targets_hit": hits,
                "stops_hit": stops,
                "hit_rate_pct": round(hits / closed * 100.0, 1) if closed else None,
                "average_return_pct": round(sum(returns) / len(returns), 2)
                if returns else None,
                "best_return_pct": round(max(returns), 2) if returns else None,
                "worst_return_pct": round(min(returns), 2) if returns else None,
                "sample_warning": (
                    "Fewer than 20 resolved calls - too small a sample to draw "
                    "any conclusion about this source."
                    if closed < 20 else None
                ),
                "methodology": (
                    "Hit rate counts calls whose target or stop was reached. "
                    "Open calls are excluded from the rate but included in the "
                    "average return, which therefore mixes realised and "
                    "unrealised outcomes."
                ),
            })
        out.sort(key=lambda r: (r["hit_rate_pct"] or -1), reverse=True)
        return out

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _validate(payload: Dict[str, Any], partial: bool = False) -> None:
        for field_name in ("rationale", "invalidation", "original_recommendation",
                           "transformation_note"):
            value = payload.get(field_name)
            if value:
                assert_no_prohibited_claim(str(value), field_name)

        side = (payload.get("side") or "WATCH").upper()
        entry_min, entry_max = payload.get("entry_min"), payload.get("entry_max")
        stop, target = payload.get("stop_loss"), payload.get("target_1")

        if entry_min is not None and entry_max is not None and entry_min > entry_max:
            raise ValueError("entry_min must not exceed entry_max.")

        if side == "BUY" and None not in (entry_max, stop) and stop >= entry_max:
            raise ValueError(
                "For a BUY, the stop loss must sit below the entry range."
            )
        if side == "SELL" and None not in (entry_min, stop) and stop <= entry_min:
            raise ValueError(
                "For a SELL, the stop loss must sit above the entry range."
            )
        if side == "BUY" and None not in (entry_max, target) and target <= entry_max:
            raise ValueError("For a BUY, target 1 must sit above the entry range.")
        if side == "SELL" and None not in (entry_min, target) and target >= entry_min:
            raise ValueError("For a SELL, target 1 must sit below the entry range.")

        source_type = payload.get("source_type")
        if source_type == "EXTERNAL_RESEARCH" and not partial:
            if not payload.get("source_name"):
                raise ValueError(
                    "An externally sourced call must name its source. The "
                    "platform will not present third-party research as its own."
                )
            if payload.get("was_transformed") and not payload.get("transformation_note"):
                raise ValueError(
                    "You marked this call as transformed from the original. "
                    "Describe the transformation."
                )

    @staticmethod
    def _snapshot(db: Session, call: ResearchCall, actor: Optional[Any],
                  reason: str, changed: Dict[str, Any]) -> None:
        db.add(ResearchCallVersion(
            call_id=call.id, version=call.version,
            snapshot_json=json.dumps(ResearchCallService._as_dict(call),
                                     default=str),
            changed_fields=json.dumps(changed, default=str) if changed else None,
            changed_by=getattr(actor, "email", None),
            change_reason=reason,
        ))
        db.flush()

    @staticmethod
    def _as_dict(call: ResearchCall) -> Dict[str, Any]:
        return {
            key: getattr(call, key)
            for key in EDITABLE_FIELDS + list(DERIVED_FIELDS)
            if hasattr(call, key)
        }

    @staticmethod
    def to_card(call: ResearchCall, evaluation: Optional[Dict[str, Any]] = None,
                source: Optional[ResearchSource] = None) -> Dict[str, Any]:
        """The payload the stock/F&O card renders."""
        is_option = call.segment == "OPTION"
        return {
            "id": call.id,
            "symbol": call.symbol,
            "company": call.company_name,
            "segment": call.segment,
            "expiry": call.expiry.isoformat() if call.expiry else None,
            "strike": call.strike,
            "option_type": call.option_type,
            "lot_size": call.lot_size,
            "side": call.side,
            "source_type": call.source_type,
            "source": {
                "name": call.source_name,
                "analyst": call.analyst_name,
                "url": call.original_url,
                "organisation": source.organisation if source else None,
                "reliability": source.reliability if source else "UNKNOWN",
                "registration_note": source.registration_note if source else None,
                "published_at": call.published_at.isoformat()
                if call.published_at else None,
                "valid_until": call.valid_until.isoformat()
                if call.valid_until else None,
                "was_transformed": call.was_transformed,
                "transformation_note": call.transformation_note,
                "original_recommendation": call.original_recommendation,
                "attribution_notice": (
                    disclaimers()["external_research"]
                    if call.source_type == "EXTERNAL_RESEARCH"
                    else disclaimers()["generated_signal"]
                ),
            },
            "ltp": call.reference_price,
            "entry_min": call.entry_min,
            "entry_max": call.entry_max,
            "stop_loss": call.stop_loss,
            "targets": [t for t in (call.target_1, call.target_2, call.target_3)
                        if t is not None],
            "status": call.status,
            "status_reason": call.status_reason,
            "lifecycle_state": call.lifecycle_state,
            "achieved_pct": call.achieved_pct,
            "potential_pct": call.potential_pct,
            "potential_label": "Potential left" if is_option
            else "Potential expected",
            "risk_reward": call.risk_reward,
            "risk_rating": call.risk_rating,
            "confidence": call.confidence,
            "horizon": call.horizon,
            "version": call.version,
            "updated_at": call.updated_at.isoformat() if call.updated_at else None,
            "rationale": call.rationale,
            "invalidation": call.invalidation,
            "why_now": _json_list(call.why_now),
            "why_not": _json_list(call.why_not),
            "is_demo": call.is_demo,
            "data_status": call.data_status,
            "evaluation": evaluation,
        }


def _json_list(value: Optional[str]) -> List[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else [parsed]
    except (json.JSONDecodeError, TypeError):
        return [value]


research_call_service = ResearchCallService()
