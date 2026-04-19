#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BASE_URL="${1:-http://localhost:8000}"
TIMESTAMP_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
STAMP_FILE="$(date -u +"%Y%m%dT%H%M%SZ")"
OUT_DIR="file_outputs/temp"
OUT_FILE="${OUT_DIR}/rerank_provider_smoke_local_${STAMP_FILE}.json"
LOCAL_RERANKER_URL="http://reranker-transformers:8080"

if [[ "${BASH_SOURCE[0]}" == "${0}" && ( "${BASE_URL}" == "-h" || "${BASE_URL}" == "--help" ) ]]; then
  cat <<'EOF'
Usage: ./scripts/testing/rerank_provider_smoke_local.sh [base_url]

Runs a local-stack rerank provider smoke across:
  - bedrock_cohere
  - local_transformers
  - none

The script restarts the local backend per provider mode, verifies backend
startup, checks the reranker service requirement contract, and runs a real
rerank probe from inside the backend container.
EOF
  exit 0
fi

# shellcheck disable=SC1091
. "${SCRIPT_DIR}/../lib/rerank_provider_common.sh"

PY_BIN=""

declare -i PASS_COUNT=0
declare -i FAIL_COUNT=0

LAST_CHECK_JSON=""
LAST_DERIVED_JSON=""

ensure_python_bin() {
  if [[ -n "${PY_BIN}" ]]; then
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    PY_BIN="python3"
    return 0
  fi

  if command -v python >/dev/null 2>&1; then
    PY_BIN="python"
    return 0
  fi

  echo "Error: python3/python is required for JSON handling in smoke script." >&2
  exit 1
}

json_escape() {
  local value="${1:-}"
  ensure_python_bin
  "${PY_BIN}" - "$value" <<'PY'
import json
import sys
print(json.dumps(sys.argv[1]))
PY
}

compose() {
  docker compose "$@"
}

compose_with_local_reranker() {
  docker compose --profile "$(rerank_local_service_compose_profile)" "$@"
}

print_stack_diagnostics() {
  echo "Rerank smoke diagnostics:" >&2
  compose ps >&2 || true
  echo "--- backend logs (tail) ---" >&2
  compose logs --tail 120 backend >&2 || true
  echo "--- reranker-transformers logs (tail) ---" >&2
  compose_with_local_reranker logs --tail 120 reranker-transformers >&2 || true
}

wait_for_backend() {
  local attempts="${1:-30}"
  local delay_sec="${2:-2}"
  local code
  for _ in $(seq 1 "${attempts}"); do
    code="$(curl -sS --max-time 5 -o /dev/null -w "%{http_code}" "${BASE_URL}/health" || true)"
    if [[ "${code}" == "200" ]]; then
      return 0
    fi
    sleep "${delay_sec}"
  done
  return 1
}

run_http_check() {
  local provider="$1"
  local test_id="$2"
  local path="$3"
  local expected="$4"
  local max_time="${5:-10}"

  local tmp_body
  tmp_body="$(mktemp)"
  local http_code
  http_code="$(curl -sS --max-time "${max_time}" -w "%{http_code}" -o "${tmp_body}" "${BASE_URL}${path}" || true)"

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

  local body_preview
  body_preview="$(printf "%s" "${body}" | head -c 500)"

  LAST_CHECK_JSON="$(printf '{"test_id":"%s","provider":"%s","path":"%s","expected_status":"%s","http_status":"%s","result":"%s","body_preview":%s}' \
    "${test_id}" \
    "${provider}" \
    "${path}" \
    "${expected}" \
    "${http_code}" \
    "${status}" \
    "$(json_escape "${body_preview}")")"

  printf "%s" "${body}"
}

append_derived_check() {
  local payload="$1"
  local status
  ensure_python_bin
  status="$("${PY_BIN}" - "${payload}" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
print(payload.get("result", "fail"))
PY
)"

  LAST_DERIVED_JSON="${payload}"
  if [[ "${status}" == "pass" ]]; then
    PASS_COUNT+=1
  else
    FAIL_COUNT+=1
  fi
}

last_check_result() {
  ensure_python_bin
  "${PY_BIN}" - "${LAST_CHECK_JSON}" <<'PY'
import json
import sys
print(json.loads(sys.argv[1]).get("result", "fail"))
PY
}

last_derived_result() {
  ensure_python_bin
  "${PY_BIN}" - "${LAST_DERIVED_JSON}" <<'PY'
import json
import sys
print(json.loads(sys.argv[1]).get("result", "fail"))
PY
}

assert_service_mode() {
  local provider="$1"
  local connections_body="$2"

  ensure_python_bin
  append_derived_check "$("${PY_BIN}" - "${provider}" "${connections_body}" <<'PY'
import json
import sys

provider = sys.argv[1]
raw = sys.argv[2]
payload = json.loads(raw)
services = payload.get("services") or {}
reranker = services.get("reranker") or {}
required = bool(reranker.get("required"))
is_healthy = reranker.get("is_healthy")

if provider == "local_transformers":
    ok = required and is_healthy is True
    reason = (
        "local_transformers requires a healthy reranker service"
        if ok
        else f"expected reranker required+healthy, got required={required!r} is_healthy={is_healthy!r}"
    )
else:
    ok = not required
    reason = (
        "provider keeps reranker optional"
        if ok
        else f"expected reranker optional, got required={required!r}"
    )

print(
    json.dumps(
        {
            "test_id": f"{provider.upper()}_SERVICE_MODE",
            "provider": provider,
            "result": "pass" if ok else "fail",
            "details": {
                "connections_status": payload.get("status"),
                "reranker_required": required,
                "reranker_is_healthy": is_healthy,
                "reason": reason,
            },
        }
    )
)
PY
)"
}

assert_local_reranker_target() {
  local provider="$1"
  local connections_body="$2"

  if [[ "${provider}" != "local_transformers" ]]; then
    return 0
  fi

  ensure_python_bin
  append_derived_check "$("${PY_BIN}" - "${provider}" "${connections_body}" "${LOCAL_RERANKER_URL}" <<'PY'
import json
import sys

provider = sys.argv[1]
raw = sys.argv[2]
expected_url = sys.argv[3]
payload = json.loads(raw)
services = payload.get("services") or {}
reranker = services.get("reranker") or {}
actual_url = reranker.get("url")

ok = actual_url == expected_url
reason = (
    "local_transformers is pinned to the local Compose reranker endpoint"
    if ok
    else f"expected local reranker URL {expected_url!r}, got {actual_url!r}"
)

print(
    json.dumps(
        {
            "test_id": f"{provider.upper()}_TARGET_URL",
            "provider": provider,
            "result": "pass" if ok else "fail",
            "details": {
                "reason": reason,
                "expected_url": expected_url,
                "actual_url": actual_url,
            },
        }
    )
)
PY
)"
}

run_rerank_probe() {
  local provider="$1"
  local tmp_stdout tmp_stderr exit_code
  tmp_stdout="$(mktemp)"
  tmp_stderr="$(mktemp)"

  if compose exec -T backend bash -lc 'cd /app/backend && python - <<'"'"'PY'"'"'
import json
import os

from src.lib.bedrock_reranker import rerank_chunks

query = "Which chunk is most relevant to human BRCA1 DNA repair through homologous recombination after double-strand breaks?"
chunks = [
    {
        "id": "chunk-baseline-a",
        "text": "Inventory checklist for freezer shelves, tube labels, and room-temperature handling notes.",
        "score": 0.98,
        "metadata": {"section_title": "Operations"},
        "_rerank_text": "Inventory checklist for freezer shelves, tube labels, and room-temperature handling notes.",
    },
    {
        "id": "chunk-baseline-b",
        "text": "Greenhouse irrigation measurements for tomato seedlings under variable humidity conditions.",
        "score": 0.95,
        "metadata": {"section_title": "Agronomy"},
        "_rerank_text": "Greenhouse irrigation measurements for tomato seedlings under variable humidity conditions.",
    },
    {
        "id": "chunk-target",
        "text": "Human BRCA1 and BRCA2 coordinate homologous recombination DNA repair after double-strand breaks.",
        "score": 0.91,
        "metadata": {"section_title": "DNA Repair"},
        "_rerank_text": "Human BRCA1 and BRCA2 coordinate homologous recombination DNA repair after double-strand breaks.",
    },
]

ranked = rerank_chunks(query, chunks, top_n=len(chunks))
print(
    json.dumps(
        {
            "provider": os.getenv("RERANK_PROVIDER", ""),
            "input_order": [chunk["id"] for chunk in chunks],
            "output_order": [chunk.get("id") for chunk in ranked],
            "top_chunk_id": ranked[0].get("id") if ranked else None,
            "reordered": [chunk.get("id") for chunk in ranked] != [chunk["id"] for chunk in chunks],
            "scores": [
                {
                    "id": chunk.get("id"),
                    "score": chunk.get("score"),
                    "retrieval_score": ((chunk.get("metadata") or {}).get("retrieval_score")),
                    "rerank_score": ((chunk.get("metadata") or {}).get("rerank_score")),
                }
                for chunk in ranked
            ],
        }
    )
)
PY' >"${tmp_stdout}" 2>"${tmp_stderr}"; then
    exit_code=0
  else
    exit_code=$?
  fi

  local stdout stderr
  stdout="$(cat "${tmp_stdout}" 2>/dev/null || true)"
  stderr="$(cat "${tmp_stderr}" 2>/dev/null || true)"
  rm -f "${tmp_stdout}" "${tmp_stderr}"

  if [[ "${exit_code}" != "0" ]]; then
    ensure_python_bin
    append_derived_check "$("${PY_BIN}" - "${provider}" "${exit_code}" "${stderr}" <<'PY'
import json
import sys

provider = sys.argv[1]
exit_code = sys.argv[2]
stderr = sys.argv[3]
print(
    json.dumps(
        {
            "test_id": f"{provider.upper()}_RERANK_BEHAVIOR",
            "provider": provider,
            "result": "fail",
            "details": {
                "reason": f"backend probe exited with status {exit_code}",
                "stderr_preview": stderr[:500],
            },
        }
    )
)
PY
)"
    return 1
  fi

  ensure_python_bin
  append_derived_check "$("${PY_BIN}" - "${provider}" "${stdout}" <<'PY'
import json
import sys

provider = sys.argv[1]
raw = sys.argv[2]
payload = json.loads(raw)
input_order = payload.get("input_order")
output_order = payload.get("output_order")
if input_order is None or output_order is None:
    print(
        json.dumps(
            {
                "test_id": f"{provider.upper()}_RERANK_BEHAVIOR",
                "provider": provider,
                "result": "fail",
                "details": {
                    "reason": "backend probe must return both input_order and output_order",
                    "probe": payload,
                },
            }
        )
    )
    raise SystemExit(0)

top_chunk_id = payload.get("top_chunk_id")
reordered = output_order != input_order

if provider in {"bedrock_cohere", "local_transformers"}:
    ok = reordered and top_chunk_id == "chunk-target"
    reason = (
        "provider moved the BRCA1 chunk to the top of the ranked output"
        if ok
        else f"expected reordered target-first output, got order={output_order!r}"
    )
else:
    ok = (not reordered) and output_order == input_order
    reason = (
        "provider disabled reranking and preserved retrieval order"
        if ok
        else f"expected preserved retrieval order, got input={input_order!r} output={output_order!r}"
    )

print(
    json.dumps(
        {
            "test_id": f"{provider.upper()}_RERANK_BEHAVIOR",
            "provider": provider,
            "result": "pass" if ok else "fail",
            "details": {
                "reason": reason,
                "probe": payload,
            },
        }
    )
)
PY
)"
}

configure_stack_for_provider() {
  local provider="$1"

  if rerank_provider_requires_local_service "${provider}"; then
    RERANK_PROVIDER="${provider}" compose_with_local_reranker up -d --wait reranker-transformers
    RERANK_PROVIDER="${provider}" compose_with_local_reranker up -d --wait backend
    return 0
  fi

  compose_with_local_reranker rm -sf reranker-transformers >/dev/null 2>&1 || true
  RERANK_PROVIDER="${provider}" compose up -d --wait backend
}

main() {
  cd "${REPO_ROOT}"
  mkdir -p "${OUT_DIR}"
  CHECKS_ITEMS=()
  DERIVED_ITEMS=()
  PROVIDERS=(bedrock_cohere local_transformers none)

  # shellcheck disable=SC1091
  . "${SCRIPT_DIR}/load-home-test-env.sh"
  ensure_python_bin

  for provider in "${PROVIDERS[@]}"; do
    echo "Running rerank provider smoke for ${provider}..."
    if ! configure_stack_for_provider "${provider}"; then
      append_derived_check "$("${PY_BIN}" - "${provider}" <<'PY'
import json
import sys

provider = sys.argv[1]
print(
    json.dumps(
        {
            "test_id": f"{provider.upper()}_STACK_STARTUP",
            "provider": provider,
            "result": "fail",
            "details": {
                "reason": "docker compose failed while reconfiguring the stack",
            },
        }
    )
)
PY
)"
      print_stack_diagnostics
      continue
    fi

    if ! wait_for_backend 30 2; then
      append_derived_check "$("${PY_BIN}" - "${provider}" <<'PY'
import json
import sys

provider = sys.argv[1]
print(
    json.dumps(
        {
            "test_id": f"{provider.upper()}_STACK_STARTUP",
            "provider": provider,
            "result": "fail",
            "details": {
                "reason": "backend /health never became ready",
            },
        }
    )
)
PY
)"
      print_stack_diagnostics
      continue
    fi

    run_http_check "${provider}" "${provider^^}_HEALTH" "/health" "200" >/dev/null
    CHECKS_ITEMS+=("${LAST_CHECK_JSON}")
    if [[ "$(last_check_result)" != "pass" ]]; then
      print_stack_diagnostics
      continue
    fi

    connections_body="$(run_http_check "${provider}" "${provider^^}_CONNECTIONS" "/api/admin/health/connections" "200")"
    CHECKS_ITEMS+=("${LAST_CHECK_JSON}")
    if [[ "$(last_check_result)" != "pass" ]]; then
      print_stack_diagnostics
      continue
    fi

    assert_service_mode "${provider}" "${connections_body}"
    DERIVED_ITEMS+=("${LAST_DERIVED_JSON}")
    if [[ "$(last_derived_result)" != "pass" ]]; then
      print_stack_diagnostics
      continue
    fi

    assert_local_reranker_target "${provider}" "${connections_body}"
    if [[ "${provider}" == "local_transformers" ]]; then
      DERIVED_ITEMS+=("${LAST_DERIVED_JSON}")
      if [[ "$(last_derived_result)" != "pass" ]]; then
        print_stack_diagnostics
        continue
      fi
    fi

    if ! run_rerank_probe "${provider}"; then
      print_stack_diagnostics
    fi
    DERIVED_ITEMS+=("${LAST_DERIVED_JSON}")
  done

  CHECKS_JSON="["
  for i in "${!CHECKS_ITEMS[@]}"; do
    if [[ "${i}" != "0" ]]; then
      CHECKS_JSON+=","
    fi
    CHECKS_JSON+="${CHECKS_ITEMS[$i]}"
  done
  CHECKS_JSON+="]"

  DERIVED_JSON="["
  for i in "${!DERIVED_ITEMS[@]}"; do
    if [[ "${i}" != "0" ]]; then
      DERIVED_JSON+=","
    fi
    DERIVED_JSON+="${DERIVED_ITEMS[$i]}"
  done
  DERIVED_JSON+="]"

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
  "derived_checks": ${DERIVED_JSON}
}
EOF

  echo "Rerank provider local smoke complete."
  echo "Result: ${OVERALL_STATUS} (pass=${PASS_COUNT}, fail=${FAIL_COUNT})"
  echo "Evidence file: ${OUT_FILE}"

  if [[ "${OVERALL_STATUS}" != "pass" ]]; then
    exit 1
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
