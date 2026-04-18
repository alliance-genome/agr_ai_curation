#!/usr/bin/env bash

RERANK_LOCAL_SERVICE_COMPOSE_PROFILE="local-reranker"

normalize_rerank_provider() {
  local provider="${1:-${RERANK_PROVIDER:-none}}"

  provider="$(printf '%s' "${provider}" | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  if [[ -z "${provider}" ]]; then
    provider="none"
  fi

  printf '%s\n' "${provider}"
}

rerank_provider_requires_local_service() {
  local provider
  provider="$(normalize_rerank_provider "${1:-${RERANK_PROVIDER:-none}}")"
  [[ "${provider}" == "local_transformers" ]]
}

rerank_local_service_compose_profile() {
  printf '%s\n' "${RERANK_LOCAL_SERVICE_COMPOSE_PROFILE}"
}

append_local_reranker_profile_args() {
  local provider="${1:-${RERANK_PROVIDER:-none}}"
  local args_array_name="${2:?args array name required}"
  # shellcheck disable=SC2178,SC2034
  local -n compose_args_ref="${args_array_name}"

  if rerank_provider_requires_local_service "${provider}"; then
    compose_args_ref+=(--profile "$(rerank_local_service_compose_profile)")
  fi
}
