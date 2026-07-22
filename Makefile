.PHONY: up down build test lint format init db-upgrade db-downgrade

up:
	docker-compose up -d

down:
	docker-compose down

build:
	docker-compose build

test:
	pytest tests/

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

init:
	uv pip install -e ".[dev]"

db-upgrade:
	alembic upgrade head

db-downgrade:
	alembic downgrade -1
