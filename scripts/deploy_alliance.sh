#!/bin/bash
# =============================================================================
# DEPLOY ALLIANCE CONTENT
# =============================================================================
# Deploys Alliance-specific content to the config directories.
# This script copies organization-specific agents and tools to the runtime
# locations where the loaders expect them.
#
# Usage:
#   ./scripts/deploy_alliance.sh           # Deploy all Alliance content
#   ./scripts/deploy_alliance.sh --clean   # Remove old content before deploying
#   ./scripts/deploy_alliance.sh --dry-run # Show what would be copied
#
# What gets deployed:
#   alliance_agents/*       -> config/agents/
#   alliance_config/*.yaml  -> config/
#   backend/tools/alliance_tools/*  -> backend/tools/custom/
#
# Note: This script is idempotent and safe to run multiple times.
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Source and destination directories
ALLIANCE_AGENTS="${PROJECT_ROOT}/alliance_agents"
ALLIANCE_CONFIG="${PROJECT_ROOT}/alliance_config"
ALLIANCE_TOOLS="${PROJECT_ROOT}/backend/tools/alliance_tools"

CONFIG_DIR="${PROJECT_ROOT}/config"
CONFIG_AGENTS="${PROJECT_ROOT}/config/agents"
CUSTOM_TOOLS="${PROJECT_ROOT}/backend/tools/custom"

# Parse arguments
CLEAN=false
DRY_RUN=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --clean)
            CLEAN=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --clean     Remove existing agent content before deploying"
            echo "  --dry-run   Show what would be copied without making changes"
            echo "  --verbose   Show detailed output"
            echo "  --help      Show this help message"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_verbose() {
    if [ "$VERBOSE" = true ]; then
        echo -e "${BLUE}[VERBOSE]${NC} $1"
    fi
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."

    if [ ! -d "$ALLIANCE_AGENTS" ]; then
        log_error "Alliance agents directory not found: $ALLIANCE_AGENTS"
        exit 1
    fi

    if [ ! -d "$ALLIANCE_CONFIG" ]; then
        log_warn "Alliance config directory not found: $ALLIANCE_CONFIG"
    fi

    if [ ! -d "$ALLIANCE_TOOLS" ]; then
        log_warn "Alliance tools directory not found: $ALLIANCE_TOOLS"
    fi

    if [ ! -d "$CONFIG_AGENTS" ]; then
        log_warn "Config agents directory not found, creating: $CONFIG_AGENTS"
        if [ "$DRY_RUN" = false ]; then
            mkdir -p "$CONFIG_AGENTS"
        fi
    fi

    if [ ! -d "$CUSTOM_TOOLS" ]; then
        log_warn "Custom tools directory not found, creating: $CUSTOM_TOOLS"
        if [ "$DRY_RUN" = false ]; then
            mkdir -p "$CUSTOM_TOOLS"
        fi
    fi

    log_success "Prerequisites check passed"
}

# Clean existing Alliance content (but preserve core files)
clean_existing() {
    if [ "$CLEAN" = false ]; then
        return
    fi

    log_info "Cleaning existing Alliance agent content..."

    # Find agent folders from alliance_agents and remove corresponding ones in config/agents
    for agent_dir in "$ALLIANCE_AGENTS"/*/; do
        agent_name=$(basename "$agent_dir")

        # Skip underscore-prefixed folders
        if [[ "$agent_name" == _* ]]; then
            continue
        fi

        target_dir="${CONFIG_AGENTS}/${agent_name}"

        if [ -d "$target_dir" ]; then
            log_verbose "Removing: $target_dir"
            if [ "$DRY_RUN" = false ]; then
                rm -rf "$target_dir"
            else
                echo "  Would remove: $target_dir"
            fi
        fi
    done

    log_success "Clean complete"
}

# Copy Alliance agents to config/agents
deploy_agents() {
    log_info "Deploying Alliance agents..."

    local count=0

    for agent_dir in "$ALLIANCE_AGENTS"/*/; do
        agent_name=$(basename "$agent_dir")

        # Skip underscore-prefixed folders (templates/examples)
        if [[ "$agent_name" == _* ]]; then
            log_verbose "Skipping template folder: $agent_name"
            continue
        fi

        target_dir="${CONFIG_AGENTS}/${agent_name}"

        log_verbose "Copying: $agent_name -> $target_dir"

        if [ "$DRY_RUN" = false ]; then
            # Use rsync for smart copying (only updates changed files)
            rsync -a --delete "$agent_dir" "$target_dir/"
        else
            echo "  Would copy: $agent_dir -> $target_dir"
        fi

        count=$((count + 1))
    done

    log_success "Deployed $count agents"
}

# Copy Alliance config files to config/
deploy_config() {
    if [ ! -d "$ALLIANCE_CONFIG" ]; then
        log_warn "Skipping config deployment - directory not found"
        return
    fi

    log_info "Deploying Alliance config files..."

    local count=0

    for config_file in "$ALLIANCE_CONFIG"/*.yaml; do
        if [ ! -f "$config_file" ]; then
            continue
        fi

        filename=$(basename "$config_file")
        target_file="${CONFIG_DIR}/${filename}"

        log_verbose "Copying: $filename -> $target_file"

        if [ "$DRY_RUN" = false ]; then
            cp "$config_file" "$target_file"
        else
            echo "  Would copy: $config_file -> $target_file"
        fi

        count=$((count + 1))
    done

    log_success "Deployed $count config files"
}

# Copy Alliance tools to backend/tools/custom/
deploy_tools() {
    if [ ! -d "$ALLIANCE_TOOLS" ]; then
        log_warn "Skipping tools deployment - directory not found"
        return
    fi

    log_info "Deploying Alliance tools..."

    local count=0

    for tool_file in "$ALLIANCE_TOOLS"/*; do
        if [ ! -f "$tool_file" ]; then
            continue
        fi

        filename=$(basename "$tool_file")

        # Skip __pycache__ and other non-Python files
        if [[ "$filename" == __* ]] || [[ "$filename" == *.pyc ]]; then
            continue
        fi

        target_file="${CUSTOM_TOOLS}/${filename}"

        log_verbose "Copying: $filename -> $target_file"

        if [ "$DRY_RUN" = false ]; then
            cp "$tool_file" "$target_file"
        else
            echo "  Would copy: $tool_file -> $target_file"
        fi

        count=$((count + 1))
    done

    log_success "Deployed $count tool files"
}

# Verify deployment
verify_deployment() {
    if [ "$DRY_RUN" = true ]; then
        return
    fi

    log_info "Verifying deployment..."

    local errors=0

    for agent_dir in "$ALLIANCE_AGENTS"/*/; do
        agent_name=$(basename "$agent_dir")

        # Skip underscore-prefixed folders
        if [[ "$agent_name" == _* ]]; then
            continue
        fi

        target_dir="${CONFIG_AGENTS}/${agent_name}"

        # Check agent.yaml exists
        if [ ! -f "$target_dir/agent.yaml" ]; then
            log_error "Missing agent.yaml: $target_dir"
            errors=$((errors + 1))
        fi

        # Check prompt.yaml exists
        if [ ! -f "$target_dir/prompt.yaml" ]; then
            log_error "Missing prompt.yaml: $target_dir"
            errors=$((errors + 1))
        fi
    done

    if [ $errors -gt 0 ]; then
        log_error "Verification failed with $errors errors"
        exit 1
    fi

    log_success "Verification passed"
}

# Print summary
print_summary() {
    echo ""
    echo "=============================================="
    if [ "$DRY_RUN" = true ]; then
        echo "  DRY RUN COMPLETE (no changes made)"
    else
        echo "  ALLIANCE DEPLOYMENT COMPLETE"
    fi
    echo "=============================================="
    echo ""

    # Count deployed agents
    local agent_count=0
    for agent_dir in "$CONFIG_AGENTS"/*/; do
        agent_name=$(basename "$agent_dir")
        if [[ "$agent_name" != _* ]] && [[ "$agent_name" != "supervisor" ]]; then
            agent_count=$((agent_count + 1))
        fi
    done

    # Count config files
    local config_count=0
    for f in "$CONFIG_DIR"/*.yaml; do
        if [ -f "$f" ]; then
            config_count=$((config_count + 1))
        fi
    done

    # Count tool files
    local tool_count=0
    for f in "$CUSTOM_TOOLS"/*.py; do
        if [ -f "$f" ]; then
            tool_count=$((tool_count + 1))
        fi
    done

    echo "  Agents deployed:  $agent_count  ($CONFIG_AGENTS)"
    echo "  Config files:     $config_count  ($CONFIG_DIR)"
    echo "  Custom tools:     $tool_count  ($CUSTOM_TOOLS)"
    echo ""

    if [ "$DRY_RUN" = false ]; then
        echo "To verify, run:"
        echo "  ls -la $CONFIG_AGENTS"
        echo "  ls -la $CONFIG_DIR/*.yaml"
        echo "  ls -la $CUSTOM_TOOLS"
    fi
}

# Main
main() {
    echo ""
    echo "=============================================="
    echo "  Alliance Content Deployment Script"
    echo "=============================================="
    echo ""

    if [ "$DRY_RUN" = true ]; then
        log_warn "DRY RUN MODE - No changes will be made"
        echo ""
    fi

    check_prerequisites
    clean_existing
    deploy_agents
    deploy_config
    deploy_tools
    verify_deployment
    print_summary
}

main
