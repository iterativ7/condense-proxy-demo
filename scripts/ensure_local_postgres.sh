#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POSTGRES_CONTAINER="${CONDENSE_POSTGRES_CONTAINER:-condense-postgres-local}"
POSTGRES_IMAGE="${CONDENSE_POSTGRES_IMAGE:-postgres:16-alpine}"
POSTGRES_PORT="${CONDENSE_POSTGRES_PORT:-5432}"
POSTGRES_DB="${CONDENSE_POSTGRES_DB:-condense}"
POSTGRES_USER="${CONDENSE_POSTGRES_USER:-condense}"
POSTGRES_PASSWORD="${CONDENSE_POSTGRES_PASSWORD:-condense}"
POSTGRES_DATA_DIR="${ROOT_DIR}/.docker/postgres-data"

mkdir -p "${POSTGRES_DATA_DIR}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required for local Postgres startup." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not reachable. Start Docker and retry." >&2
  exit 1
fi

if docker inspect "${POSTGRES_CONTAINER}" >/dev/null 2>&1; then
  if [[ "$(docker inspect -f '{{.State.Running}}' "${POSTGRES_CONTAINER}")" != "true" ]]; then
    echo "Starting existing Postgres container (${POSTGRES_CONTAINER})..."
    docker start "${POSTGRES_CONTAINER}" >/dev/null
  else
    echo "Postgres container already running (${POSTGRES_CONTAINER})."
  fi
else
  echo "Creating Postgres container (${POSTGRES_CONTAINER})..."
  docker run -d \
    --name "${POSTGRES_CONTAINER}" \
    -p "${POSTGRES_PORT}:5432" \
    -e "POSTGRES_DB=${POSTGRES_DB}" \
    -e "POSTGRES_USER=${POSTGRES_USER}" \
    -e "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" \
    -v "${POSTGRES_DATA_DIR}:/var/lib/postgresql/data" \
    "${POSTGRES_IMAGE}" >/dev/null
fi

echo "Waiting for Postgres readiness..."
for _ in $(seq 1 60); do
  if docker exec "${POSTGRES_CONTAINER}" pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null 2>&1; then
    echo "Postgres is ready on localhost:${POSTGRES_PORT} (${POSTGRES_DB})."
    exit 0
  fi
  sleep 1
done

echo "Postgres did not become ready in time." >&2
exit 1
