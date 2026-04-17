#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.test.yml"
DOCKER_MODE="${AI_CURATION_TEST_DOCKER_MODE:-rootless}"

# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/lib/rerank_provider_common.sh"

if [[ "${1:-}" == "--rootless" ]]; then
  DOCKER_MODE="rootless"
  shift
elif [[ "${1:-}" == "--rootful" ]]; then
  DOCKER_MODE="rootful"
  shift
fi

if [[ "${DOCKER_MODE}" == "rootless" ]]; then
  export DOCKER_HOST="${AI_CURATION_ROOTLESS_DOCKER_HOST:-unix://${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/docker.sock}"
fi

compose_args=(-f "${COMPOSE_FILE}")
append_local_reranker_profile_args "${RERANK_PROVIDER:-none}" compose_args

exec docker compose "${compose_args[@]}" "$@"
