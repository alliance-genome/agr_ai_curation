#!/bin/bash
# Maintenance Mode Helper Script
#
# Usage:
#   ./scripts/maintenance_mode.sh on    # Enable maintenance mode
#   ./scripts/maintenance_mode.sh off   # Disable maintenance mode
#   ./scripts/maintenance_mode.sh status # Check current status

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

check_maintenance_message() {
    if [ -f "config/maintenance_message.txt" ]; then
        # Check if there's a non-comment, non-empty line
        MESSAGE=$(grep -v '^#' config/maintenance_message.txt | grep -v '^$' | head -n 1)
        if [ -n "$MESSAGE" ]; then
            echo -e "${YELLOW}Maintenance message:${NC} $MESSAGE"
            return 0
        fi
    fi
    echo -e "${GREEN}No maintenance message set${NC}"
    return 1
}

case "$1" in
    on|enable|start)
        echo -e "${YELLOW}Enabling maintenance mode...${NC}"
        echo ""

        # Check if maintenance message is set
        check_maintenance_message || {
            echo ""
            echo -e "${RED}Warning: No maintenance message set in maintenance_message.txt${NC}"
            echo "Consider adding a message before enabling maintenance mode."
            echo ""
            read -p "Continue anyway? (y/N) " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                echo "Aborted."
                exit 1
            fi
        }

        echo ""
        echo "Stopping main services..."
        docker compose down

        echo ""
        echo "Starting maintenance page..."
        docker compose -f docker-compose.maintenance.yml up -d --build

        echo ""
        echo -e "${GREEN}Maintenance mode enabled!${NC}"
        echo "Maintenance page is now serving on port 3002"
        ;;

    off|disable|stop)
        echo -e "${YELLOW}Disabling maintenance mode...${NC}"
        echo ""

        echo "Stopping maintenance page..."
        docker compose -f docker-compose.maintenance.yml down 2>/dev/null || true

        echo ""
        echo "Starting main services..."
        docker compose up -d

        echo ""
        echo -e "${GREEN}Maintenance mode disabled!${NC}"
        echo "Main services are now running"
        echo ""
        echo -e "${YELLOW}Remember to clear the maintenance message in config/maintenance_message.txt${NC}"
        ;;

    status)
        echo -e "${YELLOW}Checking maintenance status...${NC}"
        echo ""

        # Check maintenance message
        check_maintenance_message
        echo ""

        # Check if maintenance container is running
        if docker compose -f docker-compose.maintenance.yml ps 2>/dev/null | grep -q "maintenance"; then
            echo -e "${RED}Maintenance page container: RUNNING${NC}"
            echo "Site is currently in maintenance mode"
        else
            echo -e "${GREEN}Maintenance page container: NOT RUNNING${NC}"
        fi

        # Check if main services are running
        echo ""
        if docker compose ps 2>/dev/null | grep -q "frontend"; then
            echo -e "${GREEN}Main frontend container: RUNNING${NC}"
        else
            echo -e "${RED}Main frontend container: NOT RUNNING${NC}"
        fi

        if docker compose ps 2>/dev/null | grep -q "backend"; then
            echo -e "${GREEN}Main backend container: RUNNING${NC}"
        else
            echo -e "${RED}Main backend container: NOT RUNNING${NC}"
        fi
        ;;

    *)
        echo "Maintenance Mode Helper"
        echo ""
        echo "Usage: $0 {on|off|status}"
        echo ""
        echo "Commands:"
        echo "  on, enable, start   - Stop main services and start maintenance page"
        echo "  off, disable, stop  - Stop maintenance page and start main services"
        echo "  status              - Show current maintenance status"
        echo ""
        echo "Before enabling maintenance mode, update the message in:"
        echo "  config/maintenance_message.txt"
        exit 1
        ;;
esac
