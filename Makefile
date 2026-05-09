.PHONY: install test lint format run docker-build docker-up docker-down docker-prep-integration clean

install:
	poetry install

test:
	poetry run pytest -v

test-cov:
	poetry run pytest --cov=condense --cov-report=html -v

lint:
	poetry run ruff check condense/ tests/

format:
	poetry run ruff format condense/ tests/

run:
	poetry run condense start

init:
	poetry run condense init

docker-build:
	docker build -t condense .

docker-up:
	docker compose up -d

docker-up-minimal:
	docker compose -f docker-compose.minimal.yml up -d

docker-prep-integration:
	./scripts/prepare_integration_docker.sh

docker-down:
	docker compose down

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist build .pytest_cache .coverage htmlcov .mypy_cache
