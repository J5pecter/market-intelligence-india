# Document extraction

Source: `backend/app/services/documents/`.

Reads annual reports, quarterly results, investor presentations, transcripts,
exchange filings and offer documents (DRHP/RHP), and turns them into **cited
claims**. It does not turn them into facts — that step needs a human.

## The gate

```
document → text → sections → figures/quotes → CITATIONS (review_status=PENDING)
                                                     │
                                          human reviews the quote
                                                     │
                                                 approve
                                                     │
                             financial_statements / ipo_financials / ipo_risk_factors
```

An extractor that wrote straight into the fundamentals tables would let a
misparsed unit become a P/E ratio on a research page, with nothing on screen to
say a machine guessed it. So `POST /extract` writes only citations, and only
`POST /citations/{id}/approve` promotes one — always naming the approver in the
audit log.

A promoted figure is stamped `data_status = MANUAL`. It never claims to be live
data, and if it lands on a row that was previously seeded, that row is
re-badged so it stops claiming to be demo data.

## Reading the document

| Format | Method | Notes |
| --- | --- | --- |
| PDF | `pypdf` | Pure Python, no system dependencies. |
| HTML | BeautifulSoup + lxml | Tables are flattened one row per line so labels keep their numbers. |
| Text | direct | Form feeds become page breaks. |

There is **no OCR**. A page yielding under 40 characters is marked empty, and a
document where over 70% of pages are empty is reported as a probable scan
rather than silently extracting nothing.

Encrypted PDFs are tried with an empty password (which opens many filings) and
reported honestly if that fails.

## Numbers

Indian filings break naive parsers in four specific ways, all handled:

| Printed | Parsed | Why |
| --- | --- | --- |
| `1,23,456.78` | 123456.78 | 2-2-3 digit grouping, not 3-3-3 |
| `(1,234)` | −1234 | brackets mean negative |
| `Nil`, `NA`, `—`, `-` | **None** | "no value", not zero |
| `12.5%` | 12.5 | trailing percent stripped |

## Units — the 100× problem

A statement prints `1,250` and means ₹1,250 **crore**. Guessing that multiplier
is a hundred-fold error, so the extractor refuses to guess.

Unit resolution, in precedence order:

1. attached to the number itself (`Rs 1,250 crore`)
2. declared on the same line
3. declared on the page, or inherited from the most recent page that declared
   one — with the inheriting page recorded so a reviewer can check it

If none of those produce a unit, the figure is stored with
`normalised_value = null`, flagged for review, and the UI says *"no unit
declaration was found near this figure"*. Percentages and per-share figures are
never multiplied.

| Unit | Multiplier |
| --- | --- |
| thousand | 1e3 |
| lakh | 1e5 |
| million | 1e6 |
| crore | 1e7 |
| billion | 1e9 |

## Matching a metric

A line matches when a known alias **starts** it, after an optional note number
(`1.`, `(iv)`). Anchoring at the start is what stops *"segment total revenue
disclosures in note 4 will change"* being read as revenue.

Aliases are checked longest-first, so `profit before tax` wins over `profit`
and a PBT row is never recorded as PAT. Each metric also carries
**negative cues**: `ebitda margin` on a line disqualifies it from matching
`ebitda`.

Roughly 25 metrics are covered — the P&L, balance sheet and cash-flow lines,
plus order book, promoter holding and pledge.

## Confidence

Starts pessimistic at 0.35 and earns its way up:

| Signal | Credit |
| --- | --- |
| Number within 24 characters of the label | +0.15 |
| Unit established (or not needed) | +0.15 |
| Reporting period identified | +0.12 |
| Two or more figures on the row (a comparative table) | +0.13 |
| Inside a statements section | +0.12 |
| Corroborated by the same figure on another page | +0.10 |
| Long, prose-like line | −0.10 |

Above **0.75** a figure is pre-marked as safe to accept; below it, review is
required. Every reason is stored and shown — confidence measures how well the
line matched an expected statement row, **not** whether the figure is correct,
and the review UI says exactly that.

## Sections

Headings are matched as whole lines (under 90 characters), after stripping
`SECTION IV -`, `4.2`, `A.` style prefixes. Pages inherit the most recent
heading. Recognised: risk factors, objects of the issue, MD&A, directors'
report, the three statements, business, industry, capital structure, related
party, contingent liabilities, litigation, governance, shareholding, basis for
the issue price, promoters, segments.

## Commentary and risk factors

**Commentary** is reproduced **verbatim** with its page. Cue phrases group it
into guidance, capex, demand, expansion, margin, risk and segment. The pipeline
does not paraphrase or score it, because a paraphrase of management guidance is
how a research note quietly becomes a claim.

**Risk factors** are categorised from the text (customer/supplier
concentration, geographic, litigation, contingent liability, related party,
regulatory, promoter, leverage). Severity comes only from what the text itself
states: a quantified concentration (`54.20% of our revenue`) is HIGH; an
unquantified mention is MEDIUM and says *"pending review"*. The pipeline does
not judge how serious a risk really is.

## What it will not do

- No OCR, so scanned filings yield nothing — and say so.
- No inference of a missing unit.
- No paraphrasing of management commentary.
- No writing into fundamentals without human approval.
- No claim that an extracted figure is correct — only that a line matched.
