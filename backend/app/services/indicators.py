"""Technical indicators.

Deliberately implemented in pandas/numpy rather than TA-Lib: no C toolchain to
install, and - more importantly - the arithmetic is inspectable. /methodology
links straight to these functions.

Conventions used throughout:
* Every function takes and returns a pandas Series/DataFrame indexed by time.
* Warm-up periods produce NaN rather than a fabricated value. Callers must
  treat NaN as "not enough history", never as zero.
* Wilder's smoothing (RSI, ATR, ADX) uses alpha = 1/period, which is what
  Wilder defined and what charting platforms display. This is *not* the same
  as a simple moving average of the same length.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------
# Moving averages
# --------------------------------------------------------------------------


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average, alpha = 2/(period+1).

    `adjust=False` gives the recursive form charting platforms use:
        EMA_t = alpha * price_t + (1 - alpha) * EMA_{t-1}
    """
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing: alpha = 1/period."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


# --------------------------------------------------------------------------
# Momentum
# --------------------------------------------------------------------------


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index.

        RS  = avg_gain / avg_loss   (both Wilder-smoothed)
        RSI = 100 - 100 / (1 + RS)

    When avg_loss is zero the ratio is undefined; RSI is defined as 100 there
    (an unbroken run of up-closes), and 0 when avg_gain is zero.
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = wilder_smooth(gain, period)
    avg_loss = wilder_smooth(loss, period)

    out = pd.Series(np.nan, index=close.index, dtype="float64")
    both_zero = (avg_gain == 0) & (avg_loss == 0)
    only_gain = (avg_loss == 0) & (avg_gain > 0)
    normal = avg_loss > 0

    rs = avg_gain.where(normal) / avg_loss.where(normal)
    out.loc[normal] = 100.0 - (100.0 / (1.0 + rs.loc[normal]))
    out.loc[only_gain] = 100.0
    out.loc[both_zero] = 50.0  # flat line: neither side has momentum
    out.loc[avg_gain.isna() | avg_loss.isna()] = np.nan
    return out


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, histogram."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False,
                                min_periods=signal).mean()
    return macd_line, signal_line, macd_line - signal_line


def stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series,
    k_period: int = 14, d_period: int = 3, smooth_k: int = 3,
) -> Tuple[pd.Series, pd.Series]:
    """Slow stochastic %K and %D."""
    lowest = low.rolling(k_period, min_periods=k_period).min()
    highest = high.rolling(k_period, min_periods=k_period).max()
    span = (highest - lowest).replace(0.0, np.nan)
    raw_k = 100.0 * (close - lowest) / span
    k = raw_k.rolling(smooth_k, min_periods=smooth_k).mean()
    d = k.rolling(d_period, min_periods=d_period).mean()
    return k, d


def rate_of_change(close: pd.Series, period: int = 20) -> pd.Series:
    return 100.0 * (close / close.shift(period) - 1.0)


# --------------------------------------------------------------------------
# Volatility
# --------------------------------------------------------------------------


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """max(H-L, |H-Cprev|, |L-Cprev|) - captures gaps, unlike H-L alone."""
    prev_close = close.shift(1)
    return pd.concat(
        [(high - low).abs(),
         (high - prev_close).abs(),
         (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> pd.Series:
    return wilder_smooth(true_range(high, low, close), period)


def bollinger(
    close: pd.Series, period: int = 20, std_multiplier: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Middle band, upper, lower, and %-width relative to the middle band.

    Uses the population standard deviation (ddof=0), which is what Bollinger
    specified and what charting platforms plot.
    """
    middle = sma(close, period)
    deviation = close.rolling(period, min_periods=period).std(ddof=0)
    upper = middle + std_multiplier * deviation
    lower = middle - std_multiplier * deviation
    width = 100.0 * (upper - lower) / middle.replace(0.0, np.nan)
    return middle, upper, lower, width


def historical_volatility(close: pd.Series, period: int = 20,
                          trading_days: int = 252) -> pd.Series:
    """Annualised close-to-close volatility of log returns, in percent."""
    log_returns = np.log(close / close.shift(1))
    return (
        log_returns.rolling(period, min_periods=period).std(ddof=1)
        * np.sqrt(trading_days) * 100.0
    )


# --------------------------------------------------------------------------
# Trend strength
# --------------------------------------------------------------------------


def adx(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Wilder's ADX with +DI and -DI.

    Directional movement is only counted when one side's move strictly exceeds
    the other's - simultaneous outside days contribute nothing, which is what
    stops ADX double-counting volatility as trend.
    """
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )

    atr_series = wilder_smooth(true_range(high, low, close), period)
    safe_atr = atr_series.replace(0.0, np.nan)

    plus_di = 100.0 * wilder_smooth(plus_dm, period) / safe_atr
    minus_di = 100.0 * wilder_smooth(minus_dm, period) / safe_atr

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return wilder_smooth(dx, period), plus_di, minus_di


def supertrend(
    high: pd.Series, low: pd.Series, close: pd.Series,
    period: int = 10, multiplier: float = 3.0,
) -> Tuple[pd.Series, pd.Series]:
    """Supertrend line and direction (+1 up-trend, -1 down-trend).

    The band-ratchet is the whole point: an upper band may only move down while
    price stays below it, and resets the moment price closes through. Written
    as an explicit loop because each bar depends on the previous decision.
    """
    atr_series = atr(high, low, close, period)
    hl2 = (high + low) / 2.0
    upper_basic = hl2 + multiplier * atr_series
    lower_basic = hl2 - multiplier * atr_series

    n = len(close)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    direction = np.full(n, np.nan)
    trend = np.full(n, np.nan)

    closes = close.to_numpy(dtype="float64")
    ub = upper_basic.to_numpy(dtype="float64")
    lb = lower_basic.to_numpy(dtype="float64")

    started = False
    for i in range(n):
        if np.isnan(ub[i]) or np.isnan(lb[i]):
            continue
        if not started:
            final_upper[i], final_lower[i] = ub[i], lb[i]
            direction[i] = 1.0
            trend[i] = final_lower[i]
            started = True
            continue

        prev_upper = final_upper[i - 1]
        prev_lower = final_lower[i - 1]
        prev_close = closes[i - 1]

        final_upper[i] = (
            ub[i] if (ub[i] < prev_upper or prev_close > prev_upper) else prev_upper
        )
        final_lower[i] = (
            lb[i] if (lb[i] > prev_lower or prev_close < prev_lower) else prev_lower
        )

        prev_dir = direction[i - 1]
        if prev_dir == 1.0:
            direction[i] = -1.0 if closes[i] < final_lower[i] else 1.0
        else:
            direction[i] = 1.0 if closes[i] > final_upper[i] else -1.0
        trend[i] = final_lower[i] if direction[i] == 1.0 else final_upper[i]

    return (
        pd.Series(trend, index=close.index),
        pd.Series(direction, index=close.index),
    )


# --------------------------------------------------------------------------
# Volume
# --------------------------------------------------------------------------


def vwap(high: pd.Series, low: pd.Series, close: pd.Series,
         volume: pd.Series, session_reset: bool = True) -> pd.Series:
    """Volume-weighted average price using the typical price (H+L+C)/3.

    On intraday data VWAP is meaningless across sessions, so the cumulative
    sums reset each calendar day when `session_reset` is on.
    """
    typical = (high + low + close) / 3.0
    pv = typical * volume
    if session_reset and isinstance(close.index, pd.DatetimeIndex):
        grouper = close.index.date
        return (
            pv.groupby(grouper).cumsum()
            / volume.groupby(grouper).cumsum().replace(0, np.nan)
        )
    return pv.cumsum() / volume.cumsum().replace(0, np.nan)


def volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """Current volume as a multiple of its own trailing average.

    The average is shifted by one bar so today's volume is not compared against
    an average that already contains it.
    """
    baseline = volume.rolling(period, min_periods=period).mean().shift(1)
    return volume / baseline.replace(0, np.nan)


def on_balance_volume(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum()


def volume_profile(
    close: pd.Series, volume: pd.Series, bins: int = 24
) -> pd.DataFrame:
    """Traded volume bucketed by price - the point of control and value area.

    A close-price approximation: without tick data we cannot distribute a bar's
    volume across its range, so each bar's volume is assigned to its close.
    The returned frame says so in `method` and the UI repeats it.
    """
    frame = pd.DataFrame({"close": close, "volume": volume}).dropna()
    if frame.empty or frame["close"].nunique() < 2:
        return pd.DataFrame(columns=["price_low", "price_high", "volume",
                                     "is_poc", "method"])
    cuts = pd.cut(frame["close"], bins=bins)
    grouped = frame.groupby(cuts, observed=True)["volume"].sum().reset_index()
    grouped["price_low"] = grouped["close"].apply(lambda i: float(i.left))
    grouped["price_high"] = grouped["close"].apply(lambda i: float(i.right))
    grouped = grouped.drop(columns=["close"])
    grouped["is_poc"] = grouped["volume"] == grouped["volume"].max()
    grouped["method"] = "close-price approximation (no tick data)"
    return grouped.sort_values("price_low").reset_index(drop=True)


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------


def swing_points(high: pd.Series, low: pd.Series,
                 lookback: int = 5) -> Tuple[pd.Series, pd.Series]:
    """Fractal swing highs/lows: a bar higher (lower) than `lookback` bars on
    both sides. Confirmation therefore lags by `lookback` bars - that lag is
    real and the signal engine must not pretend otherwise."""
    swing_high = (
        (high == high.rolling(2 * lookback + 1, center=True).max())
        & high.notna()
    )
    swing_low = (
        (low == low.rolling(2 * lookback + 1, center=True).min())
        & low.notna()
    )
    return swing_high, swing_low


def support_resistance_levels(
    high: pd.Series, low: pd.Series, close: pd.Series,
    volume: Optional[pd.Series] = None, lookback: int = 5,
    max_levels: int = 6, tolerance_pct: float = 1.2,
) -> list[dict]:
    """Cluster confirmed swing points into levels and score them.

    Score components, all stated in the returned dict so the UI can show the
    working:
      * touches       - how many swings clustered into the level
      * recency       - bars since the most recent touch (fresher is stronger)
      * volume weight - mean relative volume on the touching bars, when volume
                        is available
    """
    sh, sl = swing_points(high, low, lookback)
    raw: list[dict] = []
    total_bars = len(close)
    avg_volume = float(volume.mean()) if volume is not None and len(volume) else 0.0

    for idx in high.index[sh.fillna(False)]:
        raw.append({"price": float(high.loc[idx]), "kind": "RESISTANCE",
                    "position": high.index.get_loc(idx)})
    for idx in low.index[sl.fillna(False)]:
        raw.append({"price": float(low.loc[idx]), "kind": "SUPPORT",
                    "position": low.index.get_loc(idx)})

    if not raw:
        return []

    raw.sort(key=lambda item: item["price"])
    clusters: list[list[dict]] = [[raw[0]]]
    for point in raw[1:]:
        anchor = clusters[-1][0]["price"]
        if anchor and abs(point["price"] - anchor) / anchor * 100.0 <= tolerance_pct:
            clusters[-1].append(point)
        else:
            clusters.append([point])

    levels: list[dict] = []
    last_close = float(close.dropna().iloc[-1]) if close.notna().any() else None
    for cluster in clusters:
        prices = [c["price"] for c in cluster]
        level_price = float(np.mean(prices))
        touches = len(cluster)
        newest = max(c["position"] for c in cluster)
        bars_since = total_bars - 1 - newest
        recency = max(0.0, 1.0 - bars_since / max(total_bars, 1))

        vol_weight = 1.0
        if volume is not None and avg_volume > 0:
            vols = [float(volume.iloc[c["position"]]) for c in cluster
                    if c["position"] < len(volume)]
            if vols:
                vol_weight = float(np.mean(vols)) / avg_volume

        strength = round(
            min(100.0, 25.0 * min(touches, 4) + 30.0 * recency
                + 15.0 * min(vol_weight, 2.0)),
            1,
        )
        kind = "SUPPORT" if (last_close is not None
                             and level_price < last_close) else "RESISTANCE"
        levels.append({
            "price": round(level_price, 2),
            "kind": kind,
            "touches": touches,
            "bars_since_last_touch": int(bars_since),
            "relative_volume_at_touches": round(vol_weight, 2),
            "strength": strength,
            "distance_pct": round((level_price / last_close - 1.0) * 100.0, 2)
            if last_close else None,
        })

    levels.sort(key=lambda level: level["strength"], reverse=True)
    return levels[:max_levels]


def gap_analysis(open_: pd.Series, close: pd.Series,
                 threshold_pct: float = 1.0) -> Optional[dict]:
    """Classify the most recent open versus the previous close."""
    if len(close) < 2 or pd.isna(open_.iloc[-1]) or pd.isna(close.iloc[-2]):
        return None
    prev_close = float(close.iloc[-2])
    today_open = float(open_.iloc[-1])
    if prev_close == 0:
        return None
    gap_pct = (today_open / prev_close - 1.0) * 100.0
    if abs(gap_pct) < threshold_pct:
        kind = "NONE"
    else:
        kind = "GAP_UP" if gap_pct > 0 else "GAP_DOWN"
    filled = (
        kind == "GAP_UP" and float(close.iloc[-1]) <= prev_close
    ) or (
        kind == "GAP_DOWN" and float(close.iloc[-1]) >= prev_close
    )
    return {
        "type": kind,
        "gap_pct": round(gap_pct, 2),
        "previous_close": round(prev_close, 2),
        "open": round(today_open, 2),
        "filled": bool(filled) if kind != "NONE" else None,
    }


def rsi_divergence(close: pd.Series, rsi_series: pd.Series,
                   lookback: int = 40, swing: int = 5) -> Optional[dict]:
    """Detect a regular divergence between price and RSI over the window.

    Bearish: price makes a higher high while RSI makes a lower high.
    Bullish:  price makes a lower low while RSI makes a higher high... low.
    Returns None when there are not two confirmed swings of the same kind.
    """
    if len(close) < lookback + swing:
        return None
    window_close = close.iloc[-lookback:]
    window_rsi = rsi_series.iloc[-lookback:]
    highs, lows = swing_points(window_close, window_close, swing)

    high_idx = list(window_close.index[highs.fillna(False)])
    low_idx = list(window_close.index[lows.fillna(False)])

    if len(high_idx) >= 2:
        a, b = high_idx[-2], high_idx[-1]
        if (window_close[b] > window_close[a]
                and pd.notna(window_rsi.get(a)) and pd.notna(window_rsi.get(b))
                and window_rsi[b] < window_rsi[a]):
            return {
                "type": "BEARISH_DIVERGENCE",
                "price_from": round(float(window_close[a]), 2),
                "price_to": round(float(window_close[b]), 2),
                "rsi_from": round(float(window_rsi[a]), 1),
                "rsi_to": round(float(window_rsi[b]), 1),
                "note": "Price made a higher high while RSI made a lower high.",
            }
    if len(low_idx) >= 2:
        a, b = low_idx[-2], low_idx[-1]
        if (window_close[b] < window_close[a]
                and pd.notna(window_rsi.get(a)) and pd.notna(window_rsi.get(b))
                and window_rsi[b] > window_rsi[a]):
            return {
                "type": "BULLISH_DIVERGENCE",
                "price_from": round(float(window_close[a]), 2),
                "price_to": round(float(window_close[b]), 2),
                "rsi_from": round(float(window_rsi[a]), 1),
                "rsi_to": round(float(window_rsi[b]), 1),
                "note": "Price made a lower low while RSI made a higher low.",
            }
    return None


# --------------------------------------------------------------------------
# Convenience: compute everything once
# --------------------------------------------------------------------------


def compute_all(frame: pd.DataFrame) -> pd.DataFrame:
    """Add every indicator column to an OHLCV frame.

    `frame` must have columns open/high/low/close and optionally volume, with a
    DatetimeIndex. Returns a copy; the input is not mutated.
    """
    df = frame.copy()
    close, high, low = df["close"], df["high"], df["low"]
    volume = df["volume"] if "volume" in df else pd.Series(
        np.nan, index=df.index
    )

    for period in (20, 50, 100, 200):
        df[f"sma_{period}"] = sma(close, period)
    for period in (9, 20, 50):
        df[f"ema_{period}"] = ema(close, period)

    df["rsi_14"] = rsi(close)
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(close)
    df["atr_14"] = atr(high, low, close)
    df["atr_pct"] = 100.0 * df["atr_14"] / close.replace(0, np.nan)
    df["adx_14"], df["plus_di"], df["minus_di"] = adx(high, low, close)
    df["bb_mid"], df["bb_upper"], df["bb_lower"], df["bb_width"] = bollinger(close)
    df["stoch_k"], df["stoch_d"] = stochastic(high, low, close)
    df["supertrend"], df["supertrend_dir"] = supertrend(high, low, close)
    df["hist_vol_20"] = historical_volatility(close)

    if volume.notna().any():
        df["vwap"] = vwap(high, low, close, volume)
        df["volume_ratio_20"] = volume_ratio(volume)
        df["obv"] = on_balance_volume(close, volume)
    else:
        df["vwap"] = np.nan
        df["volume_ratio_20"] = np.nan
        df["obv"] = np.nan

    rolling_high = close.rolling(252, min_periods=20).max()
    rolling_low = close.rolling(252, min_periods=20).min()
    df["pct_from_52w_high"] = 100.0 * (close / rolling_high - 1.0)
    df["pct_from_52w_low"] = 100.0 * (close / rolling_low - 1.0)

    return df
