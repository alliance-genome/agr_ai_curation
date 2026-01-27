#!/bin/bash
#
# Generate coverage data for validation analysis
#
# This script:
# 1. Runs pytest with coverage in the backend container
# 2. Generates JSON coverage report
# 3. Copies it to project root for analysis
#
# Usage:
#   ./scripts/utilities/generate_coverage.sh
#

set -e

echo "======================================================================="
echo "Generating Coverage Data"
echo "======================================================================="
echo ""

# Navigate to project root
cd "$(dirname "$0")/../.."

echo "📊 Step 1: Running tests with coverage..."
docker compose exec -T backend coverage run -m pytest -v || {
    echo "⚠️  Some tests failed, but coverage data was still generated"
}

echo ""
echo "📄 Step 2: Generating JSON coverage report..."
docker compose exec -T backend coverage json -o /tmp/coverage.json

echo ""
echo "💾 Step 3: Copying coverage data to host..."
BACKEND_CONTAINER=$(docker compose ps -q backend)
docker cp "${BACKEND_CONTAINER}:/tmp/coverage.json" ./coverage.json

echo ""
echo "✅ Coverage data generated: coverage.json"
echo ""
echo "📈 Coverage Summary:"
docker compose exec -T backend coverage report --skip-empty | head -20

echo ""
echo "======================================================================="
echo "Next Steps:"
echo "  1. Run validation: python3 scripts/utilities/validate_unused_files.py"
echo "  2. View HTML report: docker compose exec backend coverage html"
echo "                      docker compose exec backend python -m http.server 9000 -d htmlcov"
echo "======================================================================="
