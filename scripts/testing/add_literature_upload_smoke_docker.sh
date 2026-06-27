#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export ABC_LITERATURE_READY_UPLOAD_SMOKE_SCRIPT_PATH="${ABC_LITERATURE_READY_UPLOAD_SMOKE_SCRIPT_PATH:-/app/scripts/testing/add_literature_upload_smoke.py}"

exec "${SCRIPT_DIR}/abc_literature_ready_upload_smoke_docker.sh" "$@"
