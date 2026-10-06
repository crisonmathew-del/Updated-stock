# Breakout: developer commands. Only Docker is required; everything runs in containers.
# Run `make help` for the list.

COMPOSE := docker compose -f infra/docker-compose.yml
RUN_API := $(COMPOSE) run --rm -T api
RUN_WEB := $(COMPOSE) run --rm -T --no-deps web

.DEFAULT_GOAL := help
.PHONY: help dev down logs ps restart test test-api test-web e2e lint lint-api lint-web fmt \
        migrate migration seed create-user universe backfill eod-update data-quality scan-now \
        fundamentals patterns setups outcomes \
        shell-api shell-db

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

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

shell-api: ## Open a shell in the api container
	$(COMPOSE) exec api bash

shell-db: ## Open psql against the development database
	$(COMPOSE) exec postgres psql -U breakout -d breakout
