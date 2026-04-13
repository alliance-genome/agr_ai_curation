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
  all)
    echo -e "${YELLOW}Running all tests...${NC}"
    "${SCRIPT_DIR}/prepare-test-stack.sh"
    test_stack_compose run --rm backend-tests
    ;;
  *)
    echo -e "${RED}Unknown test type: $TEST_TYPE${NC}"
    echo "Usage: $0 [build|prepare|unit|integration|contract|all]"
    echo ""
    echo "  build       - Build the test Docker image"
    echo "  prepare     - Start isolated test infra (Postgres + Weaviate) and run migrations"
    echo "  unit        - Run unit tests"
    echo "  integration - Run integration tests with sample PDF"
    echo "  contract    - Run contract tests for API endpoints"
    echo "  all         - Run all tests (default)"
    exit 1
    ;;
esac

echo -e "${GREEN}Tests completed!${NC}"
