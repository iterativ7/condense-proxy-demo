.PHONY: install venv-setup ui-install ui-build start-local stop-local test lint format docker-build docker-up docker-down docker-prep-integration clean

install:
	poetry install

venv-setup:
	python3 -m venv .venv
	PIP_INDEX_URL=https://pypi.org/simple .venv/bin/python -m pip install --upgrade pip
	PIP_INDEX_URL=https://pypi.org/simple .venv/bin/python -m pip install -e .

ui-install:
	cd ui && NPM_CONFIG_REGISTRY=https://registry.npmjs.org npm install

ui-build:
	cd ui && NPM_CONFIG_REGISTRY=https://registry.npmjs.org npm install && npm run build

stop-local:
	@PIDS=$$(lsof -ti tcp:8090); \
	if [ -n "$$PIDS" ]; then \
		echo "Stopping local Condense on :8090 (PID(s): $$PIDS)"; \
		kill $$PIDS; \
	else \
		echo "No local Condense process listening on :8090"; \
	fi

test:
	poetry run pytest -v

test-cov:
	poetry run pytest --cov=condense --cov-report=html -v

lint:
	poetry run ruff check condense/ tests/

format:
	poetry run ruff format condense/ tests/

start-local: venv-setup
	.venv/bin/condense start --config condense.local.yaml

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
