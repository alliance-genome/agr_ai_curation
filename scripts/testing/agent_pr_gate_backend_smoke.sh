#!/usr/bin/env bash
# Exit 3 means the explicitly permitted missing-image skip, not a test failure.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
helper="${repo_root}/scripts/testing/docker-test-compose.sh"
image="${AGENT_GATE_UNIT_TEST_IMAGE:-}"
refresh_image=0
if [[ -z "${image}" ]]; then
  # Match the canonical helper's daemon selection for inspect and docker run.
  if [[ "${AI_CURATION_TEST_DOCKER_MODE:-rootless}" == rootless ]]; then
    export DOCKER_HOST="${AI_CURATION_ROOTLESS_DOCKER_HOST:-unix://${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/docker.sock}"
  fi
  project="$(bash "${helper}" config --format json | python3 -c 'import json, sys; print(json.load(sys.stdin)["name"])')" || exit 1
  image="${project}-backend-unit-tests"
  refresh_image=1
fi

if ! docker image inspect "${image}" >/dev/null 2>&1; then
  if [[ "${AGENT_GATE_SKIP_TEST_SMOKE_IF_MISSING:-0}" == 1 ]]; then
    echo 'backend changed; skipped because image missing and AGENT_GATE_SKIP_TEST_SMOKE_IF_MISSING=1'
    exit 3
  fi
  echo "backend changed but ${image} image is missing; build backend-unit-tests with docker-test-compose.sh first" >&2
  exit 1
fi

if [[ "${refresh_image}" == 1 ]]; then
  bash "${helper}" build backend-unit-tests || exit 1
fi
# No service network or dependencies are needed for this isolated smoke test.
docker run --rm -v "${repo_root}/backend:/app/backend" "${image}" \
  python -m pytest tests/unit/test_exceptions.py -q || exit 1
