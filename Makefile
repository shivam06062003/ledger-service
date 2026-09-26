.PHONY: install up down logs db migrate revision api-key purge-idempotency test lint format typecheck check

install:        ## Create venv and install app + dev tools
	python3.13 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

up:             ## Build and start the full stack (db + migrations + api)
	docker compose up --build -d

down:           ## Stop the stack (data volume is kept)
	docker compose down

logs:           ## Tail API logs
	docker compose logs -f api

db:             ## Start only Postgres (for running tests/app locally)
	docker compose up -d db

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
