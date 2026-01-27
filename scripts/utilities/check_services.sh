#!/bin/bash

# Check Services Script
# Verifies all AI Curation services are running properly

echo "======================================"
echo "AI Curation Services Health Check"
echo "======================================"
echo ""

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check Docker services
echo "📦 Docker Services:"
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Service}}" | while IFS=$'\t' read -r name status service; do
    if [[ "$status" == *"Up"* ]] && [[ "$status" == *"healthy"* ]]; then
        echo -e "  ${GREEN}✓${NC} $service (healthy)"
    elif [[ "$status" == *"Up"* ]]; then
        echo -e "  ${YELLOW}⚠${NC} $service (running, not healthy)"
    else
        echo -e "  ${RED}✗${NC} $service (down)"
    fi
done

echo ""
echo "🌐 API Endpoints:"

# Check backend health
echo -n "  Backend API: "
if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} http://localhost:8000"
else
    echo -e "${RED}✗${NC} Not responding"
fi

# Check frontend
echo -n "  Frontend: "
if curl -s http://localhost:3002 > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} http://localhost:3002"
else
    echo -e "${RED}✗${NC} Not responding"
fi

# Check Weaviate
echo -n "  Weaviate: "
if curl -s http://localhost:8080/v1/.well-known/ready > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} http://localhost:8080"
else
    echo -e "${RED}✗${NC} Not responding"
fi

# Check Docling service (if configured)
DOCLING_URL="${DOCLING_SERVICE_URL:-}"
echo -n "  Docling Service: "
if [ -z "$DOCLING_URL" ]; then
    echo -e "${YELLOW}⚠${NC} Not configured (set DOCLING_SERVICE_URL)"
elif curl -s --max-time 2 "${DOCLING_URL}/health" > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} ${DOCLING_URL}"
else
    echo -e "${YELLOW}⚠${NC} Not reachable (${DOCLING_URL})"
fi

echo ""
echo "💾 Database:"

# Check PostgreSQL
echo -n "  PostgreSQL: "
if docker exec ai_curation_prototype-postgres-1 psql -U postgres -d ai_curation -c "SELECT 1;" > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} Database accessible"
else
    echo -e "${RED}✗${NC} Cannot connect to database"
fi

echo ""
echo "======================================"
echo "Use 'docker compose logs [service]' to check logs"
echo "Use 'docker compose restart [service]' to restart a service"