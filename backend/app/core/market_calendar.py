"""Indian market calendar.

Holidays are *data*, not code: they live in the `market_holidays` table and are
loaded/refreshed by the `market_calendar_refresh` job. The hard-coded list here
is only a bootstrap seed used when the table is empty, and every response that
depends on it carries `source: "SEED"` so the UI can say the calendar has not
been verified against the exchange yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Iterable, Optional, Set
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Configurable session times (NSE/BSE equity segment).
PRE_OPEN_START = time(9, 0)
PRE_OPEN_END = time(9, 8)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
POST_CLOSE_END = time(16, 0)


class MarketStatus(str, Enum):
    PRE_OPEN = "PRE_OPEN"
    OPEN = "OPEN"
    POST_CLOSE = "POST_CLOSE"
    CLOSED = "CLOSED"
    HOLIDAY = "HOLIDAY"
    WEEKEND = "WEEKEND"


# Bootstrap seed only - replace by loading the official exchange holiday list.
_SEED_HOLIDAYS_2026: Set[date] = {
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 4),    # Holi
    date(2026, 3, 21),   # Id-ul-Fitr
    date(2026, 4, 1),    # Annual bank closing
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 11, 11),  # Diwali Laxmi Pujan (muhurat session separate)
    date(2026, 12, 25),  # Christmas
}


@dataclass(frozen=True)
class MarketState:
    status: MarketStatus
    as_of: datetime
    timezone: str
    next_open: Optional[datetime]
    next_close: Optional[datetime]
    holiday_source: str
    note: str


def now_ist() -> datetime:
    return datetime.now(tz=IST)


def _holiday_set(holidays: Optional[Iterable[date]]) -> tuple[Set[date], str]:
    if holidays is None:
        return _SEED_HOLIDAYS_2026, "SEED"
    materialised = set(holidays)
    if not materialised:
        return _SEED_HOLIDAYS_2026, "SEED"
    return materialised, "DATABASE"


def is_trading_day(day: date, holidays: Optional[Iterable[date]] = None) -> bool:
    hol, _ = _holiday_set(holidays)
    return day.weekday() < 5 and day not in hol


def next_trading_day(from_day: date, holidays: Optional[Iterable[date]] = None) -> date:
    day = from_day + timedelta(days=1)
    for _ in range(30):
        if is_trading_day(day, holidays):
            return day
        day += timedelta(days=1)
    return day


def market_state(
    at: Optional[datetime] = None, holidays: Optional[Iterable[date]] = None
) -> MarketState:
    at = (at or now_ist()).astimezone(IST)
    today = at.date()
    hol, source = _holiday_set(holidays)

    note = (
        "Holiday list loaded from the market_holidays table."
        if source == "DATABASE"
        else "Holiday list is an unverified bootstrap seed - load the official "
        "exchange calendar before relying on it."
    )

    def _dt(d: date, t: time) -> datetime:
        return datetime.combine(d, t, tzinfo=IST)

    if today.weekday() >= 5:
        nxt = next_trading_day(today, holidays)
        return MarketState(
            MarketStatus.WEEKEND, at, "Asia/Kolkata",
            _dt(nxt, MARKET_OPEN), _dt(nxt, MARKET_CLOSE), source, note,
        )
    if today in hol:
        nxt = next_trading_day(today, holidays)
        return MarketState(
            MarketStatus.HOLIDAY, at, "Asia/Kolkata",
            _dt(nxt, MARKET_OPEN), _dt(nxt, MARKET_CLOSE), source, note,
        )

    clock = at.time()
    if PRE_OPEN_START <= clock < PRE_OPEN_END:
        status = MarketStatus.PRE_OPEN
    elif MARKET_OPEN <= clock < MARKET_CLOSE:
        status = MarketStatus.OPEN
    elif MARKET_CLOSE <= clock < POST_CLOSE_END:
        status = MarketStatus.POST_CLOSE
    else:
        status = MarketStatus.CLOSED

    if status in (MarketStatus.PRE_OPEN, MarketStatus.OPEN):
        nxt_open = _dt(today, MARKET_OPEN)
        nxt_close = _dt(today, MARKET_CLOSE)
    else:
        nxt = today if clock < MARKET_OPEN else next_trading_day(today, holidays)
        nxt_open = _dt(nxt, MARKET_OPEN)
        nxt_close = _dt(nxt, MARKET_CLOSE)

    return MarketState(status, at, "Asia/Kolkata", nxt_open, nxt_close, source, note)


def is_market_open(at: Optional[datetime] = None,
                   holidays: Optional[Iterable[date]] = None) -> bool:
    return market_state(at, holidays).status is MarketStatus.OPEN


def trading_days_between(
    start: date, end: date, holidays: Optional[Iterable[date]] = None
) -> int:
    """Inclusive of `start`, exclusive of `end` - used for time-to-expiry."""
    if end <= start:
        return 0
    count = 0
    day = start
    while day < end:
        if is_trading_day(day, holidays):
            count += 1
        day += timedelta(days=1)
    return count
