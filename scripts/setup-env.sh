#!/bin/bash
# =============================================================================
# AI Curation Platform - Environment Setup Script
# =============================================================================
#
# This script sets up the secure environment configuration directory at
# ~/.agr_ai_curation/ which keeps secrets outside the repository.
#
# Usage:
#   ./scripts/setup-env.sh
#   # or
#   make setup
#
# =============================================================================

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
CONFIG_DIR="$HOME/.agr_ai_curation"
TRACE_REVIEW_CONFIG_DIR="$CONFIG_DIR/trace_review"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}AI Curation Platform - Environment Setup${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Create directories
echo -e "${GREEN}Creating configuration directories...${NC}"
mkdir -p "$CONFIG_DIR"
mkdir -p "$TRACE_REVIEW_CONFIG_DIR"
echo "  Created: $CONFIG_DIR"
echo "  Created: $TRACE_REVIEW_CONFIG_DIR"
echo ""

# Copy main .env.example
if [ -f "$CONFIG_DIR/.env" ]; then
    echo -e "${YELLOW}Main .env already exists at $CONFIG_DIR/.env${NC}"
    echo "  Skipping copy to avoid overwriting your configuration."
    echo "  To reset, delete the file and run this script again."
else
    if [ -f "$REPO_ROOT/.env.example" ]; then
        cp "$REPO_ROOT/.env.example" "$CONFIG_DIR/.env"
        chmod 600 "$CONFIG_DIR/.env"
        echo -e "${GREEN}Created: $CONFIG_DIR/.env${NC}"
        echo "  Copied from: $REPO_ROOT/.env.example"
        echo "  Permissions set to 600 (owner read/write only)"
    else
        echo -e "${RED}Warning: $REPO_ROOT/.env.example not found${NC}"
        echo "  You'll need to create $CONFIG_DIR/.env manually."
    fi
fi
echo ""

# Copy trace_review .env.example
if [ -f "$TRACE_REVIEW_CONFIG_DIR/.env" ]; then
    echo -e "${YELLOW}trace_review .env already exists at $TRACE_REVIEW_CONFIG_DIR/.env${NC}"
    echo "  Skipping copy to avoid overwriting your configuration."
else
    if [ -f "$REPO_ROOT/trace_review/backend/.env.example" ]; then
        cp "$REPO_ROOT/trace_review/backend/.env.example" "$TRACE_REVIEW_CONFIG_DIR/.env"
        chmod 600 "$TRACE_REVIEW_CONFIG_DIR/.env"
        echo -e "${GREEN}Created: $TRACE_REVIEW_CONFIG_DIR/.env${NC}"
        echo "  Copied from: $REPO_ROOT/trace_review/backend/.env.example"
        echo "  Permissions set to 600 (owner read/write only)"
    else
        echo -e "${YELLOW}Note: $REPO_ROOT/trace_review/backend/.env.example not found${NC}"
        echo "  trace_review configuration skipped (optional component)."
    fi
fi
echo ""

# Verify setup
echo -e "${BLUE}Verification:${NC}"
echo ""
if [ -f "$CONFIG_DIR/.env" ]; then
    echo -e "  ${GREEN}✓${NC} $CONFIG_DIR/.env"
    ls -la "$CONFIG_DIR/.env" | awk '{print "    " $1 " " $9}'
else
    echo -e "  ${RED}✗${NC} $CONFIG_DIR/.env - NOT FOUND"
fi

if [ -f "$TRACE_REVIEW_CONFIG_DIR/.env" ]; then
    echo -e "  ${GREEN}✓${NC} $TRACE_REVIEW_CONFIG_DIR/.env"
    ls -la "$TRACE_REVIEW_CONFIG_DIR/.env" | awk '{print "    " $1 " " $9}'
else
    echo -e "  ${YELLOW}○${NC} $TRACE_REVIEW_CONFIG_DIR/.env - not configured (optional)"
fi
echo ""

# Next steps
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Next Steps${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo "1. Edit your configuration files with your API keys and credentials:"
echo ""
echo -e "   ${GREEN}nano $CONFIG_DIR/.env${NC}"
echo ""
echo "   Required settings to configure:"
echo "   - OPENAI_API_KEY (for LLM and embeddings)"
echo "   - POSTGRES_PASSWORD (for database)"
echo "   - REDIS_AUTH (for Redis)"
echo ""
echo "2. Start the development environment:"
echo ""
echo -e "   ${GREEN}make dev${NC}"
echo ""
echo "3. View available commands:"
echo ""
echo -e "   ${GREEN}make help${NC}"
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Setup complete!${NC}"
echo -e "${BLUE}========================================${NC}"
