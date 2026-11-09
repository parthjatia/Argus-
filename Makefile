# All targets run inside Docker, so the only host requirements are Docker and make.
.PHONY: up down logs build migrate seed demo traffic test lint format

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f server worker

build:
	docker compose build

# Applies pending files from server/migrations/ (safe to run repeatedly).
migrate:
	docker compose run --rm server python -m app.migrate

# Creates/reuses the "demo" project and prints a new API key. Override: make seed NAME=myproj
NAME ?= demo
seed:
	docker compose run --rm server python -m app.seed $(NAME)

# One debate = one trace. Needs ARGUS_API_KEY in .env (from `make seed`).
TOPIC ?=
demo:
	docker compose run --rm demo python -m arena.debate --topic "$(TOPIC)"

# Many debates in parallel to fill the dashboard.
N ?= 50
traffic:
	docker compose run --rm demo python traffic.py --n $(N)

# Starts postgres + redis (via depends_on) and runs pytest in a throwaway server container.
# SDK tests need no services; sdk/pyproject.toml puts sdk/ on the path.
test:
	docker compose run --rm server pytest
	docker compose run --rm --no-deps server pytest ../sdk/tests
	docker compose run --rm --no-deps demo pytest

lint:
	docker compose run --rm --no-deps server ruff check ..
	docker compose run --rm --no-deps server ruff format --check ..

format:
	docker compose run --rm --no-deps server ruff format ..
	docker compose run --rm --no-deps server ruff check --fix ..
