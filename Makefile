# AgentForge — local development orchestration
.DEFAULT_GOAL := help
SHELL := /bin/bash

# --- Paths ---
API_DIR   := apps/api
WEB_DIR   := apps/web
SHARED_DIR := packages/shared-schemas
WORKSPACES_ROOT ?= .workspaces

# --- Tool detection ---
UV    := $(shell command -v uv 2>/dev/null)
PNPM  := $(shell command -v pnpm 2>/dev/null)
NPM   := $(shell command -v npm 2>/dev/null)
NODE_PM := $(if $(PNPM),pnpm,npm)

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-24s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

.PHONY: setup
setup: setup-env setup-api setup-shared setup-web ## Install all dependencies and create local dirs

.PHONY: setup-env
setup-env:
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example. Edit it before running."; fi
	@mkdir -p $(WORKSPACES_ROOT) $(WORKSPACES_ROOT)-archive evals/reports

.PHONY: setup-api
setup-api:
	@if [ -z "$(UV)" ]; then echo "uv not installed. Install: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; fi
	cd $(API_DIR) && uv sync

.PHONY: setup-shared
setup-shared:
	cd $(SHARED_DIR) && $(NODE_PM) install

.PHONY: setup-web
setup-web:
	cd $(WEB_DIR) && $(NODE_PM) install

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

.PHONY: up
up: ## Start api + web in foreground (Ctrl-C to stop)
	@$(NODE_PM) exec concurrently --names api,web --prefix-colors blue,green "make api" "make web"

.PHONY: up-daemon
up-daemon: ## Start api + web in background (survives terminal exit)
	@bash scripts/dev-daemon.sh start

.PHONY: down
down: ## Stop background dev daemon and free ports 8000/3000
	@bash scripts/dev-daemon.sh stop

.PHONY: dev-status
dev-status: ## Show dev daemon PID and port listeners
	@bash scripts/dev-daemon.sh status

.PHONY: dev-health
dev-health: ## Health-check API /health and web /
	@bash scripts/dev-daemon.sh health

.PHONY: dev-logs
dev-logs: ## Tail background dev log (/tmp/agentforge-dev.log)
	@bash scripts/dev-daemon.sh logs

.PHONY: api
api: migrate ## Run the FastAPI backend (with auto-reload)
	@set -a && . ./.env && set +a && cd $(API_DIR) && PYTHONPATH=src uv run uvicorn agentforge.api.main:app --reload --host $${API_HOST:-0.0.0.0} --port $${API_PORT:-8000}

.PHONY: web
web: ## Run the Next.js frontend (dev mode; web only — use `make up` for API + web)
	cd $(WEB_DIR) && $(NODE_PM) run dev:web

.PHONY: demo
demo: setup migrate ## One-command demo: setup + migrate + open browser
	@echo "Starting AgentForge. Open http://localhost:3000 in your browser."
	$(MAKE) up

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

.PHONY: migrate
migrate: ## Apply database migrations
	@set -a && . ./.env && set +a && cd $(API_DIR) && uv run alembic upgrade head

.PHONY: migrate-down
migrate-down: ## Roll back the last migration
	@set -a && . ./.env && set +a && cd $(API_DIR) && uv run alembic downgrade -1

.PHONY: migrate-status
migrate-status: ## Show current migration revision
	@set -a && . ./.env && set +a && cd $(API_DIR) && uv run alembic current

.PHONY: db-reset
db-reset: ## Drop and recreate the local SQLite DB
	rm -f $(WORKSPACES_ROOT)/agentforge.db
	$(MAKE) migrate

# ---------------------------------------------------------------------------
# Schemas (TS mirrors)
# ---------------------------------------------------------------------------

.PHONY: snapshot-openapi
snapshot-openapi: ## Regenerate apps/api/openapi.snapshot.json from the live API
	cd $(API_DIR) && uv run python scripts/snapshot_openapi.py > openapi.snapshot.json

.PHONY: openapi-diff
openapi-diff: ## Diff current OpenAPI schema against the committed snapshot (used by CI)
	cd $(API_DIR) && uv run python scripts/snapshot_openapi.py | diff -u openapi.snapshot.json -

.PHONY: gen-schemas
gen-schemas: snapshot-openapi ## Regenerate TS types from the OpenAPI snapshot (BP7)
	pnpm gen-schemas

# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------

.PHONY: lint
lint: lint-api lint-shared lint-web ## Lint everything

.PHONY: lint-api
lint-api:
	cd $(API_DIR) && uv run ruff check .

.PHONY: lint-shared
lint-shared:
	cd $(SHARED_DIR) && $(NODE_PM) run lint

.PHONY: lint-web
lint-web:
	cd $(WEB_DIR) && $(NODE_PM) run lint

.PHONY: typecheck
typecheck: typecheck-api typecheck-shared typecheck-web ## Typecheck everything

.PHONY: typecheck-api
typecheck-api: ## Run mypy in strict mode
	cd $(API_DIR) && uv run mypy src

.PHONY: typecheck-shared
typecheck-shared:
	cd $(SHARED_DIR) && $(NODE_PM) run typecheck

.PHONY: typecheck-web
typecheck-web:
	cd $(WEB_DIR) && $(NODE_PM) run typecheck

.PHONY: format
format: ## Format all code
	cd $(API_DIR) && uv run ruff format .
	cd $(WEB_DIR) && $(NODE_PM) run format
	cd $(SHARED_DIR) && $(NODE_PM) run format

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

.PHONY: test
test: test-api test-web ## Run all tests

.PHONY: test-api
test-api:
	cd $(API_DIR) && uv run pytest -q

.PHONY: test-web
test-web:
	cd $(WEB_DIR) && $(NODE_PM) run typecheck
	cd $(WEB_DIR) && $(NODE_PM) run lint
	cd $(WEB_DIR) && $(NODE_PM) run build

# ---------------------------------------------------------------------------
# Eval
# ---------------------------------------------------------------------------

.PHONY: eval
eval: ## Run the eval suite end-to-end (3 scenarios; exits 0 on full pass)
	cd $(API_DIR) && PYTHONPATH=src uv run python -m agentforge.evals

# ---------------------------------------------------------------------------
# CI
# ---------------------------------------------------------------------------

.PHONY: ci
ci: lint typecheck test openapi-diff eval ## What CI runs on every push

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

.PHONY: clean
clean: ## Remove caches and build artifacts (keep the DB and workspaces)
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .mypy_cache -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
	find . -type d \( -name .next -o -name .next-dev -o -name .next-build \) -prune -exec rm -rf {} +
	find . -type d -name node_modules -prune -exec rm -rf {} +
	rm -rf $(API_DIR)/.uv $(API_DIR)/dist $(API_DIR)/build

.PHONY: clean-workspaces
clean-workspaces: ## Remove all session workspaces (destructive)
	rm -rf $(WORKSPACES_ROOT) $(WORKSPACES_ROOT)-archive
