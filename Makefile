# Breakout: developer commands. Only Docker is required; everything runs in containers.
# Run `make help` for the list.

COMPOSE := docker compose -f infra/docker-compose.yml
RUN_API := $(COMPOSE) run --rm -T api
RUN_WEB := $(COMPOSE) run --rm -T --no-deps web

.DEFAULT_GOAL := help
.PHONY: help dev down logs ps restart test test-api test-web lint lint-api lint-web fmt \
        migrate migration seed backfill scan-now shell-api shell-db

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

seed: ## Seed default settings (Phase 1)
	@echo "seed: not implemented yet; arrives in Phase 1 (settings defaults, benchmarks)."

backfill: ## Historical price backfill (Phase 1)
	@echo "backfill: not implemented yet; arrives in Phase 1 (universe + daily bars)."

scan-now: ## Run the end-of-day scan immediately (Phase 4)
	@echo "scan-now: not implemented yet; arrives in Phase 4 (EOD scan pipeline)."

shell-api: ## Open a shell in the api container
	$(COMPOSE) exec api bash

shell-db: ## Open psql against the development database
	$(COMPOSE) exec postgres psql -U breakout -d breakout
