#!/usr/bin/env bash
# Synthetic, disposable v2 replacement proof; never targets an existing stack.
set -euo pipefail
canary_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
canary_project="benchmark_canary_$(date +%s)_${RANDOM}"
compose=(bash "${canary_root}/scripts/testing/docker-test-compose.sh" \
  -f "${canary_root}/docker-compose.benchmark-canary.yml" "$@" -p "${canary_project}")
# Do not forward host provider credentials into even the test dependencies.
export OPENAI_API_KEY='' OPENROUTER_API_KEY='' RERANK_PROVIDER=none
export TEST_DB_NAME=benchmark_replacement_canary
export TEST_DB_PORT_HOST=0 TEST_WEAVIATE_PORT_HOST=0 TEST_WEAVIATE_GRPC_PORT_HOST=0 TEST_REDIS_PORT_HOST=0
cleanup() {
  "${compose[@]}" down --volumes --remove-orphans
}
trap cleanup EXIT
"${compose[@]}" run --rm -e BENCHMARK_REPLACEMENT_CANARY=true \
  -e BENCHMARK_CANARY_SERVER_TIMEOUT_SECONDS="${BENCHMARK_CANARY_SERVER_TIMEOUT_SECONDS:-30}" backend-persistence-tests \
  python -m pytest tests/integration/persistence/test_benchmark_replacement_canary.py \
  --confcutdir=tests/integration/persistence -v --tb=short -s
