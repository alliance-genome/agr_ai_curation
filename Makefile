# AI Curation Platform - Development Makefile
#
# This Makefile loads environment variables from ~/.agr_ai_curation/.env
# to keep secrets outside the repository.
#
# Setup: Run 'make setup' first to create the config directory and copy templates.

# Environment file locations
ENV_FILE := $(HOME)/.agr_ai_curation/.env
TRACE_REVIEW_ENV_FILE := $(HOME)/.agr_ai_curation/trace_review/.env

# Colors for output
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

# Default target
.DEFAULT_GOAL := help

# Check if env file exists
.PHONY: check-env
check-env:
	@if [ ! -f "$(ENV_FILE)" ]; then \
		echo "$(RED)Error: $(ENV_FILE) not found$(NC)"; \
		echo "Run 'make setup' to create it from the template."; \
		exit 1; \
	fi
	@./scripts/utilities/ensure_local_langfuse_env.sh "$(ENV_FILE)"

.PHONY: check-trace-review-env
check-trace-review-env:
	@if [ ! -f "$(TRACE_REVIEW_ENV_FILE)" ]; then \
		echo "$(YELLOW)Warning: $(TRACE_REVIEW_ENV_FILE) not found$(NC)"; \
		echo "trace_review services may not work correctly."; \
		echo "Run 'make setup' to create it from the template."; \
	fi

# =============================================================================
# SETUP
# =============================================================================

.PHONY: setup
setup: ## Initial setup - creates ~/.agr_ai_curation/ and copies .env templates
	@./scripts/setup-env.sh

.PHONY: install
install: ## Run the standalone installer workflow
	@./scripts/install/install.sh

# =============================================================================
# DEVELOPMENT - Full Stack
# =============================================================================

.PHONY: dev
dev: check-env ## Start all services (sources env from ~/.agr_ai_curation/.env)
	@echo "$(GREEN)Starting all services...$(NC)"
	@set -a && . "$(ENV_FILE)" && set +a && docker compose up

.PHONY: dev-build
dev-build: check-env ## Rebuild and start all services (includes git SHA in frontend build)
	@echo "$(GREEN)Rebuilding and starting all services...$(NC)"
	@set -a && . "$(ENV_FILE)" && export VITE_GIT_SHA=$$(git rev-parse --short HEAD) && set +a && docker compose up --build

.PHONY: dev-detached
dev-detached: check-env ## Start all services in background (detached)
	@echo "$(GREEN)Starting all services in background...$(NC)"
	@set -a && . "$(ENV_FILE)" && set +a && docker compose up -d

.PHONY: down
down: ## Stop all services
	@echo "$(YELLOW)Stopping all services...$(NC)"
	@docker compose down

.PHONY: restart
restart: check-env ## Restart all services
	@echo "$(YELLOW)Restarting all services...$(NC)"
	@docker compose down
	@set -a && . "$(ENV_FILE)" && set +a && docker compose up -d

# =============================================================================
# DEVELOPMENT - Individual Services
# =============================================================================

.PHONY: restart-backend
restart-backend: check-env ## Restart only the backend service
	@echo "$(YELLOW)Restarting backend...$(NC)"
	@set -a && . "$(ENV_FILE)" && set +a && docker compose restart backend

.PHONY: restart-frontend
restart-frontend: ## Restart only the frontend service
	@echo "$(YELLOW)Restarting frontend...$(NC)"
	@docker compose restart frontend

.PHONY: rebuild-backend
rebuild-backend: check-env ## Rebuild and restart backend
	@echo "$(GREEN)Rebuilding backend...$(NC)"
	@set -a && . "$(ENV_FILE)" && set +a && docker compose up -d --build backend

.PHONY: rebuild-frontend
rebuild-frontend: ## Rebuild and restart frontend (includes git SHA in build)
	@echo "$(GREEN)Rebuilding frontend...$(NC)"
	@VITE_GIT_SHA=$$(git rev-parse --short HEAD) docker compose up -d --build frontend
	@echo "$(GREEN)Built with version from package.json, SHA: $$(git rev-parse --short HEAD)$(NC)"

.PHONY: up-core
up-core: check-env ## Start core services only (postgres, redis, weaviate) - no app
	@echo "$(GREEN)Starting core infrastructure...$(NC)"
	@set -a && . "$(ENV_FILE)" && set +a && docker compose up -d postgres redis weaviate reranker-transformers

.PHONY: up-app
up-app: check-env ## Start app services only (backend, frontend) - assumes core is running
	@echo "$(GREEN)Starting app services...$(NC)"
	@set -a && . "$(ENV_FILE)" && set +a && docker compose up -d backend frontend

.PHONY: up-langfuse
up-langfuse: check-env ## Start Langfuse services only
	@echo "$(GREEN)Starting Langfuse...$(NC)"
	@set -a && . "$(ENV_FILE)" && set +a && docker compose up -d langfuse langfuse-worker clickhouse minio

.PHONY: up-logging
up-logging: ## Start logging services only (Loki, Promtail)
	@echo "$(GREEN)Starting logging services...$(NC)"
	@docker compose up -d loki promtail

# =============================================================================
# TRACE REVIEW (Agent Studio)
# =============================================================================

.PHONY: trace-review
trace-review: check-env check-trace-review-env ## Start published trace_review backend in the main stack
	@echo "$(GREEN)Starting published trace_review backend...$(NC)"
	@set -a && . "$(ENV_FILE)" && [ -f "$(TRACE_REVIEW_ENV_FILE)" ] && . "$(TRACE_REVIEW_ENV_FILE)" && set +a && \
		docker compose up -d trace_review_backend
	@echo "$(GREEN)trace_review backend running on http://localhost:8001$(NC)"

.PHONY: trace-review-standalone
trace-review-standalone: check-trace-review-env ## Start source-built trace_review stack independently
	@echo "$(GREEN)Starting source-built trace_review standalone stack...$(NC)"
	@set -a && . "$(TRACE_REVIEW_ENV_FILE)" && set +a && \
		docker compose -f trace_review/docker-compose.yml up

.PHONY: restart-trace-review
restart-trace-review: check-trace-review-env ## Restart trace_review backend
	@echo "$(YELLOW)Restarting trace_review backend...$(NC)"
	@set -a && . "$(ENV_FILE)" && [ -f "$(TRACE_REVIEW_ENV_FILE)" ] && . "$(TRACE_REVIEW_ENV_FILE)" && set +a && \
		docker compose restart trace_review_backend

# =============================================================================
# LOGS
# =============================================================================

.PHONY: logs
logs: ## Follow logs for all services
	@docker compose logs -f

.PHONY: logs-backend
logs-backend: ## Follow backend logs
	@docker compose logs -f backend

.PHONY: logs-frontend
logs-frontend: ## Follow frontend logs
	@docker compose logs -f frontend

.PHONY: logs-weaviate
logs-weaviate: ## Follow Weaviate logs
	@docker compose logs -f weaviate

.PHONY: logs-langfuse
logs-langfuse: ## Follow Langfuse logs
	@docker compose logs -f langfuse langfuse-worker

.PHONY: logs-trace-review
logs-trace-review: ## Follow trace_review logs
	@docker compose logs -f trace_review_backend

# =============================================================================
# TESTING
# =============================================================================

.PHONY: test
test: ## Run all backend tests
	@echo "$(GREEN)Running all tests...$(NC)"
	@docker compose -f docker-compose.test.yml run --rm backend-tests

.PHONY: test-unit
test-unit: ## Run unit tests only
	@echo "$(GREEN)Running unit tests...$(NC)"
	@docker compose -f docker-compose.test.yml run --rm backend-unit-tests

.PHONY: test-integration
test-integration: ## Run integration tests only
	@echo "$(GREEN)Running integration tests...$(NC)"
	@./scripts/testing/prepare-test-stack.sh
	@docker compose -f docker-compose.test.yml run --rm backend-integration-tests

.PHONY: test-contract
test-contract: ## Run contract tests only
	@echo "$(GREEN)Running contract tests...$(NC)"
	@./scripts/testing/prepare-test-stack.sh
	@docker compose -f docker-compose.test.yml run --rm backend-contract-tests

.PHONY: test-build
test-build: ## Build test image
	@echo "$(GREEN)Building test image...$(NC)"
	@docker compose -f docker-compose.test.yml build backend-tests

.PHONY: test-prepare
test-prepare: ## Start isolated test infra (postgres/weaviate) and run migrations
	@echo "$(GREEN)Preparing isolated test infrastructure...$(NC)"
	@./scripts/testing/prepare-test-stack.sh

.PHONY: test-stack-down
test-stack-down: ## Stop isolated test infrastructure
	@echo "$(YELLOW)Stopping isolated test infrastructure...$(NC)"
	@docker compose -f docker-compose.test.yml down
	@rm -f .test-stack.env

.PHONY: smoke-llm-local
smoke-llm-local: check-env ## Run local LLM provider smoke checks and capture evidence JSON
	@echo "$(GREEN)Starting backend with sourced env and running local LLM smoke checks...$(NC)"
	@docker compose --env-file "$(ENV_FILE)" up -d backend
	@./scripts/testing/llm_provider_smoke_local.sh

.PHONY: test-live
LIVE_TEST_PATH ?= backend/tests/live_integration/
LIVE_TEST_EXPR ?= manual_only
test-live: ## Run live backend tests (override with LIVE_TEST_PATH=... LIVE_TEST_EXPR=...)
	@echo "$(GREEN)Running live tests: path=$(LIVE_TEST_PATH) marker='$(LIVE_TEST_EXPR)'...$(NC)"
	@bash -lc 'set -euo pipefail; \
		set -a; source scripts/testing/load-home-test-env.sh; set +a; \
		if [[ -f .test-stack.env ]]; then \
			set -a; source .test-stack.env; set +a; \
		fi; \
		if [[ "$${DATABASE_URL:-}" == *"@postgres:"* || "$${DATABASE_URL:-}" == *"@postgres-test:"* ]]; then \
			export DATABASE_URL="postgresql://$${TEST_DB_USER:-postgres}:$${TEST_DB_PASSWORD:-postgres}@$${TEST_DB_HOST:-127.0.0.1}:$${TEST_DB_PORT:-15434}/$${TEST_DB_NAME:-ai_curation}"; \
			echo "Using host DATABASE_URL override for local live tests."; \
		fi; \
		export LIVE_LLM_ENABLE=1 LIVE_BACKEND_PDFX_ENABLE=1 LIVE_BATCH_ENABLE=1; \
		PYTHONPATH=backend .venv/bin/pytest "$(LIVE_TEST_PATH)" -m "$(LIVE_TEST_EXPR)" -q -s'

.PHONY: test-live-integration
test-live-integration: ## Run all manual live backend integration tests (OpenAI/PDFX/Batch)
	@$(MAKE) test-live LIVE_TEST_PATH=backend/tests/live_integration/ LIVE_TEST_EXPR=manual_only

.PHONY: test-live-batch
test-live-batch: ## Run live batch processing integration test only
	@$(MAKE) test-live LIVE_TEST_PATH=backend/tests/live_integration/test_backend_batch_live_processing.py LIVE_TEST_EXPR=manual_only

# =============================================================================
# DATABASE
# =============================================================================

.PHONY: db-shell
db-shell: ## Open PostgreSQL shell
	@docker compose exec postgres psql -U postgres -d ai_curation

.PHONY: db-migrate
db-migrate: check-env ## Run Alembic migrations
	@echo "$(GREEN)Running database migrations...$(NC)"
	@set -a && . "$(ENV_FILE)" && set +a && \
		docker compose exec backend alembic upgrade head

.PHONY: db-migrate-create
db-migrate-create: ## Create a new migration (usage: make db-migrate-create MSG="description")
	@if [ -z "$(MSG)" ]; then \
		echo "$(RED)Error: Please provide a migration message$(NC)"; \
		echo "Usage: make db-migrate-create MSG=\"your migration description\""; \
		exit 1; \
	fi
	@docker compose exec backend alembic revision --autogenerate -m "$(MSG)"

.PHONY: prefix-refresh
prefix-refresh: check-env ## Run optional one-shot identifier-prefix refresh (project-specific)
	@echo "$(GREEN)Running one-shot identifier prefix refresh...$(NC)"
	@set -a && . "$(ENV_FILE)" && set +a && \
		docker compose --profile curation-db run --rm prefix_refresh

# =============================================================================
# SHELLS / DEBUGGING
# =============================================================================

.PHONY: shell-backend
shell-backend: ## Open bash shell in backend container
	@docker compose exec backend bash

.PHONY: shell-frontend
shell-frontend: ## Open shell in frontend container
	@docker compose exec frontend sh

.PHONY: python
python: ## Open Python REPL in backend container
	@docker compose exec backend python

# =============================================================================
# CLEANUP
# =============================================================================

.PHONY: clean
clean: ## Stop services and remove volumes (WARNING: deletes data!)
	@echo "$(RED)WARNING: This will delete all data including databases!$(NC)"
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	@docker compose down -v
	@echo "$(GREEN)Cleaned up.$(NC)"

.PHONY: clean-images
clean-images: ## Remove project Docker images
	@echo "$(YELLOW)Removing project images...$(NC)"
	@docker compose down --rmi local

.PHONY: clean-weaviate
clean-weaviate: ## Remove Weaviate data only
	@echo "$(RED)WARNING: This will delete all Weaviate vector data!$(NC)"
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	@docker compose stop weaviate
	@rm -rf ./weaviate_data
	@echo "$(GREEN)Weaviate data removed.$(NC)"

# =============================================================================
# STATUS / INFO
# =============================================================================

.PHONY: status
status: ## Show status of all services
	@docker compose ps

.PHONY: health
health: ## Check health of all services
	@echo "Backend:  $$(curl -s http://localhost:8000/health | head -c 100 || echo 'Not running')"
	@echo "Frontend: $$(curl -s http://localhost:3002/ > /dev/null && echo 'OK' || echo 'Not running')"
	@echo "Weaviate: $$(curl -s http://localhost:8080/v1/.well-known/ready | head -c 100 || echo 'Not running')"
	@echo "Langfuse: $$(curl -s http://localhost:3000/api/health | head -c 100 || echo 'Not running')"

.PHONY: env-check
env-check: ## Verify environment configuration
	@echo "Checking environment files..."
	@echo ""
	@if [ -f "$(ENV_FILE)" ]; then \
		echo "$(GREEN)✓ $(ENV_FILE) exists$(NC)"; \
		ls -la "$(ENV_FILE)"; \
	else \
		echo "$(RED)✗ $(ENV_FILE) NOT FOUND$(NC)"; \
	fi
	@echo ""
	@if [ -f "$(TRACE_REVIEW_ENV_FILE)" ]; then \
		echo "$(GREEN)✓ $(TRACE_REVIEW_ENV_FILE) exists$(NC)"; \
		ls -la "$(TRACE_REVIEW_ENV_FILE)"; \
	else \
		echo "$(YELLOW)✗ $(TRACE_REVIEW_ENV_FILE) NOT FOUND$(NC)"; \
	fi

# =============================================================================
# DEPLOYMENT
# =============================================================================

.PHONY: deploy-alliance
deploy-alliance: ## Deploy Alliance-specific agents to config/agents/
	@echo "Deploying Alliance content..."
	@./scripts/deploy_alliance.sh

.PHONY: deploy-alliance-clean
deploy-alliance-clean: ## Clean and deploy Alliance agents
	@echo "Clean deploying Alliance content..."
	@./scripts/deploy_alliance.sh --clean

.PHONY: deploy-check
deploy-check: ## Dry-run to see what would be deployed
	@./scripts/deploy_alliance.sh --dry-run --verbose

# =============================================================================
# PRODUCTION (EC2 with GELF logging)
# =============================================================================

.PHONY: prod
prod: check-env ## Start all services with GELF logging (for EC2 deployment)
	@echo "$(GREEN)Starting all services with GELF logging...$(NC)"
	@set -a && . "$(ENV_FILE)" && set +a && \
		docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

.PHONY: prod-build
prod-build: check-env ## Rebuild and start all services with GELF logging
	@echo "$(GREEN)Rebuilding all services with GELF logging...$(NC)"
	@set -a && . "$(ENV_FILE)" && export VITE_GIT_SHA=$$(git rev-parse --short HEAD) && set +a && \
		docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

.PHONY: prod-down
prod-down: ## Stop all production services
	@echo "$(YELLOW)Stopping production services...$(NC)"
	@docker compose -f docker-compose.yml -f docker-compose.prod.yml down

.PHONY: prod-logs
prod-logs: ## Follow logs for production services (limited - use Kibana for full logs)
	@echo "$(YELLOW)Note: With GELF driver, docker logs shows limited output. Use Kibana for full logs.$(NC)"
	@docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f --tail=50

# =============================================================================
# HELP
# =============================================================================

.PHONY: help
help: ## Show this help message
	@echo "AI Curation Platform - Development Commands"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Environment files are loaded from ~/.agr_ai_curation/"
	@echo "Run 'make setup' first to initialize the configuration."
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-20s$(NC) %s\n", $$1, $$2}'
