#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${REPO_ROOT}/scripts/testing/pdf_viewer_route_preflight.sh"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

mkdir -p "${TMP_DIR}/bin" "${TMP_DIR}/out"

cat > "${TMP_DIR}/bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

headers_file=""
body_file=""
is_head=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    -I)
      is_head=true
      shift
      ;;
    -D|-o|-w|-H)
      flag="$1"
      value="$2"
      if [[ "${flag}" == "-D" ]]; then headers_file="${value}"; fi
      if [[ "${flag}" == "-o" ]]; then body_file="${value}"; fi
      shift 2
      ;;
    -sS)
      shift
      ;;
    *)
      shift
      ;;
  esac
done

status="${FAKE_CURL_STATUS:-200}"
if [[ -n "${headers_file}" ]]; then
  printf 'HTTP/1.1 %s Test\r\nContent-Type: application/pdf\r\n\r\n' "${status}" > "${headers_file}"
fi
if [[ "${is_head}" == false && -n "${body_file}" && "${status}" =~ ^2 ]]; then
  printf '0123456789abcdef' > "${body_file}"
fi
printf '%s' "${status}"
EOF
chmod +x "${TMP_DIR}/bin/curl"

PATH="${TMP_DIR}/bin:${PATH}" \
PDF_VIEWER_ROUTE_PREFLIGHT_OUT_DIR="${TMP_DIR}/out" \
FAKE_CURL_STATUS=200 \
  "${SCRIPT}" --url https://example.test/uploads/user/document/paper.pdf >/dev/null

pass_file="$(find "${TMP_DIR}/out" -type f -name '*.json' | head -n 1)"
jq -e '.status == "pass" and .probes.head.http_status == "200" and .probes.range_get.body_bytes == 16' "${pass_file}" >/dev/null

rm -f "${TMP_DIR}/out"/*.json
if PATH="${TMP_DIR}/bin:${PATH}" \
  PDF_VIEWER_ROUTE_PREFLIGHT_OUT_DIR="${TMP_DIR}/out" \
  FAKE_CURL_STATUS=502 \
  "${SCRIPT}" --url https://example.test/uploads/user/document/paper.pdf >/dev/null; then
  echo "Expected the preflight to fail on HTTP 502" >&2
  exit 1
fi

fail_file="$(find "${TMP_DIR}/out" -type f -name '*.json' | head -n 1)"
jq -e '.status == "fail" and (.errors | length) == 2' "${fail_file}" >/dev/null

echo "pdf_viewer_route_preflight tests passed"
