# All targets run inside Docker, so the only host requirements are Docker and make.
.PHONY: up down logs build test lint format

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f server

build:
	docker compose build

# Starts postgres + redis (via depends_on) and runs pytest in a throwaway server container.
test:
	docker compose run --rm server pytest

lint:
	docker compose run --rm --no-deps server ruff check ..
	docker compose run --rm --no-deps server ruff format --check ..

format:
	docker compose run --rm --no-deps server ruff format ..
	docker compose run --rm --no-deps server ruff check --fix ..
