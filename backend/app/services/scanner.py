"""Market scanners.

Scanners run against the persisted `technical_indicators` and `fundamentals`
snapshots, so a scan is a database query rather than hundreds of provider
calls. Every built-in scanner declares its filters explicitly - there is no
hidden logic, and the same declarative filter format is what a user-defined
scanner saves.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.orm import Session

from app.models.derivatives import FuturesSnapshot, OptionChainSnapshot
from app.models.fundamental import Fundamental
from app.models.instrument import Instrument
from app.models.market import Quote, TechnicalIndicatorSnapshot

METHODOLOGY = "/methodology#scanners"

# Fields a filter may reference, mapped to their column.
FIELD_MAP: Dict[str, Any] = {
    # technical
    "close": TechnicalIndicatorSnapshot.close,
    "rsi_14": TechnicalIndicatorSnapshot.rsi_14,
    "macd": TechnicalIndicatorSnapshot.macd,
    "macd_signal": TechnicalIndicatorSnapshot.macd_signal,
    "macd_hist": TechnicalIndicatorSnapshot.macd_hist,
    "sma_20": TechnicalIndicatorSnapshot.sma_20,
    "sma_50": TechnicalIndicatorSnapshot.sma_50,
    "sma_100": TechnicalIndicatorSnapshot.sma_100,
    "sma_200": TechnicalIndicatorSnapshot.sma_200,
    "ema_9": TechnicalIndicatorSnapshot.ema_9,
    "ema_20": TechnicalIndicatorSnapshot.ema_20,
    "ema_50": TechnicalIndicatorSnapshot.ema_50,
    "atr_14": TechnicalIndicatorSnapshot.atr_14,
    "atr_pct": TechnicalIndicatorSnapshot.atr_pct,
    "adx_14": TechnicalIndicatorSnapshot.adx_14,
    "bb_upper": TechnicalIndicatorSnapshot.bb_upper,
    "bb_lower": TechnicalIndicatorSnapshot.bb_lower,
    "bb_width": TechnicalIndicatorSnapshot.bb_width,
    "stoch_k": TechnicalIndicatorSnapshot.stoch_k,
    "supertrend_dir": TechnicalIndicatorSnapshot.supertrend_dir,
    "vwap": TechnicalIndicatorSnapshot.vwap,
    "volume_ratio_20d": TechnicalIndicatorSnapshot.volume_ratio_20d,
    "distance_from_52w_high_pct":
        TechnicalIndicatorSnapshot.distance_from_52w_high_pct,
    "distance_from_52w_low_pct":
        TechnicalIndicatorSnapshot.distance_from_52w_low_pct,
    "trend_score": TechnicalIndicatorSnapshot.trend_score,
    "momentum_score": TechnicalIndicatorSnapshot.momentum_score,
    # fundamental
    "pe": Fundamental.pe,
    "pb": Fundamental.pb,
    "ev_ebitda": Fundamental.ev_ebitda,
    "roe": Fundamental.roe,
    "roce": Fundamental.roce,
    "debt_to_equity": Fundamental.debt_to_equity,
    "dividend_yield": Fundamental.dividend_yield,
    "revenue_cagr_3y": Fundamental.revenue_cagr_3y,
    "pat_cagr_3y": Fundamental.pat_cagr_3y,
    "promoter_holding": Fundamental.promoter_holding,
    "fii_holding": Fundamental.fii_holding,
    "dii_holding": Fundamental.dii_holding,
    "market_cap": Fundamental.market_cap,
    "ebitda_margin": Fundamental.ebitda_margin,
    "net_margin": Fundamental.net_margin,
    # quote
    "ltp": Quote.ltp,
    "change_pct": Quote.change_pct,
    "volume": Quote.volume,
    "week52_high": Quote.week52_high,
    "week52_low": Quote.week52_low,
}

OPERATORS = {">", ">=", "<", "<=", "==", "!=", "between"}


@dataclass
class Filter:
    field: str
    op: str
    value: Any
    value2: Optional[Any] = None       # for `between`
    compare_to_field: Optional[str] = None   # compare against another column

    def build(self):
        column = FIELD_MAP.get(self.field)
        if column is None:
            raise ValueError(f"unknown scanner field: {self.field}")
        if self.op not in OPERATORS:
            raise ValueError(f"unknown operator: {self.op}")

        right = (
            FIELD_MAP.get(self.compare_to_field) if self.compare_to_field
            else self.value
        )
        if self.compare_to_field and right is None:
            raise ValueError(f"unknown comparison field: {self.compare_to_field}")

        if self.op == "between":
            return and_(column >= self.value, column <= self.value2)
        if self.op == ">":
            return column > right
        if self.op == ">=":
            return column >= right
        if self.op == "<":
            return column < right
        if self.op == "<=":
            return column <= right
        if self.op == "==":
            return column == right
        return column != right

    def describe(self) -> str:
        if self.op == "between":
            return f"{self.field} between {self.value} and {self.value2}"
        target = self.compare_to_field or self.value
        return f"{self.field} {self.op} {target}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScannerDefinitionSpec:
    key: str
    name: str
    category: str          # TECHNICAL | FUNDAMENTAL | FNO | COMBINED
    description: str
    filters: List[Filter]
    logic: str = "AND"
    methodology_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "category": self.category,
            "description": self.description, "logic": self.logic,
            "filters": [f.to_dict() for f in self.filters],
            "filter_descriptions": [f.describe() for f in self.filters],
            "methodology_note": self.methodology_note,
        }


# --------------------------------------------------------------------------
# Built-in scanners
# --------------------------------------------------------------------------

BUILTIN_SCANNERS: List[ScannerDefinitionSpec] = [
    ScannerDefinitionSpec(
        "rsi_oversold", "RSI oversold", "TECHNICAL",
        "RSI(14) below 30 - price has fallen persistently relative to its own "
        "recent range.",
        [Filter("rsi_14", "<", 30)],
        methodology_note="Oversold is a description of momentum, not a buy "
                         "trigger. Strong downtrends stay oversold for weeks.",
    ),
    ScannerDefinitionSpec(
        "rsi_overbought", "RSI overbought", "TECHNICAL",
        "RSI(14) above 70.",
        [Filter("rsi_14", ">", 70)],
        methodology_note="Overbought readings persist through strong uptrends.",
    ),
    ScannerDefinitionSpec(
        "macd_bullish_cross", "MACD bullish", "TECHNICAL",
        "MACD line above its signal line with a positive histogram.",
        [Filter("macd_hist", ">", 0),
         Filter("macd", ">", 0, compare_to_field="macd_signal")],
    ),
    ScannerDefinitionSpec(
        "macd_bearish_cross", "MACD bearish", "TECHNICAL",
        "MACD line below its signal line with a negative histogram.",
        [Filter("macd_hist", "<", 0),
         Filter("macd", "<", 0, compare_to_field="macd_signal")],
    ),
    ScannerDefinitionSpec(
        "ema_golden_alignment", "EMA alignment (9 > 20 > 50)", "TECHNICAL",
        "Short-term averages stacked above longer ones.",
        [Filter("ema_9", ">", 0, compare_to_field="ema_20"),
         Filter("ema_20", ">", 0, compare_to_field="ema_50")],
    ),
    ScannerDefinitionSpec(
        "above_200dma", "Above the 200-DMA", "TECHNICAL",
        "Close above the 200-day simple moving average.",
        [Filter("close", ">", 0, compare_to_field="sma_200")],
    ),
    ScannerDefinitionSpec(
        "near_52w_high", "Near 52-week high", "TECHNICAL",
        "Within 3% of the 52-week high.",
        [Filter("distance_from_52w_high_pct", ">=", -3)],
    ),
    ScannerDefinitionSpec(
        "near_52w_low", "Near 52-week low", "TECHNICAL",
        "Within 5% of the 52-week low.",
        [Filter("distance_from_52w_low_pct", "<=", 5)],
    ),
    ScannerDefinitionSpec(
        "volume_breakout", "Volume breakout", "TECHNICAL",
        "Volume at least twice its own 20-day average.",
        [Filter("volume_ratio_20d", ">=", 2.0)],
        methodology_note="Volume is compared against the 20 bars *before* the "
                         "current one, so today's print does not inflate its "
                         "own baseline.",
    ),
    ScannerDefinitionSpec(
        "price_volume_breakout", "Price + volume breakout", "TECHNICAL",
        "Above the 20-DMA on at least 1.5x average volume with RSI above 55.",
        [Filter("close", ">", 0, compare_to_field="sma_20"),
         Filter("volume_ratio_20d", ">=", 1.5),
         Filter("rsi_14", ">", 55)],
    ),
    ScannerDefinitionSpec(
        "bollinger_breakout", "Bollinger breakout", "TECHNICAL",
        "Close above the upper Bollinger band.",
        [Filter("close", ">", 0, compare_to_field="bb_upper")],
    ),
    ScannerDefinitionSpec(
        "bollinger_squeeze", "Bollinger squeeze", "TECHNICAL",
        "Band width below 8% of the middle band - compressed range.",
        [Filter("bb_width", "<", 8)],
        methodology_note="Compression often precedes expansion, but the "
                         "direction of that expansion is not implied.",
    ),
    ScannerDefinitionSpec(
        "atr_expansion", "ATR expansion", "TECHNICAL",
        "Average true range above 3.5% of price.",
        [Filter("atr_pct", ">", 3.5)],
    ),
    ScannerDefinitionSpec(
        "supertrend_bullish", "Supertrend up-phase", "TECHNICAL",
        "Supertrend direction is positive.",
        [Filter("supertrend_dir", ">", 0)],
    ),
    ScannerDefinitionSpec(
        "strong_trend", "Strong trend (ADX > 25)", "TECHNICAL",
        "ADX(14) above 25 with price above the 50-DMA.",
        [Filter("adx_14", ">", 25),
         Filter("close", ">", 0, compare_to_field="sma_50")],
    ),

    # -- fundamental ------------------------------------------------------
    ScannerDefinitionSpec(
        "high_roe", "High return on equity", "FUNDAMENTAL",
        "ROE above 18%.", [Filter("roe", ">", 18)],
    ),
    ScannerDefinitionSpec(
        "high_roce", "High return on capital", "FUNDAMENTAL",
        "ROCE above 20%.", [Filter("roce", ">", 20)],
    ),
    ScannerDefinitionSpec(
        "low_debt", "Low leverage", "FUNDAMENTAL",
        "Debt to equity below 0.4x.", [Filter("debt_to_equity", "<", 0.4)],
    ),
    ScannerDefinitionSpec(
        "earnings_growth", "Earnings growth", "FUNDAMENTAL",
        "Three-year PAT CAGR above 15%.", [Filter("pat_cagr_3y", ">", 15)],
    ),
    ScannerDefinitionSpec(
        "sales_growth", "Sales growth", "FUNDAMENTAL",
        "Three-year revenue CAGR above 12%.",
        [Filter("revenue_cagr_3y", ">", 12)],
    ),
    ScannerDefinitionSpec(
        "dividend_payers", "Dividend yield above 2%", "FUNDAMENTAL",
        "Trailing dividend yield above 2%.",
        [Filter("dividend_yield", ">", 2)],
    ),
    ScannerDefinitionSpec(
        "high_promoter_holding", "High promoter holding", "FUNDAMENTAL",
        "Promoter holding above 50%.", [Filter("promoter_holding", ">", 50)],
    ),
    ScannerDefinitionSpec(
        "institutional_interest", "Institutional interest", "FUNDAMENTAL",
        "FII holding above 10%.", [Filter("fii_holding", ">", 10)],
    ),
    ScannerDefinitionSpec(
        "quality_compounders", "Quality compounders", "COMBINED",
        "ROE above 18%, debt/equity below 0.5x, three-year PAT CAGR above 12%, "
        "and price above the 200-DMA.",
        [Filter("roe", ">", 18), Filter("debt_to_equity", "<", 0.5),
         Filter("pat_cagr_3y", ">", 12),
         Filter("close", ">", 0, compare_to_field="sma_200")],
        methodology_note="Combines a quality screen with a trend filter so the "
                         "list is not dominated by good businesses in downtrends.",
    ),
    ScannerDefinitionSpec(
        "momentum_with_earnings", "Momentum with earnings support", "COMBINED",
        "Above the 50-EMA, RSI above 55, volume above 1.5x average, and "
        "positive three-year PAT CAGR.",
        [Filter("close", ">", 0, compare_to_field="ema_50"),
         Filter("rsi_14", ">", 55), Filter("volume_ratio_20d", ">", 1.5),
         Filter("pat_cagr_3y", ">", 0)],
    ),
]

BUILTIN_BY_KEY = {s.key: s for s in BUILTIN_SCANNERS}


@dataclass
class ScanResult:
    scanner_key: str
    scanner_name: str
    as_of: Optional[str]
    matches: List[Dict[str, Any]] = field(default_factory=list)
    universe_size: int = 0
    filters_applied: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "match_count": len(self.matches),
            "methodology": METHODOLOGY,
        }


class ScannerService:

    def list_scanners(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in BUILTIN_SCANNERS]

    def run(
        self,
        db: Session,
        scanner_key: Optional[str] = None,
        filters: Optional[List[Filter]] = None,
        logic: str = "AND",
        limit: int = 200,
        segment: str = "EQUITY",
        include_demo: bool = True,
    ) -> ScanResult:
        spec = BUILTIN_BY_KEY.get(scanner_key) if scanner_key else None
        active_filters = list(spec.filters) if spec else list(filters or [])
        active_logic = spec.logic if spec else logic

        if not active_filters:
            return ScanResult(
                scanner_key or "custom", spec.name if spec else "Custom scan",
                None, warnings=["No filters were supplied."],
            )

        warnings: List[str] = []

        # Latest indicator snapshot date - a scan is only as fresh as its inputs.
        latest = db.execute(
            select(TechnicalIndicatorSnapshot.as_of)
            .order_by(TechnicalIndicatorSnapshot.as_of.desc()).limit(1)
        ).scalar_one_or_none()

        if latest is None:
            return ScanResult(
                scanner_key or "custom", spec.name if spec else "Custom scan",
                None,
                warnings=[
                    "No technical indicator snapshots exist yet. Run the "
                    "`indicator_refresh` job before scanning."
                ],
            )

        stmt: Select = (
            select(
                Instrument.symbol, Instrument.name, Instrument.sector,
                Instrument.exchange_code, Instrument.segment,
                TechnicalIndicatorSnapshot, Fundamental, Quote,
            )
            .select_from(Instrument)
            .join(TechnicalIndicatorSnapshot,
                  TechnicalIndicatorSnapshot.instrument_id == Instrument.id)
            .outerjoin(Fundamental, Fundamental.instrument_id == Instrument.id)
            .outerjoin(Quote, Quote.instrument_id == Instrument.id)
            .where(TechnicalIndicatorSnapshot.as_of == latest)
            .where(Instrument.segment == segment)
            .where(Instrument.is_active.is_(True))
        )
        if not include_demo:
            stmt = stmt.where(Instrument.is_demo.is_(False))

        try:
            clauses = [f.build() for f in active_filters]
        except ValueError as exc:
            return ScanResult(
                scanner_key or "custom", spec.name if spec else "Custom scan",
                latest.isoformat(), warnings=[str(exc)],
            )

        stmt = stmt.where(and_(*clauses) if active_logic == "AND"
                          else or_(*clauses))
        stmt = stmt.limit(limit)

        rows = db.execute(stmt).all()

        matches: List[Dict[str, Any]] = []
        for symbol, name, sector, exchange, seg, tech, fund, quote in rows:
            matches.append({
                "symbol": symbol,
                "name": name,
                "sector": sector,
                "exchange": exchange,
                "segment": seg,
                "ltp": quote.ltp if quote else (tech.close if tech else None),
                "change_pct": quote.change_pct if quote else None,
                "data_status": quote.data_status if quote else tech.data_status,
                "is_demo": bool(quote.is_demo if quote else tech.is_demo),
                "matched_values": {
                    f.field: _value_of(f.field, tech, fund, quote)
                    for f in active_filters
                },
                "rsi_14": tech.rsi_14 if tech else None,
                "volume_ratio_20d": tech.volume_ratio_20d if tech else None,
                "adx_14": tech.adx_14 if tech else None,
                "roe": fund.roe if fund else None,
                "pe": fund.pe if fund else None,
            })

        universe = db.execute(
            select(TechnicalIndicatorSnapshot.id)
            .where(TechnicalIndicatorSnapshot.as_of == latest)
        ).all()

        fundamental_fields = [
            f.field for f in active_filters
            if FIELD_MAP.get(f.field) is not None
            and getattr(FIELD_MAP[f.field], "table", None) is not None
            and FIELD_MAP[f.field].table.name == "fundamentals"
        ]
        if fundamental_fields:
            warnings.append(
                "Rows without a fundamentals record are excluded by the "
                f"filters on {', '.join(sorted(set(fundamental_fields)))}. "
                "Missing data reads as 'does not match', not as 'passes'."
            )

        if (date.today() - latest).days > 3:
            warnings.append(
                f"The newest indicator snapshot is from {latest.isoformat()}, "
                f"which is {(date.today() - latest).days} days old."
            )

        return ScanResult(
            scanner_key=scanner_key or "custom",
            scanner_name=spec.name if spec else "Custom scan",
            as_of=latest.isoformat(),
            matches=matches,
            universe_size=len(universe),
            filters_applied=[f.describe() for f in active_filters],
            warnings=warnings,
        )


def _value_of(field_name: str, tech, fund, quote) -> Any:
    for holder in (tech, fund, quote):
        if holder is not None and hasattr(holder, field_name):
            return getattr(holder, field_name)
    return None


scanner_service = ScannerService()
