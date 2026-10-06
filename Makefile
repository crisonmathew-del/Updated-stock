# Breakout: developer commands. Only Docker is required; everything runs in containers.
# Run `make help` for the list.

COMPOSE := docker compose -f infra/docker-compose.yml
RUN_API := $(COMPOSE) run --rm -T api
RUN_WEB := $(COMPOSE) run --rm -T --no-deps web
# Production (on the server; docs/deploy.md). Compose reads DOMAIN etc. from the repo's .env.
PROD := docker compose -f infra/docker-compose.prod.yml --env-file .env

.DEFAULT_GOAL := help
.PHONY: help dev down logs ps restart test test-api test-web e2e lint lint-api lint-web fmt \
        digests replay export-recording volume-curve backtest \
        migrate migration seed create-user universe backfill eod-update data-quality scan-now \
        fundamentals patterns setups outcomes \
        shell-api shell-db \
        deploy prod-up prod-down prod-logs prod-ps prod-cli prod-create-user \
        backup-now backup-list backup-verify restore backup-test

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-17s\033[0m %s\n", $$1, $$2}'

dev: ## Build and start the whole stack, waiting until every service is healthy
	$(COMPOSE) up --build --detach --wait --renew-anon-volumes
	@echo ""
	@echo "  Web  http://localhost:$${WEB_PORT:-3000}"
	@echo "  API  http://localhost:$${API_PORT:-8000}/api/docs"
	@echo ""
	@echo "  make logs    follow logs     make down    stop everything"

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

logs: ## Follow logs from every service
	$(COMPOSE) logs --follow --tail=100

ps: ## Show service status
	$(COMPOSE) ps

restart: ## Restart the backend services (api, worker, scheduler, streamer)
	$(COMPOSE) restart api worker scheduler streamer

test: test-api test-web ## Run every test suite

test-api: ## Backend tests, including integration tests against Postgres and Redis
	$(RUN_API) pytest

test-web: ## Frontend unit tests
	$(RUN_WEB) pnpm test

# The one native command: it drives a real browser against a production build. Needs `make dev`
# running (for Postgres and Redis on localhost), uv, pnpm and `pnpm exec playwright install
# chromium` once. It seeds its own `breakout_e2e` database; dev data is never touched.
e2e: ## End-to-end browser tests (native; see the comment in the Makefile)
	cd web && pnpm exec playwright test

lint: lint-api lint-web ## Lint, format-check and type-check everything

lint-api:
	$(RUN_API) sh -c "ruff check . && ruff format --check . && mypy"

lint-web:
	$(RUN_WEB) sh -c "pnpm lint && pnpm format:check && pnpm typecheck"

fmt: ## Auto-format and auto-fix both codebases
	$(RUN_API) sh -c "ruff check --fix . && ruff format ."
	$(RUN_WEB) pnpm format

migrate: ## Apply database migrations
	$(COMPOSE) run --rm -T migrate

migration: ## Create a new migration: make migration m="add tickers table"
	$(RUN_API) alembic revision --autogenerate -m "$(m)"

seed: ## Insert default settings (never overwrites your changes)
	$(RUN_API) python -m app.cli seed

create-user: ## Create the login user: make create-user email=you@example.com
	@test -n "$(email)" || (echo 'Usage: make create-user email=you@example.com' && exit 1)
	$(COMPOSE) run --rm api python -m app.cli create-user --email "$(email)"

universe: ## Rebuild the universe now, then backfill new tickers
	$(RUN_API) python -m app.cli universe --then-backfill

backfill: ## Load history for pending tickers. Options: years=10 symbols=AAPL,MSFT force=1
	$(RUN_API) python -m app.cli backfill $(if $(years),--years $(years)) \
		$(if $(symbols),--symbols $(symbols)) $(if $(force),--force)

eod-update: ## Fetch the latest session's bars and run quality checks. Option: date=YYYY-MM-DD
	$(RUN_API) python -m app.cli eod-update $(if $(date),--date $(date))

data-quality: ## Run the data-quality checks
	$(RUN_API) python -m app.cli data-quality

scan-now: ## Recompute analytics from stored prices. Options: full=1 date=YYYY-MM-DD
	$(RUN_API) python -m app.cli scan $(if $(full),--full) $(if $(date),--date $(date))

fundamentals: ## Load statements, earnings dates, insider trades. Options: full=1 symbols=AAPL,MSFT
	$(RUN_API) python -m app.cli fundamentals $(if $(full),--full) \
		$(if $(symbols),--symbols $(symbols))

patterns: ## Grades + pattern detection as of a date. Options: date=YYYY-MM-DD symbols=NVDA,SMCI
	$(RUN_API) python -m app.cli patterns $(if $(date),--date $(date)) \
		$(if $(symbols),--symbols $(symbols))

setups: ## Scores, lifecycle, signals for sessions not processed yet (re-scores the latest). Options: date=YYYY-MM-DD
	$(RUN_API) python -m app.cli setups $(if $(date),--date $(date))

outcomes: ## Update signal outcomes (returns after 1-60 sessions, stop/2R/+20% hit dates)
	$(RUN_API) python -m app.cli outcomes

digests: ## Send the daily or weekly digest now if one is due (the scheduler does this every 5 minutes)
	$(RUN_API) python -m app.cli digests

replay: ## Replay a recorded session through the watcher. file=recordings/x.csv.gz [speed=60] [start=09:55] [close=0]
	$(RUN_API) python -m app.cli replay --file $(file) $(if $(speed),--speed $(speed)) $(if $(start),--start $(start)) $(if $(filter 0,$(close)),--no-close)

export-recording: ## Save a stored session's minute bars as a recording. date=YYYY-MM-DD out=recordings/x.csv.gz [symbols=A,B]
	$(RUN_API) python -m app.cli export-recording --date $(date) --out $(out) $(if $(symbols),--symbols $(symbols))

volume-curve: ## Learn the time-of-day volume curve from stored minute bars
	$(RUN_API) python -m app.cli volume-curve

backtest: ## Backtest the default rules (also in the web lab). [start=YYYY-MM-DD] [end=…] [sensitivity=1]
	$(RUN_API) python -m app.cli backtest $(if $(start),--start $(start)) $(if $(end),--end $(end)) $(if $(sensitivity),--sensitivity)

shell-api: ## Open a shell in the api container
	$(COMPOSE) exec api bash

shell-db: ## Open psql against the development database
	$(COMPOSE) exec postgres psql -U breakout -d breakout

# --- Production (run on the server; see docs/deploy.md) ------------------------------------------

deploy: ## Production: build and (re)start everything behind HTTPS, then show status
	@test -f .env || (echo "No .env: copy .env.example to .env and fill in the production section." && exit 1)
	$(PROD) up --build --detach --wait --remove-orphans
	$(PROD) ps

prod-up: ## Production: start everything without rebuilding
	$(PROD) up --detach --wait

prod-down: ## Production: stop everything (data volumes and backups are kept)
	$(PROD) down

prod-logs: ## Production: follow logs. Option: service=api
	$(PROD) logs --follow --tail=100 $(service)

prod-ps: ## Production: service status
	$(PROD) ps

prod-cli: ## Production: run an operator command, e.g. make prod-cli cmd="universe --then-backfill"
	@test -n "$(cmd)" || (echo 'Usage: make prod-cli cmd="eod-update" (any `python -m app.cli` command)' && exit 1)
	$(PROD) run --rm -T api python -m app.cli $(cmd)

prod-create-user: ## Production: create the login user: make prod-create-user email=you@example.com
	@test -n "$(email)" || (echo 'Usage: make prod-create-user email=you@example.com' && exit 1)
	$(PROD) run --rm api python -m app.cli create-user --email "$(email)"

backup-now: ## Production: back the database up now (also copies off-site when configured)
	$(PROD) run --rm -T backup now

backup-list: ## Production: the backups on the server, newest first
	$(PROD) run --rm -T backup list

backup-verify: ## Production: restore a backup into a scratch database and compare row counts. [file=latest]
	$(PROD) run --rm -T backup verify $(or $(file),latest)

restore: ## Production: replace the database with a backup: make restore file=<name from backup-list>|latest
	@test -n "$(file)" || (echo 'Usage: make restore file=<name from make backup-list> (or file=latest)' && exit 1)
	@if [ "$(yes)" != 1 ]; then \
		printf 'Replace the production database with %s? The current one is saved to pre-restore/ first. Type yes: ' "$(file)"; \
		read answer; [ "$$answer" = yes ] || (echo "Cancelled." && exit 1); \
	fi
	$(PROD) stop caddy web api worker scheduler streamer
	$(PROD) run --rm -T backup restore $(file)
	$(PROD) up --detach --wait

# Development/CI: the backup image's restore test against a scratch database (needs `make dev`).
backup-test: ## Restore test for the backup image (dump, verify, damage, restore) on a scratch database
	docker build --quiet --tag breakout-backup:test infra/backup
	$(COMPOSE) exec -T postgres psql -U breakout -d postgres -qc "DROP DATABASE IF EXISTS breakout_backup_test WITH (FORCE)" -c "CREATE DATABASE breakout_backup_test"
	$(COMPOSE) run --rm -T -e DATABASE_URL=postgresql+asyncpg://breakout:breakout@postgres:5432/breakout_backup_test migrate
	PGHOST=localhost PGPORT=$${POSTGRES_PORT:-5432} PGUSER=breakout PGPASSWORD=breakout PGDATABASE=breakout_backup_test \
		infra/backup/test-restore.sh breakout-backup:test
	$(COMPOSE) exec -T postgres psql -U breakout -d postgres -qc "DROP DATABASE breakout_backup_test WITH (FORCE)"
