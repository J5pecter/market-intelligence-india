"""Seed data.

Everything created here is stamped `is_demo=True` and `data_status="DEMO"`, and
the provider chain refuses to serve it when APP_ENV is STAGING or PRODUCTION.

The illustrative setups mirror the reference cards this platform was specified
against (HDFCBANK, BAJAJELEC, VOLTAS, BDL 1440 CE, SIEMENS 3900 PE). They are
*not* live recommendations and are not attributed to any real research provider
- the seeded source is explicitly named as a demonstration placeholder.

Price history is synthetic: a deterministic random walk seeded per symbol so
charts, indicators, scanners and backtests have something to operate on in a
fresh checkout. It is not market data and never claims to be.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.market_calendar import IST, is_trading_day
from app.core.security import Role, hash_password
from app.models.derivatives import (OptionChainSnapshot, OptionSnapshot)
from app.models.fundamental import (CompanyProfile, CorporateAction,
                                    EarningsEvent, FinancialStatement,
                                    Fundamental, Shareholding)
from app.models.instrument import Exchange, Instrument, MarketHoliday
from app.models.ipo import (Ipo, IpoFinancials, IpoGmpHistory, IpoRiskFactor,
                            IpoSubscription)
from app.models.market import HistoricalPrice, Quote
from app.models.research import Catalyst, ResearchCall, ResearchSource
from app.models.system import ComplianceDocument, DataProviderStatus
from app.models.user import User
from app.models.user_data import Watchlist, WatchlistItem
from app.providers.registry import registry
from app.services.research_calls import research_call_service

logger = logging.getLogger(__name__)

DEMO_SOURCE_NAME = "Demonstration dataset (not a real research provider)"

DEMO_STOCKS = [
    {
        "symbol": "HDFCBANK", "name": "HDFC Bank Ltd", "sector": "Financial Services",
        "industry": "Private Sector Bank", "isin": "INE040A01034",
        "bse_code": "500180", "lot_size": 550, "fno": True, "anchor": 727.00,
        "ratios": {"pe": 18.4, "pb": 2.6, "roe": 16.8, "roce": 12.4,
                   "debt_to_equity": 0.9, "ebitda_margin": 32.0,
                   "net_margin": 24.5, "dividend_yield": 1.1,
                   "revenue_cagr_3y": 18.2, "pat_cagr_3y": 15.4,
                   "promoter_holding": 25.6, "fii_holding": 47.2,
                   "dii_holding": 21.3, "promoter_pledge": 0.0,
                   "eps_ttm": 39.5, "beta": 0.92, "current_ratio": 1.2,
                   "interest_coverage": 4.1, "market_cap": 11_20_000_00_00_000.0},
    },
    {
        "symbol": "BAJAJELEC", "name": "Bajaj Electricals Ltd",
        "sector": "Consumer Durables", "industry": "Household Appliances",
        "isin": "INE193E01025", "bse_code": "500031", "lot_size": 1800,
        "fno": False, "anchor": 362.40,
        "ratios": {"pe": 42.1, "pb": 3.4, "roe": 8.9, "roce": 11.2,
                   "debt_to_equity": 0.35, "ebitda_margin": 6.8,
                   "net_margin": 3.1, "dividend_yield": 0.9,
                   "revenue_cagr_3y": 6.4, "pat_cagr_3y": -4.2,
                   "promoter_holding": 62.9, "fii_holding": 8.4,
                   "dii_holding": 18.1, "promoter_pledge": 0.0,
                   "eps_ttm": 8.6, "beta": 1.18, "current_ratio": 1.6,
                   "interest_coverage": 6.2, "market_cap": 41_80_00_00_000.0},
    },
    {
        "symbol": "VOLTAS", "name": "Voltas Ltd", "sector": "Consumer Durables",
        "industry": "Air Conditioners", "isin": "INE226A01021",
        "bse_code": "500575", "lot_size": 500, "fno": True, "anchor": 1320.50,
        "ratios": {"pe": 55.2, "pb": 6.1, "roe": 9.8, "roce": 12.9,
                   "debt_to_equity": 0.18, "ebitda_margin": 7.4,
                   "net_margin": 4.2, "dividend_yield": 0.5,
                   "revenue_cagr_3y": 14.8, "pat_cagr_3y": 9.1,
                   "promoter_holding": 30.3, "fii_holding": 22.7,
                   "dii_holding": 33.4, "promoter_pledge": 0.0,
                   "eps_ttm": 23.9, "beta": 1.05, "current_ratio": 1.9,
                   "interest_coverage": 12.4, "market_cap": 4_37_00_00_00_000.0},
    },
    {
        "symbol": "BDL", "name": "Bharat Dynamics Ltd", "sector": "Capital Goods",
        "industry": "Defence", "isin": "INE171Z01018", "bse_code": "541143",
        "lot_size": 325, "fno": True, "anchor": 1425.00,
        "ratios": {"pe": 78.4, "pb": 12.2, "roe": 16.4, "roce": 19.8,
                   "debt_to_equity": 0.02, "ebitda_margin": 21.3,
                   "net_margin": 15.9, "dividend_yield": 0.4,
                   "revenue_cagr_3y": 11.7, "pat_cagr_3y": 18.9,
                   "promoter_holding": 74.9, "fii_holding": 3.1,
                   "dii_holding": 12.6, "promoter_pledge": 0.0,
                   "eps_ttm": 18.2, "beta": 1.42, "current_ratio": 1.4,
                   "interest_coverage": 45.0, "market_cap": 52_00_00_00_000.0},
    },
    {
        "symbol": "SIEMENS", "name": "Siemens Ltd", "sector": "Capital Goods",
        "industry": "Heavy Electrical Equipment", "isin": "INE003A01024",
        "bse_code": "500550", "lot_size": 150, "fno": True, "anchor": 3845.00,
        "ratios": {"pe": 62.8, "pb": 9.4, "roe": 15.1, "roce": 20.4,
                   "debt_to_equity": 0.01, "ebitda_margin": 12.6,
                   "net_margin": 9.2, "dividend_yield": 0.3,
                   "revenue_cagr_3y": 17.4, "pat_cagr_3y": 24.1,
                   "promoter_holding": 75.0, "fii_holding": 6.8,
                   "dii_holding": 11.2, "promoter_pledge": 0.0,
                   "eps_ttm": 61.2, "beta": 1.12, "current_ratio": 2.1,
                   "interest_coverage": 88.0, "market_cap": 1_37_00_00_00_000.0},
    },
    {
        "symbol": "RELIANCE", "name": "Reliance Industries Ltd",
        "sector": "Oil Gas & Consumable Fuels", "industry": "Refineries",
        "isin": "INE002A01018", "bse_code": "500325", "lot_size": 500,
        "fno": True, "anchor": 1418.00,
        "ratios": {"pe": 24.6, "pb": 2.1, "roe": 8.9, "roce": 9.4,
                   "debt_to_equity": 0.44, "ebitda_margin": 16.2,
                   "net_margin": 7.6, "dividend_yield": 0.4,
                   "revenue_cagr_3y": 12.1, "pat_cagr_3y": 7.8,
                   "promoter_holding": 50.3, "fii_holding": 21.9,
                   "dii_holding": 17.4, "promoter_pledge": 0.0,
                   "eps_ttm": 57.6, "beta": 1.01, "current_ratio": 1.1,
                   "interest_coverage": 5.8, "market_cap": 19_18_000_00_00_000.0},
    },
    {
        "symbol": "INFY", "name": "Infosys Ltd", "sector": "Information Technology",
        "industry": "IT Services", "isin": "INE009A01021", "bse_code": "500209",
        "lot_size": 400, "fno": True, "anchor": 1562.00,
        "ratios": {"pe": 26.2, "pb": 8.4, "roe": 31.8, "roce": 39.2,
                   "debt_to_equity": 0.09, "ebitda_margin": 24.1,
                   "net_margin": 17.3, "dividend_yield": 2.6,
                   "revenue_cagr_3y": 9.4, "pat_cagr_3y": 8.1,
                   "promoter_holding": 14.7, "fii_holding": 33.2,
                   "dii_holding": 38.6, "promoter_pledge": 0.0,
                   "eps_ttm": 63.4, "beta": 0.88, "current_ratio": 2.4,
                   "interest_coverage": 62.0, "market_cap": 6_48_000_00_00_000.0},
    },
    {
        "symbol": "ICICIBANK", "name": "ICICI Bank Ltd",
        "sector": "Financial Services", "industry": "Private Sector Bank",
        "isin": "INE090A01021", "bse_code": "532174", "lot_size": 700,
        "fno": True, "anchor": 1284.00,
        "ratios": {"pe": 17.9, "pb": 3.1, "roe": 18.4, "roce": 13.1,
                   "debt_to_equity": 1.1, "ebitda_margin": 34.2,
                   "net_margin": 26.8, "dividend_yield": 0.8,
                   "revenue_cagr_3y": 19.4, "pat_cagr_3y": 24.6,
                   "promoter_holding": 0.0, "fii_holding": 44.8,
                   "dii_holding": 41.2, "promoter_pledge": 0.0,
                   "eps_ttm": 71.7, "beta": 0.96, "current_ratio": 1.1,
                   "interest_coverage": 3.8, "market_cap": 9_05_000_00_00_000.0},
    },
]


def seed_all(db: Session, force: bool = False) -> Dict[str, int]:
    """Idempotent. Returns a count of what was created."""
    if settings.is_production and not force:
        logger.warning("refusing to seed demo data in PRODUCTION")
        return {"skipped": 1}

    counts: Dict[str, int] = {}
    counts["exchanges"] = _seed_exchanges(db)
    counts["holidays"] = _seed_holidays(db)
    counts["providers"] = _seed_provider_rows(db)
    counts["compliance_documents"] = _seed_compliance_documents(db)
    counts["instruments"] = _seed_instruments(db)
    counts["quotes"] = _seed_quotes(db)
    counts["history_bars"] = _seed_history(db)
    counts["fundamentals"] = _seed_fundamentals(db)
    counts["corporate_actions"] = _seed_corporate_actions(db)
    counts["option_chain"] = _seed_option_chain(db)
    counts["research_sources"] = _seed_research_source(db)
    counts["research_calls"] = _seed_research_calls(db)
    counts["catalysts"] = _seed_catalysts(db)
    counts["ipos"] = _seed_ipos(db)
    db.commit()
    logger.info("seed complete: %s", counts)
    return counts


# --------------------------------------------------------------------------


def _seed_exchanges(db: Session) -> int:
    created = 0
    for code, name, suffix in (("NSE", "National Stock Exchange of India", ".NS"),
                               ("BSE", "BSE Ltd", ".BO")):
        if db.execute(select(Exchange).where(Exchange.code == code)).scalars().first():
            continue
        db.add(Exchange(code=code, name=name, yahoo_suffix=suffix,
                        website=f"https://www.{code.lower()}india.com"))
        created += 1
    db.flush()
    return created


def _seed_holidays(db: Session) -> int:
    """Seeded from a bootstrap list and marked UNVERIFIED so the UI says so."""
    from app.core.market_calendar import _SEED_HOLIDAYS_2026

    created = 0
    for day in sorted(_SEED_HOLIDAYS_2026):
        exists = db.execute(
            select(MarketHoliday)
            .where(MarketHoliday.holiday_date == day)
            .where(MarketHoliday.exchange_code == "NSE")
        ).scalars().first()
        if exists:
            continue
        db.add(MarketHoliday(
            exchange_code="NSE", holiday_date=day,
            description="Bootstrap seed - verify against the exchange calendar",
            provider="seed", source_name="Bootstrap seed list",
            data_status="UNVERIFIED", is_demo=True,
        ))
        created += 1
    db.flush()
    return created


def _seed_provider_rows(db: Session) -> int:
    created = 0
    for provider in registry.all():
        exists = db.execute(
            select(DataProviderStatus)
            .where(DataProviderStatus.name == provider.name)
        ).scalars().first()
        if exists:
            continue
        described = provider.describe()
        db.add(DataProviderStatus(
            name=provider.name,
            provider_type="MARKET_DATA",
            base_url=described.get("base_url"),
            requires_auth=described.get("requires_auth", False),
            rate_limit_per_minute=described.get("rate_limit_per_minute"),
            licence=described.get("licence"),
            terms_url=described.get("terms_url"),
            is_delayed=described.get("is_delayed", True),
            reliability=described.get("reliability", "UNKNOWN"),
            notes=json.dumps(described.get("capabilities", [])),
        ))
        created += 1
    db.flush()
    return created


def _seed_compliance_documents(db: Session) -> int:
    """Tracked as UNVERIFIED. A human must check each against the regulator."""
    documents = [
        {
            "name": "SEBI (Research Analysts) Regulations, 2014",
            "url": "https://www.sebi.gov.in/legal/regulations/",
            "regulator": "SEBI", "document_type": "REGULATION",
            "summary": "Governs who may publish research recommendations and "
                       "on what terms. Check SEBI's site for the current "
                       "consolidated text and latest amendment date.",
            "applies_to": "Any entity publishing research recommendations",
        },
        {
            "name": "SEBI (Investment Advisers) Regulations, 2013",
            "url": "https://www.sebi.gov.in/legal/regulations/",
            "regulator": "SEBI", "document_type": "REGULATION",
            "summary": "Governs personalised investment advice.",
            "applies_to": "Any entity giving personalised investment advice",
        },
        {
            "name": "SEBI master circular for Research Analysts",
            "url": "https://www.sebi.gov.in/",
            "regulator": "SEBI", "document_type": "CIRCULAR",
            "summary": "Consolidated operational requirements for registered "
                       "research analysts. Master circulars are reissued "
                       "periodically - verify you are reading the current one.",
            "applies_to": "Registered research analysts",
        },
        {
            "name": "SEBI investor education material on equity derivatives",
            "url": "https://investor.sebi.gov.in/",
            "regulator": "SEBI", "document_type": "INVESTOR_EDUCATION",
            "summary": "Source for any statistic this platform quotes about "
                       "individual F&O trader outcomes. Always quote the study "
                       "period alongside the figure.",
            "applies_to": "Derivatives risk disclosure",
        },
    ]
    created = 0
    for document in documents:
        exists = db.execute(
            select(ComplianceDocument)
            .where(ComplianceDocument.name == document["name"])
        ).scalars().first()
        if exists:
            continue
        db.add(ComplianceDocument(**document, status="UNVERIFIED"))
        created += 1
    db.flush()
    return created


def _seed_instruments(db: Session) -> int:
    created = 0
    now = datetime.now(tz=timezone.utc)
    for spec in DEMO_STOCKS:
        exists = db.execute(
            select(Instrument).where(Instrument.symbol == spec["symbol"])
            .where(Instrument.segment == "EQUITY")
        ).scalars().first()
        if exists:
            continue
        db.add(Instrument(
            symbol=spec["symbol"], name=spec["name"], exchange_code="NSE",
            segment="EQUITY", isin=spec["isin"], nse_code=spec["symbol"],
            bse_code=spec["bse_code"], series="EQ", sector=spec["sector"],
            industry=spec["industry"], lot_size=spec["lot_size"],
            is_fno_eligible=spec["fno"], provider="demo",
            source_name=DEMO_SOURCE_NAME, data_status="DEMO",
            observed_at=now, is_demo=True,
        ))
        created += 1

    for symbol, name in (("NIFTY 50", "NIFTY 50 Index"),
                         ("NIFTY BANK", "NIFTY Bank Index"),
                         ("SENSEX", "BSE SENSEX")):
        exists = db.execute(
            select(Instrument).where(Instrument.symbol == symbol)
        ).scalars().first()
        if exists:
            continue
        db.add(Instrument(
            symbol=symbol, name=name,
            exchange_code="BSE" if symbol == "SENSEX" else "NSE",
            segment="INDEX", provider="demo", source_name=DEMO_SOURCE_NAME,
            data_status="DEMO", observed_at=now, is_demo=True,
        ))
        created += 1
    db.flush()
    return created


def _seed_quotes(db: Session) -> int:
    created = 0
    now = datetime.now(tz=timezone.utc)
    for spec in DEMO_STOCKS:
        instrument = db.execute(
            select(Instrument).where(Instrument.symbol == spec["symbol"])
            .where(Instrument.segment == "EQUITY")
        ).scalars().first()
        if instrument is None:
            continue
        if db.execute(
            select(Quote).where(Quote.instrument_id == instrument.id)
        ).scalars().first():
            continue

        rng = random.Random(spec["symbol"])
        anchor = spec["anchor"]
        previous_close = round(anchor / (1 + rng.uniform(-0.02, 0.02)), 2)
        change = round(anchor - previous_close, 2)

        db.add(Quote(
            instrument_id=instrument.id, symbol=spec["symbol"], ltp=anchor,
            open=round(previous_close * (1 + rng.uniform(-0.008, 0.008)), 2),
            high=round(anchor * (1 + rng.uniform(0.002, 0.015)), 2),
            low=round(anchor * (1 - rng.uniform(0.002, 0.015)), 2),
            previous_close=previous_close, change=change,
            change_pct=round(change / previous_close * 100.0, 2),
            volume=rng.randint(500_000, 12_000_000),
            average_volume_20d=rng.randint(400_000, 9_000_000),
            vwap=round(anchor * (1 + rng.uniform(-0.004, 0.004)), 2),
            week52_high=round(anchor * rng.uniform(1.08, 1.45), 2),
            week52_low=round(anchor * rng.uniform(0.58, 0.88), 2),
            market_cap=spec["ratios"]["market_cap"],
            provider="demo", source_name=DEMO_SOURCE_NAME,
            data_status="DEMO", observed_at=now, is_demo=True,
            market_status="DEMO",
        ))
        created += 1
    db.flush()
    return created


def _seed_history(db: Session, sessions: int = 500) -> int:
    """Deterministic synthetic OHLCV so charts and backtests have inputs."""
    created = 0
    for spec in DEMO_STOCKS:
        instrument = db.execute(
            select(Instrument).where(Instrument.symbol == spec["symbol"])
            .where(Instrument.segment == "EQUITY")
        ).scalars().first()
        if instrument is None:
            continue
        if db.execute(
            select(HistoricalPrice)
            .where(HistoricalPrice.instrument_id == instrument.id).limit(1)
        ).scalars().first():
            continue

        rng = random.Random(f"{spec['symbol']}-history")
        anchor = spec["anchor"]
        # Walk backwards from the anchor so the last bar matches the quote.
        prices: List[float] = [anchor]
        drift = rng.uniform(-0.0004, 0.0009)
        volatility = rng.uniform(0.010, 0.022)
        for _ in range(sessions - 1):
            shock = rng.gauss(drift, volatility)
            prices.append(max(1.0, prices[-1] / (1 + shock)))
        prices.reverse()

        day = date.today()
        days: List[date] = []
        while len(days) < sessions:
            if is_trading_day(day):
                days.append(day)
            day -= timedelta(days=1)
        days.reverse()

        for bar_date, close in zip(days, prices):
            spread = close * rng.uniform(0.004, 0.018)
            open_price = close * (1 + rng.uniform(-0.008, 0.008))
            high = max(open_price, close) + spread * rng.uniform(0.2, 0.7)
            low = min(open_price, close) - spread * rng.uniform(0.2, 0.7)
            db.add(HistoricalPrice(
                instrument_id=instrument.id, symbol=spec["symbol"],
                interval="1d",
                bar_time=datetime.combine(bar_date, time(15, 30), tzinfo=IST)
                .astimezone(timezone.utc),
                open=round(open_price, 2), high=round(high, 2),
                low=round(max(low, 0.5), 2), close=round(close, 2),
                raw_close=round(close, 2),
                volume=rng.randint(200_000, 15_000_000),
                provider="demo", source_name=DEMO_SOURCE_NAME,
                data_status="DEMO", is_demo=True,
            ))
            created += 1
        db.flush()
    return created


def _seed_fundamentals(db: Session) -> int:
    created = 0
    now = datetime.now(tz=timezone.utc)
    for spec in DEMO_STOCKS:
        instrument = db.execute(
            select(Instrument).where(Instrument.symbol == spec["symbol"])
            .where(Instrument.segment == "EQUITY")
        ).scalars().first()
        if instrument is None:
            continue
        if db.execute(
            select(Fundamental).where(Fundamental.instrument_id == instrument.id)
        ).scalars().first():
            continue

        ratios = spec["ratios"]
        db.add(Fundamental(
            instrument_id=instrument.id, symbol=spec["symbol"],
            as_of=date.today(), **{
                k: v for k, v in ratios.items()
                if k in Fundamental.__table__.columns.keys()
            },
            provider="demo", source_name=DEMO_SOURCE_NAME,
            data_status="DEMO", observed_at=now, is_demo=True,
        ))
        db.add(CompanyProfile(
            instrument_id=instrument.id, symbol=spec["symbol"],
            description=(
                f"{spec['name']} operates in the {spec['industry']} space "
                f"within {spec['sector']}. This description is placeholder "
                f"demonstration text, not a company disclosure."
            ),
            industry=spec["industry"], sector=spec["sector"],
            products=json.dumps(["Demonstration product line"]),
            business_segments=json.dumps([spec["industry"]]),
            geographies=json.dumps(["India"]),
            peers=json.dumps([
                s["symbol"] for s in DEMO_STOCKS
                if s["sector"] == spec["sector"] and s["symbol"] != spec["symbol"]
            ]),
            provider="demo", source_name=DEMO_SOURCE_NAME,
            data_status="DEMO", observed_at=now, is_demo=True,
        ))

        rng = random.Random(f"{spec['symbol']}-fin")
        base_revenue = ratios["market_cap"] / rng.uniform(2.0, 6.0)
        for offset in range(5, 0, -1):
            year_end = date(date.today().year - offset + 1, 3, 31)
            growth = (1 + ratios["revenue_cagr_3y"] / 100.0) ** (5 - offset)
            revenue = base_revenue * growth / 5.0
            ebitda = revenue * ratios["ebitda_margin"] / 100.0
            pat = revenue * ratios["net_margin"] / 100.0
            db.add(FinancialStatement(
                instrument_id=instrument.id, symbol=spec["symbol"],
                period_type="ANNUAL", period_end=year_end,
                period_label=f"FY{str(year_end.year)[-2:]}",
                statement_type="PNL", revenue=round(revenue, 2),
                ebitda=round(ebitda, 2),
                ebitda_margin=ratios["ebitda_margin"],
                ebit=round(ebitda * 0.82, 2),
                interest=round(ebitda * 0.08, 2),
                depreciation=round(ebitda * 0.10, 2),
                pbt=round(pat * 1.33, 2), tax=round(pat * 0.33, 2),
                pat=round(pat, 2),
                eps=round(ratios["eps_ttm"] * (0.7 + 0.075 * (5 - offset)), 2),
                total_assets=round(revenue * 1.8, 2),
                total_debt=round(revenue * ratios["debt_to_equity"] * 0.4, 2),
                cash_and_equivalents=round(revenue * 0.12, 2),
                net_worth=round(revenue * 0.9, 2),
                working_capital=round(revenue * 0.22, 2),
                operating_cash_flow=round(pat * rng.uniform(0.85, 1.35), 2),
                capex=round(revenue * 0.05, 2),
                free_cash_flow=round(pat * rng.uniform(0.4, 0.95), 2),
                published_at=year_end + timedelta(days=45),
                provider="demo", source_name=DEMO_SOURCE_NAME,
                data_status="DEMO", is_demo=True,
            ))
        db.add(Shareholding(
            symbol=spec["symbol"], as_of=date.today().replace(day=1),
            promoter=ratios["promoter_holding"],
            promoter_pledged=ratios["promoter_pledge"],
            fii=ratios["fii_holding"], dii=ratios["dii_holding"],
            public=round(100.0 - ratios["promoter_holding"]
                         - ratios["fii_holding"] - ratios["dii_holding"], 2),
            provider="demo", source_name=DEMO_SOURCE_NAME,
            data_status="DEMO", is_demo=True,
        ))
        created += 1
    db.flush()
    return created


def _seed_corporate_actions(db: Session) -> int:
    created = 0
    today = date.today()
    for offset, spec in enumerate(DEMO_STOCKS[:5]):
        exists = db.execute(
            select(CorporateAction)
            .where(CorporateAction.symbol == spec["symbol"])
        ).scalars().first()
        if exists:
            continue
        db.add(CorporateAction(
            symbol=spec["symbol"], action_type="DIVIDEND",
            description=f"Interim dividend (demonstration record)",
            announcement_date=today + timedelta(days=offset * 3),
            ex_date=today + timedelta(days=10 + offset * 4),
            record_date=today + timedelta(days=11 + offset * 4),
            payment_date=today + timedelta(days=25 + offset * 4),
            value=round(spec["anchor"] * 0.004, 2),
            provider="demo", source_name=DEMO_SOURCE_NAME,
            data_status="DEMO", is_demo=True,
        ))
        db.add(EarningsEvent(
            symbol=spec["symbol"],
            quarter_label=f"Q{((today.month - 1) // 3) + 1}FY{str(today.year)[-2:]}",
            expected_date=today + timedelta(days=6 + offset * 5),
            status="SCHEDULED", provider="demo",
            source_name=DEMO_SOURCE_NAME, data_status="DEMO", is_demo=True,
        ))
        created += 1
    db.flush()
    return created


def _seed_option_chain(db: Session) -> int:
    """A demonstration chain for BDL and SIEMENS so the F&O screens render."""
    created = 0
    now = datetime.now(tz=timezone.utc)
    expiry = _next_last_thursday()

    for spec in [s for s in DEMO_STOCKS if s["symbol"] in ("BDL", "SIEMENS")]:
        exists = db.execute(
            select(OptionChainSnapshot)
            .where(OptionChainSnapshot.underlying_symbol == spec["symbol"])
        ).scalars().first()
        if exists:
            continue

        spot = spec["anchor"]
        step = 20.0 if spot < 2000 else 100.0
        atm = round(spot / step) * step
        rng = random.Random(f"{spec['symbol']}-chain")

        snapshot = OptionChainSnapshot(
            underlying_symbol=spec["symbol"], expiry=expiry, captured_at=now,
            underlying_value=spot, atm_strike=atm,
            provider="demo", source_name=DEMO_SOURCE_NAME,
            data_status="DEMO", observed_at=now, is_demo=True,
        )
        db.add(snapshot)
        db.flush()

        total_ce_oi = total_pe_oi = 0
        strikes = [atm + step * i for i in range(-10, 11)]
        for strike in strikes:
            distance = abs(strike - spot) / spot
            for option_type in ("CE", "PE"):
                itm = (strike < spot) if option_type == "CE" else (strike > spot)
                intrinsic = (
                    max(0.0, spot - strike) if option_type == "CE"
                    else max(0.0, strike - spot)
                )
                extrinsic = spot * 0.028 * max(0.05, 1 - distance * 6)
                ltp = round(max(0.05, intrinsic + extrinsic * rng.uniform(0.7, 1.3)), 2)
                oi = int(max(150, rng.gauss(60_000, 25_000) * max(0.1, 1 - distance * 4)))
                oi_change = int(rng.gauss(0, oi * 0.15))
                if option_type == "CE":
                    total_ce_oi += oi
                else:
                    total_pe_oi += oi
                db.add(OptionSnapshot(
                    snapshot_id=snapshot.id, underlying_symbol=spec["symbol"],
                    expiry=expiry, strike=strike, option_type=option_type,
                    ltp=ltp, change=round(rng.uniform(-0.12, 0.12) * ltp, 2),
                    change_pct=round(rng.uniform(-12, 12), 2),
                    open_interest=oi, oi_change=oi_change,
                    volume=int(abs(rng.gauss(oi * 0.4, oi * 0.2))),
                    implied_volatility=round(
                        26 + distance * 90 + rng.uniform(-3, 3), 2
                    ),
                    bid=round(ltp * 0.985, 2), ask=round(ltp * 1.015, 2),
                    bid_qty=rng.randint(1, 50) * spec["lot_size"],
                    ask_qty=rng.randint(1, 50) * spec["lot_size"],
                    moneyness="ITM" if itm else ("ATM" if strike == atm else "OTM"),
                    provider="demo", source_name=DEMO_SOURCE_NAME,
                    data_status="DEMO", observed_at=now, is_demo=True,
                ))
                created += 1

        snapshot.total_call_oi = total_ce_oi
        snapshot.total_put_oi = total_pe_oi
        snapshot.pcr_oi = round(total_pe_oi / total_ce_oi, 3) if total_ce_oi else None
        snapshot.strike_count = len(strikes)
        db.flush()
    return created


def _next_last_thursday() -> date:
    """Indian monthly derivatives expire on the last Thursday of the month."""
    today = date.today()
    year, month = today.year, today.month
    for _ in range(3):
        if month == 12:
            first_next = date(year + 1, 1, 1)
        else:
            first_next = date(year, month + 1, 1)
        last_day = first_next - timedelta(days=1)
        offset = (last_day.weekday() - 3) % 7
        candidate = last_day - timedelta(days=offset)
        if candidate > today:
            return candidate
        month = month + 1 if month < 12 else 1
        year = year if month != 1 else year + 1
    return today + timedelta(days=30)


def _seed_research_source(db: Session) -> int:
    exists = db.execute(
        select(ResearchSource).where(ResearchSource.name == DEMO_SOURCE_NAME)
    ).scalars().first()
    if exists:
        return 0
    db.add(ResearchSource(
        name=DEMO_SOURCE_NAME, source_type="EXTERNAL_RESEARCH",
        organisation=None, website=None,
        registration_note=(
            "This is a placeholder used by the seeded demonstration dataset. "
            "It is not a real research provider and carries no registration."
        ),
        reliability="UNKNOWN",
        licence_note="Illustrative sample records shipped with the repository.",
    ))
    db.flush()
    return 1


def _seed_research_calls(db: Session) -> int:
    """The illustrative setups from the specification, all badged DEMO."""
    source = db.execute(
        select(ResearchSource).where(ResearchSource.name == DEMO_SOURCE_NAME)
    ).scalars().first()
    now = datetime.now(tz=timezone.utc)

    setups = [
        {
            "symbol": "HDFCBANK", "company_name": "HDFC Bank Ltd",
            "segment": "EQUITY", "side": "BUY",
            "entry_min": 725.30, "entry_max": 727.30, "stop_loss": 715.00,
            "target_1": 746.00, "horizon": "SWING",
            "rationale": "Illustrative sample setup used to demonstrate the "
                         "card layout, the status engine and the risk/reward "
                         "arithmetic. It is not a live view on the stock.",
            "invalidation": "A close below the published stop removes the "
                            "structure this sample was built around.",
        },
        {
            "symbol": "BAJAJELEC", "company_name": "Bajaj Electricals Ltd",
            "segment": "EQUITY", "side": "BUY",
            "entry_min": 361.40, "entry_max": 362.40, "stop_loss": 352.80,
            "target_1": 378.00, "horizon": "SWING",
            "rationale": "Illustrative sample setup. Demonstrates the "
                         "'within entry range' state where the last price sits "
                         "exactly on the upper bound.",
            "invalidation": "A close below the stop.",
        },
        {
            "symbol": "VOLTAS", "company_name": "Voltas Ltd",
            "segment": "EQUITY", "side": "BUY",
            "entry_min": 1532.00, "entry_max": 1532.00, "stop_loss": 1240.00,
            "target_1": 1920.00, "horizon": "POSITIONAL",
            "rationale": "Illustrative sample setup. Demonstrates a call whose "
                         "price has fallen well below the published entry - the "
                         "status engine reports NOT_ACTIVATED with a negative "
                         "achieved figure rather than continuing to show BUY.",
            "invalidation": "A close below the stop.",
        },
        {
            "symbol": "BDL", "company_name": "Bharat Dynamics Ltd",
            "segment": "OPTION", "side": "BUY", "strike": 1440.0,
            "option_type": "CE", "expiry": _next_last_thursday(),
            "lot_size": 325,
            "entry_min": 20.00, "entry_max": 21.00, "stop_loss": 5.00,
            "target_1": 55.00, "horizon": "INTRADAY",
            "rationale": "Illustrative sample option setup. Demonstrates the "
                         "F&O card, the break-even calculation and the theta "
                         "risk panel.",
            "invalidation": "Premium closing below the stop, or the underlying "
                            "failing to clear the strike before expiry.",
        },
        {
            "symbol": "SIEMENS", "company_name": "Siemens Ltd",
            "segment": "OPTION", "side": "BUY", "strike": 3900.0,
            "option_type": "PE", "expiry": _next_last_thursday(),
            "lot_size": 150,
            "entry_min": 47.50, "entry_max": 48.50, "stop_loss": 27.50,
            "target_1": 82.00, "horizon": "INTRADAY",
            "rationale": "Illustrative sample put setup, included to show that "
                         "the platform handles both sides of the chain.",
            "invalidation": "Premium closing below the stop.",
        },
    ]

    created = 0
    for setup in setups:
        exists = db.execute(
            select(ResearchCall)
            .where(ResearchCall.symbol == setup["symbol"])
            .where(ResearchCall.segment == setup["segment"])
            .where(ResearchCall.is_demo.is_(True))
        ).scalars().first()
        if exists:
            continue

        payload = {
            **setup,
            "source_type": "EXTERNAL_RESEARCH",
            "source_name": DEMO_SOURCE_NAME,
            "source_id": source.id if source else None,
            "analyst_name": None,
            "original_recommendation": (
                "Reproduced from the illustrative example in the platform "
                "specification. No third party published this."
            ),
            "was_transformed": False,
            "published_at": now - timedelta(hours=6),
            "valid_until": now + timedelta(days=21),
            "is_published": True,
            "is_demo": True,
            "why_now": [
                "This is seeded demonstration data - there is no live evidence "
                "behind it.",
            ],
            "why_not": [
                "It is not market data. Do not act on it.",
                "The levels are copied from the specification's example cards.",
            ],
            "provider": "demo",
        }
        try:
            call = research_call_service.create(db, payload)
        except ValueError as exc:
            logger.warning("skipped demo call %s: %s", setup["symbol"], exc)
            continue
        call.data_status = "DEMO"
        call.is_demo = True
        research_call_service.refresh_status(db, call)
        created += 1
    db.flush()
    return created


def _seed_catalysts(db: Session) -> int:
    created = 0
    today = date.today()
    for offset, spec in enumerate(DEMO_STOCKS[:5]):
        exists = db.execute(
            select(Catalyst).where(Catalyst.symbol == spec["symbol"])
        ).scalars().first()
        if exists:
            continue
        db.add(Catalyst(
            symbol=spec["symbol"], scope="STOCK",
            sector=spec["sector"],
            title=f"{spec['name']} quarterly results (demonstration record)",
            category="EARNINGS",
            event_date=today + timedelta(days=6 + offset * 5),
            expected_impact="HIGH" if offset < 2 else "MEDIUM",
            risk_level="HIGH" if offset < 2 else "MEDIUM",
            historical_reaction_note=(
                "No measured historical reaction is stored for this seeded "
                "record."
            ),
            is_confirmed=False, provider="demo",
            source_name=DEMO_SOURCE_NAME, data_status="DEMO", is_demo=True,
        ))
        created += 1
    db.flush()
    return created


def _seed_ipos(db: Session) -> int:
    today = date.today()
    specs = [
        {
            "slug": "demo-cloud-infrastructure", "company_name":
            "Demo Cloud Infrastructure Ltd", "status": "OPEN",
            "open_date": today - timedelta(days=1),
            "close_date": today + timedelta(days=2),
            "listing_date": today + timedelta(days=8),
            "price_band_low": 475.0, "price_band_high": 500.0,
            "face_value": 2.0, "lot_size": 30, "issue_size_cr": 1850.0,
            "fresh_issue_cr": 1200.0, "ofs_cr": 650.0,
            "industry": "Information Technology", "registrar": "Demo Registrar",
            "gmp": 145.0, "subscription": {"qib": 4.2, "nii": 6.8,
                                           "retail": 2.1, "total": 3.4},
            "financials": [
                {"label": "FY23", "revenue": 620.0, "ebitda": 118.0,
                 "pat": 74.0, "eps": 12.4, "net_worth": 340.0,
                 "total_debt": 40.0, "roe": 21.8, "roce": 26.4},
                {"label": "FY24", "revenue": 812.0, "ebitda": 168.0,
                 "pat": 104.0, "eps": 16.9, "net_worth": 455.0,
                 "total_debt": 32.0, "roe": 22.9, "roce": 28.1},
                {"label": "FY25", "revenue": 1_090.0, "ebitda": 235.0,
                 "pat": 148.0, "eps": 22.6, "net_worth": 620.0,
                 "total_debt": 25.0, "roe": 23.9, "roce": 30.2},
            ],
            "risks": [
                {"category": "CUSTOMER_CONCENTRATION",
                 "description": "The top five customers accounted for a "
                                "material share of revenue in the last "
                                "disclosed period (demonstration record).",
                 "severity": "HIGH", "quantum": 54.0, "quantum_unit": "%"},
                {"category": "REGULATORY",
                 "description": "Data-localisation requirements could raise "
                                "operating costs (demonstration record).",
                 "severity": "MEDIUM"},
            ],
        },
        {
            "slug": "demo-clean-energy", "company_name":
            "Demo Clean Energy Ltd", "status": "UPCOMING",
            "open_date": today + timedelta(days=6),
            "close_date": today + timedelta(days=9),
            "listing_date": today + timedelta(days=15),
            "price_band_low": 210.0, "price_band_high": 225.0,
            "face_value": 10.0, "lot_size": 65, "issue_size_cr": 3200.0,
            "fresh_issue_cr": 900.0, "ofs_cr": 2300.0,
            "industry": "Power", "registrar": "Demo Registrar",
            "gmp": 18.0, "subscription": None,
            "financials": [
                {"label": "FY23", "revenue": 1_420.0, "ebitda": 340.0,
                 "pat": 62.0, "eps": 4.1, "net_worth": 890.0,
                 "total_debt": 2_100.0, "roe": 7.0, "roce": 9.2},
                {"label": "FY24", "revenue": 1_680.0, "ebitda": 402.0,
                 "pat": 71.0, "eps": 4.6, "net_worth": 960.0,
                 "total_debt": 2_290.0, "roe": 7.4, "roce": 9.6},
                {"label": "FY25", "revenue": 1_910.0, "ebitda": 455.0,
                 "pat": 58.0, "eps": 3.6, "net_worth": 1_010.0,
                 "total_debt": 2_480.0, "roe": 5.7, "roce": 8.8},
            ],
            "risks": [
                {"category": "LEVERAGE",
                 "description": "Debt to equity above 2x with a rising "
                                "absolute debt load (demonstration record).",
                 "severity": "HIGH", "quantum": 2.45, "quantum_unit": "x"},
                {"category": "REGULATORY",
                 "description": "Tariff renegotiation risk on existing power "
                                "purchase agreements (demonstration record).",
                 "severity": "HIGH"},
                {"category": "PROMOTER",
                 "description": "A large share of the issue is an offer for "
                                "sale by existing shareholders.",
                 "severity": "MEDIUM", "quantum": 71.9, "quantum_unit": "%"},
            ],
        },
    ]

    created = 0
    now = datetime.now(tz=timezone.utc)
    for spec in specs:
        if db.execute(select(Ipo).where(Ipo.slug == spec["slug"])).scalars().first():
            continue
        ipo = Ipo(
            slug=spec["slug"], company_name=spec["company_name"],
            status=spec["status"], ipo_type="MAINBOARD",
            open_date=spec["open_date"], close_date=spec["close_date"],
            listing_date=spec["listing_date"],
            price_band_low=spec["price_band_low"],
            price_band_high=spec["price_band_high"],
            face_value=spec["face_value"], lot_size=spec["lot_size"],
            retail_min_investment=round(spec["price_band_high"]
                                        * spec["lot_size"], 2),
            issue_size_cr=spec["issue_size_cr"],
            fresh_issue_cr=spec["fresh_issue_cr"], ofs_cr=spec["ofs_cr"],
            industry=spec["industry"], registrar=spec["registrar"],
            listing_exchanges="NSE, BSE",
            lead_managers=json.dumps(["Demo Capital Markets"]),
            use_of_proceeds=json.dumps([
                "Repayment of borrowings (demonstration record)",
                "General corporate purposes",
            ]),
            provider="demo", source_name=DEMO_SOURCE_NAME,
            data_status="DEMO", observed_at=now, is_demo=True,
        )
        db.add(ipo)
        db.flush()

        rng = random.Random(spec["slug"])
        for days_back in range(10, -1, -1):
            drift = rng.uniform(-0.12, 0.10)
            value = round(max(0.0, spec["gmp"] * (1 + drift * days_back / 10)), 1)
            db.add(IpoGmpHistory(
                ipo_id=ipo.id,
                observed_on=now - timedelta(days=days_back),
                gmp=value,
                gmp_pct=round(value / spec["price_band_high"] * 100.0, 2),
                estimated_listing_price=round(spec["price_band_high"] + value, 2),
                reference_price=spec["price_band_high"],
                provider="demo", source_name=DEMO_SOURCE_NAME,
                data_status="DEMO", is_demo=True,
            ))

        if spec["subscription"]:
            sub = spec["subscription"]
            db.add(IpoSubscription(
                ipo_id=ipo.id, observed_at=now, day_number=1,
                qib_times=sub["qib"], nii_times=sub["nii"],
                retail_times=sub["retail"], total_times=sub["total"],
                provider="demo", source_name=DEMO_SOURCE_NAME,
                data_status="DEMO", is_demo=True,
            ))

        for financial in spec["financials"]:
            db.add(IpoFinancials(
                ipo_id=ipo.id, period_label=financial["label"],
                period_end=date(2000 + int(financial["label"][2:]), 3, 31),
                revenue=financial["revenue"], ebitda=financial["ebitda"],
                ebitda_margin=round(financial["ebitda"] / financial["revenue"]
                                    * 100.0, 2),
                pat=financial["pat"],
                net_margin=round(financial["pat"] / financial["revenue"]
                                 * 100.0, 2),
                eps=financial["eps"], net_worth=financial["net_worth"],
                total_debt=financial["total_debt"], roe=financial["roe"],
                roce=financial["roce"],
                provider="demo", source_name=DEMO_SOURCE_NAME,
                data_status="DEMO", is_demo=True,
            ))

        for risk in spec["risks"]:
            db.add(IpoRiskFactor(
                ipo_id=ipo.id, category=risk["category"],
                description=risk["description"], severity=risk["severity"],
                quantum=risk.get("quantum"),
                quantum_unit=risk.get("quantum_unit"),
                provider="demo", source_name=DEMO_SOURCE_NAME,
                data_status="DEMO", is_demo=True,
            ))
        created += 1
    db.flush()
    return created


def bootstrap_admin(db: Session) -> Optional[str]:
    """Create the first admin from the environment, if configured and empty."""
    if db.execute(select(User).limit(1)).scalars().first():
        return None
    if not settings.bootstrap_admin_password:
        logger.info(
            "no BOOTSTRAP_ADMIN_PASSWORD set - register the first user through "
            "the API and it will be granted the ADMIN role automatically"
        )
        return None
    user = User(
        email=settings.bootstrap_admin_email.lower(),
        display_name="Administrator",
        password_hash=hash_password(settings.bootstrap_admin_password),
        role=Role.ADMIN.value,
    )
    db.add(user)
    db.flush()
    db.add(Watchlist(user_id=user.id, name="Monitoring",
                     description="Default watchlist"))
    db.flush()
    for symbol in ("HDFCBANK", "VOLTAS", "BDL"):
        watchlist = db.execute(
            select(Watchlist).where(Watchlist.user_id == user.id)
        ).scalars().first()
        db.add(WatchlistItem(watchlist_id=watchlist.id, symbol=symbol))
    db.commit()
    logger.info("bootstrap admin created: %s", user.email)
    return user.id
