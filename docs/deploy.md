# Deploying Breakout

Breakout runs on one Linux server with Docker. [Caddy](https://caddyserver.com) is the only
public service: it gets and renews the HTTPS certificate from Let's Encrypt and passes `/api/*`
to the API and everything else to the web app. PostgreSQL, Redis and the app services have no
public ports. A backup service dumps the database every night.

```
internet ──443──▶ caddy ─┬─ /api/* ──▶ api ──┬── postgres (TimescaleDB)
                         └─ /*     ──▶ web   └── redis ◀── worker, scheduler, streamer
                                    backup (02:30 ET) ──▶ backups volume ──▶ S3 (optional)
```

The files: `infra/docker-compose.prod.yml` (the services), `infra/Caddyfile` (HTTPS and
routing), `infra/backup/` (the backup image and its restore test).

## What you need

- **A server**: Ubuntu 24.04 (any Linux with Docker works). 4 vCPUs, 8 GB RAM and 80 GB of SSD
  are comfortable for the 6,000-stock universe: the end-of-day scan takes about a minute on 4
  cores, and a 5-year backtest of ~3,000 stocks an estimated 25 minutes the first time (its
  candidate tape is cached afterwards). Hetzner, DigitalOcean, Linode and Vultr all have one for roughly
  $25–50 a month.
- **A domain name** with a DNS `A` (and, if the server has IPv6, `AAAA`) record pointing at the
  server, e.g. `breakout.example.com`.
- **An email address** for Let's Encrypt (it writes only about certificate problems).
- Ports **80 and 443** open to the internet (80 is needed for the certificate challenge and
  redirects to HTTPS), plus SSH for you.

## First deployment

1. Install Docker and allow only SSH and the web ports:

   ```sh
   curl -fsSL https://get.docker.com | sh
   sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443 && sudo ufw enable
   ```

2. Get the code and create `.env`:

   ```sh
   git clone https://github.com/<you>/<repo>.git breakout && cd breakout
   cp .env.example .env && chmod 600 .env
   ```

   Fill in at least:

   | Key | Value |
   |---|---|
   | `SESSION_SECRET` | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `DOMAIN` | `breakout.example.com` |
   | `ACME_EMAIL` | your email |
   | `POSTGRES_PASSWORD` | `python3 -c "import secrets; print(secrets.token_hex(24))"` (letters and digits only) |
   | `SEC_USER_AGENT` | `Breakout you@example.com` (SEC requires contact details) |
   | email | `RESEND_API_KEY` or the `SMTP_*` keys, and `EMAIL_FROM` |

   Data provider keys (`PRICE_PROVIDER=massive` + `MASSIVE_API_KEY`, `ALPACA_*`, …) and
   `ANTHROPIC_API_KEY` go here too when you have them. **yfinance (the default price source) is
   for development only**: it's unofficial and can break or be rate limited at any time. Switch
   to a paid provider before relying on the app.

   You don't set `APP_ENV`, `DATABASE_URL`, `REDIS_URL` or `PUBLIC_URL`: the production compose
   file sets them (`PUBLIC_URL` becomes `https://DOMAIN`).

3. Build and start everything:

   ```sh
   make deploy
   ```

   The first build takes a few minutes. Migrations and the default settings run before the app
   starts. Caddy fetches the certificate on the first request (give it up to a minute).

4. Create your login:

   ```sh
   make prod-create-user email=you@example.com
   ```

5. Open `https://breakout.example.com`, sign in, and check **Admin → Status**: every row should
   be green except *Nightly backup* ("no backup yet"). Run `make backup-now` once to check
   backups work, and the row turns green.

6. Load the market. With an empty universe the scheduler starts the universe build and the
   price backfill itself (watch it on **Admin → Data**; 10 years of history for ~6,000 stocks
   takes a few hours on the free sources). Then fundamentals: `make prod-cli cmd="fundamentals --full"`.
   From then on everything runs on the schedule (end-of-day update after the close, digests,
   the Sunday universe rebuild, backups at 02:30 ET).

## Everyday operation

| Command | What it does |
|---|---|
| `git pull && make deploy` | Update: rebuild the images, run new migrations, restart what changed |
| `make prod-ps` / `make prod-logs [service=worker]` | Status / follow the logs |
| `make prod-cli cmd="…"` | Any operator command, e.g. `cmd="eod-update"`, `cmd="backtest --sensitivity"` |
| `make prod-up` / `make prod-down` | Start / stop everything (data and backups are kept) |
| `make backup-now` | Back up now (also copies off-site when configured) |
| `make backup-list` | The backups on the server, newest first |
| `make backup-verify [file=latest]` | Restore a backup into a scratch database and compare every table's row count |
| `make restore file=<name>` | Replace the database with a backup (asks first; see below) |

Logs are rotated (5 × 20 MB per service). The status page (**Admin → Status**, or
`GET /api/health/ready`) fails the *Nightly backup* row when the last backup is more than 26
hours old (`BACKUP_MAX_AGE_HOURS`) or its off-site copy failed.

## Backups

The `backup` service runs `pg_dump` (custom format, compressed) every night at `BACKUP_TIME`
(default 02:30 US/Eastern, after the evening jobs and before the pre-market scan) into the
`backups` volume:

- `daily/`: the last `BACKUP_KEEP_DAILY` dumps (default 14);
- `weekly/`: Sunday's dump, the last `BACKUP_KEEP_WEEKLY` (default 8);
- `pre-restore/`: the database as it was just before each restore;
- `last.json`: the last backup's file, time, size and off-site result (read by the status page).

**Off-site copies.** A backup on the same disk doesn't survive losing the server. Set the
`BACKUP_S3_*` keys in `.env` (any S3-compatible storage: AWS S3, Backblaze B2, Cloudflare R2,
Wasabi) and run `make deploy`; after each dump the backup folder is mirrored to
`s3://BACKUP_S3_BUCKET/BACKUP_S3_PREFIX/` with [rclone](https://rclone.org). Use a bucket and an
access key for this alone. To copy a dump to your own machine instead:

```sh
docker compose -f infra/docker-compose.prod.yml --env-file .env cp backup:/backups/daily/<name>.dump .
scp server:breakout/<name>.dump .
```

**Restoring.** `make restore file=breakout_2026-10-06_0230.dump` (or `file=latest`) stops the
app, saves the current database to `pre-restore/`, replaces the database with the dump
(TimescaleDB's restore procedure: `timescaledb_pre_restore()`, `pg_restore`,
`timescaledb_post_restore()`), then starts everything again; newer migrations run on the way up.
To restore on a new server, deploy it first, copy the dump in, then restore:

```sh
docker compose -f infra/docker-compose.prod.yml --env-file .env cp <name>.dump backup:/backups/daily/
make restore file=<name>.dump
```

The backtest tape cache (the `backtests` volume) isn't backed up: it's rebuilt on demand.

**The restore test.** CI builds the backup image and runs `infra/backup/test-restore.sh` against
a migrated database: it seeds tickers and two years of bars (compressing the older chunks),
checks rotation, dumps, verifies the dump in a scratch database, then drops a table, deletes rows
and restores in place, checking that the rows, hypertables and compressed chunks come back.
Run it locally with `make backup-test` (needs `make dev`).

## Trying the production stack locally

Set `DOMAIN=localhost`, `ACME_EMAIL=you@example.com`, `POSTGRES_PASSWORD` and `SESSION_SECRET`
in `.env` (and `HTTP_PORT=8080`, `HTTPS_PORT=8443` if those ports are taken), then `make deploy`.
Caddy serves `https://localhost:8443` with a certificate from its own local CA, which browsers
don't trust (`curl -k`, or accept the warning).

## Troubleshooting

- **The certificate isn't issued** (`make prod-logs service=caddy`): the DNS record must point
  at this server and port 80 must be reachable from the internet. Let's Encrypt rate-limits
  repeated failures, so fix DNS first, then `make prod-up`.
- **"password authentication failed for user breakout"**: `POSTGRES_PASSWORD` is only applied
  when the database is first created. To change it later, run
  `docker compose -f infra/docker-compose.prod.yml --env-file .env exec postgres psql -U breakout -c "ALTER USER breakout PASSWORD '<new>'"`,
  then update `.env` and `make deploy`.
- **The API won't start: "SESSION_SECRET must be set"**: set it in `.env` (production refuses
  the development default).
- **HSTS**: once a browser has seen the site over HTTPS it refuses plain HTTP to that domain
  for a year. Keep the domain on HTTPS.
- **Docker Hub "429 Too Many Requests"** during a build: anonymous pulls are rate limited; wait,
  or `docker login`.
