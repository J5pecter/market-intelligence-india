"""Technical indicators.

These check the arithmetic against values that can be derived by hand, and
check the warm-up behaviour: an indicator with insufficient history must be
NaN, never a fabricated number.
"""

import numpy as np
import pandas as pd
import pytest

from app.services import indicators as ind


@pytest.fixture
def frame() -> pd.DataFrame:
    """A deterministic 260-bar series with a mild uptrend and real ranges."""
    rng = np.random.default_rng(42)
    index = pd.date_range("2024-01-01", periods=260, freq="B", tz="UTC")
    close = pd.Series(
        100 * np.cumprod(1 + rng.normal(0.0006, 0.012, 260)), index=index
    )
    spread = close * 0.01
    return pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close + spread,
        "low": close - spread,
        "close": close,
        "volume": pd.Series(rng.integers(1e5, 5e6, 260), index=index),
    })


# --------------------------------------------------------------------------
# Moving averages
# --------------------------------------------------------------------------


def test_sma_matches_a_hand_computed_mean():
    series = pd.Series([1, 2, 3, 4, 5], dtype="float64")
    result = ind.sma(series, 3)
    assert pd.isna(result.iloc[0]) and pd.isna(result.iloc[1])
    assert result.iloc[2] == pytest.approx(2.0)
    assert result.iloc[4] == pytest.approx(4.0)


def test_ema_uses_the_recursive_form():
    series = pd.Series([10.0] * 5 + [20.0] * 5)
    result = ind.ema(series, 3)
    alpha = 2 / (3 + 1)
    expected = 10.0
    for value in series.iloc[3:]:
        expected = alpha * value + (1 - alpha) * expected
    assert result.iloc[-1] == pytest.approx(expected, rel=1e-6)


def test_warm_up_periods_are_nan_not_zero(frame):
    enriched = ind.compute_all(frame)
    assert enriched["sma_200"].iloc[:199].isna().all()
    assert not pd.isna(enriched["sma_200"].iloc[-1])


# --------------------------------------------------------------------------
# RSI
# --------------------------------------------------------------------------


def test_rsi_is_100_on_an_unbroken_rally():
    series = pd.Series(np.arange(1, 40, dtype="float64"))
    assert ind.rsi(series, 14).iloc[-1] == pytest.approx(100.0)


def test_rsi_is_0_on_an_unbroken_decline():
    series = pd.Series(np.arange(40, 1, -1, dtype="float64"))
    assert ind.rsi(series, 14).iloc[-1] == pytest.approx(0.0)


def test_rsi_of_a_flat_line_is_50_not_undefined():
    series = pd.Series([100.0] * 40)
    assert ind.rsi(series, 14).iloc[-1] == pytest.approx(50.0)


def test_rsi_stays_inside_its_bounds(frame):
    values = ind.rsi(frame["close"]).dropna()
    assert values.between(0, 100).all()


# --------------------------------------------------------------------------
# MACD, ATR, ADX, Bollinger
# --------------------------------------------------------------------------


def test_macd_histogram_is_the_difference_of_the_two_lines(frame):
    macd_line, signal, histogram = ind.macd(frame["close"])
    diff = (macd_line - signal).dropna()
    assert np.allclose(diff.values, histogram.dropna().values)


def test_true_range_captures_gaps():
    high = pd.Series([10.0, 20.0])
    low = pd.Series([9.0, 19.0])
    close = pd.Series([9.5, 19.5])
    # Second bar gapped up from 9.5 to a 19-20 range: TR is 20 - 9.5 = 10.5
    assert ind.true_range(high, low, close).iloc[1] == pytest.approx(10.5)


def test_atr_is_positive_and_finite(frame):
    values = ind.atr(frame["high"], frame["low"], frame["close"]).dropna()
    assert (values > 0).all()
    assert np.isfinite(values).all()


def test_adx_and_di_stay_in_range(frame):
    adx, plus_di, minus_di = ind.adx(frame["high"], frame["low"], frame["close"])
    for series in (adx, plus_di, minus_di):
        clean = series.dropna()
        assert clean.between(0, 100).all()


def test_bollinger_bands_bracket_the_middle_band(frame):
    middle, upper, lower, width = ind.bollinger(frame["close"])
    mask = middle.notna()
    assert (upper[mask] >= middle[mask]).all()
    assert (lower[mask] <= middle[mask]).all()
    assert (width.dropna() >= 0).all()


def test_supertrend_direction_is_only_plus_or_minus_one(frame):
    _, direction = ind.supertrend(frame["high"], frame["low"], frame["close"])
    assert set(direction.dropna().unique()) <= {1.0, -1.0}


# --------------------------------------------------------------------------
# Volume
# --------------------------------------------------------------------------


def test_volume_ratio_excludes_the_current_bar_from_its_own_baseline():
    volume = pd.Series([100.0] * 20 + [500.0])
    ratio = ind.volume_ratio(volume, 20)
    # Baseline is the 20 previous bars (all 100), so the ratio is exactly 5.
    assert ratio.iloc[-1] == pytest.approx(5.0)


def test_volume_ratio_is_nan_before_the_baseline_exists():
    volume = pd.Series([100.0] * 5)
    assert ind.volume_ratio(volume, 20).isna().all()


def test_vwap_resets_each_session_on_intraday_data():
    index = pd.DatetimeIndex([
        "2026-01-01 09:15", "2026-01-01 09:16",
        "2026-01-02 09:15", "2026-01-02 09:16",
    ])
    high = pd.Series([10, 10, 20, 20], index=index, dtype="float64")
    low = high.copy()
    close = high.copy()
    volume = pd.Series([100, 100, 100, 100], index=index, dtype="float64")
    result = ind.vwap(high, low, close, volume, session_reset=True)
    # Day two must not be dragged toward day one's level.
    assert result.iloc[2] == pytest.approx(20.0)


def test_volume_profile_labels_its_approximation(frame):
    profile = ind.volume_profile(frame["close"], frame["volume"], bins=10)
    assert not profile.empty
    assert profile["is_poc"].sum() == 1
    assert "approximation" in profile["method"].iloc[0]


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------


def test_support_resistance_levels_are_scored_and_sorted(frame):
    levels = ind.support_resistance_levels(
        frame["high"], frame["low"], frame["close"], frame["volume"]
    )
    assert levels
    strengths = [level["strength"] for level in levels]
    assert strengths == sorted(strengths, reverse=True)
    for level in levels:
        assert level["touches"] >= 1
        assert 0 <= level["strength"] <= 100
        assert level["kind"] in ("SUPPORT", "RESISTANCE")


def test_gap_analysis_detects_a_gap_up():
    open_ = pd.Series([100.0, 106.0])
    close = pd.Series([100.0, 107.0])
    gap = ind.gap_analysis(open_, close)
    assert gap["type"] == "GAP_UP"
    assert gap["gap_pct"] == pytest.approx(6.0)
    assert gap["filled"] is False


def test_gap_analysis_returns_none_without_two_bars():
    assert ind.gap_analysis(pd.Series([100.0]), pd.Series([100.0])) is None


# --------------------------------------------------------------------------
# compute_all
# --------------------------------------------------------------------------


def test_compute_all_does_not_mutate_its_input(frame):
    before = frame.copy(deep=True)
    ind.compute_all(frame)
    pd.testing.assert_frame_equal(frame, before)


def test_compute_all_handles_a_frame_without_volume(frame):
    enriched = ind.compute_all(frame.drop(columns=["volume"]))
    assert enriched["volume_ratio_20"].isna().all()
    assert not pd.isna(enriched["rsi_14"].iloc[-1])
