#!/usr/bin/env bash

set -euo pipefail

OUT_DIR="${PDF_VIEWER_ROUTE_PREFLIGHT_OUT_DIR:-/tmp/agr_ai_curation_pdf_viewer_route_preflight}"
DOCUMENT_URL=""

usage() {
  cat <<'EOF'
Usage: scripts/testing/pdf_viewer_route_preflight.sh --url DOCUMENT_URL

Verifies that the public PDF viewer route supports both the viewer's HEAD probe
and a ranged GET. Writes a JSON evidence file under /tmp by default.

Environment:
  PDF_VIEWER_ROUTE_PREFLIGHT_OUT_DIR  Override the evidence output directory
  PDF_VIEWER_ROUTE_PREFLIGHT_COOKIE   Optional Cookie header for protected URLs
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)
      DOCUMENT_URL="${2:?--url requires a value}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${DOCUMENT_URL}" ]]; then
  echo "--url is required" >&2
  usage >&2
  exit 2
fi

if [[ ! "${DOCUMENT_URL}" =~ ^https?:// ]]; then
  echo "--url must be an absolute HTTP(S) URL" >&2
  exit 2
fi

mkdir -p "${OUT_DIR}"
STAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
OUT_FILE="${OUT_DIR}/pdf_viewer_route_preflight_${STAMP}.json"
HEADERS_FILE="$(mktemp)"
BODY_FILE="$(mktemp)"

cleanup() {
  rm -f "${HEADERS_FILE}" "${BODY_FILE}"
}
trap cleanup EXIT

CURL_AUTH_ARGS=()
if [[ -n "${PDF_VIEWER_ROUTE_PREFLIGHT_COOKIE:-}" ]]; then
  CURL_AUTH_ARGS=(-H "Cookie: ${PDF_VIEWER_ROUTE_PREFLIGHT_COOKIE}")
fi

head_exit=0
head_status="$(curl -sS -I "${CURL_AUTH_ARGS[@]}" -D "${HEADERS_FILE}" -o /dev/null -w '%{http_code}' "${DOCUMENT_URL}")" || head_exit=$?

range_exit=0
range_status="$(curl -sS "${CURL_AUTH_ARGS[@]}" -H 'Range: bytes=0-15' -o "${BODY_FILE}" -w '%{http_code}' "${DOCUMENT_URL}")" || range_exit=$?

body_bytes="$(wc -c < "${BODY_FILE}" | tr -d '[:space:]')"
content_type="$(awk 'BEGIN { IGNORECASE=1 } /^content-type:/ { sub(/^[^:]*:[[:space:]]*/, ""); sub(/\r$/, ""); value=$0 } END { print value }' "${HEADERS_FILE}")"

status="pass"
errors=()
if [[ "${head_exit}" -ne 0 || ! "${head_status}" =~ ^2 ]]; then
  status="fail"
  errors+=("HEAD probe failed (curl_exit=${head_exit}, http_status=${head_status})")
fi
if [[ "${range_exit}" -ne 0 || ! "${range_status}" =~ ^2 || "${body_bytes}" -eq 0 ]]; then
  status="fail"
  errors+=("Range probe failed (curl_exit=${range_exit}, http_status=${range_status}, body_bytes=${body_bytes})")
fi

errors_json='[]'
if [[ "${#errors[@]}" -gt 0 ]]; then
  errors_json="$(printf '%s\n' "${errors[@]}" | jq -R . | jq -s .)"
fi

jq -n \
  --arg timestamp_utc "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
  --arg status "${status}" \
  --arg document_url "${DOCUMENT_URL}" \
  --arg content_type "${content_type}" \
  --arg head_status "${head_status}" \
  --arg range_status "${range_status}" \
  --argjson head_curl_exit "${head_exit}" \
  --argjson range_curl_exit "${range_exit}" \
  --argjson range_body_bytes "${body_bytes}" \
  --argjson errors "${errors_json}" \
  '{
    timestamp_utc: $timestamp_utc,
    status: $status,
    document_url: $document_url,
    content_type: $content_type,
    probes: {
      head: {http_status: $head_status, curl_exit: $head_curl_exit},
      range_get: {
        http_status: $range_status,
        curl_exit: $range_curl_exit,
        body_bytes: $range_body_bytes
      }
    },
    errors: $errors
  }' > "${OUT_FILE}"

echo "PDF viewer route preflight complete: ${status} (HEAD=${head_status}, range=${range_status}, bytes=${body_bytes})"
echo "Evidence file: ${OUT_FILE}"

if [[ "${status}" != "pass" ]]; then
  exit 1
fi
