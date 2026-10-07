# Breakout: guide for Claude Code

A growth-stock scanning, scoring and alerting platform (CAN SLIM / Weinstein stages / Minervini
Trend Template + VCP / pocket pivots / episodic pivots). **The full build spec is
[`docs/spec.md`](docs/spec.md). It is the source of truth; read it before starting any phase.**

## Current phase

- **Phase 0 (Scaffold):** complete and approved.
- **Phase 1 (Data foundation):** built and approved. Its live acceptance run (real universe,
  ≥ 2 years of bars, clean data health) still needs the data hosts (see Gotchas) or a run on
  the owner's machine.
- **Phase 2 (Indicators, regime, RS, groups):** built and approved, including the regime
  judgement calls in `market/regime.py`'s docstring. Its live acceptance also needs real data:
  the owner names 5 tickers + their charting platform to compare Trend Template values on
  `/admin/inspect`, and the follow-through / distribution-day dates the regime engine should
  reproduce.
- **Phase 3 (Fundamentals & patterns):** built and approved. Its live acceptance needs real
  data: the owner's well-known historical breakouts (`make patterns date=… symbols=…`) and a
  reviewed random sample of 20 detections on `/admin/patterns`.
- **Phase 4 (Scoring, lifecycle, trade plans, scanner):** built and approved. Its acceptance
  (EOD < 5 min: 52 s for 6,000 stocks; explainable setups; signals logged) was checked on a
  synthetic market; the live run needs real data.
- **Phase 5 (Core UI):** built and approved (⌘K search, stock page with the full chart
  overlays, dashboard, screener with presets/builder/saved screens, watchlists, phone layout,
  Playwright e2e). Spec §10 targets measured on a synthetic
  6,000-stock market (production build): search p95 52 ms, stock page first paint ~110 ms with
  the chart drawn at ~640 ms, screener scroll 60 fps, dashboard Lighthouse 95 mobile / 100
  desktop. Deferred by the owner: intraday chart, holdings, alert bell, screen → alert and the
  live setups board (Phase 6); news and the AI summary (Phase 7 / when keys arrive).
- **Phase 6 (Real-time & alerts):** built and approved, with its defaults confirmed (option A:
  the Alpaca adapter, switched on by keys in `.env`; IEX volume is scaled and provisional until
  the close). Acceptance on a replayed breakout through the full path (watcher → Redis → API
  WebSocket → Next.js proxy → browser; SMTP to Mailpit): toast 73–158 ms and email 129–138 ms
  after the triggering print; the close confirmed it (and rejects a fading one,
  `tests/test_watcher.py`). The live run needs Alpaca keys.
- **Phase 7 (backtest lab, signal performance, AI summary, deployment, backups):** built on the
  recommended defaults (the owner said to go ahead without reviewing the plan), **awaiting the
  owner's approval**. Acceptance on a synthetic 5-year, 600-stock market: the default ruleset's
  full report (18 trades; the B-grade variant 95) and the 30-cell sensitivity heatmap (tape in
  7.5 min on 3 workers); signal performance over a year of replayed nightly scans (9,303
  signals); 9 Playwright journeys; the production stack served over HTTPS locally (Caddy, `DOMAIN=localhost`);
  nightly backups with a restore test in CI. The real report needs real data; the deployment
  needs the owner's server, domain and Let's Encrypt email.
- **Next: Phase 8 (polish & extras).** No Phase 8 code until the owner approves Phase 7 and a plan.

## Owner decisions (answers to spec §0.3)

| Question | Answer |
|---|---|
| Data providers | **No API keys yet.** Use keyless sources: Nasdaq Trader symbol directory (universe), SEC EDGAR XBRL company facts (fundamentals, point-in-time via filing dates), yfinance for prices (**dev only**, never in production paths). Massive/Alpaca/FMP/Finnhub adapters switch on via `*_PROVIDER` env vars when keys arrive. |
| Account defaults | "You choose": **$100,000, 1% risk per trade, 25% max position**. Seed into `settings`; editable. |
| Notifications | **In-app + email** (Resend or SMTP). No browser push or Telegram for now. |
| Markets | **US large/mid caps**: NYSE/NASDAQ/AMEX common stock, price ≥ $10, ADV50 ≥ $20M, market cap ≥ $1B. Small-cap mode is a setting. |
| Auth | Single user, **email + password** (`make create-user`), built in Phase 1. |
| ADRs | **Included** in the universe (stored as `type=adr`); the `include_adrs` setting filters them at scan time. |
| Phase 2 defaults | Build Phase 2 before the Phase 1 live acceptance; overall market = **weaker of SPY and QQQ** (+IWM in small-cap mode); follow-through threshold **1.25%** (spec; IBD now uses ~1.7%, a setting); industry groups from **SEC SIC codes**. |
| Phase 4 defaults | Setup Score: trend 20, RS 20, fundamentals 20, pattern 20, group 10, accumulation 10; a part without data (e.g. no Fundamentals Grade) is left out and the rest scaled up; × regime 1.0 / 0.8 / 0.5; red-flag penalties extended, late stage, climax −10, wide-and-loose, distribution −5, earnings risk 0; A+ ≥ 90, A ≥ 80, B ≥ 70, C ≥ 60. Breakouts confirmed **at the close** (≥ 140% volume, close in the top third) until real-time data (Phase 6). Outcomes measured from the signal session's close, plus R from the plan. **Plain admin pages** until the Phase 5 design. |
| Phase 6 | **Option A**: build the Alpaca live adapter now (free keys later switch it on); IEX volume stays provisional until the close. Alerts in-app + email (Resend/SMTP; Mailpit in dev); digests 17:30 ET daily and Sunday 18:00 ET weekly; cooldown 390 min (once a session); setup alerts on stocks you don't hold or watch need grade ≥ A (a setting). Confirmed with the results: stream cap 30 (holdings → near pivot → broken out → rule targets → basing → watchlists); holdings warnings: stop, close below the 50-day (high) / 21-day (normal), breakeven at 2R or +10%, profit zone 20–25%, earnings within 5 sessions. |
| Phase 7 | Owner: "go ahead" on the recommendations. Hosting: one Docker VPS behind Caddy (automatic HTTPS); the owner supplies server, domain and Let's Encrypt email, and deploys with `make deploy` / `docs/deploy.md`. Backups: nightly `pg_dump` 02:30 ET, 14 daily + 8 weekly, optional S3-compatible off-site copy. Backtest defaults: $100k, 1% risk, 25% max position, max 10 positions, slippage 0.1%, $0 commission, buy-stop at the trigger the next session (fill at the open inside the buy zone, skip above it), exits: stop, close below the 50-day (21-day optional), time stop < +5% after 15 sessions, sell ⅓ at +20%, breakeven at 2R/+10%; 70/30 in/out-of-sample; sensitivity breakout volume 100–200% × VCP final contraction 6–14%. Survivorship bias (no delisted names in free data) is labelled in every report. AI summary built now, off until `ANTHROPIC_API_KEY` (default model `claude-opus-5-5` via `ANTHROPIC_MODEL`; only numbers from the data; cached 24 h per stock and session). Settings page included. Judgement call to confirm: unconfirmed breakouts (volume or close position short at the close) are **sold at the close by default** in backtests (`backtest_sell_unconfirmed`), so the heatmap's volume axis means something; the lab can switch it off. |
| Phase 3 defaults | Grade points/cutoffs as in `fundamentals/grade.py` (EPS growth 25, acceleration 10, sales 15, 3-year EPS 20, ROE 10, margins 10, accumulation 10, insider cluster +5; A ≥ 80, B ≥ 65, C ≥ 50, D ≥ 35); insider Form 4 now, **13F deferred** to a paid provider; next earnings date **estimated** from last year; review charts **server-rendered** (matplotlib). |

## Non-negotiables (spec §0, §2)

- **No lookahead bias.** A calculation for date D uses only data available at D's close (or that
  intraday moment). Fundamentals are keyed by `reported_date`. A lookahead guard test is required.
- **Explainable.** Every score is a sum of named components; the UI shows each rule pass/fail with
  the actual numbers.
- **Test the maths first.** Every indicator and pattern detector gets hand-verified fixtures
  before it is used anywhere.
- **Secrets** live only in `.env`, read only by the backend. The browser talks only to our API.
- **Never place trades.** Screen, score, alert, plan.
- **Every threshold is configurable** (the `settings` table, seeded with spec §14 defaults).
- Every setup ships with a stop and a size; every signal is logged and its outcome tracked.

## Layout

```
api/                 Python 3.12 · FastAPI · uv (non-packaged app; run commands from api/)
  app/main.py        FastAPI app; every route under /api; CSRF header check on writes
  app/cli.py         operator commands (create-user, seed, universe, backfill, eod-update, …)
  app/core/          config (env), logging, db, redis, heartbeat, lifecycle, security (auth),
                     calendar (NYSE sessions), rate_limit (Redis token bucket),
                     jobs (job_runs tracking + Redis locks), queue (enqueue from the API)
  app/models/        SQLAlchemy models (one module per area)
  app/settings/      AppSettings (every spec §14 threshold, typed + bounded) and its store
  app/providers/     base.py interfaces + registry; nasdaq_trader, sec_edgar (+ sec_facts,
                     sec_filings parsers), yfinance_dev
  app/data/          classify (security types), universe, bars (bulk upserts), backfill,
                     eod_update, market_cap, quality (checks + issue register), loaders
                     (read: connectorx → Polars; write: Polars → ADBC binary COPY),
                     jobs (entry points for worker/CLI)
  app/indicators/    pure Polars indicator functions (MAs, ATR, volatility, volume, 52-week
                     ranges, relative strength, stage); compute.py applies them all
  app/fundamentals/  ingest (statements, earnings calendar, insider trades → DB), earnings
                     (next-date estimate), grade (Fundamentals Grade A-E, pure), scan (grade
                     every stock as of a date)
  app/patterns/      bars (NumPy arrays + weekly bars), swings (ZigZag), context (shared
                     building blocks and score parts), bases (VCP, flat, cup, ascending, HTF),
                     weekly (3WT), events (pocket pivot, earnings gap), detect (all), scan
                     (many stocks → `patterns` table), chart (review PNG)
  app/scoring/       trend_template.py (8 checks + checklist text), setup_score.py (Setup
                     Score), red_flags.py, lifecycle.py (stage machine), triggers.py
                     (pullback, undercut & rally)
  app/risk/          trade_plan.py (entry, stop, size, targets, management)
  app/intraday/      session (phases, minutes), day (DayState per print → minute bars), volume
                     (time-of-day curve, projection), store (minute bars, learned curve), plan
                     (what to stream + the scans' universe), watcher (the streamer's engine),
                     live (what counts as live, Redis keys), service (feed building, replay CLI)
  app/alerts/        engine (drafts → alerts: who, once, channels; live:events pub/sub), email
                     (Resend/SMTP senders), render (alert/digest emails, mini chart), delivery
                     (send queued emails, digests), eod (close confirmation, signals → alerts,
                     holdings' sell rules), screens (saved screen → new-match alerts), jobs
  app/market/        regime.py (distribution days, rally/FTD state machine), breadth.py
  app/groups/        classification.py (SIC → groups/sectors), industry_rank.py
  app/backtest/      context (point-in-time inputs per stock), tape (stage 1: the EOD pipeline
                     replayed per stock → candidate tape, Parquet under BACKTEST_DIR, cached by
                     settings hash), engine (stage 2: the pure daily portfolio simulation),
                     metrics, reports (report JSON: samples, heatmap, breakdowns), jobs
  app/ai/            summary.py: the stock page's AI summary (Claude API; numbers check)
  app/scanner/       snapshot.py: indicators + Trend Template for every stock on a date
                     (screener, setups); screener_rows.py: the screener snapshot + filter
                     matching; intraday_scan.py: the per-print rules; live_scans.py:
                     pre-market and sweep rules; eod_scan.py: the analytics pipeline (full / stale /
                     incremental);
                     daily.py: per-session stages in order (detection.py: grades + patterns;
                     setups.py: load/store around evaluate.py, the pure per-stock setup rules;
                     outcomes.py); universe_filter.py: point-in-time liquidity filter;
                     performance.py: signal performance statistics (pure)
  app/api/routes/    health, auth, settings, admin, market (+ index quotes), stocks (summary,
                     peers, note, watchlist membership), stock_chart (chart series + overlay),
                     search (ranked trigram search), screener (cached columnar snapshot + saved
                     screens), watchlists, patterns (review), setups (setups, signals), alerts
                     (history, rules, tests, status), holdings, live (WebSocket /ws, /live,
                     intraday bars), backtests (runs, report, trade charts), performance, ai
                     (summary), settings (+ /settings/keys: which keys are set, never values)
  app/worker.py      arq worker: `arq app.worker.WorkerSettings`
  app/scheduler.py   APScheduler (US/Eastern): `python -m app.scheduler`
  app/streamer.py    live feed: `python -m app.streamer` (STREAM_PROVIDER alpaca | replay | none)
  alembic/           async migrations; URL comes from app settings, never alembic.ini
  tests/             pytest; fakes.py has in-memory providers; fixtures/providers/ has
                     real-shaped directory and SEC files; e2e_seed.py seeds the web e2e database
web/                 Next.js 16 App Router · React 19 · TS strict · Tailwind 4 · pnpm
  proxy.ts           sends signed-out visitors to /login (cookie presence only)
  app/(app)/         signed-in pages with the top bar: / (dashboard), /stocks/[symbol],
                     /screener, /watchlists, /live, /holdings, /alerts, /performance,
                     /backtests, /backtests/[id], /settings;
                     /admin/{status,data,inspect,patterns,setups,signals}
  app/login/         sign-in page (no top bar)
  app/globals.css    design tokens (approved Phase 5 palette) for dark (default) and .light
  components/        UI components (+ colocated *.test.tsx): shell/ (top bar, ⌘K palette,
                     shortcuts, toasts, live-provider), ui/ (badges, button, section,
                     stock-link), stock/ (chart, intraday chart, base overlay, panels, score +
                     plan), dashboard/, screener/, watchlists/, alerts/ (bell, alerts centre),
                     live/ (live board), holdings/, backtests/ (lab: form, runs, report,
                     equity + trade charts), performance/, settings/; admin pages: admin/,
                     inspect/, patterns/, setups/
  lib/api.ts         typed API client: CSRF header on writes, 401 → /login, response types
  lib/format.ts      number/date/duration formatters, safeNext() redirect guard
  lib/screener.ts    screener field catalogue, presets, filters, sorting keys, CSV (pure)
  lib/sizing.ts      client trade-plan sizing (mirrors risk/trade_plan.py fixtures)
  lib/live.ts        the /api/ws client (reconnect, ping); lib/positions.ts live P&L in R
  lib/theme.ts       theme cookie + tokens for canvas code; stages.ts, keys.ts, use-*.ts
  lib/server-api.ts  server components: fetch from the API with the visitor's cookie, prefetch
                     into a QueryClient for HydrationBoundary (components/*/queries.ts share keys)
  stores/            Zustand: list.ts (the list `[`/`]` flip through), toast.ts, live.ts
                     (quotes, setup events, scans from the socket)
  test-utils.tsx     renderWithClient, mockApi (fetch stub keyed by "METHOD /path")
  e2e/ + playwright.config.ts   Playwright journeys against a production build (`make e2e`)
infra/docker-compose.yml   web, api, worker, scheduler, streamer, migrate (one-shot), postgres, redis,
                           mailpit (dev mail catcher, UI :8025)
infra/docker-compose.prod.yml + Caddyfile   production: Caddy (HTTPS, the only public service)
                           in front of the prod images, migrate (+ seed), postgres, redis, backup
infra/backup/              backup image (pg_dump nightly, rotation, rclone S3 copy, verify,
                           restore) and test-restore.sh (the CI restore test)
docs/spec.md         the build specification; docs/deploy.md: deploying and backups
.github/workflows/ci.yml   api (ruff, mypy, pytest + services), web (lint, prettier, tsc,
                           vitest, build), e2e (Playwright), deploy (backup restore test, prod
                           compose + Caddyfile validation), prod Docker image builds
```

The spec's planned backend modules (`providers/`, `data/`, `indicators/`, `patterns/`, `market/`,
`fundamentals/`, `groups/`, `scoring/`, `risk/`, `scanner/`, `alerts/`, `backtest/`, `ai/`) are
added under `api/app/` as their phases arrive. Follow spec §4 for names.

## Commands

Only Docker is required; `make` targets run inside containers (except `make e2e`).

| Command | What it does |
|---|---|
| `make dev` | Build and start everything, wait for healthy. Web :3000, API :8000 (`/api/docs`) |
| `make down` / `make logs` / `make ps` | Stop (keeps volumes) / follow logs / status |
| `make test` | API pytest (incl. integration) + web vitest |
| `make lint` | ruff + ruff format --check + mypy strict; eslint + prettier --check + tsc |
| `make fmt` | Auto-fix formatting in both codebases |
| `make migrate` / `make migration m="..."` | Apply / autogenerate an Alembic migration |
| `make seed` | Insert default settings (never overwrites changes) |
| `make create-user email=…` | Create the login user (prompts for a password) |
| `make universe` | Rebuild the universe, then backfill new tickers |
| `make backfill [years=10] [symbols=A,B] [force=1]` | Load history (resumable) |
| `make eod-update [date=YYYY-MM-DD]` | Latest session's bars + quality checks |
| `make data-quality` | Quality checks only |
| `make scan-now [full=1] [date=…]` | Recompute analytics from stored prices, then grades + patterns (`full=1` after changing stage or Trend Template settings) |
| `make fundamentals [full=1] [symbols=A,B]` | Statements, earnings dates, insider trades (nightly mode by default) |
| `make patterns [date=…] [symbols=A,B]` | Grades + pattern detection as of a date; with symbols, lists the detections (acceptance checks) |
| `make setups [date=…]` | Scores, lifecycle and signals for every session not processed yet (re-scores the latest after a settings change) |
| `make outcomes` | Update signal outcomes (also runs after each scan and at 17:00 ET) |
| `make digests` | Send the daily/weekly digest if due (the scheduler checks every 5 minutes) |
| `make replay file=… [speed=60] [start=09:55] [close=0]` | Replay recorded minute bars through the watcher (alerts, emails, live board), then the close |
| `make export-recording date=… out=… [symbols=A,B]` | Save a stored session's minute bars as a recording |
| `make volume-curve` | Learn the time-of-day volume curve from stored minute bars (nightly at 20:30 ET) |
| `make backtest [start=…] [end=…] [sensitivity=1]` | Backtest the default rules (the web lab does the same; the worker runs it) |
| `make backup-test` | The backup image's restore test on a scratch database (needs `make dev`) |
| `make e2e` | Playwright end-to-end tests, run natively: needs `make dev` (Postgres/Redis), uv, pnpm and `pnpm exec playwright install chromium`; seeds its own `breakout_e2e` |
| `make shell-api` / `make shell-db` | bash in api container / psql |

Production (on the server, `docs/deploy.md`): `make deploy` (build + up behind HTTPS),
`prod-up` / `prod-down` / `prod-logs [service=…]` / `prod-ps`, `prod-cli cmd="…"` (any
`app.cli` command), `prod-create-user email=…`, `backup-now`, `backup-list`,
`backup-verify [file=latest]`, `restore file=…|latest` (asks first; `yes=1` skips).

Running natively (faster loop, needs `make dev` for Postgres/Redis on localhost):
- API: `cd api && uv sync && uv run pytest` (`-m "not integration"` needs no services;
  `-m network` runs the live smoke tests against the real data sources, never in CI),
  `uv run ruff check . && uv run ruff format --check . && uv run mypy`
- Web: `cd web && pnpm install && pnpm lint && pnpm format:check && pnpm typecheck && pnpm test && pnpm build`

## Conventions

- **Python:** async everywhere; Pydantic v2 models for every request/response; `mypy --strict`
  clean; ruff (line length 100). Pytest runs with `filterwarnings = error`. Log with
  `get_logger(__name__)` and event-style names (`"scheduler.startup"`) plus key/value context.
- **Two kinds of settings:** `app.core.config.Settings` is environment/infrastructure (add new
  env vars there **and** to `.env.example`; secrets are `SecretStr`). `app.settings.AppSettings`
  holds every user-tunable threshold (spec §14) with bounds and a description; load it with
  `await store.load(session)`, never hardcode a threshold. Settings are instance-wide for now.
- **Providers:** ingestion code only talks to `providers/base.py` interfaces via
  `providers.registry`. A paid adapter is one new module plus a registry branch. Every adapter
  rate-limits through `RateLimiter` (shared across processes) and retries via `providers/http.py`.
- **Jobs:** new background work goes in `app/data/jobs.py` (or a sibling for later phases),
  wrapped in `track_job` (job_runs row) and `job_lock`; register it on the worker, the CLI and
  (if scheduled) the scheduler. Jobs that write tickers/bars share the `ingest` lock.
- **Data quality:** checks are pure functions over Polars frames. Severity `critical` is only
  for problems that make today's scan untrustworthy (benchmarks, EOD not run, universe empty or
  widely stale); single-ticker problems are `warning`/`info`.
- **Point in time:** prices are split-adjusted as of today (`daily_bars`); `corporate_actions`
  recovers as-traded prices. Share counts are keyed by `filed_date`. A new split re-fetches the
  ticker's whole history.
- **Indicators:** pure functions over frames sorted by (ticker_id, date), computed per ticker
  with `.over("ticker_id")`, backward-looking only, never NaN (zero denominators → null). Each
  gets hand-calculated fixtures (working in comments) plus Hypothesis properties
  (no lookahead, tickers never mix). Add new stored columns to `INDICATOR_COLUMNS`, the
  `IndicatorDaily` model and a migration together.
- **Analytics pipeline (`scanner/eod_scan.py`):** full rebuild when nothing is computed yet or
  > 20% of tickers are stale; otherwise recompute stale tickers' whole history (new listings,
  split re-fetches), then incremental new sessions with a 600-session warm-up. RS Rating is
  ranked per date across stocks (common + ADR, not benchmarks). Breadth and group ranks are
  stored per date; the regime history is recomputed every run so it follows the settings.
  Tests prove incremental == full rebuild and the spec §12 lookahead guard.
- **Fundamentals:** statements are versioned by filing date (`reported_date`); never overwrite a
  version, add one. Read them through `grade.as_known(rows, as_of, splits)` (latest version per
  period by then, EPS adjusted for later splits). The grade is a pure function; its components
  carry the numbers shown in the UI. Earnings release semantics (8-K item 2.02) stay inside the
  SEC adapter (`CompanyFilings.releases`).
- **Patterns:** detectors are pure functions of `Bars` cut at the as-of session; never pass
  data after it. Each detector documents its hard rules in its docstring; everything else is
  score. Every threshold is a setting. New detectors get a textbook synthetic chart, a
  near-miss per rule, and join the no-lookahead/scale property tests (`tests/test_patterns.py`).
  Bases report while forming or within 3 sessions of a breakout; the scan retires rows that
  stop matching (`failed` / `expired`); an older as-of run never overwrites a newer row.
- **Setups (`scanner/evaluate.py` rules, `scanner/setups.py` storage):** one active setup per
  stock (unique partial index: update a closing setup *before* inserting its successor).
  Sessions are processed forward only and in order (`scan_progress`; catch-up capped at 10), so
  a breakout is judged on its own day. Re-running the latest session restores each setup from
  `setups.previous` (snapshot before that session) and deletes what the session recorded
  (`setup_transitions.recorded_on`, setups first seen that day). Signals are immutable and
  unique per (date, type, stock); a re-run only re-links `setup_id`. New signal types go in
  `SIGNAL_LABELS`. Trade plans are recomputed daily before a breakout and frozen at it.
  Outcomes are % from the signal session's close; R only for breakouts (other signals fire
  before the entry is reached).
- **Intraday (Phase 6):** every rule runs on the feed's clock (event timestamps, never the wall
  clock), so a replay behaves like a live day. Volume is projected with the time-of-day curve
  (`intraday/volume.py`: standard until ≥ 20 full sessions are stored, then learned); a partial
  (IEX) feed's volume is scaled by `partial_feed_volume_share_pct` and said so. Intraday
  breakouts are *provisional*: logged as `breakout_provisional` signals, settled at the close by
  `alerts/eod.confirm_provisional`. The pure rules are `scanner/intraday_scan.py` (per print)
  and `scanner/live_scans.py` (pre-market, sweep); the watcher only feeds them. "Live" means
  newer than the latest processed close (`intraday/live.py`).
- **Alerts:** everything goes through `alerts/engine.raise_alerts` (drafts → per-user alerts;
  grade filter for setup kinds on stocks you don't follow; Redis cooldown dedupe; delivery
  recorded per channel in words; in-app pushed on `live:events`). Emails are sent by the caller
  (`alerts/delivery.send_alert_emails`), never inline in the engine. EOD alerts are raised for
  the latest session only, once per signal (an alert row links it). New alert kinds get a
  label in `KIND_LABELS`. The WebSocket batches 250 ms windows and sends alerts only to their
  user.
- **Backtests (Phase 7):** two stages. Stage 1 (`backtest/tape.py`) walks each stock forward
  session by session, emulating the EOD pipeline's database state (pattern ids, retirement,
  setups reloaded from stored columns) with the live code (`detect_variants`, `evaluate`), so a
  setup in the tape is exactly what the nightly scan would have said that day; tests prove it
  matches the pipeline session by session and that no input reaches past the session. Cells are
  sensitivity variants (VCP final contraction × breakout volume); identical cells are evaluated
  once. Stage 2 (`backtest/engine.py`) is a pure daily simulation over the tape (buy-stop next
  session, exits, sizing, costs). Tapes are cached (Parquet + `backtest_tapes`) by a settings
  hash that ignores the BACKTEST/ALERTS/INTRADAY/DATA categories; changing a pattern or scoring
  setting builds a new tape. Every report is labelled hypothetical and states survivorship bias.
  One run at a time per user; runs go through the worker (`backtest` job, 8 h limit).
- **AI summary:** `ai/summary.py` only; the key is read server-side and never logged. The data
  sent is the stock page's own facts (`routes/ai.stock_facts`); the reply must use only numbers
  in that data (`unverified` checks it, one regeneration, leftovers shown on the page). Cached
  24 h per stock and session in Redis; off (503 with the fix in words) without a key.
- **Deployment:** production config lives in `infra/docker-compose.prod.yml` (it sets APP_ENV,
  DATABASE_URL, REDIS_URL, PUBLIC_URL, BACKTEST_DIR itself; `.env` holds secrets and choices).
  Only Caddy publishes ports. New env vars for production go in `.env.example`'s production
  section; new services get log rotation (`*logging`) and a restart policy. The backup service
  writes `last.json`, which `/api/health/ready` reads (BACKUP_STATUS_FILE).
- **Regime definitions** are in `market/regime.py`'s docstring (DD count restarts at a
  follow-through; the below-50-day rules apply only after the index reclaimed its 50-day since
  the follow-through). Every state change and day carries human-readable reasons.
- **Numerics (from Phase 2):** Polars first, NumPy second, pandas only where a library forces it.
- **Reads into Polars:** jobs and the pipeline use `read_frame` (connectorx: fast for bulk, but a
  new connection per call, ~20 ms). Request handlers use `query_frame(session, sql)` (the pooled
  connection; same frame shape, tested against connectorx). Functions shared by both take a
  `read` callable (see `fundamentals.scan.load_grade_inputs`).
- **Tests:** `tests/conftest.py` points `DATABASE_URL` at `<db>_test` and `REDIS_URL` at Redis
  DB 15 *before* the app is imported, so tests never touch dev data. Fixtures: `db` (empty,
  migrated database), `clean_redis`, `client` (sends the CSRF header), `user`, `signed_in`.
  Mark DB/Redis tests `@pytest.mark.integration`. Use `tests/fakes.py` providers instead of the
  network; live calls belong in `test_providers_live.py` (`network` marker).
- **Migrations:** one Alembic revision per schema change; never edit an applied revision.
  TimescaleDB internal schemas are excluded from autogenerate (`alembic/env.py`).
- **Web:** Server Components by default, `"use client"` only where needed. All API calls go
  through `lib/api.ts` (`api.get/post/patch/put/delete`) and TanStack Query. Component tests use
  `test-utils.tsx`; journeys go in `e2e/`.
  - **Design (approved Phase 5 plan):** colours only from the tokens in `app/globals.css` (Ink,
    Slate, Paper; Rise blue = up/pass/breakout, Fall orange = down/fail/stop, Tide teal = pivot,
    buy zone, focus); IBM Plex Sans; dark by default, light via the `breakout_theme` cookie
    (`lib/theme.ts`; canvas code reads tokens with `token()` and redraws on theme change).
    Numbers use `tabular`. Colour always comes with a symbol or label (▲/▼, ✓/✕, stage icons):
    use `Change`, `GradeBadge`, `StageBadge`, `StatusMark`. Validate new colours with the
    dataviz palette script (protan/deutan, contrast) before adding tokens.
  - **Lists and keys:** rows that open a stock use `StockLink` (sets the `[`/`]` list in
    `stores/list.ts`, marks the row for `j`/`k`). Global keys live in `shell/shortcuts.tsx`; a view
    with its own key handling (the virtualised screener) listens in the capture phase and calls
    `preventDefault()` so the globals stand down. Big tables are virtualised (TanStack Table v8 +
    Virtual) and sort/filter client-side; the screener snapshot is one cached request.
  - **First paint:** pages where it matters (dashboard, stock page) are server components that
    prefetch their above-the-fold queries (`lib/server-api.ts` → `HydrationBoundary`), so they
    render with data and nothing shifts (CLS 0). Define each page's queries once
    (`components/<area>/queries.ts`, plain modules) and use those keys in the components. Don't
    inline big payloads (the chart series): they slow the first paint on slow connections.
  - **Charts:** Lightweight Charts v5 on the stock page (`stock/price-chart.tsx`, base overlay as
    a series primitive); dashboard bars are plain SVG/divs in a table. State that must survive
    reloads (chart range, MAs) goes in `localStorage`, read after mount.
- **Copy:** plain and specific; errors say what went wrong and how to fix it.
- **Git:** small commits with clear messages; run lint, types and tests before each commit.
  Develop on the branch named for the session; never push elsewhere without asking.

## How the pieces talk

- Browser → Next.js (`/api/*` rewrite) → FastAPI. `next.config.ts` is evaluated **at build time**
  for production, so `API_URL` is a build arg in `web/Dockerfile`.
- Auth: `POST /api/auth/login` sets an HTTP-only `breakout_session` cookie holding an opaque
  token; Redis maps an HMAC of it to the user (30-day sliding expiry). Every route except
  health and login depends on `current_user`. Unsafe methods need `X-Requested-With: breakout`.
- Data flow: scheduler (clock) → arq queue → worker → `app/data/jobs.py` → providers → Postgres.
  EOD: the scheduler checks every 10 min from 13:00–23:50 ET on weekdays and enqueues
  `eod_update` for the latest closed session until a run succeeds (handles half-days and
  retries). The update runs the analytics pipeline, then for each new session: grades →
  patterns → setups → signals, then outcomes (also scheduled at 17:00 ET). Universe rebuild: Sundays 18:00 ET. Empty universe at scheduler start → universe +
  backfill. Backfill progress lives in Redis (`backfill:progress`); the admin page polls it.
- Live: the streamer (or `make replay`) publishes alerts, quotes (≤ every 250 ms) and setup
  events on Redis `live:events`; `/api/ws` relays them (batched) to the browser through the
  Next.js proxy (rewrites proxy WebSocket upgrades). Rule and holding changes publish
  `watch:refresh` so the streamer reloads its plan. Digests: a scheduler tick every 5 min →
  `digests` job. Volume curve: weekdays 20:30 ET.
- Backtests: the lab posts `/api/backtests` → arq `backtest` job (worker) → tape (built or
  reused) → simulation → report and trades stored on `backtest_runs`; the page polls progress.
- Production: browser → Caddy (HTTPS) → `/api/*` straight to FastAPI (the WebSocket too),
  everything else to Next.js (which calls the API at `http://api:8000` server-side). The backup
  container runs its own nightly loop (02:30 ET) → `backups` volume (+ S3) → `last.json`.
- Worker, scheduler and streamer each write `heartbeat:<service>` to Redis every 10 s (TTL 30 s).
  `GET /api/health/ready` checks Postgres, TimescaleDB, Redis and those heartbeats (plus the last
  backup in production) and returns 503 if anything is down; `/admin/status` renders it.
  `GET /api/health` is plain liveness.
- `HealthNoiseFilter` (core/logging.py) drops successful health probes and heartbeat-job logs;
  failures always log.

## Gotchas

- **Next.js 16 differs from older versions.** Read `web/node_modules/next/dist/docs/` before
  using an unfamiliar API (see `web/AGENTS.md`). `LayoutProps`/`PageProps` are generated types,
  so `pnpm typecheck` runs `next typegen` first. `middleware` is now `proxy`.
- **arq pins redis-py to 5.x.** Don't bump redis-py independently. arq's typed `func()` wants
  `(ctx, *args, **kwargs)`; wrap named-arg jobs with `_task()` in `worker.py`.
- **SQLAlchemy 2.1** types selects as `Select[str, int]` (not `Select[tuple[...]]`). A model
  field named `date` shadows the `date` type inside the class body: use `import datetime as dt`.
- **Polars:** sums of booleans and `pl.len()` are *unsigned*; cast to Int64 before subtracting
  (this broke net new highs once). Nested `.over()` inside `.over()` fails: compute helper
  columns first.
- **ADBC writes** need exact column types (Int32 → integer, Int16 → smallint) and run on their
  own connection: commit deletes/truncates before appending. A full rebuild drops the
  `indicators_daily` PK/FK/date index for speed; `_ensure_indicator_constraints` restores them
  at the start of every run. connectorx returns JSONB columns as text.
- **yfinance is dev-only** and its `history()` end date is exclusive (the adapter adds a day).
- **SEC_USER_AGENT** must be set for CIKs, SIC codes and market caps; without it the universe
  still builds and data health shows a warning. ADR market caps are left empty on purpose:
  SEC share counts are ordinary shares, not ADSs.
- In web tests, Next's route announcer also has `role="alert"`; scope alert queries to the form.
- **SEC acceptance times** in the submissions API end in "Z" but are US/Eastern clock times; the
  parser treats them as Eastern (verified by `test_providers_live.py` once SEC is reachable).
- **Review charts** use matplotlib's object API (`Figure`, no pyplot) so rendering is safe in
  threads; the web shows them with `next/image` `unoptimized` (they need the session cookie) and
  requests the current theme (`?theme=dark|light`).
- **Lightweight Charts:** pin `localization.locale` ("en-US"): some browsers report locale
  tags `Intl` rejects (e.g. `en-US@posix`) and the chart throws on creation.
- **Web test environment (jsdom):** cmdk needs the `ResizeObserver` stub in `vitest.setup.ts`;
  TanStack Virtual measures `offsetHeight`/`offsetWidth`, so tests of virtualised tables stub
  them (see `screener.test.tsx`). `@tanstack/react-table` is pinned to v8 (v9 is a different API).
- **Radix popovers:** a closing popover returns focus to its trigger, which counts as an outside
  interaction for another popover opening at the same moment (it closes again). Prevent
  `onCloseAutoFocus` when one popover hands over to another (see `screener/filter-bar.tsx`).
- **cmdk** keeps the highlighted item across result changes; the palette controls `value` so the
  top hit is highlighted when new results arrive (Enter opens it).
- **`output: "standalone"`:** serve production builds with `node .next/standalone/server.js`
  after copying `.next/static` (as the Dockerfile and `playwright.config.ts` do), not
  `next start`. The server renames its process to `next-server`, so `pkill -f server.js` misses
  it (free the port with `fuser -k <port>/tcp`). API_URL is needed at build time (rewrites) and
  at runtime (server-side prefetch).
- **Server components importing from `"use client"` modules** get client references, not values:
  keep constants both sides need (chart ranges, query keys) in plain modules.
- Pattern tests that compare float prices across scales multiply by 8 (exact in binary
  floating point); ×10 can flip a comparison that sits exactly on a threshold.
- **Pattern detection runs in a forkserver process pool** (`PATTERN_WORKERS`, 0 = cores − 1;
  small scans stay in-process). Workers re-import the launching script, so every entry point
  that can run a scan needs an `if __name__ == "__main__":` guard (the CLI, scheduler and
  streamer have one; scratch scripts must too).
- **Mailpit** (dev) receives every email when no provider is configured: compose sets
  `MAIL_CATCHER_HOST=mailpit` for the backend services. Resend inline images use `content_id`
  (`cid:` in the HTML); SMTP builds multipart/related.
- **`make replay`** runs the watcher in the CLI process and reports itself as the streamer
  (heartbeat + status): don't run it while the streamer service is streaming. A replay of an
  older session works against a database whose analytics end the day before it (the e2e/demo
  seeds), and its close step stores the day's bars from the recording when missing.
- The session cookie is SameSite=Lax, which browsers don't send on a cross-site WebSocket
  handshake: that, plus the session check, is the socket's CSRF protection.
- **`.env` parsing differs:** Docker Compose reads `KEY=   # note` as the value "# note"
  (python-dotenv reads it as empty), so `.env.example` keeps notes for empty keys on the line
  above (a test checks). Empty values mean "not set" (`env_ignore_empty`), so `REPLAY_START=`
  no longer fails its pattern.
- **`POSTGRES_PASSWORD`** only applies when the data volume is first initialised; changing it
  later needs `ALTER USER` (docs/deploy.md).
- The prod api image runs as uid 10001 without a home: `MPLCONFIGDIR=/tmp/matplotlib` and
  `/data/backtests` (created in the image so the volume is writable) are set in the Dockerfile.
- **TimescaleDB restores** need `timescaledb_pre_restore()` / `timescaledb_post_restore()`
  around `pg_restore` (infra/backup/backup.sh); pg_dump's warning about circular foreign keys
  in TimescaleDB's catalog (`continuous_agg`) is harmless for a full dump.
- `make test-api` mounts only `api/` into its container: tests that read repo-root files
  (`.env.example`) skip there and run in CI/natively.
- The Docker image installs uv from PyPI (pinned `0.12.23`; keep in sync with CI and local).
- **Claude Code cloud sandbox only:**
  - The Docker daemon isn't running by default; start it with `dockerd &`.
  - Container TLS is intercepted. Build with CA-shimmed base images passed via the Dockerfiles'
    `PYTHON_IMAGE`/`NODE_IMAGE` build args from a scratch compose override. **Never commit
    proxy or CA config.**
  - The network policy also blocks Alpine's package mirror (dl-cdn.alpinelinux.org), so images
    must not `apk add` (the backup image copies rclone from its official image instead), and
    Docker Hub rate-limits anonymous pulls (429): re-tag a local image (e.g.
    `docker tag rclone/rclone:1 rclone/rclone:1.75.1`) rather than pulling again.
  - Test the production stack with a scratch `.env` (`DOMAIN=localhost`, `HTTP_PORT=8080`,
    `HTTPS_PORT=8443`) plus the CA-shim override; delete that `.env` afterwards.
  - The network policy blocks ghcr.io blobs and the market-data hosts (www.nasdaqtrader.com,
    www.sec.gov, data.sec.gov, query1/query2.finance.yahoo.com). Until the owner allows them in
    the environment's network settings, verify ingestion with the fake providers (the
    universe job fails with "HTTP 403" here, which is expected).
