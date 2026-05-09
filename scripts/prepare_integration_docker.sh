#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_CONFIG="condense.local.yaml"
DEFAULT_MODEL="gemma3:4b"
OLLAMA_CONTAINER="ollama-local"

prompt_with_default() {
  local prompt="$1"
  local default_value="$2"
  local user_value
  read -r -p "${prompt} [${default_value}]: " user_value
  if [[ -z "${user_value}" ]]; then
    echo "${default_value}"
  else
    echo "${user_value}"
  fi
}

wait_for_ollama() {
  local retries=60
  local delay_s=2
  local attempt=1
  while (( attempt <= retries )); do
    if docker exec "${OLLAMA_CONTAINER}" ollama list >/dev/null 2>&1; then
      return 0
    fi
    sleep "${delay_s}"
    attempt=$((attempt + 1))
  done
  return 1
}

collect_8080_containers() {
  local line
  local name
  local ports
  while IFS=$'\t' read -r _ name ports; do
    [[ -z "${name}" ]] && continue
    if [[ "${ports}" == *"0.0.0.0:8080->"* || "${ports}" == *":::8080->"* ]]; then
      echo "${name}"
    fi
  done < <(docker ps --format '{{.ID}}\t{{.Names}}\t{{.Ports}}')
}

echo "Preparing Docker + Ollama prerequisites for integration tests."

pushd "${ROOT_DIR}" >/dev/null

CONFIG_FILE="$(prompt_with_default "Config file to run with" "${DEFAULT_CONFIG}")"
MODEL_NAME="$(prompt_with_default "Ollama model to prepare" "${DEFAULT_MODEL}")"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "Config file not found: ${CONFIG_FILE}" >&2
  exit 1
fi

echo "Checking Docker daemon access..."
docker info >/dev/null

echo "Using config: ${CONFIG_FILE}"
cp "${CONFIG_FILE}" condense.yaml

if docker inspect "${OLLAMA_CONTAINER}" >/dev/null 2>&1; then
  if [[ "$(docker inspect -f '{{.State.Running}}' "${OLLAMA_CONTAINER}")" != "true" ]]; then
    echo "Starting existing Ollama container (${OLLAMA_CONTAINER})..."
    docker start "${OLLAMA_CONTAINER}" >/dev/null
  fi
else
  echo "Creating Ollama container (${OLLAMA_CONTAINER})..."
  docker run -d --name "${OLLAMA_CONTAINER}" -p 11434:11434 ollama/ollama >/dev/null
fi

echo "Waiting for Ollama API..."
if ! wait_for_ollama; then
  echo "Ollama did not become ready in time." >&2
  exit 1
fi

if docker exec "${OLLAMA_CONTAINER}" ollama list | awk 'NR>1 {print $1}' | awk -v model="${MODEL_NAME}" '$0 == model {found=1} END {exit found ? 0 : 1}'; then
  echo "Model already available: ${MODEL_NAME}"
else
  echo "Pulling model: ${MODEL_NAME}"
  docker exec "${OLLAMA_CONTAINER}" ollama pull "${MODEL_NAME}"
fi

port_8080_containers=()
while IFS= read -r container_name; do
  [[ -n "${container_name}" ]] && port_8080_containers+=("${container_name}")
done < <(collect_8080_containers)
if (( ${#port_8080_containers[@]} > 0 )); then
  echo "Port 8080 is currently used by:"
  printf '  - %s\n' "${port_8080_containers[@]}"
  read -r -p "Stop these containers so Docker integration tests can run? [y/N]: " stop_conflicts
  stop_conflicts_normalized="$(printf '%s' "${stop_conflicts}" | tr '[:upper:]' '[:lower:]')"
  if [[ "${stop_conflicts_normalized}" == "y" || "${stop_conflicts_normalized}" == "yes" ]]; then
    docker stop "${port_8080_containers[@]}" >/dev/null
    echo "Stopped conflicting containers."
  else
    echo "Leaving running containers as-is. Docker integration test may fail on port 8080 conflict." >&2
    exit 1
  fi
fi

echo "Ready."
echo "Run integration tests with:"
echo "  .venv/bin/python -m pytest tests/test_server/test_docker_ollama_integration.py -q"

popd >/dev/null
