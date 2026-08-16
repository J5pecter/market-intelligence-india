# Fundamentals, news and IPO scoring

Sources: `fundamental_analysis.py`, `news_analysis.py`, `ipo_analysis.py`.

## Company quality score

Seven categories with a fixed points budget:

| Category | Budget |
| --- | --- |
| Business quality | 20 |
| Financial quality | 20 |
| Growth | 15 |
| Profitability | 15 |
| Balance sheet | 10 |
| Valuation | 10 |
| Governance / risk | 10 |

Each metric declares its share of the category budget, a band table mapping its
value to a fraction of those points, the calculation, and the source. A metric
that cannot be computed is excluded from **both** the numerator and the
denominator, and the shortfall is reported as **coverage**. The headline score
therefore means "quality of what we could see", and coverage tells you how much
that was.

Two metrics worth calling out because they carry more signal than their weight
suggests:

- **Operating cash flow / PAT** — cash conversion. Persistently below 1 means
  reported profit is not turning into cash. It is the single most useful
  quality check on this list.
- **Promoter pledge** — a well-documented stress channel. A price fall can
  force sales that deepen the fall.

Some bands **taper** rather than rising forever: a current ratio above 3x may
indicate idle working capital rather than strength, and a dividend yield above
6% often reflects a falling price rather than generosity.

## News impact scoring

Generic sentiment models are close to useless on financial headlines — "profit
falls 40% but beats estimates" is positive for the tape and negative to a
general model. The impact score is a **product**, not a sum:

```
impact = 100
       * (0.35*importance + 0.35*|sentiment| + 0.30*historical_reaction)
       * (0.55 + 0.45*company_relevance)
       * (0.80 + 0.20*sector_relevance)
       * (0.65 + 0.35*source_credibility)
       * recency_multiplier
```

A product, because a huge event about a company you do not hold is not a big
signal for you.

- **Event taxonomy** — regex patterns map a headline to a category with a base
  importance and a directional prior.
- **Lexicon** — a fixed, inspectable word list. Negation flips a term when a
  negator appears within three words before it, which catches "does not beat
  estimates".
- **Source credibility** — exchange and regulator filings score 1.0; anything
  not in the table scores 0.5 and the payload names the source so a reader can
  judge for themselves.
- **Recency** — decays from 1.0 to 0.3 over a week.

No external model, no API key, no black box. The limitations are returned with
every assessment: it reads the headline only, and it will misread sarcasm,
unusual phrasing and companies with similar names.

## IPO research score

Deliberately **not** a SUBSCRIBE/AVOID button.

| Component | Weight |
| --- | --- |
| Financial quality | 0.24 |
| Business quality | 0.22 |
| Valuation attractiveness | 0.22 |
| Risk (inverted before blending) | 0.12 |
| Subscription strength | 0.12 |
| Grey market signal | **0.08** |

The grey-market component is capped at 8% because the grey market is
unofficial and unregulated. A wild premium cannot carry a weak issue to a
strong label.

The **risk** component is blended `0.6 * mean + 0.4 * worst`, the same
worst-case rule the risk engine uses, so one severe disclosure is not averaged
away by three benign ones.

### The label

| Label | Condition |
| --- | --- |
| Insufficient data | coverage below 25% |
| High risk | two or more high-severity risk factors, **or** risk >= 75 |
| Speculative | financial quality below 30 |
| Positive but valuation sensitive | overall >= 68 with valuation below 40 |
| Strong research profile | overall >= 68 |
| Neutral | overall >= 50 |
| Weak research profile | otherwise |

The high-severity count decides the label **directly**, because that is a fact
about the offer document rather than an arithmetic artefact.

### Grey Market Premium

An unofficial quote from private dealers. It is not a price discovered on any
exchange, it is not a listing price, and it can change or disappear without
notice. The platform stores every observation with its own source and
timestamp, charts the **trend** (which carries more information than the
level), and repeats the disclaimer wherever the number appears.

The application simulator always includes a **"lists below issue price"**
scenario, and always states that every scenario assumes full allotment — which,
in an oversubscribed retail book, is not the likely outcome for a single-lot
application.

## Historical analogues

For a current configuration, the engine searches this instrument's own history
for bars with similar RSI, distance from the 52-week high, ATR%, relative
volume, ADX and position against the 50-DMA. Similarity uses a triangular
kernel: 1.0 at an exact match, 0.0 at twice the tolerance.

Guards against look-ahead and against false precision:

- A candidate's feature vector uses only data available up to that bar.
- Candidates within the forward horizon of the series end are excluded — their
  outcome has not happened yet.
- The current bar and its immediate neighbours are excluded, so the sample is
  not dominated by autocorrelated copies of today.
- Fewer than **8 matches**, or a candidate pool below **150 bars**, produces no
  statistics at all. A labelled bad number still gets read as a number.

Every result states that forward windows overlap, so the cases are correlated
and the effective sample is smaller than the count suggests — and that this
describes a past sample, never a probability for the current setup.
