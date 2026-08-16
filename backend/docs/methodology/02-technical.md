# Technical analysis

Source: `backend/app/services/indicators.py` and
`backend/app/services/technical_analysis.py`.

## Indicator conventions

All indicators are implemented in pandas/numpy rather than a compiled library,
so the arithmetic is readable. Two conventions matter:

- **Warm-up produces `NaN`, never a value.** A 200-day average does not exist
  on day 40, and the platform will not invent one.
- **Wilder's smoothing** (`alpha = 1/period`) is used for RSI, ATR and ADX,
  which is what Wilder defined and what charting platforms display. It is *not*
  a simple moving average of the same length.

### Formulas

| Indicator | Formula |
| --- | --- |
| SMA(n) | mean of the last `n` closes |
| EMA(n) | `alpha*price + (1-alpha)*prev`, `alpha = 2/(n+1)` |
| RSI(14) | `100 - 100/(1+RS)`, `RS = avg_gain/avg_loss`, both Wilder-smoothed |
| MACD | `EMA(12) - EMA(26)`, signal `EMA(9)` of that line |
| True range | `max(H-L, abs(H-Cprev), abs(L-Cprev))` |
| ATR(14) | Wilder smoothing of true range |
| ADX(14) | Wilder smoothing of `100 * abs(+DI - -DI) / (+DI + -DI)` |
| Bollinger(20, 2) | `SMA(20) +/- 2 * population stdev` |
| Supertrend(10, 3) | ratcheting ATR bands, direction flips on a close through |
| VWAP | cumulative `(H+L+C)/3 * volume / volume`, reset each session |

### Edge cases that are handled explicitly

- RSI is **100** on an unbroken rally, **0** on an unbroken decline and **50**
  on a flat line, rather than dividing by zero.
- Relative volume compares today against the **previous** 20 bars, so today's
  print cannot inflate its own baseline.
- Volume profile buckets each bar's volume at its **close**, because without
  tick data the distribution inside the bar is unknown. The payload says so.

## Support and resistance

Confirmed fractal swing points (a bar higher or lower than `lookback` bars on
both sides) are clustered within a 1.2% tolerance. Each level is scored from:

- **touches** — how many swings clustered into it (capped at 4)
- **recency** — bars since the most recent touch
- **volume weight** — mean relative volume on the touching bars

`strength = min(100, 25*min(touches,4) + 30*recency + 15*min(vol_weight,2))`

Confirmation lags by `lookback` bars. That lag is real and the signal engine
does not pretend otherwise.

## The technical score

Each observation becomes an `EvidenceItem` with a stance (positive, negative,
neutral) and a weight. The score maps the weighted pull onto 0-100 around a
neutral baseline of 50:

```
normalised = sum(direction * |weight|) / sum(|weight|)      # in [-1, 1]
score      = 50 + normalised * (50 if normalised < 0 else 50)
```

Weights, as configured today:

| Evidence | Weight |
| --- | --- |
| Price vs 200-DMA | 2.0 |
| Price vs 20/50-DMA | 1.5 |
| Moving-average alignment | 1.5 |
| RSI(14) | 1.5 |
| ADX(14) | 1.5 |
| Nearest resistance (within 2%) | 1.4 |
| Volume vs 20-day average | 1.3 |
| MACD (1.8 on a fresh crossover) | 1.2 |
| Supertrend | 1.2 |
| RSI divergence | 1.2 |
| ATR regime | 1.0 |
| Distance from the 52-week high | 1.0 |

## Market regime

| Regime | Condition |
| --- | --- |
| `HIGH_VOLATILITY` | ATR >= 4.5% of price (checked first) |
| `STRONG_BULLISH` | ADX >= 25 and technical score >= 65 |
| `STRONG_BEARISH` | ADX >= 25 and technical score <= 35 |
| `BULLISH` | score >= 60 |
| `BEARISH` | score <= 40 |
| `NEUTRAL` | otherwise |

The reasons that produced the label are always returned with it.

## Limitations

Indicators describe what price has already done. They are not a forecast and
carry no probability of any future outcome. With fewer than 30 bars the service
returns no conclusion at all, and says that this is a data limitation rather
than a neutral reading.
