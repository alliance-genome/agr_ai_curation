#!/usr/bin/env bash
# VM/agent-safe production Loki query helper.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/prod_loki_tunnel_common.sh
source "${SCRIPT_DIR}/../lib/prod_loki_tunnel_common.sh"

usage() {
  cat <<'EOF'
Usage:
  symphony_prod_loki_query.sh [options]

Options:
  --endpoint-file PATH
  --labels
  --services
  --service NAME
  --since DURATION          Default: 1h
  --until VALUE             Loki end timestamp.
  --contains TEXT
  --trace-id ID
  --session-id ID
  --feedback-id ID
  --level LEVEL
  --limit N                 Default: 200, max sent by helper: 5000.
  --raw-logql QUERY
  --json                    Print raw JSON.
  --print-logql             Print built LogQL and exit.
EOF
}

endpoint_file=""
mode="query"
service=""
since="1h"
until=""
contains=()
level=""
limit="200"
raw_logql=""
json=0
print_logql=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --endpoint-file)
      endpoint_file="${2:-}"
      shift 2
      ;;
    --labels)
      mode="labels"
      shift
      ;;
    --services)
      mode="services"
      shift
      ;;
    --service)
      service="${2:-}"
      shift 2
      ;;
    --since)
      since="${2:-}"
      shift 2
      ;;
    --until)
      until="${2:-}"
      shift 2
      ;;
    --contains)
      contains+=("${2:-}")
      shift 2
      ;;
    --trace-id)
      contains+=("${2:-}")
      shift 2
      ;;
    --session-id)
      contains+=("${2:-}")
      shift 2
      ;;
    --feedback-id)
      contains+=("${2:-}")
      shift 2
      ;;
    --level)
      level="${2:-}"
      shift 2
      ;;
    --limit)
      limit="${2:-}"
      shift 2
      ;;
    --raw-logql)
      raw_logql="${2:-}"
      shift 2
      ;;
    --json)
      json=1
      shift
      ;;
    --print-logql)
      print_logql=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      prod_loki_error "Unknown argument: $1"
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "${limit}" =~ ^[0-9]+$ ]]; then
  prod_loki_error "--limit must be an integer"
  exit 2
fi
if [[ "${limit}" -gt 5000 ]]; then
  prod_loki_error "--limit ${limit} exceeds helper maximum 5000"
  exit 2
fi

repo_root="$(prod_loki_repo_root)"
endpoint_file="${endpoint_file:-$(prod_loki_endpoint_file "${repo_root}")}"
prod_loki_load_endpoint_file "${endpoint_file}"

curl_json() {
  curl -fsS --get "$@"
}

if [[ "${mode}" == "labels" ]]; then
  curl_json "${LOKI_URL%/}/loki/api/v1/labels"
  exit 0
fi

if [[ "${mode}" == "services" ]]; then
  curl_json "${LOKI_URL%/}/loki/api/v1/label/service/values"
  exit 0
fi

logql="$(
  python3 - "$service" "$raw_logql" "$level" "${contains[@]}" <<'PY'
import sys

service = sys.argv[1]
raw = sys.argv[2]
level = sys.argv[3]
contains = sys.argv[4:]

def esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')

if raw:
    print(raw)
    raise SystemExit

selector = "{service=\"" + esc(service) + "\"}" if service else "{service=~\".+\"}"
parts = [selector]
if level:
    parts.append('|= "' + esc(level) + '"')
for item in contains:
    if item:
        parts.append('|= "' + esc(item) + '"')
print(" ".join(parts))
PY
)"

if [[ "${print_logql}" -eq 1 ]]; then
  printf '%s\n' "${logql}"
  exit 0
fi

args=(
  "${LOKI_URL%/}/loki/api/v1/query_range"
  --data-urlencode "query=${logql}"
  --data-urlencode "limit=${limit}"
)

if [[ -n "${since}" ]]; then
  args+=(--data-urlencode "since=${since}")
fi
if [[ -n "${until}" ]]; then
  args+=(--data-urlencode "end=${until}")
fi

if [[ "${json}" -eq 1 || ! -t 1 ]]; then
  curl_json "${args[@]}"
  exit 0
fi

response="$(curl_json "${args[@]}")"
if command -v jq >/dev/null 2>&1; then
  printf '%s\n' "${response}" | jq -r '
    .data.result[]? as $stream
    | $stream.values[]?
    | "\(.[0]) \($stream.stream.service // "unknown") \(.[1])"
  '
else
  printf '%s\n' "${response}"
fi
