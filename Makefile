.PHONY: install up down logs db migrate revision api-key purge-idempotency test lint format typecheck check worker-logs observability reconcile loadtest

install:        ## Create venv and install app + dev tools
	python3.13 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

up:             ## Build and start the full stack (db + migrations + api)
	docker compose up --build -d

down:           ## Stop the stack (data volume is kept)
	docker compose down

logs:           ## Tail API logs
	docker compose logs -f api

db:             ## Start Postgres + Redis (for running tests/app locally)
	docker compose up -d db redis

migrate:        ## Apply migrations against DATABASE_URL
	.venv/bin/alembic upgrade head

revision:       ## New migration from model changes: make revision m="add accounts"
	.venv/bin/alembic revision --autogenerate -m "$(m)"

api-key:        ## Issue an API key (stack must be up): make api-key name=ops scopes="admin"
	@docker compose exec -T api python -m app.cli create-api-key --name "$(name)" $(foreach s,$(scopes),--scope $(s))

purge-idempotency: ## Delete idempotency keys past their retention window
	docker compose exec -T api python -m app.cli purge-idempotency-keys

test:
	.venv/bin/pytest -v

lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

format:
	.venv/bin/ruff check --fix .
	.venv/bin/ruff format .

typecheck:
	.venv/bin/mypy app

check: lint typecheck test   ## Everything CI runs

worker-logs:    ## Tail webhook worker logs
	docker compose logs -f worker

observability:  ## Full stack + Prometheus (:9090), Grafana (:3001), Jaeger (:16686), tracing on
	OTEL_ENABLED=true docker compose --profile observability up -d --build

reconcile:      ## Verify ledger invariants now (exit 1 on discrepancies)
	docker compose exec -T worker python -m app.cli reconcile

loadtest:       ## k6 load test: make loadtest scenario=spread|hot [vus=50 | rate=150] duration=30s
	@RATE_LIMIT_ENABLED=false docker compose up -d api >/dev/null 2>&1; sleep 5
	@KEY=$$(docker compose exec -T api python -m app.cli create-api-key --name loadtest --scope admin 2>/dev/null); \
	docker run --rm --network ledger-service_default -v "$(CURDIR)/loadtest:/scripts:ro" grafana/k6:1.2.3 run \
		-e API_KEY=$$KEY -e SCENARIO=$(or $(scenario),spread) -e VUS=$(or $(vus),50) $(if $(rate),-e RATE=$(rate)) -e DURATION=$(or $(duration),30s) \
		/scripts/transfers.js
	@docker compose up -d api >/dev/null 2>&1   # restore rate limiting
