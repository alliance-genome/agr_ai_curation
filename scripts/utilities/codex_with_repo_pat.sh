#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PAT_ENV="${REPO_ROOT}/.symphony/github_pat_env.sh"

if [[ ! -r "${PAT_ENV}" ]]; then
  echo "Missing GitHub PAT env helper: ${PAT_ENV}" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${PAT_ENV}"
symphony_load_github_pat_env --require

exec codex "$@"
