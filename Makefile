.PHONY: install venv-setup ui-install ui-build start-local stop-local test lint format run docker-build docker-up docker-down docker-prep-integration benchmark benchmark-build benchmark-run benchmark-summary benchmark-data benchmark-lint benchmark-ab clean

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

run:
	poetry run condense start

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

benchmark-build:
	poetry run python benchmarks/build_production_like_profiles.py

benchmark-run: benchmark-build
	poetry run python benchmarks/run_gemini_profile_matrix.py

benchmark-summary:
	poetry run python benchmarks/summarize_profile_matrix.py

benchmark: benchmark-run benchmark-summary

benchmark-data:
	poetry run python benchmarks/download_llm_benchmark_datasets.py
	poetry run python benchmarks/convert_llm_benchmark_datasets.py --limit 50
	poetry run python benchmarks/build_heavy_token_dataset.py
	poetry run python benchmarks/build_production_like_profiles.py

benchmark-lint:
	poetry run ruff check benchmarks/

benchmark-ab:
	.venv/bin/python benchmarks/run_condense_ab_test.py --limit 10

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist build .pytest_cache .coverage htmlcov .mypy_cache
