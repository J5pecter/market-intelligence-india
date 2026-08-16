# Market Intelligence India

An evidence-first research and market-intelligence terminal for NSE and BSE
equities, indices, futures, options and IPOs.

**Live:** [market-intelligence-india.vercel.app](https://market-intelligence-india.vercel.app) ·
**API:** [`/api/health`](https://market-intelligence-api-lmwi.onrender.com/api/health) ·
[API docs](https://market-intelligence-api-lmwi.onrender.com/docs) ·
[Project page](https://j5pecter.github.io/market-intelligence-india/)

> The deployment runs with `APP_ENV=DEMO`, so it serves the seeded sample
> dataset. Every row is badged `DEMO` in the UI and in the API payload. Both
> tiers are free, so the API sleeps after 15 minutes of inactivity — the first
> request after a nap can take up to a minute.

**This is a research platform, not a signal service.** It does not predict
prices, it does not guarantee outcomes, and it is not investment advice. Its
whole design goal is that you can always answer the question *"why does it say
that?"* — every number on screen carries the calculation, the source, the
timestamp and the data status behind it.

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Folder structure](#folder-structure)
- [Running it locally](#running-it-locally)
- [Environment variables](#environment-variables)
- [Database](#database)
- [Data providers and their limits](#data-providers-and-their-limits)
- [Compliance configuration](#compliance-configuration)
- [Testing](#testing)
- [Deployment](#deployment)
- [Extending it](#extending-it)
- [Known limitations](#known-limitations)

---

## What it does

| Area | What you get |
| --- | --- |
| **Dashboard** | Indices, breadth, sector performance, movers, breakouts, FII/DII flows, active research, IPO watch, events, news |
| **Stocks** | Overview, candlestick chart with overlays, technical evidence chain, fundamentals with a documented quality score, news with impact scoring, corporate actions, full research view |
| **Indices** | Trend, regime with its reasons, breadth, constituent contribution, option positioning |
| **F&O** | Institutional-style option chain with OI bars, computed Greeks, PCR, max pain, build-up classification, IV skew, futures basis |
| **IPO** | Issue detail, GMP history chart, subscription, extracted financials, risk factors, six-component research score, SWOT with evidence, application simulator |
| **Documents** | Upload or link filings (annual reports, results, DRHP/RHP, transcripts); extract figures, risk factors and management commentary with page-level citations; review queue before anything becomes a fact |
| **Research** | Cards whose status is recomputed from live price on every read; third-party calls reproduced with attribution; source performance tracking |
| **Scanners** | 25 built-in technical / fundamental / combined screens plus a custom filter builder |
| **Backtesting** | Declarative strategy builder, no-look-ahead engine, costs, in-sample vs out-of-sample, walk-forward |
| **Personal** | Watchlists, alerts across 20 condition types, paper trading, portfolio with XIRR and concentration analysis |
| **Governance** | Compliance page, methodology documents, append-only audit log, admin panel, system health |

### The evidence chain

Every analytical output is an `EvidenceChain` of items carrying:

```
metric -> value -> weight -> calculation -> interpretation -> source -> timestamp
```

The UI renders "Why?" panels mechanically from those fields, so the interface
cannot display a conclusion the data does not support. Every setup also carries
a mandatory **"Why this may fail"** panel built from counter-evidence, risk
factors and data gaps.

When dimensions disagree, the platform reports
`MIXED_WAIT_FOR_CONFIRMATION` and **refuses to generate a direction**. Mixed is
a legitimate answer.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Next.js 16 · React 19 · TypeScript · Tailwind               │
│  Server components fetch; client components chart and filter │
│  /api/* is proxied to FastAPI, so no API origin ships to the │
│  browser and there is no CORS round-trip                     │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│  FastAPI · Pydantic v2 · SQLAlchemy 2.0                      │
│                                                              │
│  api/routes ──▶ services (all business logic lives here)     │
│                   technical · fundamental · greeks · options │
│                   risk · confidence · backtest · news · ipo  │
│                   research · alerts · portfolio · scanner    │
│                        │                                     │
│                        ▼                                     │
│              provider registry (ordered failover,            │
│              retry with backoff, circuit breaker,            │
│              rate limits, health tracking)                   │
│                        │                                     │
│     ┌──────────┬───────┴────┬──────────┬─────────┐           │
│   yahoo      nse     google news    manual     demo          │
│  (delayed) (opt-in)    (RSS)      (admin)   (seeded)         │
└───────────────────────────┬──────────────────────────────────┘
                            │
        PostgreSQL / SQLite ·  Redis or in-process cache
                  APScheduler background jobs
```

**No component contains business logic.** Every calculation lives in
`backend/app/services/`; the frontend only presents what the API computed.

---

## Folder structure

```
.
├── backend/
│   ├── app/
│   │   ├── main.py                 FastAPI entrypoint, lifespan, middleware
│   │   ├── core/
│   │   │   ├── config.py           environment-driven settings
│   │   │   ├── security.py         PBKDF2 hashing, JWT, RBAC, key encryption
│   │   │   ├── compliance.py       registration claims and guard rails
│   │   │   ├── data_quality.py     the Sourced envelope and freshness rules
│   │   │   ├── market_calendar.py  IST sessions and holidays
│   │   │   ├── cache.py            Redis/in-process cache, rate limit, breaker
│   │   │   └── logging.py          structured JSON logs, job instrumentation
│   │   ├── db/                     base, session, seed (all demo data)
│   │   ├── models/                 ~45 tables across 8 modules
│   │   ├── providers/              base protocol, registry, adapters
│   │   ├── services/               every calculation in the platform
│   │   │   └── documents/          filing extraction: text, sections,
│   │   │                           figures, citations, review gate
│   │   ├── api/routes/             auth, market, stocks, derivatives, ipo,
│   │   │                           research, documents, user_data,
│   │   │                           system, admin
│   │   └── jobs/                   scheduler and job definitions
│   ├── config/
│   │   ├── compliance.json         how the platform may describe itself
│   │   └── branding.json           name, colours, typography, footer
│   ├── docs/methodology/           served at /api/methodology
│   ├── tests/                      267 tests
│   └── requirements.txt
└── frontend/
    ├── src/
    │   ├── app/                    25 routes (App Router)
    │   ├── components/             Shell, ResearchCard, EvidencePanel,
    │   │                           PriceChart, SearchDialog, primitives
    │   └── lib/                    api client, formatters
    └── package.json
```

---

## Running it locally

Requirements: **Python 3.11+** and **Node 20+**. Nothing else — the default
configuration uses SQLite and an in-process cache.

### Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --reload --port 8000
```

On first start it creates the schema and seeds the demonstration dataset. API
docs are at <http://127.0.0.1:8000/docs>.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Open <http://localhost:3000>.

### First account

Registration is open, and **the first account created becomes the
administrator**. No default password ships with this project.

```bash
curl -X POST http://127.0.0.1:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"a-long-enough-password"}'
```

Then sign in at `/settings` and open `/admin`.

### Loading real data

The demo seed exists so a fresh checkout renders. To pull live data, open
**Admin → Jobs** and run, in order:

1. `instrument_sync` — imports the instrument universe (needs the NSE adapter
   enabled, or add instruments manually)
2. `quote_refresh` — prices watchlisted and researched symbols first
3. `history_refresh` — daily bars
4. `indicator_refresh` — the snapshot scanners query
5. `news_refresh` — Google News RSS per symbol

The scheduler then runs these automatically at market-aware cadences.

---

## Environment variables

Full list in `backend/.env.example`. The ones that matter:

| Variable | Default | Effect |
| --- | --- | --- |
| `APP_ENV` | `DEMO` | `DEMO`/`LOCAL` serve seeded rows badged DEMO. `STAGING`/`PRODUCTION` refuse to serve them at all. |
| `DATABASE_URL` | `sqlite:///./market_intel.db` | Point at PostgreSQL for production. |
| `REDIS_URL` | *(empty)* | Blank uses the in-process cache — per-worker, so run one uvicorn worker or set this. |
| `SECRET_KEY` | insecure default | **Change it.** Signs JWTs and encrypts stored provider keys. |
| `ENABLE_NSE_PROVIDER` | `false` | Opt-in. See below. |
| `QUOTE_PROVIDERS` etc. | `yahoo,nse,manual,demo` | Ordered failover chains, comma-separated. |
| `ENABLE_SCHEDULER` | `true` | Turn off in serverless environments. |
| `TELEGRAM_BOT_TOKEN`, `SMTP_*` | *(empty)* | Optional alert channels. Unconfigured channels report `NOT_CONFIGURED` rather than failing silently. |

Never commit `.env`. `.env.example` is the template.

---

## Database

SQLAlchemy models cover ~45 tables. On start-up `Base.metadata.create_all`
ensures the schema, which is enough for SQLite and for getting going.

On SQLite, start-up also reconciles **additively**: any column the models
declare but the database lacks is added with `ALTER TABLE ... ADD COLUMN`,
and every addition is logged by name. Nothing is ever dropped, renamed or
retyped. `/api/health` reports remaining schema drift.

For PostgreSQL and versioned migrations, Alembic is in `requirements.txt`:

```bash
cd backend
alembic init migrations          # once
# point migrations/env.py at: from app.models import Base; target_metadata = Base.metadata
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

The initial revision is generated from the models rather than hand-written, so
it always matches what the code expects.

---

## Data providers and their limits

Business logic never imports a provider. It asks the registry for a capability
and receives a `Sourced` envelope; swapping Yahoo for a licensed feed is a
configuration change.

| Adapter | Cost | Status | Provides |
| --- | --- | --- | --- |
| `yahoo` | Free | **Delayed**, personal/non-commercial use | quotes, daily and intraday history, fundamentals, major indices |
| `nse` | Free | **Disabled by default** | quotes, option chains, indices, instrument master, corporate actions |
| `google_news_rss` | Free | Aggregated headlines only | news headlines, links, publishers |
| `manual` | — | Operator-entered, badged `MANUAL` | anything an admin enters |
| `demo` | — | Seeded, badged `DEMO`, never served in STAGING/PRODUCTION | everything, for a working first run |

### About the NSE adapter

NSE publishes JSON endpoints that its own website consumes. They are not a
documented public API, and NSE's terms restrict automated access. The adapter
is therefore **off by default**, and when enabled it:

- sends one honest, identifiable User-Agent and never rotates it;
- obtains a session cookie the ordinary way and does **not** attempt to defeat
  any challenge, CAPTCHA or bot check;
- treats HTTP 401/403 as *"the operator has declined access"* — it opens the
  circuit breaker and stops calling, rather than retrying with a new identity;
- honours a conservative per-minute request budget.

If NSE blocks you, the correct response is a licensed feed or a broker API, not
a workaround. While it is off, option-chain and futures panels report
`UNAVAILABLE` with the reason and the remedy — which is the honest state.

### Free-tier reality

The MVP runs with **no paid API**. What free sources cannot give you:

- Real-time exchange-grade ticks. Yahoo is delayed; treat it as such.
- Intraday history beyond the provider's cap (~7 days at 1-minute, ~60 days at
  5–30 minute).
- FII/DII daily flow, index constituent weights, analyst consensus estimates,
  and reliable corporate-action adjustment factors. Those panels report
  unavailable until you configure a source.

---

## Compliance configuration

`backend/config/compliance.json` is the single source of truth for how the
platform describes itself. **The application never invents a registration
number.**

With no registration configured — the shipped default — the platform:

- describes itself as an *"Educational / informational market research
  platform"*;
- renders **no** verification badge of any kind;
- **rejects** any research call whose text contains a prohibited claim
  ("SEBI Verified", "guaranteed returns", "risk-free", …), enforced in the API
  before the record can be saved.

Configure a real registration by editing the file (or `PUT /api/admin/compliance`,
which validates and writes an audit entry). A registration *status* of
`REGISTERED` without a number is rejected, and a number without a
`legal_reviewer` is rejected.

Regulatory sources are tracked as **data** in `compliance_documents` — name,
URL, version, status, who last checked it and when. Nothing about the rules is
hard-coded, so an administrator or legal reviewer can keep them current.
Documents ship with status `UNVERIFIED` until a human confirms them.

Statistical claims (such as SEBI's cited finding on individual F&O trader
outcomes) always display **with their study period**, and are withheld entirely
in `PRODUCTION` until someone records verifying them against the current
source.

---

## Testing

```bash
cd backend
.venv/Scripts/python -m pytest          # 267 tests
```

| Suite | Covers |
| --- | --- |
| `test_trade_status.py` | Every entry-range edge case, terminal states, zero-width stops, missing and stale data, position sizing, charges. Asserts the five reference cards reproduce their published percentages exactly. |
| `test_indicators.py` | Indicator arithmetic against hand-computed values, warm-up NaN behaviour, RSI boundary conditions, VWAP session resets. |
| `test_greeks.py` | Put-call parity, Greek signs, gamma behaviour near expiry, IV solver recovery, no-arbitrage rejection, expired contracts. |
| `test_engines.py` | Evidence scoring, conflict detection, confidence penalties, risk blending, news lexicon and negation, IPO labels, analogue sample guards. |
| `test_backtest.py` | No look-ahead, pessimistic intrabar resolution, cost application, sample separation, walk-forward. |
| `test_providers.py` | Failover, the no-data vs failure distinction, freshness classification, stale banners. |
| `test_documents.py` | Indian number formats, unit resolution and refusal, metric matching, sectioning, commentary, risk quantums, and a real generated PDF end to end. |
| `test_documents_api.py` | The review gate: extraction writes citations not facts, approval promotes and is audited, rejection sticks, re-extraction does not duplicate. |
| `test_api.py` | Auth, RBAC, account-enumeration resistance, compliance rejection, provenance on every payload, audit trail, derived-field protection. |

Frontend:

```bash
cd frontend
npm run typecheck
npm run build
```

---

## Deployment

Designed for free tiers, with their limits handled rather than ignored.

The live deployment uses exactly this setup.

**Backend — Render / Railway / Fly**

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set `APP_ENV=PRODUCTION`, a real `SECRET_KEY`, `DATABASE_URL` and
`CORS_ORIGINS`. On a free tier that sleeps, set `ENABLE_SCHEDULER=false` and
drive the jobs from an external cron hitting
`POST /api/admin/jobs/{job}/run`.

**Database — Supabase / Neon**

```
DATABASE_URL=postgresql+psycopg://user:password@host:5432/dbname
```

**Cache — Upstash** (optional but recommended above one worker)

```
REDIS_URL=rediss://...
```

**Frontend — Vercel / Cloudflare Pages**

Set `BACKEND_URL` to your API's public URL. `/api/*` is rewritten to it, so the
browser only ever talks to your own origin.

### Before going live

- [ ] `SECRET_KEY` changed from the default
- [ ] `APP_ENV=PRODUCTION` (demo rows become unservable)
- [ ] `CORS_ORIGINS` restricted to your domain
- [ ] Compliance configuration reviewed and `last_reviewed_date` set
- [ ] Provider terms read and `ENABLE_NSE_PROVIDER` decided deliberately
- [ ] TLS terminated upstream (HSTS is set automatically on HTTPS)

---

## Extending it

**Add a provider** — subclass `MarketDataProvider` in
`backend/app/providers/`, implement the methods you can, raise
`ProviderNoData` for a missing key and `ProviderError` for a genuine failure
(only the latter counts against health). Register it in
`ProviderRegistry._register_defaults` and add its name to the relevant chain.

**Add an indicator** — write it in `services/indicators.py` with its formula in
the docstring, add it to `compute_all`, and it becomes available to charts,
scanners and the backtest DSL at once.

**Add a scanner** — append a `ScannerDefinitionSpec` to `BUILTIN_SCANNERS` in
`services/scanner.py`. Reference any column in `FIELD_MAP`.

**Add an alert type** — add an entry to `ALERT_TYPES` and an `_eval_<type>`
method on `AlertService`. It returns whether it fired *and the sentence
explaining why*, which becomes the notification body.

**Add a document metric** — add an entry to `METRIC_DEFINITIONS` in
`services/documents/patterns.py` with its aliases and negative cues. That
file is the whole vocabulary; no parsing logic changes.

**Add a research source** — `POST /api/admin/sources`, then attribute calls to
it. External calls must name their source; the platform will not present
someone else's research as its own.

---

## Known limitations

Stated plainly, because a research tool that hides its own gaps is worse than
useless.

1. **Delayed data.** With the default free providers, nothing is real-time.
   Every quote is badged `DELAYED` or `STALE`.
2. **Option chains need a source.** With the NSE adapter off, they are
   unavailable unless entered manually or supplied by a licensed feed.
3. **No FII/DII flow, index weights or consensus estimates** in the free
   configuration. Those panels say so.
4. **Corporate-action adjustment** relies on the history provider's adjusted
   closes. Unadjusted splits would appear as false gaps.
5. **Document extraction has no OCR.** Scanned filings yield nothing, and the
   pipeline reports that rather than extracting silently. Text-based PDFs,
   HTML and plain text are read fully.
6. **No AI chat assistant.** The structured evidence chain that such an
   assistant would need is complete and exposed at
   `/api/stocks/{symbol}/research`; the conversational layer over it is not
   built.
7. **WebSockets are not implemented.** The UI polls. The provider layer is
   ready for streaming when a feed that supports it is configured.
8. **PDF/Excel export** is not wired up, though `reportlab` and `openpyxl` are
   available and every payload is already JSON-serialisable.
9. **Backtests are daily-bar, long-only, single-position.** Intraday, short and
   portfolio-level backtesting are not implemented.
10. **This deployment holds no registration.** It is an informational and
    educational research platform, and says so on every page.

---

## Licence and disclaimer

Information produced by this platform is for informational and educational
purposes only. It does not constitute investment advice, research advice,
solicitation, recommendation, or a guarantee of returns. Market investments,
including derivatives, involve risk and may result in partial or complete loss
of capital. Evaluate investments independently and consider consulting an
appropriately qualified and registered professional.

Review each data provider's terms before using it, particularly in a commercial
deployment.
