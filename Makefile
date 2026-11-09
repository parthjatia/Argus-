# All targets run inside Docker, so the only host requirements are Docker and make.
.PHONY: up down logs build migrate seed test lint format

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f server

build:
	docker compose build

# Applies pending files from server/migrations/ (safe to run repeatedly).
migrate:
	docker compose run --rm server python -m app.migrate

# Creates/reuses the "demo" project and prints a new API key. Override: make seed NAME=myproj
NAME ?= demo
seed:
	docker compose run --rm server python -m app.seed $(NAME)

# Starts postgres + redis (via depends_on) and runs pytest in a throwaway server container.
test:
	docker compose run --rm server pytest

lint:
	docker compose run --rm --no-deps server ruff check ..
	docker compose run --rm --no-deps server ruff format --check ..

format:
	docker compose run --rm --no-deps server ruff format ..
	docker compose run --rm --no-deps server ruff check --fix ..
