# Breakout: guide for Claude Code

A growth-stock scanning, scoring and alerting platform (CAN SLIM / Weinstein stages / Minervini
Trend Template + VCP / pocket pivots / episodic pivots). **The full build spec is
[`docs/spec.md`](docs/spec.md). It is the source of truth; read it before starting any phase.**

## Current phase

- **Phase 0 (Scaffold): complete**, awaiting the owner's review.
- **Next: Phase 1 (Data foundation).** Write a short plan (files, data flows, tests) and get it
  approved before building, as for every phase (spec §0.2). Pause at the end of each phase.

## Owner decisions (answers to spec §0.3)

| Question | Answer |
|---|---|
| Data providers | **No API keys yet.** Use keyless sources: Nasdaq Trader symbol directory (universe), SEC EDGAR XBRL company facts (fundamentals, point-in-time via filing dates), yfinance for prices (**dev only**, never in production paths). Massive/Alpaca/FMP/Finnhub adapters switch on via `*_PROVIDER` env vars when keys arrive. |
| Account defaults | "You choose": **$100,000, 1% risk per trade, 25% max position**. Seed into `settings`; editable. |
| Notifications | **In-app + email** (Resend or SMTP). No browser push or Telegram for now. |
| Markets | **US large/mid caps**: NYSE/NASDAQ/AMEX common stock, price ≥ $10, ADV50 ≥ $20M, market cap ≥ $1B. Small-cap mode is a setting. |
| Auth | Not assigned to a phase by the spec; single-user login lands in **Phase 1** with the `users` table. |

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
  app/main.py        FastAPI app; every route lives under /api
  app/core/          config (pydantic-settings), logging (structlog JSON), db, redis,
                     heartbeat (service liveness), lifecycle (signal handling)
  app/api/routes/    route modules (health so far)
  app/worker.py      arq worker: `arq app.worker.WorkerSettings`
  app/scheduler.py   APScheduler (US/Eastern): `python -m app.scheduler`
  app/streamer.py    live feed (Phase 6): `python -m app.streamer`
  alembic/           async migrations; URL comes from app settings, never alembic.ini
  tests/             pytest; `integration` marker = needs Postgres + Redis
web/                 Next.js 16 App Router · React 19 · TS strict · Tailwind 4 · pnpm
  app/               routes; providers.tsx holds the TanStack Query client
  components/        UI components (+ colocated *.test.tsx)
  lib/api.ts         typed API client (same-origin /api/*, proxied to FastAPI)
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
| `make shell-api` / `make shell-db` | bash in api container / psql |
| `make seed`, `backfill`, `scan-now` | Stubs until Phases 1 and 4 |

Running natively (faster loop, needs `make dev` for Postgres/Redis on localhost):
- API: `cd api && uv sync && uv run pytest` (`-m "not integration"` needs no services),
  `uv run ruff check . && uv run ruff format --check . && uv run mypy`
- Web: `cd web && pnpm install && pnpm lint && pnpm format:check && pnpm typecheck && pnpm test && pnpm build`

## Conventions

- **Python:** async everywhere; Pydantic v2 models for every request/response; `mypy --strict`
  clean; ruff (line length 100). Settings via `get_settings()` (cached); add new env vars to
  `Settings` **and** `.env.example`. Secrets are `SecretStr`. Log with
  `get_logger(__name__)` and event-style names (`"scheduler.startup"`) plus key/value context.
  Pytest runs with `filterwarnings = error`.
- **Numerics (from Phase 2):** Polars first, NumPy second, pandas only where a library forces it.
- **Tests:** `tests/conftest.py` points `DATABASE_URL` at `<db>_test` and `REDIS_URL` at Redis
  DB 15 *before* the app is imported, so tests never touch dev data. Use the `migrated_db` and
  `clean_redis` fixtures for integration tests and mark them `@pytest.mark.integration`.
- **Migrations:** one Alembic revision per schema change; never edit an applied revision.
  TimescaleDB internal schemas are excluded from autogenerate (`alembic/env.py`).
- **Web:** Server Components by default, `"use client"` only where needed. All API calls go
  through `lib/api.ts`. Colours come from CSS tokens in `app/globals.css` (the real palette arrives
  with the Phase 5 design plan, which needs owner approval before any UI work). Numbers use the
  `tabular` class. Pair colour with a symbol (✓/✕, ▲/▼) for colour-blind users.
- **Copy:** plain and specific; errors say what went wrong and how to fix it.
- **Git:** small commits with clear messages; run lint, types and tests before each commit.
  Develop on the branch named for the session; never push elsewhere without asking.

## How the pieces talk

- Browser → Next.js (`/api/*` rewrite) → FastAPI. `next.config.ts` is evaluated **at build time**
  for production, so `API_URL` is a build arg in `web/Dockerfile`.
- Worker, scheduler and streamer each write `heartbeat:<service>` to Redis every 10 s (TTL 30 s).
  `GET /api/health/ready` checks Postgres, TimescaleDB, Redis and those heartbeats and returns 503
  if anything is down; the home page renders it. `GET /api/health` is plain liveness.
- `HealthNoiseFilter` (core/logging.py) drops successful health probes and heartbeat-job logs;
  failures always log.

## Gotchas

- **Next.js 16 differs from older versions.** Read `web/node_modules/next/dist/docs/` before
  using an unfamiliar API (see `web/AGENTS.md`). `LayoutProps`/`PageProps` are generated types,
  so `pnpm typecheck` runs `next typegen` first. `middleware` is now `proxy`.
- **arq pins redis-py to 5.x.** Don't bump redis-py independently.
- The Docker image installs uv from PyPI (pinned `0.12.23`; keep in sync with CI and local).
- **Claude Code cloud sandbox only:**
  - The Docker daemon isn't running by default; start it with `dockerd &`.
  - Container TLS is intercepted. Build with CA-shimmed base images passed via the Dockerfiles'
    `PYTHON_IMAGE`/`NODE_IMAGE` build args from a scratch compose override. **Never commit
    proxy or CA config.**
  - The network policy blocks ghcr.io blobs and the market-data hosts (data.sec.gov,
    www.nasdaqtrader.com, query1/query2.finance.yahoo.com). The owner must allow them in the
    environment's network settings before Phase 1 data work can run here.
