#!/usr/bin/env bash
# VM/agent-safe production Loki status helper.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/prod_loki_tunnel_common.sh
source "${SCRIPT_DIR}/../lib/prod_loki_tunnel_common.sh"

endpoint_file=""
json=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --endpoint-file)
      endpoint_file="${2:-}"
      shift 2
      ;;
    --json)
      json=1
      shift
      ;;
    --help|-h)
      echo "Usage: symphony_prod_loki_status.sh [--endpoint-file PATH] [--json]"
      exit 0
      ;;
    *)
      prod_loki_error "Unknown argument: $1"
      exit 2
      ;;
  esac
done

repo_root="$(prod_loki_repo_root)"
endpoint_file="${endpoint_file:-$(prod_loki_endpoint_file "${repo_root}")}"

if ! prod_loki_load_endpoint_file "${endpoint_file}"; then
  echo "Production Loki endpoint is not configured."
  echo "Repair: use the Symphony UI repair button or run scripts/utilities/symphony_prod_loki_host_repair.sh on Chris's workstation."
  exit 1
fi

ready_status="not_ready"
labels_status="not_checked"
status="offline"
if curl -fsS -m 3 "${LOKI_URL%/}/ready" >/dev/null 2>&1; then
  ready_status="ready"
  status="online"
  if curl -fsS -m 5 "${LOKI_URL%/}/loki/api/v1/labels" >/dev/null 2>&1; then
    labels_status="ready"
  else
    labels_status="failed"
    status="degraded"
  fi
fi

if [[ "${json}" -eq 1 ]]; then
  python3 - "$status" "$LOKI_URL" "$ready_status" "$labels_status" <<'PY'
import json
import sys

status, url, ready, labels = sys.argv[1:]
print(json.dumps({
    "status": status,
    "loki_url": url,
    "ready_status": ready,
    "labels_status": labels,
}, sort_keys=True))
PY
else
  echo "status=${status}"
  echo "loki_url=${LOKI_URL}"
  echo "ready_status=${ready_status}"
  echo "labels_status=${labels_status}"
  if [[ "${status}" != "online" ]]; then
    echo "repair=Use the Symphony UI repair button or run scripts/utilities/symphony_prod_loki_host_repair.sh on Chris's workstation."
  fi
fi

[[ "${status}" == "online" ]]
