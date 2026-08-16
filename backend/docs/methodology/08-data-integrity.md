# Data integrity: sources, reconciliation and what "real-time" actually means

Source: `backend/app/providers/`, `backend/app/services/reconciliation.py`.

This is the document to read before trusting any number this platform shows
you. It covers where each figure comes from, how fresh it really is, and what
the platform does when two sources disagree.

## What is genuinely real-time, and what is not

There is no free real-time feed for NSE or BSE. Anyone claiming otherwise is
either delayed, redistributing someone else's licensed feed, or scraping. The
honest tiers are:

| Tier | Source | Latency | Cost | Status stamped |
| --- | --- | --- | --- | --- |
| Real-time | Your broker's API | sub-second | free with a demat account (Kite is paid) | `LIVE` |
| Delayed | Yahoo Finance | ~15 min | free | `DELAYED` |
| End-of-day | NSE / BSE published archives | after the close | free | `DELAYED`, session-dated |
| Reference | AMFI, RBI, World Bank | daily to annual | free | `DELAYED` / `UNVERIFIED` |

A broker adapter is the **only** one permitted to stamp an envelope `LIVE`, and
even then `freshness()` decides from the observation timestamp — a reconnect
that replays an old tick cannot masquerade as live.

Configure a broker and it is inserted at the head of the quote, history and
option-chain chains automatically. Leave it unconfigured and it is removed from
every chain entirely, rather than sitting there failing and burning a failover
slot.

### Why the archives are not "scraping"

`nse.py` talks to the JSON endpoints behind NSE's website. Those are
undocumented, bot-challenged, and restricted by NSE's terms — which is why that
adapter is **off by default**.

`nse_archives.py` downloads the **published archive files**: the daily
bhavcopy, the securities-wise delivery report, the bulk and block deal
registers. These are static files that NSE and BSE publish *for download*.
There is no challenge to defeat and no session to forge. That is why they are
on by default.

If either ever returns 401 or 403, the adapter reports that the exchange
declined the request and stops. It does not retry under a different identity.

## Reconciliation: the platform's answer to "is this number right?"

Everywhere else, the provider registry returns the **first** source that
answers. That keeps a screen populated but is exactly wrong for research: one
vendor's bad tick silently becomes your input.

`/api/exchange/verify/{symbol}` does the opposite. It asks **every** capable
source independently and compares them.

### The verdicts

| Verdict | Meaning |
| --- | --- |
| `CONFIRMED` | Two or more independent sources agree within tolerance. A consensus is published. |
| `MINOR_DIVERGENCE` | Outside tolerance but within 3×. No consensus. |
| `CONFLICT` | Materially different numbers. No consensus. |
| `SINGLE_SOURCE` | Only one source could answer. Value returned, explicitly unverified. |
| `UNAVAILABLE` | Nobody could answer, with each failure reason listed. |

**A consensus is published only on `CONFIRMED`.** Handing back a median of
conflicting numbers would invent a figure no source reported — precisely the
failure this exists to prevent.

### Why the median, not the mean

With three sources and one bad tick, the mean is dragged toward the bad value
and can push two good sources outside tolerance. The median ignores it.

### Outliers need three sources

With exactly two readings the median sits between them, so both would always be
flagged — which says nothing. Two sources disagreeing is a disagreement, not an
outlier, and you have to pick. Outlier labelling starts at three.

### Tolerances

Expressed in percent, per metric, because sources differ in kind and not just
in quality:

| Metric | Tolerance | Why |
| --- | --- | --- |
| settled close | 0.1% | a settled close is a fact; sources must match it |
| LTP | 0.5% | feeds sampled seconds apart |
| OHL | 0.25% | |
| volume | 2% | feeds differ on whether blocks are included |
| P/E, P/B, EPS | 5% | vendors use different trailing windows |
| dividend yield | 10% | trailing vs indicated differ legitimately |

**Cross-venue comparisons get 1.0%.** NSE and BSE are separate order books; the
same stock genuinely closes at different prices on each. That is a fact about
the market, not a data error, and flagging it would cry wolf on every
dual-listed name. The explanation says so explicitly when it applies.

### Authority, and why it is only a tie-break

When sources genuinely conflict, the platform names the most authoritative one
rather than averaging:

1. exchange archives (the settled record)
2. licensed broker feeds
3. NSE site endpoints, AMFI, RBI, World Bank
4. Yahoo
5. manually entered rows

But agreement between two independent sources is stronger evidence than either
one's reputation, which is why the numbers decide first and this hierarchy only
breaks ties. Reliability describes a source *in general*, not this particular
observation: an archive is authoritative for a settled close and silent about a
price thirty seconds ago.

Seeded demo rows are excluded from reconciliation entirely. A sample row must
never corroborate real data.

## The limit of all this

Agreement means sources are **consistent**, not that they are **correct**. Two
vendors can share an upstream feed and repeat the same error. The evidence
chain says this in its limitations on every reconciliation response, and it is
the reason the exchange archive outranks everything: it is not another opinion
about the close, it *is* the close.
