#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  symphony_prepare_docker_config.sh [--workspace-dir DIR]

Behavior:
  - Creates a workspace-local writable Docker config directory.
  - Seeds config.json from ~/.docker/config.json when available.
  - Prints the Docker config directory path (for DOCKER_CONFIG).
USAGE
}

workspace_dir="${PWD}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      workspace_dir="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ ! -d "${workspace_dir}" ]]; then
  echo "Workspace directory does not exist: ${workspace_dir}" >&2
  exit 2
fi

workspace_dir="$(cd "${workspace_dir}" && pwd -P)"
docker_config_dir="${workspace_dir}/.symphony-docker-config"
mkdir -p "${docker_config_dir}"

source_config="${HOME}/.docker/config.json"
target_config="${docker_config_dir}/config.json"
if [[ -f "${source_config}" && ! -f "${target_config}" ]]; then
  cp "${source_config}" "${target_config}" >/dev/null 2>&1 || true
fi

printf '%s\n' "${docker_config_dir}"
