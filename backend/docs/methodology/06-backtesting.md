# Backtesting

Source: `backend/app/services/backtest.py`.

Rules enforced in code, not promised in a docstring.

## No look-ahead

A signal computed from bar *i* can only be acted on at bar *i+1*'s **open**.
The engine never fills at the price that generated the signal. Rule-based exits
fill the same way. Every indicator used is causal — backward-looking only — so
computing them once over the whole series is safe.

## Intrabar ambiguity resolves pessimistically

If a bar's range touches both the stop and the target, the **stop** is taken
and the exit reason is labelled `STOP_LOSS (ambiguous bar)`. Without tick data
we cannot know which came first, and the optimistic assumption is the one that
flatters a strategy into looking tradeable.

## Costs always apply

Brokerage, STT, exchange charges, SEBI fee, stamp duty, GST and slippage are
deducted on both legs of every trade. A cost-free backtest is a marketing
exercise.

## Nothing is left open

Any position still open at the end of the series is force-closed at the last
close and labelled `END_OF_DATA`, so the metrics are never quietly missing an
open loser.

## Metrics

| Metric | Definition |
| --- | --- |
| Win rate | winning trades / total trades |
| CAGR | `(final/initial)^(1/years) - 1` on the closed-trade equity curve |
| Sharpe | mean/stdev of **per-trade** returns, annualised by trade frequency |
| Sortino | same, using downside deviation only |
| Max drawdown | largest peak-to-trough fall on the closed-trade equity curve |
| Profit factor | gross profit / gross loss |
| Expectancy | `win_rate * avg_win + (1-win_rate) * avg_loss` |
| Expectancy (R) | expectancy / average loss |

Three caveats returned with every result:

- Sharpe and Sortino are computed on per-trade returns, **not** daily returns.
  They are not comparable with a daily-return Sharpe from another tool.
- Profit factor is **null** when there are no losing trades, not infinity.
- Max drawdown is measured on closed trades, so it **understates** intra-trade
  drawdown.

## Sample separation

- **In-sample / out-of-sample** — split at a date you choose. The degradation
  note compares win rate, profit factor and expectancy across the split. A
  large fall is the normal signature of a strategy fitted to its in-sample
  period.
- **Walk-forward** — chronological folds reported individually, with a count of
  how many were profitable. A strategy that works in one fold is describing
  that period, not an edge.

## Assumptions returned with every run

Initial capital, position size, slippage per leg, the charges model, the fill
rule, the intrabar rule, the gap rule, survivorship, corporate actions and the
no-look-ahead guarantee. Change any of them and the results change.

## What a backtest is not

It is a description of how a rule would have behaved on the data supplied,
after modelled costs. It is not evidence that the rule will work, and this
platform attaches no probability to that question.
