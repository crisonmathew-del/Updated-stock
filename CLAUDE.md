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
- **Next: Phase 3 (Fundamentals & patterns).** Plan written and waiting for the owner's
  approval. Don't build until it's approved (spec §0.2).

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
  app/providers/     base.py interfaces + registry; nasdaq_trader, sec_edgar, yfinance_dev
  app/data/          classify (security types), universe, bars (bulk upserts), backfill,
                     eod_update, market_cap, quality (checks + issue register), loaders
                     (read: connectorx → Polars; write: Polars → ADBC binary COPY),
                     jobs (entry points for worker/CLI)
  app/indicators/    pure Polars indicator functions (MAs, ATR, volatility, volume, 52-week
                     ranges, relative strength, stage); compute.py applies them all
  app/scoring/       trend_template.py (8 checks + checklist text)
  app/market/        regime.py (distribution days, rally/FTD state machine), breadth.py
  app/groups/        classification.py (SIC → groups/sectors), industry_rank.py
  app/scanner/       eod_scan.py: the analytics pipeline (full / stale / incremental)
  app/api/routes/    health, auth, settings, admin, market, stocks
  app/worker.py      arq worker: `arq app.worker.WorkerSettings`
  app/scheduler.py   APScheduler (US/Eastern): `python -m app.scheduler`
  app/streamer.py    live feed (Phase 6): `python -m app.streamer`
  alembic/           async migrations; URL comes from app settings, never alembic.ini
  tests/             pytest; fakes.py has in-memory providers; fixtures/providers/ has
                     real-shaped directory and SEC files
web/                 Next.js 16 App Router · React 19 · TS strict · Tailwind 4 · pnpm
  proxy.ts           sends signed-out visitors to /login (cookie presence only)
  app/(app)/         signed-in pages with the header: / (status), /admin/data, /admin/inspect
  app/login/         sign-in page (no header)
  components/        UI components (+ colocated *.test.tsx); admin/ = data page panels,
                     inspect/ = analytics inspection panels
  lib/api.ts         typed API client: CSRF header on writes, 401 → /login, response types
  lib/format.ts      number/date/duration formatters, safeNext() redirect guard
  test-utils.tsx     renderWithClient, mockApi (fetch stub keyed by "METHOD /path")
  stores/            Zustand stores (empty so far)
infra/docker-compose.yml   web, api, worker, scheduler, streamer, migrate (one-shot), postgres, redis
docs/spec.md         the build specification
.github/workflows/ci.yml   api (ruff, mypy, pytest + services), web (lint, prettier, tsc,
                           vitest, build), prod Docker image builds
```

The spec's planned backend modules (`providers/`, `data/`, `indicators/`, `patterns/`, `market/`,
`fundamentals/`, `groups/`, `scoring/`, `risk/`, `scanner/`, `alerts/`, `backtest/`, `ai/`) are
added under `api/app/` as their phases arrive. Follow spec §4 for names.

## Commands

Only Docker is required; `make` targets run inside containers.

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
| `make scan-now [full=1] [date=…]` | Recompute analytics from stored prices (`full=1` after changing stage or Trend Template settings) |
| `make shell-api` / `make shell-db` | bash in api container / psql |

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
- **Regime definitions** are in `market/regime.py`'s docstring (DD count restarts at a
  follow-through; the below-50-day rules apply only after the index reclaimed its 50-day since
  the follow-through). Every state change and day carries human-readable reasons.
- **Numerics (from Phase 2):** Polars first, NumPy second, pandas only where a library forces it.
- **Tests:** `tests/conftest.py` points `DATABASE_URL` at `<db>_test` and `REDIS_URL` at Redis
  DB 15 *before* the app is imported, so tests never touch dev data. Fixtures: `db` (empty,
  migrated database), `clean_redis`, `client` (sends the CSRF header), `user`, `signed_in`.
  Mark DB/Redis tests `@pytest.mark.integration`. Use `tests/fakes.py` providers instead of the
  network; live calls belong in `test_providers_live.py` (`network` marker).
- **Migrations:** one Alembic revision per schema change; never edit an applied revision.
  TimescaleDB internal schemas are excluded from autogenerate (`alembic/env.py`).
- **Web:** Server Components by default, `"use client"` only where needed. All API calls go
  through `lib/api.ts` (`api.get/post/patch`). Component tests use `test-utils.tsx`. Colours come from CSS tokens in `app/globals.css` (the real palette arrives
  with the Phase 5 design plan, which needs owner approval before any UI work). Numbers use the
  `tabular` class. Pair colour with a symbol (✓/✕, ▲/▼) for colour-blind users.
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
  retries). Universe rebuild: Sundays 18:00 ET. Empty universe at scheduler start → universe +
  backfill. Backfill progress lives in Redis (`backfill:progress`); the admin page polls it.
- Worker, scheduler and streamer each write `heartbeat:<service>` to Redis every 10 s (TTL 30 s).
  `GET /api/health/ready` checks Postgres, TimescaleDB, Redis and those heartbeats and returns 503
  if anything is down; the home page renders it. `GET /api/health` is plain liveness.
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
- The Docker image installs uv from PyPI (pinned `0.12.23`; keep in sync with CI and local).
- **Claude Code cloud sandbox only:**
  - The Docker daemon isn't running by default; start it with `dockerd &`.
  - Container TLS is intercepted. Build with CA-shimmed base images passed via the Dockerfiles'
    `PYTHON_IMAGE`/`NODE_IMAGE` build args from a scratch compose override. **Never commit
    proxy or CA config.**
  - The network policy blocks ghcr.io blobs and the market-data hosts (www.nasdaqtrader.com,
    www.sec.gov, data.sec.gov, query1/query2.finance.yahoo.com). Until the owner allows them in
    the environment's network settings, verify ingestion with the fake providers (the
    universe job fails with "HTTP 403" here, which is expected).
