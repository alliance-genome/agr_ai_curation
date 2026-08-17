#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${REPO_ROOT}"
cache_paths="$(
  find backend \
    \( \
      \( -type d \( -name __pycache__ -o -name .pytest_cache \) \) -o \
      \( -type f -name '*.py[co]' \) \
    \) \
    -print
)"

if [[ -n "${cache_paths}" ]]; then
  echo "Docker-backed tests wrote cache artifacts into the backend workspace:" >&2
  printf '%s\n' "${cache_paths}" >&2
  exit 1
fi

echo "BACKEND_TEST_CACHE_STATUS=clean"
