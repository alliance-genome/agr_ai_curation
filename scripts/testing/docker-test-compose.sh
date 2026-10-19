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

# A checkout-specific project also scopes Compose-generated image names. Explicit
# -p/--project-name and COMPOSE_PROJECT_NAME remain available to callers.
workspace_hash="$(printf '%s' "${REPO_ROOT}" | sha256sum)"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-agr-tests-${workspace_hash:0:16}}"

# Insert --build at the command boundary, never inside the container command.
# Compose global options that take values must be skipped as pairs.
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[i]}" in
    -f|--file|-p|--project-name|--project-directory|--env-file|--profile|--ansi|--progress|--parallel)
      i=$((i + 1)) ;;
    --*=*|-*) ;;
    run|up)
      args=("${args[@]:0:i+1}" --build "${args[@]:i+1}")
      break ;;
    *) break ;;
  esac
done

exec docker compose "${compose_args[@]}" "${args[@]}"
