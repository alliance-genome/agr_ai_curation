#!/usr/bin/env bash
# Restart the host-owned production Loki read-only endpoint.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "${SCRIPT_DIR}/symphony_prod_loki_host_stop.sh" >/dev/null 2>&1 || true
exec bash "${SCRIPT_DIR}/symphony_prod_loki_host_start.sh" "$@"
