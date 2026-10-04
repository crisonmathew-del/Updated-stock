# Breakout — Stock Scanning & Recommendation Platform
### Master build specification for Claude Code

> **Claude Code: read this entire document before writing any code.** It is the single source of truth for this project. Follow the "How to work" rules in Section 0, then build in the phases defined in Section 15. When something here is ambiguous, ask me rather than guessing.

---

## 0. How to work (instructions for Claude Code)

1. **Read everything first.** Then create a `CLAUDE.md` at the repo root that summarises the architecture, commands, conventions and the current phase, and keep it updated as the project evolves.
2. **Plan before building each phase.** At the start of each phase, write a short plan (files to create, data flows, tests) and show it to me before implementing.
3. **Ask me these questions before Phase 1 starts** (use sensible defaults if I say "you choose"):
   - Which data providers and plan tiers I have API keys for (see Section 5).
   - Account size and risk-per-trade % to use as defaults for position sizing.
   - Which notification channels I want (in-app, browser push, Telegram, email).
   - Which markets to cover (default: US-listed common stocks on NYSE/NASDAQ/AMEX).
4. **Never hardcode secrets.** All keys live in `.env` (provide `.env.example`). Keys are only ever used server-side.
5. **No lookahead bias, ever.** Every calculation for a given date may only use data available at or before that date's close (or that intraday moment). This matters for the scanner and is critical for the backtester.
6. **Every recommendation must be explainable.** If the system flags a stock, the UI must show exactly which rules passed/failed with the actual numbers.
7. **Test the maths.** Every indicator and pattern detector gets unit tests with hand-verified fixtures before it is used anywhere.
8. **Work in small, committed steps** with clear commit messages. Run linting, type-checking and tests before each commit.
9. **This platform does not place trades.** It screens, scores, alerts and plans. No broker order execution in any phase unless I explicitly ask later.

---

## 1. Product summary

A fast, interactive web platform that does three jobs:

1. **Search & analyse** — I type any ticker or company name and instantly get a full breakdown: interactive chart with patterns drawn on it, trend and relative-strength status, fundamentals grade, industry group rank, a composite score, and a ready-made trade plan (entry, stop, size, targets).
2. **Scan & recommend** — the system continuously scans the whole market in the background for stocks that meet a proven growth/momentum breakout methodology (Section 6), ranks them, and tracks each one through a setup lifecycle (building a base → near pivot → breaking out → extended / failed).
3. **Alert** — when a stock enters a buy zone, breaks out on volume, or a watchlist name triggers a condition, I get notified immediately (in-app, browser push, Telegram, email).

The methodology is the classic institutional growth-stock playbook: buy fundamentally strong leaders, in leading industry groups, in a healthy market, as they break out of sound bases on heavy volume — and cut losses quickly.

---

## 2. Non-negotiable principles

- **Speed and smoothness.** Search results in under 100 ms, stock pages render in under 1 s, charts scroll and zoom at 60 fps, tables of 5,000+ rows stay smooth (virtualised).
- **Explainability over black boxes.** Every score is a sum of visible, named components.
- **Market first.** All recommendations are conditioned on market regime (Section 6.1). In a correction, the system says so loudly and stops pushing buy alerts by default.
- **Risk first.** Every setup ships with a stop and a position size. No setup is shown as actionable without a defined risk.
- **Track the truth.** Every signal the system ever generates is logged and its forward performance tracked, so I can see what actually works (Section 11).
- **Configurable.** Every threshold in Section 6 lives in a config table with the defaults given, editable from a Settings page.
- **Not investment advice.** The UI shows a persistent, unobtrusive note that outputs are screening signals, not financial advice.

---

## 3. Tech stack (and why)

This is a web application: one codebase, works on desktop and mobile (as an installable PWA with push notifications), no app-store friction, and gives access to the best charting and quant tooling.

### Frontend
| Concern | Choice |
|---|---|
| Framework | **Next.js (latest stable, App Router) + React + TypeScript (strict)** |
| Styling | **Tailwind CSS** + **shadcn/ui** (Radix primitives) for accessible components |
| Charts | **TradingView Lightweight Charts** (latest major version) — candlesticks, volume, MAs, overlays, custom primitives for drawing bases/pivots |
| Secondary charts | Recharts or visx for small analytics charts (backtest equity curves, distributions) |
| Server state | **TanStack Query** (caching, background refetch, optimistic updates) |
| Tables | **TanStack Table + TanStack Virtual** (sorting, filtering, column pinning, virtualised rows) |
| Client state | **Zustand** |
| Command palette / search | **cmdk** |
| Real-time | Native WebSocket client with auto-reconnect and backoff |
| Motion | Framer Motion, used sparingly (see Section 9) |
| Package manager | **pnpm** |

### Backend
| Concern | Choice |
|---|---|
| API | **Python 3.12+ / FastAPI**, Pydantic v2, fully async |
| Numerics | **Polars** (primary, fast) + NumPy; pandas only where a library needs it |
| Indicators | Implement core indicators ourselves in vectorised NumPy/Polars with tests (they're simple and we need exact control). TA-Lib optional for cross-checking |
| Database | **PostgreSQL 16 + TimescaleDB** extension (hypertables for price bars) |
| ORM / migrations | SQLAlchemy 2.0 (async) + Alembic |
| Cache, pub/sub, queue | **Redis** |
| Background jobs | **arq** (async Redis job queue) workers + **APScheduler** in a dedicated scheduler service |
| Real-time push to UI | FastAPI WebSocket endpoint fed by Redis pub/sub |
| Notifications | Telegram Bot API, email via Resend (or SMTP), Web Push (VAPID) |
| AI narrative (optional) | Anthropic Claude API via official Python SDK; model ID set in `.env` as `ANTHROPIC_MODEL` |
| Package manager | **uv** |

### Infrastructure
- **Docker Compose** for local dev: `web`, `api`, `worker`, `scheduler`, `streamer`, `postgres` (Timescale image), `redis`.
- Single `make` / `just` file with commands: `dev`, `test`, `lint`, `migrate`, `seed`, `backfill`, `scan-now`.
- Deployment target (Phase 7): frontend on Vercel; backend services + Postgres + Redis on Railway, Fly.io or a small VPS. Keep it provider-agnostic via Docker.
- Auth: single user to start (email + password or magic link, session cookie, all routes protected). Data model includes `user_id` everywhere so multi-user is possible later.

---

## 4. Architecture

```
                 ┌──────────────────────────── Next.js (web) ────────────────────────────┐
                 │ Search ⌘K │ Stock page │ Screener │ Setups board │ Alerts │ Backtest   │
                 └──────────────┬──────────────────────────────┬─────────────────────────┘
                         REST (JSON)                     WebSocket (live updates)
                 ┌──────────────▼──────────────────────────────▼─────────────────────────┐
                 │                         FastAPI (api)                                  │
                 └──────┬──────────────────────┬───────────────────────┬─────────────────┘
                        │                      │                       │
                 ┌──────▼──────┐        ┌──────▼──────┐         ┌──────▼──────┐
                 │ PostgreSQL  │◄───────┤   Workers   │◄────────┤  Scheduler  │
                 │ +Timescale  │        │ (arq jobs)  │  jobs   │ (APScheduler)│
                 └──────▲──────┘        └──────┬──────┘         └─────────────┘
                        │                      │ publish
                 ┌──────┴──────┐        ┌──────▼──────┐         ┌──────────────────┐
                 │  Streamer   │───────►│    Redis    │────────►│ Notifier (Telegram│
                 │ (live feed) │ ticks  │ cache/pubsub│ alerts  │ email, web push) │
                 └──────▲──────┘        └─────────────┘         └──────────────────┘
                        │
              Market data providers (via adapter layer)
```

**Backend module layout** (`/api/app`):
```
core/          config, logging, db session, redis, security
providers/     base.py (abstract interfaces), massive.py, alpaca.py, fmp.py, finnhub.py,
               sec_edgar.py, yfinance_dev.py
data/          ingestion, backfill, corporate actions, universe builder
indicators/    moving_averages.py, atr.py, volatility.py, volume.py, relative_strength.py, swings.py
patterns/      bases.py, vcp.py, cup_handle.py, flat_base.py, high_tight_flag.py,
               pocket_pivot.py, earnings_gap.py, tightness.py
market/        regime.py (distribution days, follow-through day, breadth)
fundamentals/  growth.py, grading.py
groups/        industry_rank.py
scoring/       composite.py, lifecycle.py
risk/          trade_plan.py, position_sizing.py
scanner/       eod_scan.py, intraday_scan.py, premarket_scan.py
alerts/        rules.py, dedupe.py, channels/
backtest/      engine.py, metrics.py, reports.py
ai/            narrative.py (optional)
api/routes/    stocks, search, screener, setups, watchlists, alerts, market, backtest, settings, ws
```

**Frontend layout** (`/web`): `app/` routes per page in Section 8, `components/` (chart, tables, score widgets), `lib/` (api client, ws client, formatters), `stores/`.

---

## 5. Data layer

### 5.1 Provider adapter pattern
Define abstract interfaces in `providers/base.py` so providers are swappable via `.env`:
- `PriceProvider`: daily bars, intraday bars, grouped daily (whole market for a date), snapshots, splits/dividends, ticker reference data.
- `StreamProvider`: real-time trades/minute-bars over WebSocket.
- `FundamentalsProvider`: quarterly & annual income statements, EPS (actual + estimates + surprise), revenue, margins, ROE, shares/float, institutional ownership, earnings calendar.
- `NewsProvider`: company news headlines.
- `FilingsProvider`: SEC EDGAR Form 4 (insider transactions) and 13F (institutional holdings) — free, no key, must send a proper User-Agent and respect rate limits.

### 5.2 Recommended providers (verify current pricing when we start)
- **Prices (primary): Massive (formerly Polygon.io)** — REST, WebSockets, flat files. Its "grouped daily" endpoint returns every US stock's daily bar in one call, ideal for end-of-day full-market scans. Free tier is end-of-day and rate-limited; paid tiers add longer history and delayed/real-time data. Use `api.massive.com` as the base URL.
- **Real-time stream (budget option): Alpaca market data** — free plan streams IEX-only data (partial volume); paid plan gives full SIP volume. **Important:** breakout volume confirmation needs full consolidated (SIP) volume, so with IEX-only data, intraday volume checks must be marked "provisional" and re-confirmed at the close.
- **Fundamentals: Financial Modeling Prep (FMP)** — statements, growth, earnings calendar, estimates. Entry tiers are US-only; higher tiers add UK/Canada and 13F/institutional data.
- **News/earnings backup:** Finnhub (free tier).
- **Dev-only fallback:** `yfinance` — unofficial, can break or rate-limit; never use in production paths.

### 5.3 Universe
- Default universe: US-listed **common stocks** (exclude ETFs, ETNs, warrants, rights, units, preferreds, OTC) — roughly 5,000–6,000 names.
- Liquidity filter for scanning (configurable): price ≥ $10, 50-day average dollar volume ≥ $20M, market cap ≥ $1B. Also keep a separate "small-cap" mode (price ≥ $5, ADV ≥ $5M, mcap ≥ $300M) toggled in settings.
- Benchmarks always loaded: SPY, QQQ, IWM, DIA, ^VIX proxy (VIXY or VIX index if available), and the 11 SPDR sector ETFs.
- Universe rebuilt weekly; delisted tickers retained in the DB (flagged inactive) to avoid survivorship bias in backtests.

### 5.4 Storage schema (key tables)
- `tickers` (symbol, name, exchange, type, sector, industry, industry_group_id, market_cap, float, active, listed_date, delisted_date)
- `daily_bars` (hypertable: ticker_id, date, o/h/l/c, volume, vwap, adjusted flag) — **store split-adjusted prices**; keep raw too if provider supplies them.
- `intraday_bars` (hypertable, 1-min, retain 30 days for watchlist/near-pivot names only)
- `corporate_actions` (splits, dividends)
- `fundamentals_quarterly`, `fundamentals_annual` (with `reported_date` so backtests use point-in-time data)
- `earnings_calendar`
- `insider_transactions`, `institutional_holdings`
- `industry_groups`, `group_rank_history`
- `indicators_daily` (precomputed per ticker per day: SMAs, EMAs, ATR, RS raw, RS rating, 52w hi/lo, avg volume, up/down volume ratio, etc.)
- `market_regime_daily`
- `patterns` (ticker, type, start/end dates, pivot price, depth %, contractions JSON, quality score, status)
- `setups` (ticker, pattern_id, lifecycle_state, score breakdown JSON, trade plan JSON, first_seen, last_updated)
- `signals` (immutable log: every alert-worthy event with full context snapshot at that moment)
- `signal_outcomes` (forward returns at +1, +5, +10, +20, +60 days, max favourable/adverse excursion, stop hit?)
- `watchlists`, `watchlist_items`, `alert_rules`, `alerts_sent`, `notes`, `settings`, `users`

### 5.5 Ingestion jobs
- **Initial backfill:** 10+ years of daily bars for the universe if the plan allows (minimum 2 years — RS and 52-week metrics need 252 sessions; backtests want much more). Show progress in an admin page.
- **Daily EOD update:** after the close, fetch the day's grouped bars, apply corporate actions, recompute indicators incrementally.
- **Fundamentals:** refresh nightly for names with earnings in the past 2 days; full refresh weekly.
- **Data quality checks:** missing days, zero volume, price spikes >50% without a corporate action, stale tickers. Log and surface in an admin "Data health" panel.
- Respect rate limits with a token-bucket limiter per provider; retry with exponential backoff; cache responses in Redis.

---

## 6. The trading methodology (the core engine)

This is the heart of the platform. It combines the market-direction discipline of William O'Neil (CAN SLIM), Stan Weinstein's stage analysis, Mark Minervini's Trend Template and Volatility Contraction Pattern (VCP), Morales & Kacher's pocket pivots, and Pradeep Bonde's episodic pivots. All thresholds below are **defaults** and must be configurable.

### 6.1 Market regime (the "M" — checked first, always)
Compute daily for SPY and QQQ (and IWM for small caps):
- **Trend:** index vs. 21-day EMA, 50-day SMA, 200-day SMA, and slope of each.
- **Distribution days:** index closes down ≥ 0.2% on volume higher than the prior day. Count over the rolling last 25 sessions; a distribution day expires early if the index later rises 5%+ above that day's close.
- **Follow-through day (FTD):** after a correction low, a rally attempt begins on the first up day; an FTD is day 4 or later of that attempt where the index gains ≥ 1.25% on higher volume than the prior day. Undercutting the rally-attempt low resets the count.
- **Breadth:** % of universe above their 50-day and 200-day SMAs; net new 52-week highs minus new lows; advance/decline line.
- **Output state:**
  - `CONFIRMED_UPTREND` — index above 50-day, ≤ 4 distribution days, breadth improving, or a recent FTD.
  - `UPTREND_UNDER_PRESSURE` — 5+ distribution days, or index below 21-day EMA while above 50-day.
  - `CORRECTION` — index below 50-day with weak breadth, or distribution clustering, until an FTD.
- **Effect:** regime multiplies the composite score (1.0 / 0.8 / 0.5) and in `CORRECTION` buy alerts are muted by default (still logged). The dashboard shows the regime prominently with the reasons.

### 6.2 Stage analysis (Weinstein)
Using the 30-week (150-day) SMA:
- **Stage 1 (basing):** MA flat, price oscillating around it.
- **Stage 2 (advancing):** price above a rising MA. **Only Stage 2 stocks are candidates for long setups.**
- **Stage 3 (topping):** MA flattening after an advance, price whipping through it.
- **Stage 4 (declining):** price below a falling MA. Excluded.
- Also compute **base count** within the current Stage 2 (each completed base ≥ 5 weeks then breakout increments the count). Bases 1–2 are best; 3 acceptable; 4+ flagged "late-stage, higher failure risk".

### 6.3 Trend Template (all must pass to be a "trend leader")
1. Close > 150-day SMA and > 200-day SMA
2. 150-day SMA > 200-day SMA
3. 200-day SMA trending up for at least 1 month (ideally 4–5 months) — test: today's 200-day SMA > value 21 sessions ago
4. 50-day SMA > 150-day SMA and > 200-day SMA
5. Close > 50-day SMA
6. Close ≥ 30% above the 52-week low
7. Close within 25% of the 52-week high
8. RS Rating ≥ 70 (prefer ≥ 80; true leaders often ≥ 90)

UI shows this as an 8-item checklist with actual values.

### 6.4 Relative strength
- **RS Rating (1–99):** weighted performance `0.4 × ROC(63) + 0.2 × ROC(126) + 0.2 × ROC(189) + 0.2 × ROC(252)`, then percentile-ranked across the full universe daily.
- **RS line:** close ÷ SPY close, plotted under the price chart.
- **RS line new high ahead of price:** flag when the RS line makes a new 52-week high while price hasn't yet — a strong leading signal ("RS leads price"). Draw a marker on the chart.
- **RS trend:** slope of RS line over 21 and 63 days.

### 6.5 Fundamentals (CAN SLIM-style "C", "A", "N", "S", "I")
Compute a **Fundamentals Grade (A–E)** from:
- **Current quarterly EPS growth (YoY):** ≥ 25% (strong ≥ 40%)
- **EPS acceleration:** growth rate increasing over the last 2–3 quarters
- **Quarterly sales growth (YoY):** ≥ 20%, ideally accelerating
- **Annual EPS growth:** ≥ 25% average over the last 3 years
- **Return on equity:** ≥ 17%
- **Margins:** net/operating margin expanding YoY
- **Earnings surprise & revisions:** beat last quarter; forward estimates revised up (if data available)
- **Supply/demand:** 50-day up/down volume ratio (sum of volume on up days ÷ on down days) ≥ 1.2 = accumulation
- **Institutional sponsorship:** number of institutional holders rising quarter over quarter (from 13F, if available)
- **Insider buying:** cluster buys (2+ insiders buying in open market within 30 days) is a positive flag
- **"N" — something new:** surfaced via news/AI narrative (new product, new management, new highs)

Stocks with no earnings (e.g., early-stage growth) get a "Revenue-led" grade path that weights sales growth and margin trend instead of EPS. Use `reported_date` for point-in-time correctness.

### 6.6 Industry group strength
- Group stocks by industry (use the provider's industry classification; ~150–200 groups).
- **Group RS:** median RS Rating of members, plus group-index performance over 3 and 6 months. Rank all groups 1–N daily.
- **Group leadership:** number of members passing the Trend Template, and number making new highs.
- Prefer stocks in the **top 40 groups**; top 20 get a bonus. Show group rank and its trend (rising/falling over 4 weeks).
- Sector rotation view: rank the 11 sector ETFs by relative strength.

### 6.7 Base & pattern detection
Shared building blocks:
- **Swing detection:** ZigZag with an ATR-scaled threshold (default 1.5 × ATR(14)) to find swing highs/lows on daily and weekly bars.
- **Prior uptrend requirement:** a valid base must follow an advance of ≥ 25–30% off a low.
- **Volume dry-up:** within a base, 10-day average volume below 50-day average, with several days < 50% of average near the right side.
- **Tightness:** daily range contraction (ATR(10)/ATR(50) falling), Bollinger Band width at a 6-month low, NR7 and inside days near the pivot.

Patterns to detect (each returns: start/end, pivot price, depth %, duration, a 0–100 quality score, and the swing points used, so the frontend can draw them):

1. **Volatility Contraction Pattern (VCP)** — the primary pattern.
   - 2–6 successive contractions (swing high → swing low), each shallower than the previous (each ≤ ~0.7× the prior depth). Example progression: 25% → 15% → 8% → 4%.
   - Final contraction ≤ 10% deep (ideal < 6%).
   - Volume contracts along with price; final contraction shows marked dry-up.
   - Duration 3–65 weeks.
   - **Pivot = high of the final contraction.**
2. **Cup with handle** — U-shaped (not V) correction of 12–33% (up to 50% in bear markets), 7–65 weeks; handle forms in the upper half of the cup, drifts down 5–12% on light volume over ≥ 1 week. Pivot = handle high.
3. **Flat base** — ≤ 15% deep, ≥ 5 weeks, typically after a breakout from a prior base. Pivot = base high.
4. **High tight flag** — ≥ 90–100% gain in ≤ 8 weeks, then a 10–25% pullback over 3–5 weeks on dry volume. Rare and powerful; flag specially.
5. **Three-weeks-tight** — three consecutive weekly closes within ~1.5% of each other. Pivot = high of the pattern.
6. **Ascending base** — three pullbacks of 10–20%, each low higher than the last, over 9–16 weeks.

Pattern quality score considers: depth vs. allowed max, symmetry and contraction progression, volume dry-up, tightness at the pivot, position of close within the base (upper half), base count, and RS line behaviour inside the base.

### 6.8 Entry triggers
- **Pivot breakout:** price trades above pivot. **Confirmed** when:
  - Projected daily volume ≥ 140% of 50-day average (strong ≥ 200%). Projection must use a **time-of-day volume curve** (historical cumulative-volume profile by minute), not a linear extrapolation.
  - Close in the upper third of the day's range (checked at close; intraday shows "provisional").
  - **Buy zone:** pivot to pivot + 5%. Above that = "extended — don't chase".
- **Pocket pivot:** an up day whose volume exceeds the highest down-day volume of the prior 10 sessions, occurring within a constructive base or near the 10-day/50-day MA, in a Stage 2 stock. An early entry inside the base.
- **Episodic pivot / earnings gap:** gap up ≥ 8% (configurable) on ≥ 3× average volume on a catalyst (earnings beat, guidance raise, major news), ideally from a neglected or basing stock. Valid while price holds above the gap-day low.
- **Pullback to rising MA (add-on / secondary entry):** a leader (RS ≥ 85, broke out recently) pulls back to the 10-day or 21-day EMA, or first test of the 50-day SMA, on lighter volume and bounces.
- **Undercut & rally:** price briefly undercuts a prominent prior low in the base then reclaims it on volume.

### 6.9 Red flags & exclusions (shown as warnings, some auto-exclude)
- **Earnings within 5 trading days** — warn prominently (binary gap risk). Default: still show, but mark "earnings risk".
- Extended > 25% above the 50-day SMA, or > 5% above pivot.
- Wide and loose structure: weekly ranges > 15% with closes at lows.
- Late-stage base (4th+).
- Heavy distribution in the base (multiple high-volume down days).
- **Climax run warnings** for held/extended names: largest daily or weekly gain since the advance began, exhaustion gap, 70%+ move in 1–3 weeks, highest volume ever on an up day after a long run.
- Failed breakout: closes back below pivot within 3 sessions, or falls 7–8% below entry.
- Below liquidity filters.

### 6.10 Composite score & lifecycle
**Setup Score (0–100)**, each component visible:
| Component | Weight |
|---|---|
| Trend Template & stage | 20 |
| Relative strength (rating + RS line behaviour) | 20 |
| Fundamentals grade | 20 |
| Pattern quality | 20 |
| Industry group rank | 10 |
| Accumulation (up/down volume, pocket pivots, institutional/insider) | 10 |

Then: `final = raw × regime_multiplier`, minus penalties for red flags. Letter grades: A+ ≥ 90, A ≥ 80, B ≥ 70, C ≥ 60, below 60 not shown as a recommendation.

Separately compute **Readiness**: distance from current price to pivot (%), so I can sort by "about to break out".

**Setup lifecycle state machine** (stored per setup, transitions logged):
```
WATCH ──► BASING ──► NEAR_PIVOT (within 3%) ──► BREAKOUT (confirmed) ──► EXTENDED (>5% over pivot)
             │              │                        │
             └──────────────┴──► INVALIDATED         └──► FAILED (back under pivot / stop hit)
```

### 6.11 Trade plan generator
For every setup at NEAR_PIVOT or BREAKOUT:
- **Entry:** pivot + $0.10 (or +0.1% for high-priced stocks).
- **Stop:** the tighter of (a) just below the low of the final contraction / handle / gap-day low, and (b) a maximum loss of 7–8% from entry. If the logical stop is wider than 8%, flag "risk too wide — wait for tighter setup".
- **Position size:** `shares = (account_size × risk_per_trade%) ÷ (entry − stop)`, capped so position value ≤ 25% of account (configurable). Show £/$ risk, shares, position value, % of account.
- **Targets & management:** show 2R and 3R levels; suggest taking partial profits at +20–25%; trail with a close below the 21-day EMA (aggressive) or 50-day SMA (standard); raise stop to breakeven once up 2R or +10%.
- **Display:** risk/reward ratio, and all of the above drawn on the chart as horizontal lines.

### 6.12 Optional AI analyst (Claude API)
On the stock page, a "Summarise" button sends the structured data (scores, fundamentals, recent headlines, earnings date, group rank) to the Claude API and returns a short, plain-English thesis: why it qualifies, what the catalyst might be ("N"), and the main risks. Rules: the system prompt forbids inventing numbers — it may only cite values passed in; output is labelled "AI summary"; results cached for 24 hours.

---

## 7. Background scanner & alerts

### 7.1 Schedule (all times US Eastern; must handle market holidays and early closes via the provider's market calendar)
| Job | When | What |
|---|---|---|
| Pre-market scan | 08:00–09:25 every 5 min | Gap-ups/downs ≥ 4% on heavy pre-market volume; earnings reactions; flags episodic-pivot candidates |
| Intraday watcher | Market hours, streaming | Subscribes to live data for all NEAR_PIVOT setups + watchlists (cap by plan limits). Fires breakout, buy-zone, stop and custom alerts in real time |
| Intraday sweep | Every 15 min in market hours | Snapshot scan of the full universe for unusual volume and new breakouts not on the watch list |
| End-of-day scan | 16:20 | Full pipeline on the whole universe: indicators → regime → groups → patterns → scores → lifecycle transitions → confirm/reject provisional intraday signals |
| Nightly fundamentals | 20:00 | Refresh fundamentals for recent reporters; update grades |
| Signal outcomes | 17:00 daily | Update forward returns for all past signals |
| Weekly review | Sunday 18:00 | Weekly-chart pattern scan, universe rebuild, group rank trends, weekly digest notification |

The EOD scan over ~6,000 tickers must finish in under 5 minutes. Use vectorised Polars across all tickers at once where possible, and parallelise pattern detection across worker processes.

### 7.2 Alert types
- New A/A+ setup found
- Setup moved to NEAR_PIVOT
- Breakout triggered (provisional) → Breakout confirmed at close (or "rejected: weak close/low volume")
- Pocket pivot detected
- Earnings gap / episodic pivot
- RS line new high ahead of price
- Watchlist name crosses price level / MA / volume threshold (user-defined rules)
- Stop level hit, or close below 21-EMA / 50-SMA for names I mark as "holding"
- Climax-run warning on a held name
- Market regime change (e.g., FTD, or shift to correction)
- Earnings in 5 days for a watched/held name
- Daily and weekly digest

### 7.3 Alert behaviour
- **De-duplication:** same ticker + same alert type at most once per session (configurable cooldown).
- **Priority levels:** high (breakouts, stops, regime change) push immediately; normal goes to digest.
- **Quiet hours** and per-channel toggles.
- **Rich payload:** ticker, alert type, price, % change, volume vs. average, score, pivot, stop, size, a mini chart image (render server-side to PNG for Telegram/email), and a deep link to the stock page.
- **Alert rule builder** in the UI: "When [ticker or any in watchlist] [condition] [value] then notify via [channels]".

---

## 8. Frontend: pages & features

### 8.1 Global
- **Command palette (⌘K / Ctrl+K):** fuzzy search across ticker and company name with instant results (prefix index in Redis or Postgres trigram), keyboard navigable, shows price, % change, score badge. Also commands: "Go to screener", "Add AAPL to watchlist", etc.
- **Top bar:** market regime pill (colour + state + distribution-day count), major index mini-quotes, alert bell with unread count.
- **Live connection indicator** and graceful offline/stale-data state.
- **Keyboard shortcuts:** `/` search, `j/k` move through lists, `w` add to watchlist, `[`/`]` previous/next stock in the current list (flip through a scan like a professional), `1–5` change chart timeframe.
- Light and dark themes; dark is default.

### 8.2 Dashboard (home)
- Market regime panel with the reasons (distribution days, FTD date, breadth figures, net new highs).
- Today's top setups (by score) and "about to break out" (by readiness).
- Today's breakouts and their status.
- Leading industry groups (top 10, with trend arrows) and sector rotation chart.
- Recent alerts feed (live).

### 8.3 Stock detail page — the most important screen
- Header: ticker, name, price (live), change, volume vs. average, market cap, sector/group with rank, earnings date countdown, score badge and grade, lifecycle state.
- **Main chart** (Lightweight Charts):
  - Candles + volume (volume bars coloured; above-average volume highlighted), daily/weekly/intraday toggle.
  - Overlays: 10 & 21 EMA, 50/150/200 SMA (toggleable).
  - **Pattern overlay:** draw the detected base outline, numbered contractions with their depth %, pivot line, buy-zone band, stop line, 2R/3R lines.
  - Markers: pocket pivots, earnings dates, gaps, RS-new-high dots, past signals.
  - RS line pane under price.
  - Crosshair with OHLCV legend; smooth zoom/pan; remembers my last timeframe.
- **Checklist panels:** Trend Template (8 items), Fundamentals (with quarterly EPS/sales bars), Group strength, Pattern details, Red flags.
- **Score breakdown** widget showing each component's contribution.
- **Trade plan card** with editable entry/stop (recalculates size live) and "Add to holdings" to start tracking.
- Quarterly EPS & revenue growth chart, institutional & insider activity, news headlines, AI summary (optional), my notes.
- Peers in the same group with their scores.

### 8.4 Screener
- Preset screens: "Trend Template leaders", "VCPs near pivot", "Breakouts today", "Pocket pivots today", "Earnings gap-ups", "RS leads price", "High tight flags", "Top group leaders", "Fundamentals A + RS ≥ 90".
- **Custom screen builder:** add filters on any computed field (numeric ranges, booleans, enums), save screens, and promote a saved screen into a background alert.
- Results table: virtualised, sortable, column chooser, sparkline column, score badges, inline add-to-watchlist. Click a row to open a side-panel chart preview without leaving the screener. Export to CSV.

### 8.5 Setups board
A kanban of lifecycle states (Basing → Near pivot → Breakout → Extended / Failed) showing live setup cards that move between columns in real time.

### 8.6 Watchlists & holdings
- Multiple named watchlists, drag-to-reorder, notes per name, quick chart flip-through.
- Holdings tracker (manual entry; no broker connection): entry, stop, size, live P&L in R-multiples, sell-rule warnings.

### 8.7 Alerts centre
History with filters, rule builder, channel settings, test-notification buttons.

### 8.8 Signal performance (the honesty page)
For every signal type and score bucket: count, win rate, average gain/loss, expectancy in R, median days to +20%, % that hit stop, and performance by market regime. This tells me which setups actually work in the current market.

### 8.9 Backtest lab
Pick a rule set (or a saved screen) and date range; run in a background job; show equity curve, drawdown, trade list with charts, and metrics (Section 11).

### 8.10 Settings
All methodology thresholds (Section 6 defaults, with "reset to default"), universe filters, account size and risk %, notification channels, API key status (show configured / missing — never the keys), data health panel.

---

## 9. Design direction

This is a professional trading tool used for hours at a time. Design goals: dense but calm, readable at a glance, nothing decorative that competes with the chart.

- **The chart is the hero.** On the stock page it gets the most space; everything else supports it.
- **Colour with meaning, not decoration.** Choose a distinctive, deliberate palette (not the default "black + neon green" trading-app cliché). Up/down colours must be distinguishable for colour-blind users — pair colour with shape/sign (▲/▼, +/−). Define all colours as CSS variables/tokens for both themes. Grades and lifecycle states each get a consistent colour used everywhere.
- **Typography:** pick one well-chosen sans-serif family with excellent tabular figures for all numbers (`font-variant-numeric: tabular-nums`) so columns of prices align. Clear type scale; sentence case for labels.
- **Motion:** only where it explains a change — a price cell briefly tinting on update, a setup card sliding to its new column, a toast for a new alert. Respect `prefers-reduced-motion`.
- **Copy:** plain, specific language. Buttons say what they do ("Add to watchlist", "Run backtest"). Empty states tell me what to do next. Errors say what went wrong and how to fix it.
- **Responsive:** full experience on desktop; on mobile, prioritise alerts, watchlists, the stock page and the chart.
- **Accessibility:** keyboard reachable everywhere, visible focus states, adequate contrast.
- Before building the UI (Phase 5), propose a short design plan — palette (4–6 named hex values), typeface choices, and an ASCII wireframe of the stock page — and wait for my approval.

---

## 10. Performance & smoothness requirements

- Ticker search: < 100 ms p95 (server) with debounced input (~100 ms) and prefetching.
- Stock page: first meaningful paint < 1 s; chart data for 2 years daily loads in one request; prefetch the next/previous stock in a list.
- Screener table: 6,000 rows scroll at 60 fps (virtualised); filter/sort client-side when the result set is < 10k rows.
- Live price updates batched (e.g., every 250 ms) to avoid render storms.
- API responses cached in Redis with sensible TTLs (EOD data until next close; fundamentals 24 h).
- Use React Server Components for initial page data where it helps; TanStack Query for client refreshes.
- Lighthouse performance ≥ 90 on the dashboard.

---

## 11. Backtesting & validation

- **Engine:** event-driven daily simulation using only point-in-time data (prices adjusted as of each date, fundamentals by `reported_date`, universe membership as of each date including later-delisted names).
- **Realism:** entries at the trigger price if the day's range reaches it (or next open if gapped above the buy zone → skip), slippage (default 0.1%), commissions configurable, max concurrent positions, position sizing per Section 6.11.
- **Exit rules:** stop loss, trailing MA exits, time stop (e.g., no progress after 15 sessions), partial profit at +20–25%.
- **Metrics:** CAGR, max drawdown, win rate, average win/loss, payoff ratio, expectancy (R), profit factor, Sharpe/Sortino, exposure %, number of trades, performance by market regime and by pattern type.
- **Robustness:** walk-forward / out-of-sample split; parameter sensitivity heatmap (e.g., volume threshold × max final contraction) so I can see if results depend on a single lucky setting.
- Clearly label all backtest results as hypothetical.

---

## 12. Testing & quality

- **Backend:** pytest. Unit tests for every indicator (compare against hand-calculated fixtures), every pattern detector (synthetic price series that should and should not match, plus a few real historical examples stored as fixtures), regime logic (known FTD/distribution-day dates), position sizing, and lifecycle transitions. Property-based tests (Hypothesis) for indicator edge cases (gaps, NaNs, short histories).
- **Lookahead guard test:** run the EOD pipeline for a date with future rows present in the DB and assert identical output to running it with future rows removed.
- **Frontend:** Vitest + React Testing Library for components; Playwright end-to-end tests for search → stock page → add to watchlist → alert rule.
- **Static checks:** Ruff + mypy (strict) for Python; ESLint + TypeScript strict + Prettier for the web.
- **CI:** GitHub Actions running all of the above on every push.
- **Observability:** structured JSON logs, job run history table with durations and failures, an admin "System health" page (last scan time, provider status, queue depth, websocket connections).

---

## 13. Security & configuration

- `.env.example` documenting every variable: `MASSIVE_API_KEY`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `ALPACA_FEED` (iex|sip), `FMP_API_KEY`, `FINNHUB_API_KEY`, `SEC_USER_AGENT`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `RESEND_API_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `DATABASE_URL`, `REDIS_URL`, `SESSION_SECRET`, provider selection vars.
- Secrets never sent to the browser; frontend talks only to our API.
- Rate limiting on auth endpoints; HTTPS in production; secure, HTTP-only cookies.
- Respect each data provider's terms (no redistribution of their raw data).

---

## 14. Configurable defaults (seed the `settings` table with these)

| Key | Default |
|---|---|
| min_price | 10 |
| min_avg_dollar_volume_50d | 20,000,000 |
| min_market_cap | 1,000,000,000 |
| rs_rating_min | 70 |
| pct_above_52w_low_min | 30 |
| pct_below_52w_high_max | 25 |
| ma200_uptrend_lookback_days | 21 |
| eps_growth_q_min | 25 |
| sales_growth_q_min | 20 |
| eps_growth_annual_min | 25 |
| roe_min | 17 |
| top_groups_preferred | 40 |
| vcp_min_contractions / max | 2 / 6 |
| vcp_contraction_ratio_max | 0.7 |
| vcp_final_contraction_max_pct | 10 |
| flat_base_max_depth_pct | 15 |
| cup_max_depth_pct | 33 |
| handle_max_depth_pct | 12 |
| breakout_volume_min_pct_of_avg | 140 |
| buy_zone_max_pct_above_pivot | 5 |
| near_pivot_pct | 3 |
| earnings_gap_min_pct | 8 |
| earnings_gap_min_volume_multiple | 3 |
| earnings_warning_days | 5 |
| distribution_day_min_drop_pct | 0.2 |
| ftd_min_gain_pct | 1.25 |
| max_stop_loss_pct | 8 |
| account_size | (ask me) |
| risk_per_trade_pct | 1.0 |
| max_position_pct | 25 |
| alert_cooldown_minutes | 390 (one session) |
| regime_multipliers | 1.0 / 0.8 / 0.5 |

---

## 15. Build phases & acceptance criteria

Build in this order. Do not start a phase until the previous phase's acceptance criteria pass. Pause and show me the result at the end of each phase.

**Phase 0 — Scaffold**
Monorepo (`/web`, `/api`, `/infra`), Docker Compose with all services, CI, linting, `CLAUDE.md`, `.env.example`, health-check endpoints.
✅ `make dev` brings everything up; the web app shows a placeholder page that calls the API health endpoint.

**Phase 1 — Data foundation**
Provider adapters, DB schema & migrations, universe builder, historical backfill with progress UI, EOD update job, data-quality checks.
✅ Universe loaded; ≥ 2 years of daily bars for all universe tickers; daily update runs automatically; data-health panel shows no critical issues.

**Phase 2 — Indicators, regime, RS, groups**
All indicators, RS rating & RS line, market regime engine, industry group ranking, stage analysis, Trend Template.
✅ Tests pass; for 5 tickers I name, the Trend Template values match what I see on a charting platform; regime engine reproduces known FTD and distribution-day dates on historical SPY/QQQ data.

**Phase 3 — Fundamentals & patterns**
Fundamentals ingestion & grading; swing detection; VCP, cup-with-handle, flat base, HTF, 3-weeks-tight, ascending base, pocket pivot, earnings gap detectors with quality scores.
✅ Detectors pass synthetic tests and correctly identify at least a handful of well-known historical breakouts I'll provide; false-positive rate reviewed with me on a random sample of 20 detections.

**Phase 4 — Scoring, lifecycle, trade plans, scanner**
Composite score, lifecycle state machine, trade plan generator, scheduled EOD scan, signal logging and outcome tracking.
✅ Full EOD scan completes in < 5 min; setups table populated with explainable scores; signals logged.

**Phase 5 — Core UI**
Design plan approval first. Then: command palette search, stock detail page with full chart overlays, dashboard, screener with presets and builder, watchlists.
✅ Performance targets in Section 10 met; I can search any ticker and see a complete analysis with patterns drawn on the chart.

**Phase 6 — Real-time & alerts**
Streamer service, intraday watcher with time-of-day volume projection, pre-market scan, alert rules, de-dupe, all notification channels, setups board with live movement, holdings tracker.
✅ A simulated breakout (replay mode using recorded intraday data) triggers in-app, push and Telegram alerts within 2 seconds; provisional signals are confirmed or rejected at the close.

**Phase 7 — Backtest lab, signal performance, AI summary, deployment**
Backtest engine and UI, signal performance page, optional Claude AI summaries, production deployment, backups.
✅ Backtest of the default ruleset over ≥ 5 years produces a full report; app deployed and reachable over HTTPS; nightly DB backups configured.

**Phase 8 — Polish & extras (as I prioritise)**
Weekly digest email, PWA install & mobile polish, UK/LSE market support (if the fundamentals plan covers it), options-flow or short-interest data, multi-user support.

---

## 16. Glossary (for clarity in code and UI)

- **Pivot / buy point:** the price level that, when crossed, signals a breakout from a base.
- **Base:** a period of consolidation after an advance.
- **Contraction:** a single swing-high-to-swing-low pullback within a base.
- **Buy zone:** pivot to +5%; beyond it the stock is extended.
- **RS Rating:** a 1–99 percentile rank of 12-month weighted price performance vs. all stocks.
- **RS line:** stock price divided by the S&P 500 (SPY).
- **Distribution day:** an index decline on higher volume, signalling institutional selling.
- **Follow-through day:** a strong, higher-volume index gain on day 4+ of a rally attempt, signalling a possible new uptrend.
- **R / R-multiple:** profit or loss expressed in units of initial risk (entry − stop).
- **Stage 2:** a stock in a confirmed uptrend above a rising 30-week moving average.

---

*End of specification. Claude Code: start by confirming you've read everything, then ask me the Section 0 questions before beginning Phase 0.*
