"""Research assembly: the piece that stitches every engine together.

This is where the platform's central promise is kept:

    DATA -> ANALYSIS -> SIGNAL -> RISK -> HISTORICAL CONTEXT
         -> SOURCES -> TIMESTAMP -> DISCLAIMER

`build_instrument_research` gathers what is available, runs each engine, scores
confidence across them, detects conflict, and returns a payload where every
number can be traced to its origin. If a dimension is unavailable it is
reported as unavailable - never quietly dropped and never scored as neutral.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.compliance import disclaimers
from app.core.config import settings
from app.core.data_quality import (DataStatus, Sourced, data_quality_score,
                                   stale_banner)
from app.providers.base import Bar, OptionChainData, QuoteData
from app.providers.registry import registry
from app.services import indicators as ind
from app.services.confidence import confidence_service
from app.services.evidence import EvidenceChain, EvidenceItem, Stance
from app.services.fundamental_analysis import fundamental_analysis_service
from app.services.historical_analogue import historical_analogue_service
from app.services.news_analysis import news_analysis_service
from app.services.options_analysis import options_analysis_service
from app.services.risk import risk_service
from app.services.technical_analysis import (bars_to_frame,
                                             technical_analysis_service)
from app.services.trade_status import SetupLevels, evaluate_status

METHODOLOGY = "/methodology"


@dataclass
class ResearchBundle:
    symbol: str
    company_name: str
    segment: str
    generated_at: datetime
    quote: Optional[Dict[str, Any]]
    technical: Optional[Dict[str, Any]]
    fundamental: Optional[Dict[str, Any]]
    options: Optional[Dict[str, Any]]
    news: List[Dict[str, Any]]
    catalysts: List[Dict[str, Any]]
    analogues: Optional[Dict[str, Any]]
    risk: Optional[Dict[str, Any]]
    confidence: Optional[Dict[str, Any]]
    scorecard: Dict[str, Any]
    why_now: List[Dict[str, Any]]
    why_not: List[Dict[str, Any]]
    trade_setup: Optional[Dict[str, Any]]
    sources: List[Dict[str, Any]]
    data_quality: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "company_name": self.company_name,
            "segment": self.segment,
            "generated_at": self.generated_at.isoformat(),
            "quote": self.quote,
            "technical": self.technical,
            "fundamental": self.fundamental,
            "options": self.options,
            "news": self.news,
            "catalysts": self.catalysts,
            "historical_analogues": self.analogues,
            "risk": self.risk,
            "confidence": self.confidence,
            "scorecard": self.scorecard,
            "why_now": self.why_now,
            "why_not": self.why_not,
            "trade_setup": self.trade_setup,
            "sources": self.sources,
            "data_quality": self.data_quality,
            "warnings": self.warnings,
            "methodology": METHODOLOGY,
            "disclaimer": disclaimers()["generated_signal"],
        }


class ResearchService:

    def build_instrument_research(
        self,
        db: Session,
        symbol: str,
        segment: str = "EQUITY",
        interval: str = "1d",
        include_options: bool = True,
        include_analogues: bool = True,
        horizon_bars: int = 10,
    ) -> ResearchBundle:
        symbol = symbol.upper()
        envelopes: List[Sourced[Any]] = []
        warnings: List[str] = []
        chains: List[EvidenceChain] = []

        instrument = self._instrument(db, symbol, segment)
        company_name = instrument.name if instrument else symbol

        # ---- quote -------------------------------------------------------
        quote_env = registry.fetch("quote", symbol, db=db)
        envelopes.append(quote_env)
        quote_payload = self._quote_payload(quote_env)
        ltp = quote_env.value.ltp if quote_env.value else None

        # ---- history + technical ----------------------------------------
        history_env = registry.fetch("history", symbol, interval=interval, db=db)
        envelopes.append(history_env)
        technical_view = technical_analysis_service.analyse(
            symbol, history_env, interval
        )
        chains.append(technical_view.chain)

        enriched: Optional[pd.DataFrame] = None
        frame = bars_to_frame(history_env.value or [])
        if len(frame) >= 60:
            enriched = ind.compute_all(frame)

        # ---- fundamentals ------------------------------------------------
        fundamental_payload = None
        if segment == "EQUITY":
            fundamental_payload, fundamental_chain = self._fundamentals(
                db, symbol, instrument, envelopes
            )
            if fundamental_chain:
                chains.append(fundamental_chain)
        else:
            warnings.append(
                "Fundamental analysis applies to the underlying, not to a "
                "derivative contract."
            )

        # ---- options -----------------------------------------------------
        options_payload = None
        if include_options:
            underlying = (
                instrument.underlying_symbol
                if instrument and instrument.underlying_symbol else symbol
            )
            chain_env = registry.fetch("option_chain", underlying, db=db)
            if chain_env.is_usable:
                envelopes.append(chain_env)
                view = options_analysis_service.analyse(chain_env)
                if view:
                    options_payload = view.to_dict()
                    chains.append(view.chain)
            else:
                warnings.append(
                    f"Option chain unavailable for {underlying}: "
                    f"{chain_env.notes or 'no provider returned data'}."
                )

        # ---- news --------------------------------------------------------
        news_payload, news_chain = self._news(db, symbol, company_name,
                                              instrument, envelopes)
        chains.append(news_chain)

        # ---- catalysts ---------------------------------------------------
        catalysts, catalyst_chain = self._catalysts(db, symbol)
        chains.append(catalyst_chain)

        # ---- historical analogues ---------------------------------------
        analogues_payload = None
        analogue_chain = EvidenceChain(dimension="HISTORICAL",
                                       methodology_ref=METHODOLOGY)
        if include_analogues and enriched is not None:
            result = historical_analogue_service.find(
                enriched, horizon_bars=horizon_bars
            )
            analogues_payload = result.to_dict()
            analogue_chain = self._analogue_chain(result, history_env)
        else:
            analogue_chain.note_gap("insufficient history for analogue search")
        chains.append(analogue_chain)

        # ---- volume chain (kept separate so confidence can weigh it) -----
        chains.append(self._volume_chain(technical_view, history_env))

        # ---- data quality -------------------------------------------------
        quality = data_quality_score(envelopes)
        banner = stale_banner(envelopes)
        if banner:
            warnings.append(banner)

        # ---- risk ---------------------------------------------------------
        risk_payload = self._risk(
            symbol, segment, technical_view, quote_env, options_payload,
            catalysts, quality, db,
        )

        # ---- confidence ---------------------------------------------------
        confidence = confidence_service.score(chains, data_quality=quality)

        # ---- why now / why not --------------------------------------------
        why_now, why_not = self._why(chains, risk_payload, options_payload,
                                     technical_view)

        # ---- trade setup ---------------------------------------------------
        setup = self._trade_setup(
            symbol, segment, technical_view, ltp, confidence, risk_payload,
            analogues_payload, catalysts, instrument,
        )

        scorecard = self._scorecard(chains, risk_payload, confidence, quality,
                                    fundamental_payload)

        if any(e.is_demo for e in envelopes):
            warnings.insert(
                0,
                "One or more inputs are seeded demonstration data, not market "
                "data. This research view is illustrative only.",
            )

        return ResearchBundle(
            symbol=symbol,
            company_name=company_name,
            segment=segment,
            generated_at=datetime.now(tz=timezone.utc),
            quote=quote_payload,
            technical=technical_view.to_dict(),
            fundamental=fundamental_payload,
            options=options_payload,
            news=news_payload,
            catalysts=catalysts,
            analogues=analogues_payload,
            risk=risk_payload,
            confidence=confidence.to_dict(),
            scorecard=scorecard,
            why_now=why_now,
            why_not=why_not,
            trade_setup=setup,
            sources=[e.to_dict() for e in envelopes],
            data_quality={
                "score": quality,
                "banner": banner,
                "app_env": settings.app_env.value,
                "input_count": len(envelopes),
                "statuses": {
                    e.source_name: e.status.value for e in envelopes
                },
            },
            warnings=warnings,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _instrument(db: Session, symbol: str, segment: str):
        from app.models.instrument import Instrument

        return db.execute(
            select(Instrument)
            .where(Instrument.symbol == symbol)
            .where(Instrument.segment == segment)
            .limit(1)
        ).scalars().first() or db.execute(
            select(Instrument).where(Instrument.symbol == symbol).limit(1)
        ).scalars().first()

    @staticmethod
    def _quote_payload(env: Sourced[QuoteData]) -> Optional[Dict[str, Any]]:
        if not env.value:
            return {
                "available": False,
                "reason": env.notes or "no provider returned a quote",
                "provenance": env.to_dict(),
            }
        q = env.value
        return {
            "available": True,
            "ltp": q.ltp, "open": q.open, "high": q.high, "low": q.low,
            "previous_close": q.previous_close, "change": q.change,
            "change_pct": q.change_pct, "volume": q.volume,
            "vwap": q.vwap, "week52_high": q.week52_high,
            "week52_low": q.week52_low, "market_cap": q.market_cap,
            "bid": q.bid, "ask": q.ask,
            "open_interest": q.open_interest, "oi_change": q.oi_change,
            "provenance": env.to_dict(),
        }

    def _fundamentals(self, db: Session, symbol: str, instrument,
                      envelopes: List[Sourced[Any]]):
        from app.models.fundamental import (CompanyProfile, FinancialStatement,
                                            Fundamental)

        row = db.execute(
            select(Fundamental).where(Fundamental.symbol == symbol)
        ).scalars().first()

        ratios: Dict[str, Optional[float]] = {}
        source = observed = None
        status = DataStatus.UNAVAILABLE

        if row is not None:
            ratios = {
                col: getattr(row, col) for col in (
                    "market_cap", "enterprise_value", "pe", "forward_pe", "pb",
                    "ev_ebitda", "ev_sales", "peg", "eps_ttm", "book_value",
                    "dividend_yield", "roe", "roce", "roa", "debt_to_equity",
                    "interest_coverage", "current_ratio", "ebitda_margin",
                    "net_margin", "revenue_cagr_3y", "pat_cagr_3y", "beta",
                    "promoter_holding", "fii_holding", "dii_holding",
                    "promoter_pledge",
                )
            }
            source, observed = row.source_name, row.observed_at
            status = DataStatus(row.data_status) if row.data_status else DataStatus.MANUAL
            envelopes.append(Sourced(
                value=True, provider=row.provider, source_name=row.source_name,
                status=status, observed_at=row.observed_at,
            ))
        else:
            # Fall back to the live provider chain.
            env = registry.fetch("fundamentals", symbol, db=db)
            if env.is_usable and env.value:
                envelopes.append(env)
                ratios = dict(env.value.ratios)
                source, observed, status = env.source_name, env.observed_at, env.status

        statements = [
            {
                "period_type": s.period_type, "period_end": s.period_end,
                "period_label": s.period_label, "revenue": s.revenue,
                "ebitda": s.ebitda, "ebit": s.ebit, "pat": s.pat, "eps": s.eps,
                "interest": s.interest, "total_debt": s.total_debt,
                "net_worth": s.net_worth, "operating_cash_flow": s.operating_cash_flow,
                "free_cash_flow": s.free_cash_flow, "total_assets": s.total_assets,
                "cash_and_equivalents": s.cash_and_equivalents,
                "ebitda_margin": s.ebitda_margin,
            }
            for s in db.execute(
                select(FinancialStatement)
                .where(FinancialStatement.symbol == symbol)
                .order_by(FinancialStatement.period_end)
            ).scalars().all()
        ]

        if not ratios and not statements:
            chain = EvidenceChain(dimension="FUNDAMENTAL")
            chain.note_gap("no fundamental data is stored or retrievable")
            chain.finalise()
            chain.summary = chain.explain()
            return (
                {
                    "available": False,
                    "reason": "No fundamentals are available from the configured "
                              "providers for this symbol.",
                },
                chain,
            )

        quality = fundamental_analysis_service.score(
            ratios, statements, source=source, observed_at=observed
        )
        peers = self._peer_context(db, instrument, ratios)
        chain = fundamental_analysis_service.build_evidence(
            quality, ratios, peers, source=source, observed_at=observed
        )

        profile = db.execute(
            select(CompanyProfile).where(CompanyProfile.symbol == symbol)
        ).scalars().first()

        return (
            {
                "available": True,
                "ratios": ratios,
                "statements": [
                    {**s, "period_end": s["period_end"].isoformat()
                     if isinstance(s["period_end"], date) else s["period_end"]}
                    for s in statements
                ],
                "quality_score": quality.to_dict(),
                "peer_context": peers,
                "profile": {
                    "description": profile.description if profile else None,
                    "industry": (profile.industry if profile else None)
                    or (instrument.industry if instrument else None),
                    "sector": (profile.sector if profile else None)
                    or (instrument.sector if instrument else None),
                    "website": profile.website if profile else None,
                    "products": _json_or_none(profile.products if profile else None),
                    "business_segments": _json_or_none(
                        profile.business_segments if profile else None),
                    "geographies": _json_or_none(
                        profile.geographies if profile else None),
                    "competitive_position":
                        profile.competitive_position if profile else None,
                } if profile or instrument else None,
                "provenance": {
                    "source": source, "status": status.value if status else None,
                    "observed_at": observed.isoformat() if observed else None,
                },
                "evidence_chain": chain.to_dict(),
            },
            chain,
        )

    @staticmethod
    def _peer_context(db: Session, instrument,
                      ratios: Dict[str, Optional[float]]) -> Dict[str, Any]:
        """Median peer multiples for the same sector."""
        from app.models.fundamental import Fundamental
        from app.models.instrument import Instrument

        if instrument is None or not instrument.sector:
            return {}
        rows = db.execute(
            select(Fundamental)
            .join(Instrument, Instrument.id == Fundamental.instrument_id)
            .where(Instrument.sector == instrument.sector)
            .where(Instrument.symbol != instrument.symbol)
        ).scalars().all()
        if len(rows) < 2:
            return {}

        out: Dict[str, Any] = {}
        for metric in ("pe", "pb", "ev_ebitda", "roe", "dividend_yield"):
            values = sorted(
                v for v in (getattr(r, metric) for r in rows)
                if v is not None and v > 0
            )
            own = ratios.get(metric)
            if not values or own is None:
                continue
            out[metric] = {
                "value": own,
                "peer_median": round(values[len(values) // 2], 3),
                "peer_min": round(values[0], 3),
                "peer_max": round(values[-1], 3),
                "peer_count": len(values),
                "sector": instrument.sector,
            }
        return out

    def _news(self, db: Session, symbol: str, company_name: str, instrument,
              envelopes: List[Sourced[Any]]):
        env = registry.fetch(
            "news", symbol=symbol, company_name=company_name, limit=20, db=db
        )
        if not env.is_usable or not env.value:
            chain = EvidenceChain(dimension="NEWS")
            chain.note_gap("no news provider returned results")
            chain.finalise()
            chain.summary = chain.explain()
            return [], chain

        envelopes.append(env)
        assessments = [
            news_analysis_service.assess(
                headline=item.headline, publisher=item.publisher, url=item.url,
                published_at=item.published_at, symbol=symbol,
                company_name=company_name,
                sector=instrument.sector if instrument else None,
            )
            for item in env.value
        ]
        assessments.sort(key=lambda a: a.impact_score, reverse=True)
        chain = news_analysis_service.build_evidence(assessments)
        return [a.to_dict() for a in assessments], chain

    @staticmethod
    def _catalysts(db: Session, symbol: str):
        from app.models.fundamental import EarningsEvent
        from app.models.research import Catalyst

        today = date.today()
        horizon = today + timedelta(days=45)

        rows = db.execute(
            select(Catalyst)
            .where(Catalyst.symbol == symbol)
            .where(Catalyst.event_date >= today)
            .where(Catalyst.event_date <= horizon)
            .order_by(Catalyst.event_date)
        ).scalars().all()

        catalysts = [
            {
                "title": c.title, "category": c.category,
                "event_date": c.event_date.isoformat() if c.event_date else None,
                "days_away": (c.event_date - today).days if c.event_date else None,
                "expected_impact": c.expected_impact,
                "risk_level": c.risk_level,
                "historical_reaction": c.historical_reaction_note,
                "confirmed": c.is_confirmed,
                "source": c.source_name, "is_demo": c.is_demo,
            }
            for c in rows
        ]

        earnings = db.execute(
            select(EarningsEvent)
            .where(EarningsEvent.symbol == symbol)
            .where(EarningsEvent.expected_date >= today)
            .order_by(EarningsEvent.expected_date)
            .limit(1)
        ).scalars().first()
        if earnings and earnings.expected_date:
            catalysts.insert(0, {
                "title": f"{earnings.quarter_label} results",
                "category": "EARNINGS",
                "event_date": earnings.expected_date.isoformat(),
                "days_away": (earnings.expected_date - today).days,
                "expected_impact": "HIGH",
                "risk_level": "HIGH",
                "historical_reaction": (
                    f"Last reported move: {earnings.price_reaction_1d_pct}% on "
                    f"the day." if earnings.price_reaction_1d_pct is not None
                    else None
                ),
                "confirmed": earnings.status == "CONFIRMED",
                "source": earnings.source_name, "is_demo": earnings.is_demo,
            })

        chain = EvidenceChain(dimension="CATALYST", methodology_ref=METHODOLOGY)
        if not catalysts:
            chain.note_gap("no scheduled catalysts recorded in the next 45 days")
        else:
            for item in catalysts[:5]:
                high = str(item.get("expected_impact", "")).upper() == "HIGH"
                chain.add(EvidenceItem(
                    metric=f"Upcoming: {item['title']}",
                    value=item["days_away"], unit="days",
                    stance=Stance.NEGATIVE if high else Stance.NEUTRAL,
                    weight=1.2 if high else 0.7,
                    calculation=f"event on {item['event_date']}",
                    interpretation=(
                        f"{item['title']} is {item['days_away']} days away with "
                        f"{item.get('expected_impact', 'unknown')} expected impact."
                    ),
                    source=item.get("source"),
                ))
        chain.finalise()
        chain.summary = chain.explain()
        return catalysts, chain

    @staticmethod
    def _analogue_chain(result, history_env) -> EvidenceChain:
        chain = EvidenceChain(dimension="HISTORICAL",
                              methodology_ref="/methodology#historical-analogues")
        if not result.sample_sufficient:
            chain.note_gap(
                f"only {result.matches_found} comparable cases "
                f"(minimum 8 required)"
            )
            chain.limit(
                "Historical context is not scored because the sample is too "
                "small to describe."
            )
            chain.finalise()
            chain.summary = chain.explain()
            return chain

        stats = result.statistics
        hit_rate = stats["hit_rate_pct"]
        chain.add(EvidenceItem(
            metric="Historical hit rate in similar configurations",
            value=hit_rate, unit="%",
            stance=(
                Stance.POSITIVE if hit_rate >= 60 else
                Stance.NEGATIVE if hit_rate <= 40 else Stance.NEUTRAL
            ),
            weight=1.3,
            calculation=(
                f"{stats['positive_cases']} positive of {stats['sample_size']} "
                f"matched cases over {result.horizon_bars} bars"
            ),
            interpretation=(
                f"{hit_rate}% of {stats['sample_size']} similar past setups in "
                f"this instrument closed higher after {result.horizon_bars} "
                f"bars. This describes a sample; it is not a probability for "
                f"the current setup."
            ),
            source=history_env.source_name,
            data_status=history_env.status.value,
            observed_at=history_env.observed_at,
        ))
        chain.add(EvidenceItem(
            metric="Median forward return in similar configurations",
            value=stats["median_return_pct"], unit="%",
            stance=(
                Stance.POSITIVE if stats["median_return_pct"] > 1 else
                Stance.NEGATIVE if stats["median_return_pct"] < -1
                else Stance.NEUTRAL
            ),
            weight=1.2,
            calculation=f"median of {stats['sample_size']} forward returns",
            interpretation=(
                f"Median {stats['median_return_pct']}%, best "
                f"{stats['best_return_pct']}%, worst {stats['worst_return_pct']}%, "
                f"average worst drawdown inside the window "
                f"{stats['mean_max_adverse_pct']}%."
            ),
            source=history_env.source_name,
            data_status=history_env.status.value,
        ))
        for limitation in result.limitations[:4]:
            chain.limit(limitation)
        chain.finalise()
        chain.summary = chain.explain()
        return chain

    @staticmethod
    def _volume_chain(technical_view, history_env) -> EvidenceChain:
        chain = EvidenceChain(dimension="VOLUME", methodology_ref=METHODOLOGY)
        ratio = technical_view.indicators.get("volume_ratio_20")
        if ratio is None:
            chain.note_gap("volume data unavailable from the history provider")
        else:
            chain.add(EvidenceItem(
                metric="Relative volume", value=round(ratio, 2), unit="x",
                stance=(
                    Stance.POSITIVE if ratio >= 1.5 else
                    Stance.NEGATIVE if ratio <= 0.6 else Stance.NEUTRAL
                ),
                weight=1.0,
                calculation="latest volume / mean of the previous 20 bars",
                interpretation=(
                    f"Participation is {ratio:.2f}x the recent norm. Moves on "
                    f"thin volume carry less information than moves on heavy "
                    f"volume."
                ),
                source=history_env.source_name,
                data_status=history_env.status.value,
                observed_at=history_env.observed_at,
            ))
        chain.finalise()
        chain.summary = chain.explain()
        return chain

    @staticmethod
    def _risk(symbol, segment, technical_view, quote_env, options_payload,
              catalysts, quality, db) -> Dict[str, Any]:
        quote = quote_env.value
        turnover = None
        if quote and quote.ltp and quote.volume:
            turnover = quote.ltp * quote.volume

        earnings_days = None
        for item in catalysts:
            if item.get("category") == "EARNINGS" and item.get("days_away") is not None:
                earnings_days = item["days_away"]
                break

        iv = days_to_expiry = None
        if options_payload:
            iv = (options_payload.get("iv_structure") or {}).get("atm_iv")
            try:
                expiry = date.fromisoformat(options_payload["expiry"])
                days_to_expiry = (expiry - date.today()).days
            except (KeyError, ValueError):
                days_to_expiry = None

        assessment = risk_service.assess(
            symbol=symbol,
            segment=segment,
            atr_pct=technical_view.indicators.get("atr_pct"),
            average_daily_turnover=turnover,
            average_volume=quote.volume if quote else None,
            implied_volatility=iv,
            days_to_expiry=days_to_expiry,
            upcoming_events=catalysts,
            earnings_within_days=earnings_days,
            data_quality=quality,
        )
        return assessment.to_dict()

    @staticmethod
    def _why(chains, risk_payload, options_payload, technical_view):
        """Requirements 96 and 97: both panels, always, built from real items."""
        why_now: List[Dict[str, Any]] = []
        why_not: List[Dict[str, Any]] = []

        for chain in chains:
            for item in chain.items:
                if item.stance is Stance.POSITIVE:
                    why_now.append({
                        "dimension": chain.dimension,
                        "point": item.interpretation or f"{item.metric}: {item.value}",
                        "metric": item.metric,
                        "value": item.value,
                        "calculation": item.calculation,
                        "source": item.source,
                        "observed_at": item.observed_at.isoformat()
                        if item.observed_at else None,
                        "weight": item.weight,
                    })
            for item in chain.counter_items:
                why_not.append({
                    "dimension": chain.dimension,
                    "point": item.interpretation or f"{item.metric}: {item.value}",
                    "metric": item.metric,
                    "value": item.value,
                    "calculation": item.calculation,
                    "source": item.source,
                    "observed_at": item.observed_at.isoformat()
                    if item.observed_at else None,
                    "weight": item.weight,
                })
            for gap in chain.data_gaps:
                why_not.append({
                    "dimension": chain.dimension,
                    "point": f"Blind spot: {gap}.",
                    "metric": "data gap", "value": None,
                    "calculation": None, "source": None,
                    "observed_at": None, "weight": 0.5,
                })

        for factor in (risk_payload or {}).get("factors", []):
            if factor.get("score") is not None and factor["score"] >= 60:
                why_not.append({
                    "dimension": "RISK",
                    "point": factor["explanation"],
                    "metric": factor["label"], "value": factor["score"],
                    "calculation": f"risk sub-score {factor['score']}/100 "
                                   f"at weight {factor['weight']}",
                    "source": factor.get("source"), "observed_at": None,
                    "weight": factor["weight"],
                })

        why_now.sort(key=lambda x: x["weight"], reverse=True)
        why_not.sort(key=lambda x: x["weight"], reverse=True)

        if not why_now:
            why_now.append({
                "dimension": "NONE",
                "point": "No positive evidence cleared the threshold. There is "
                         "no case to make here right now.",
                "metric": None, "value": None, "calculation": None,
                "source": None, "observed_at": None, "weight": 0,
            })
        if not why_not:
            why_not.append({
                "dimension": "NONE",
                "point": "No counter-evidence was found in the dimensions that "
                         "could be evaluated - which is itself worth treating "
                         "with suspicion rather than comfort.",
                "metric": None, "value": None, "calculation": None,
                "source": None, "observed_at": None, "weight": 0,
            })
        return why_now[:12], why_not[:12]

    @staticmethod
    def _trade_setup(symbol, segment, technical_view, ltp, confidence,
                     risk_payload, analogues, catalysts, instrument):
        """A calculated analytical scenario - explicitly not a recommendation."""
        if ltp is None or technical_view.chain.score is None:
            return {
                "available": False,
                "reason": "A setup needs a live price and a scored technical "
                          "reading; one of them is missing.",
            }

        atr = technical_view.indicators.get("atr_14")
        if not atr:
            return {
                "available": False,
                "reason": "ATR is unavailable, so stop distance cannot be "
                          "derived from the instrument's own volatility.",
            }

        state = confidence.recommendation_state
        if state in ("MIXED_WAIT_FOR_CONFIRMATION", "INSUFFICIENT_EVIDENCE"):
            return {
                "available": False,
                "direction": "NEUTRAL",
                "reason": (
                    "Evidence conflict detected."
                    if state == "MIXED_WAIT_FOR_CONFIRMATION"
                    else "Evidence coverage is too thin to characterise a setup."
                ),
                "state": state,
                "detail": confidence.conflict.get("message"),
                "note": (
                    "The platform will not manufacture a direction when the "
                    "evidence disagrees. Mixed is a legitimate answer."
                ),
            }

        score = technical_view.chain.score
        direction = "LONG" if score >= 55 else "SHORT" if score <= 45 else "NEUTRAL"
        if direction == "NEUTRAL":
            return {
                "available": False,
                "direction": "NEUTRAL",
                "reason": f"Technical score {score}/100 sits in the neutral band "
                          f"(45-55); no directional setup is derived.",
                "state": state,
            }

        is_long = direction == "LONG"
        # Stop at 1.5 ATR, targets at 1.5/2.5/4 R. Stated, not hidden.
        stop_distance = 1.5 * atr
        stop = ltp - stop_distance if is_long else ltp + stop_distance
        entry_low = round(ltp * (0.997 if is_long else 1.0), 2)
        entry_high = round(ltp * (1.0 if is_long else 1.003), 2)
        entry_ref = entry_high if is_long else entry_low

        targets = []
        for multiple in (1.5, 2.5, 4.0):
            move = multiple * stop_distance
            targets.append(round(entry_ref + move if is_long else entry_ref - move, 2))

        levels = SetupLevels(
            side="BUY" if is_long else "SELL",
            entry_min=entry_low, entry_max=entry_high,
            stop_loss=round(stop, 2),
            target_1=targets[0], target_2=targets[1], target_3=targets[2],
        )
        evaluation = evaluate_status(levels, ltp)

        nearest_resistance = next(
            (l for l in technical_view.levels  # noqa: E741
             if l["price"] > ltp), None
        )
        invalidation = [
            f"A close beyond the stop at {levels.stop_loss:g} "
            f"({1.5} x ATR of {atr:.2f}) removes the technical basis for this "
            f"scenario.",
        ]
        if nearest_resistance and is_long:
            invalidation.append(
                f"Rejection at {nearest_resistance['price']:g} "
                f"(strength {nearest_resistance['strength']}/100) would cap the "
                f"first target."
            )
        high_impact = [c for c in catalysts
                       if str(c.get("expected_impact", "")).upper() == "HIGH"]
        if high_impact:
            invalidation.append(
                f"{high_impact[0]['title']} in {high_impact[0]['days_away']} days "
                f"can re-rate the instrument independently of this structure."
            )

        return {
            "available": True,
            "direction": direction,
            "instrument": symbol,
            "segment": segment,
            "timeframe": technical_view.interval,
            "entry_zone": [entry_low, entry_high],
            "stop_loss": levels.stop_loss,
            "targets": evaluation.targets,
            "risk_reward": evaluation.risk_reward,
            "status": evaluation.to_dict(),
            "confidence": confidence.overall,
            "confidence_band": confidence.band,
            "risk_rating": (risk_payload or {}).get("rating"),
            "state": state,
            "sizing_basis": {
                "stop_method": "1.5 x ATR(14)",
                "atr_14": round(atr, 4),
                "target_method": "1.5R / 2.5R / 4R from the entry reference",
                "entry_reference": entry_ref,
            },
            "invalidation": invalidation,
            "historical_context": (
                analogues.get("explanation") if analogues else None
            ),
            "technical_rationale": technical_view.chain.explain(),
            "lot_size": instrument.lot_size if instrument else None,
            "disclaimer": (
                "This is a calculated analytical scenario derived from the "
                "levels and volatility shown. It is not a recommendation, it "
                "carries no probability, and it is not advice."
            ),
        }

    @staticmethod
    def _scorecard(chains, risk_payload, confidence, quality,
                   fundamental_payload) -> Dict[str, Any]:
        by_dimension = {c.dimension: c for c in chains}

        def _entry(dimension: str) -> Dict[str, Any]:
            chain = by_dimension.get(dimension)
            if chain is None or chain.score is None:
                return {"score": None, "stance": "UNKNOWN",
                        "note": "Not available."}
            return {
                "score": chain.score,
                "stance": chain.stance.value,
                "note": chain.summary or chain.explain(),
            }

        valuation_score = None
        if fundamental_payload and fundamental_payload.get("available"):
            categories = (fundamental_payload.get("quality_score") or {}).get(
                "categories", {}
            )
            valuation_score = (categories.get("valuation") or {}).get("score_pct")

        risk_score = (risk_payload or {}).get("score")
        return {
            "technical": _entry("TECHNICAL"),
            "fundamental": _entry("FUNDAMENTAL"),
            "valuation": {
                "score": valuation_score,
                "stance": (
                    "POSITIVE" if valuation_score and valuation_score >= 60 else
                    "NEGATIVE" if valuation_score is not None and valuation_score <= 40
                    else "NEUTRAL" if valuation_score is not None else "UNKNOWN"
                ),
                "note": "Derived from the valuation category of the quality score.",
            },
            "momentum": _entry("TECHNICAL"),
            "news": _entry("NEWS"),
            "options": _entry("OPTIONS"),
            "volume": _entry("VOLUME"),
            "catalyst": _entry("CATALYST"),
            "historical": _entry("HISTORICAL"),
            "risk": {
                "score": risk_score,
                "stance": (
                    "NEGATIVE" if risk_score and risk_score >= 55 else
                    "POSITIVE" if risk_score is not None and risk_score < 35
                    else "NEUTRAL" if risk_score is not None else "UNKNOWN"
                ),
                "note": (risk_payload or {}).get("explanation"),
                "inverted": True,
                "inverted_note": "Higher is riskier, unlike the other scores.",
            },
            "data_quality": {
                "score": quality,
                "stance": (
                    "POSITIVE" if quality >= 75 else
                    "NEGATIVE" if quality < 45 else "NEUTRAL"
                ),
                "note": "Weighted by source reliability and observation age.",
            },
            "overall_research_score": confidence.overall,
            "overall_state": confidence.recommendation_state,
        }


def _json_or_none(value: Optional[str]):
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


research_service = ResearchService()
