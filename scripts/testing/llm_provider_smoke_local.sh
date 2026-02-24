#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
TIMESTAMP_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
STAMP_FILE="$(date -u +"%Y%m%dT%H%M%SZ")"
OUT_DIR="file_outputs/temp"
OUT_FILE="${OUT_DIR}/llm_provider_smoke_local_${STAMP_FILE}.json"

mkdir -p "${OUT_DIR}"

PY_BIN=""
if command -v python3 >/dev/null 2>&1; then
  PY_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PY_BIN="python"
else
  echo "Error: python3/python is required for JSON escaping in smoke script." >&2
  exit 1
fi

AUTH_ARGS=()
if [[ -n "${SMOKE_BEARER_TOKEN:-}" ]]; then
  AUTH_ARGS=(-H "Authorization: Bearer ${SMOKE_BEARER_TOKEN}")
fi

declare -i PASS_COUNT=0
declare -i FAIL_COUNT=0

PROVIDER_HEALTH_BODY=""
LAST_CHECK_JSON=""

json_escape() {
  local value="${1:-}"
  "${PY_BIN}" - "$value" <<'PY'
import json
import sys
print(json.dumps(sys.argv[1]))
PY
}

wait_for_backend() {
  local attempts="${1:-20}"
  local delay_sec="${2:-1}"
  local code
  for _ in $(seq 1 "${attempts}"); do
    code="$(curl -sS --max-time 2 "${AUTH_ARGS[@]}" -o /dev/null -w "%{http_code}" "${BASE_URL}/health" || true)"
    if [[ "${code}" == "200" ]]; then
      return 0
    fi
    sleep "${delay_sec}"
  done
  return 1
}

run_check() {
  local test_id="$1"
  local path="$2"
  local expected="$3"
  local max_time="${4:-8}"

  local tmp_body
  tmp_body="$(mktemp)"
  local http_code
  http_code="$(curl -sS --max-time "${max_time}" "${AUTH_ARGS[@]}" \
    -w "%{http_code}" -o "${tmp_body}" "${BASE_URL}${path}" || true)"

  local body
  body="$(cat "${tmp_body}" 2>/dev/null || true)"
  rm -f "${tmp_body}"

  local status="pass"
  if [[ "${http_code}" != "${expected}" ]]; then
    status="fail"
  fi

  if [[ "${status}" == "pass" ]]; then
    PASS_COUNT+=1
  else
    FAIL_COUNT+=1
  fi

  if [[ "${test_id}" == "A1" ]]; then
    PROVIDER_HEALTH_BODY="${body}"
  fi

  local body_preview
  body_preview="$(printf "%s" "${body}" | head -c 500)"

  LAST_CHECK_JSON="$(printf '{"test_id":"%s","path":"%s","expected_status":"%s","http_status":"%s","result":"%s","body_preview":%s}' \
    "${test_id}" \
    "${path}" \
    "${expected}" \
    "${http_code}" \
    "${status}" \
    "$(json_escape "${body_preview}")")"
}

CHECKS_ITEMS=()
wait_for_backend 20 1 || true
run_check "BASE_HEALTH" "/health" "200"
CHECKS_ITEMS+=("${LAST_CHECK_JSON}")
run_check "A1" "/api/admin/health/llm-providers" "200"
CHECKS_ITEMS+=("${LAST_CHECK_JSON}")
run_check "A1B" "/api/agent-studio/models" "200"
CHECKS_ITEMS+=("${LAST_CHECK_JSON}")

CHECKS_JSON="["
for i in "${!CHECKS_ITEMS[@]}"; do
  if [[ "${i}" != "0" ]]; then
    CHECKS_JSON+=","
  fi
  CHECKS_JSON+="${CHECKS_ITEMS[$i]}"
done
CHECKS_JSON+="]"

A1_STRUCTURAL_STATUS="not_evaluated"
A1_STRUCTURAL_ERRORS="[]"

if [[ -n "${PROVIDER_HEALTH_BODY}" ]]; then
  A1_ANALYSIS="$("${PY_BIN}" - "${PROVIDER_HEALTH_BODY}" <<'PY'
import json
import sys

raw = sys.argv[1] if len(sys.argv) > 1 else ""
out = {
    "status": "not_json",
    "error_count": None,
}

try:
    payload = json.loads(raw)
except Exception:
    print(json.dumps(out))
    raise SystemExit(0)

errors = payload.get("errors", [])
if isinstance(errors, list):
    out["error_count"] = len(errors)
    out["errors"] = errors
    out["status"] = "pass" if len(errors) == 0 else "fail"
else:
    out["status"] = "invalid_errors_field"
print(json.dumps(out))
PY
)"

  A1_STRUCTURAL_STATUS="$("${PY_BIN}" - "${A1_ANALYSIS}" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
print(payload.get("status", "not_evaluated"))
PY
)"
  A1_STRUCTURAL_ERRORS="$("${PY_BIN}" - "${A1_ANALYSIS}" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
errors = payload.get("errors", [])
print(json.dumps(errors))
PY
)"

  if [[ "${A1_STRUCTURAL_STATUS}" == "pass" ]]; then
    PASS_COUNT+=1
  else
    FAIL_COUNT+=1
  fi
else
  FAIL_COUNT+=1
fi

OVERALL_STATUS="pass"
if (( FAIL_COUNT > 0 )); then
  OVERALL_STATUS="fail"
fi

cat > "${OUT_FILE}" <<EOF
{
  "timestamp_utc": "${TIMESTAMP_UTC}",
  "base_url": "${BASE_URL}",
  "overall_status": "${OVERALL_STATUS}",
  "pass_count": ${PASS_COUNT},
  "fail_count": ${FAIL_COUNT},
  "checks": ${CHECKS_JSON},
  "derived_checks": [
    {
      "test_id": "A1_STRUCTURAL",
      "result": "${A1_STRUCTURAL_STATUS}",
      "errors": ${A1_STRUCTURAL_ERRORS}
    }
  ]
}
EOF

echo "LLM provider local smoke complete."
echo "Result: ${OVERALL_STATUS} (pass=${PASS_COUNT}, fail=${FAIL_COUNT})"
echo "Evidence file: ${OUT_FILE}"
