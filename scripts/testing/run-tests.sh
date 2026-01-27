#!/bin/bash

# Script to build and run tests using Docker Compose
# Follows Constitution requirement: Unified Docker Compose Standard

set -e

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

    docker compose -f docker-compose.test.yml build backend-tests
    echo -e "${GREEN}✅ Build complete!${NC}"
    exit 0
fi

echo -e "${GREEN}Running tests via Docker Compose...${NC}"

case $TEST_TYPE in
  unit)
    echo -e "${YELLOW}Running unit tests...${NC}"
    docker compose -f docker-compose.test.yml run --rm backend-unit-tests
    ;;
  integration)
    echo -e "${YELLOW}Running integration tests with sample PDF...${NC}"
    docker compose -f docker-compose.test.yml run --rm backend-integration-tests
    ;;
  contract)
    echo -e "${YELLOW}Running contract tests...${NC}"
    docker compose -f docker-compose.test.yml run --rm backend-contract-tests
    ;;
  all)
    echo -e "${YELLOW}Running all tests...${NC}"
    docker compose -f docker-compose.test.yml run --rm backend-tests
    ;;
  *)
    echo -e "${RED}Unknown test type: $TEST_TYPE${NC}"
    echo "Usage: $0 [build|unit|integration|contract|all]"
    echo ""
    echo "  build       - Build the test Docker image"
    echo "  unit        - Run unit tests"
    echo "  integration - Run integration tests with sample PDF"
    echo "  contract    - Run contract tests for API endpoints"
    echo "  all         - Run all tests (default)"
    exit 1
    ;;
esac

# Cleanup
echo -e "${GREEN}Cleaning up test containers...${NC}"
docker compose -f docker-compose.test.yml down

echo -e "${GREEN}Tests completed!${NC}"