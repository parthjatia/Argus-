# All targets run inside Docker, so the only host requirements are Docker and make.
.PHONY: up down logs build migrate seed demo traffic test lint format load chaos

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f server worker dashboard

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
# Dashboard unit tests use Node's built-in runner (no npm install needed);
# `docker compose build dashboard` additionally type-checks and builds it.
test:
	docker compose run --rm server pytest
	docker compose run --rm --no-deps server pytest ../sdk/tests
	docker compose run --rm --no-deps demo pytest
	docker run --rm -v "$(CURDIR)/dashboard:/app" -w /app node:22-alpine npm test

lint:
	docker compose run --rm --no-deps server ruff check ..
	docker compose run --rm --no-deps server ruff format --check ..

format:
	docker compose run --rm --no-deps server ruff format ..
	docker compose run --rm --no-deps server ruff check --fix ..

# Load test of POST /v1/ingest: creates a fresh project, runs k6 in Docker, then measures how
# fast the worker drains the stream. Results: loadtest/results/. Runs on the host (Python 3.12,
# stdlib only). More API processes: WEB_CONCURRENCY=4 docker compose up -d server
VUS ?= 20
DURATION ?= 30s
load:
	python loadtest/run_load.py --vus $(VUS) --duration $(DURATION)

# Sends spans while killing/restarting the worker and the API, then checks every accepted
# span is stored exactly once. Runs on the host because it drives `docker compose`.
chaos:
	python loadtest/chaos.py
