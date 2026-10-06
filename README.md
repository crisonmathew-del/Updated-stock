# Breakout

A fast, explainable growth-stock scanner. It searches and analyses any US stock, scans the whole
market for breakout setups using a CAN SLIM / Trend Template / VCP methodology, plans each trade
(entry, stop, size, targets) and alerts you when a setup triggers.

> Outputs are screening signals, not financial advice. The platform never places trades.

The full product and build specification is in [`docs/spec.md`](docs/spec.md).

## Quick start

Requirements: Docker (with Compose v2) and `make`.

```bash
cp .env.example .env                       # then set SEC_USER_AGENT="Breakout you@example.com"
make dev                                   # builds and starts all services, waits until healthy
make seed                                  # default settings ($100k account, 1% risk, spec §14)
make create-user email=you@example.com     # your login (prompts for a password)
```

- Web: <http://localhost:3000>. Sign in, then open **Admin → Data** to watch the universe and
  backfill. Once prices and the evening scan are in, the dashboard, screener, stock pages and
  watchlists fill in. Press ⌘K (or `/`) anywhere to search.
- API docs: <http://localhost:8000/api/docs>

On first start the scheduler builds the universe (~5,000 US stocks and ADRs) and backfills 10
years of daily bars. With the free development sources that takes roughly 30–60 minutes; it is
resumable (`make backfill`). After that, prices update automatically 20 minutes after each
close, followed by analytics, Fundamentals Grades, pattern detection, setup scores and their
lifecycle (watch → basing → near pivot → breakout → extended / failed), trade plans and the
signal log, whose outcomes are tracked for 60 sessions. Fundamentals (statements, earnings
dates, insider trades) load nightly from SEC EDGAR; run `make fundamentals full=1` once after
the first backfill to load them straight away.

No API keys are needed for development: the universe comes from the Nasdaq Trader symbol
directory, company data from SEC EDGAR, and prices from yfinance (development only; switch
`PRICE_PROVIDER` to a paid feed before relying on it).

`make help` lists every command. `make test` and `make lint` run all checks inside the
containers; `make e2e` runs the Playwright journeys (natively; see the Makefile).

### Live data and alerts

- **Alerts work without any keys.** In development every email goes to Mailpit, a local mail
  catcher: open <http://localhost:8025> to read them (nothing leaves your machine). For real
  email set `RESEND_API_KEY` and `EMAIL_FROM` (a sender on a domain verified in Resend), or
  `EMAIL_PROVIDER=smtp` with `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`.
  **Alerts → Channels** shows what's configured and sends a test.
- **Live prices** come from Alpaca's free plan: create an account, then set
  `STREAM_PROVIDER=alpaca`, `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` and run
  `make restart`. The free plan streams IEX only (a few % of the market's volume), so intraday
  volume is scaled up and every intraday breakout stays *provisional* until the close confirms
  it. Without keys the streamer idles and everything else works on end-of-day data.
- **Replay a recorded session** to see it all work: `make replay file=recordings/day.csv.gz
  [speed=60] [start=09:55]` plays minute bars through the watcher (alerts, emails, the live
  board), then runs the close. The streamer records the minute bars of the stocks it watches;
  `make export-recording date=YYYY-MM-DD out=recordings/day.csv.gz` saves a session.

## Services

| Service | Role |
|---|---|
| `web` | Next.js app. Proxies `/api/*` to the API so secrets stay server-side |
| `api` | FastAPI: REST + the live WebSocket (`/api/ws`) |
| `worker` | arq background jobs: scans, backfills, fundamentals |
| `scheduler` | APScheduler: runs the market-hours schedule in US/Eastern |
| `streamer` | Live feed (Alpaca, or a replayed session): intraday alerts, quotes, scans |
| `postgres` | PostgreSQL 16 + TimescaleDB |
| `redis` | Cache, pub/sub and job queue |
| `mailpit` | Development mail catcher for alert emails (<http://localhost:8025>) |

## Status

| Phase | | |
|---|---|---|
| 0 | Scaffold | ✅ done |
| 1 | Data foundation | ✅ built (live acceptance pending data access) |
| 2 | Indicators, regime, RS, groups | ✅ built (live acceptance pending data access) |
| 3 | Fundamentals & patterns | ✅ built (live acceptance pending data access) |
| 4 | Scoring, lifecycle, trade plans, scanner | ✅ built (live acceptance pending data access) |
| 5 | Core UI | ✅ done |
| 6 | Real-time & alerts | ✅ done (live run pending Alpaca keys) |
| 7 | Backtest lab, signal performance, AI summary, deployment | plan in review |
| 8 | Polish & extras | planned |

![Live board](docs/screenshots/phase6-live-board.png)

_Phase 6, a replayed session: SPOT trades above its 92.46 pivot at 10:15:30 on projected volume
of ~290% of average. The live board marks the provisional breakout, the toast and bell arrive
over the WebSocket, and the email goes out with a mini chart
([email](docs/screenshots/phase6-alert-email.png)). At the close the lifecycle confirms it
(233% of average volume, closed 79% up the day's range) and says so in the
[alerts centre](docs/screenshots/phase6-alerts-history.png) and by email; a breakout that fades
is rejected with the reason. Also: the [intraday chart](docs/screenshots/phase6-stock-intraday.png)
(1- and 5-minute bars, keys `6`/`7`, updated by live quotes), [holdings](docs/screenshots/phase6-holdings.png)
with P&L in R and sell rules, [alert rules](docs/screenshots/phase6-alerts-rules.png), alert
[channels](docs/screenshots/phase6-alerts-channels.png) and the [phone layout](docs/screenshots/phase6-live-phone.png)._

**Phase 6 acceptance** (a replayed breakout through the full path: the watcher the streamer runs
(`make replay`) → Redis → API WebSocket → Next.js proxy → browser, and SMTP to Mailpit):

| Target | Measured (from the triggering print reaching the streamer) |
|---|---|
| In-app alert within 2 s | alert stored 11–15 ms; toast on screen 73–158 ms |
| Email within 2 s | in Mailpit 129–138 ms (with the chart rendered) |
| Provisional confirmed or rejected at the close | confirmed (breakout session); rejected with the reason (fading session, automated test) |

![Stock page](docs/screenshots/phase5-stock.png)

_The stock page: the chart is the hero, with the detected base drawn on it (outline, numbered
contractions and their depth, buy-zone band, pivot, stop, 2R/3R), the moving averages, volume,
the RS line and markers for pocket pivots, earnings, RS highs and past signals. Around it: the
Setup Score part by part, a trade plan that resizes as you edit the entry or stop, the Trend
Template with the actual numbers, fundamentals, the pattern's scoring, group peers, insider
buying and your notes. Keys: `1`–`5` change the range, `w` adds to the watchlist, `[` / `]`
flip through the list you came from. [Light theme](docs/screenshots/phase5-stock-light.png)._

![Screener](docs/screenshots/phase5-screener.png)

_The screener on a 6,000-stock synthetic market: nine presets (Trend Template leaders, VCPs
near pivot, breakouts and pocket pivots today, earnings gap-ups, RS leads price, high tight
flags, top group leaders, fundamentals A + RS ≥ 90), filters on any computed field, saved
screens, column chooser, CSV export and a side-panel chart preview. Also: the
[dashboard](docs/screenshots/phase5-dashboard.png) (regime with its reasons and breadth, top
setups, names about to break out, recent breakouts and how they're doing, leading groups,
sector rotation, signals) and [watchlists](docs/screenshots/phase5-watchlists.png) (several
lists, drag to reorder, a note per stock, flip-through). Phones get a bottom tab bar._

**Phase 5 performance** (spec §10; production build, 6,000 stocks, single API worker):

| Target | Measured |
|---|---|
| Ticker search < 100 ms p95 (server) | 52 ms p95 over 1,000 mixed queries (symbols, names, typos) |
| Stock page first meaningful paint < 1 s | content at ~110 ms; chart drawn at ~640 ms (median of 12) |
| Screener: 6,000 rows at 60 fps | 60 fps scrolling 6,000 rows (no frame over 20 ms); sort ≈ 0.2 s |
| Lighthouse performance ≥ 90 on the dashboard | 95 mobile / 100 desktop (accessibility 100) |

Earlier phases: [setups](docs/screenshots/phase4-setups.png),
[signal log](docs/screenshots/phase4-signals.png), [pattern review](docs/screenshots/phase3-patterns.png),
[inspection](docs/screenshots/phase3-inspect.png), [data](docs/screenshots/phase1-data.png)
(under **Admin**).
