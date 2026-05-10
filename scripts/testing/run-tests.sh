#!/usr/bin/env bash

# Script to build and run tests using Docker Compose
# Follows Constitution requirement: Unified Docker Compose Standard

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Enable BuildKit for better caching
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

# Parse command line arguments
TEST_TYPE="${1:-all}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="docker-compose.test.yml"
TEST_STACK_ENV_FILE=".test-stack.env"
SHOULD_CLEANUP=0

test_stack_compose() {
    "${SCRIPT_DIR}/docker-test-compose.sh" "$@"
}

cleanup_test_stack() {
    if [[ "${SHOULD_CLEANUP}" != "1" ]]; then
        return 0
    fi

    echo -e "${GREEN}Cleaning up test containers...${NC}"
    test_stack_compose down >/dev/null 2>&1 || true
    rm -f "${TEST_STACK_ENV_FILE}"
}

trap cleanup_test_stack EXIT

load_live_db_tunnel_env_if_present() {
    local tunnel_env_file="${ROOT_DIR}/scripts/local_db_tunnel_env.sh"

    if [[ -f "${tunnel_env_file}" ]]; then
        # shellcheck disable=SC1090
        . "${tunnel_env_file}"
    fi
}

# Ensure scripts in this folder are executable when checked out without +x.
chmod +x "${SCRIPT_DIR}/prepare-test-stack.sh" "${SCRIPT_DIR}/load-home-test-env.sh" "${SCRIPT_DIR}/docker-test-compose.sh" 2>/dev/null || true

# Handle build command
if [ "$TEST_TYPE" = "build" ]; then
    echo -e "${BLUE}Building test Docker image with layer caching...${NC}"
    echo -e "${YELLOW}This will take several minutes on first build.${NC}"
    echo -e "${GREEN}Subsequent builds will be much faster due to layer caching.${NC}"

    # Check if image exists for caching
    if docker images | grep -q "ai-curation-backend-tests"; then
        echo -e "${GREEN}Found existing image for cache.${NC}"
    else
        echo -e "${YELLOW}First build - no cache available yet.${NC}"
    fi

    test_stack_compose build backend-tests
    echo -e "${GREEN}✅ Build complete!${NC}"
    exit 0
fi

if [ "$TEST_TYPE" = "prepare" ]; then
    echo -e "${BLUE}Preparing isolated test infrastructure...${NC}"
    "${SCRIPT_DIR}/prepare-test-stack.sh"
    exit 0
fi

echo -e "${GREEN}Running tests via Docker Compose...${NC}"
SHOULD_CLEANUP=1

case $TEST_TYPE in
  unit)
    echo -e "${YELLOW}Running unit tests...${NC}"
    test_stack_compose run --rm backend-unit-tests
    ;;
  integration)
    echo -e "${YELLOW}Running integration tests with sample PDF...${NC}"
    "${SCRIPT_DIR}/prepare-test-stack.sh"
    test_stack_compose run --rm backend-integration-tests
    ;;
  contract)
    echo -e "${YELLOW}Running contract tests...${NC}"
    "${SCRIPT_DIR}/prepare-test-stack.sh"
    test_stack_compose run --rm backend-contract-tests
    ;;
  domain-envelope-unit)
    echo -e "${YELLOW}Running offline domain-envelope release unit suite...${NC}"
    test_stack_compose run --rm backend-unit-tests \
      bash /app/backend/tests/unit/run_ci_unit_tests.sh --suite domain-envelope-release
    ;;
  alliance-domain-contract)
    echo -e "${YELLOW}Running Alliance domain-pack contract suite...${NC}"
    "${SCRIPT_DIR}/prepare-test-stack.sh"
    test_stack_compose run --rm backend-contract-tests \
      bash -lc "alembic upgrade head && bash tests/contract/run_ci_contract_core_tests.sh --suite alliance-domain-pack"
    ;;
  alliance-live-db-contract)
    echo -e "${YELLOW}Running explicit Alliance live DB contract suite...${NC}"
    load_live_db_tunnel_env_if_present
    export ALLIANCE_LIVE_DB_CONTRACT_TESTS=1
    test_stack_compose run --rm backend-contract-tests \
      bash tests/contract/run_ci_contract_core_tests.sh --suite alliance-live-db
    ;;
  domain-envelope-release)
    echo -e "${YELLOW}Running full 0.7.0 domain-envelope release gate...${NC}"
    test_stack_compose run --rm backend-unit-tests \
      bash /app/backend/tests/unit/run_ci_unit_tests.sh --suite domain-envelope-release
    "${SCRIPT_DIR}/prepare-test-stack.sh"
    test_stack_compose run --rm backend-contract-tests \
      bash -lc "alembic upgrade head && bash tests/contract/run_ci_contract_core_tests.sh --suite alliance-domain-pack"
    load_live_db_tunnel_env_if_present
    export ALLIANCE_LIVE_DB_CONTRACT_TESTS=1
    test_stack_compose run --rm backend-contract-tests \
      bash tests/contract/run_ci_contract_core_tests.sh --suite alliance-live-db
    ;;
  all)
    echo -e "${YELLOW}Running all tests...${NC}"
    "${SCRIPT_DIR}/prepare-test-stack.sh"
    test_stack_compose run --rm backend-tests
    ;;
  *)
    echo -e "${RED}Unknown test type: $TEST_TYPE${NC}"
    echo "Usage: $0 [build|prepare|unit|integration|contract|domain-envelope-unit|alliance-domain-contract|alliance-live-db-contract|domain-envelope-release|all]"
    echo ""
    echo "  build       - Build the test Docker image"
    echo "  prepare     - Start isolated test infra (Postgres + Weaviate) and run migrations"
    echo "  unit        - Run unit tests"
    echo "  integration - Run integration tests with sample PDF"
    echo "  contract    - Run contract tests for API endpoints"
    echo "  domain-envelope-unit       - Run offline provider-agnostic domain-envelope release unit tests"
    echo "  alliance-domain-contract   - Run Alliance domain-pack + LinkML contract tests"
    echo "  alliance-live-db-contract  - Run explicit live DB projection contract tests"
    echo "  domain-envelope-release    - Run provider-agnostic, Alliance LinkML, and explicit live DB gates"
    echo "  all         - Run all tests (default)"
    exit 1
    ;;
esac

echo -e "${GREEN}Tests completed!${NC}"
