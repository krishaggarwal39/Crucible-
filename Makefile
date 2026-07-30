.PHONY: up down build logs test test-fe lint lint-fe format init init-fe \
        dev-backend dev-worker dev-frontend db-upgrade db-downgrade db-check check

# ── Infrastructure ────────────────────────────────────────────────────────────

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f --tail=100

# ── Setup ─────────────────────────────────────────────────────────────────────

init:
	uv pip install -e ".[dev]"

init-fe:
	cd src/frontend && npm ci

# ── Running locally ───────────────────────────────────────────────────────────
# The API is run on the host (not in compose) so you get hot reload.

# --no-proxy-headers so request.client stays the real socket peer; the app decides
# whether to trust X-Forwarded-For via TRUSTED_PROXY_COUNT.
dev-backend:
	PYTHONPATH=src uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000 --no-proxy-headers

dev-worker:
	PYTHONPATH=src uv run celery -A backend.worker.celery_app worker --loglevel=info

dev-frontend:
	cd src/frontend && npm run dev

# ── Tests ─────────────────────────────────────────────────────────────────────
# conftest.py redirects to <POSTGRES_DB>_test and refuses to run against a
# database whose name does not end in _test, because the fixtures truncate tables.

test:
	PYTHONPATH=src uv run pytest tests/

test-fe:
	cd src/frontend && npm test

# ── Quality ───────────────────────────────────────────────────────────────────

lint:
	uv run ruff check src/ tests/

lint-fe:
	cd src/frontend && npm run lint && npm run typecheck

format:
	uv run ruff format src/ tests/

# Everything CI runs, in one command.
check: lint test lint-fe test-fe db-check

# ── Database ──────────────────────────────────────────────────────────────────

db-upgrade:
	PYTHONPATH=src uv run alembic upgrade head

db-downgrade:
	PYTHONPATH=src uv run alembic downgrade -1

# Fails if the models have drifted away from the migration chain.
db-check:
	PYTHONPATH=src uv run alembic check
