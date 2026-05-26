#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
START_HELPER="${REPO_ROOT}/scripts/utilities/symphony_prod_loki_host_start.sh"
STATUS_HELPER="${REPO_ROOT}/scripts/utilities/symphony_prod_loki_host_status.sh"
STOP_HELPER="${REPO_ROOT}/scripts/utilities/symphony_prod_loki_host_stop.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

free_port() {
  python3 - <<'PY'
import socket
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY
}

write_stub_ssh() {
  local stub_dir="$1"
  mkdir -p "${stub_dir}"

  cat > "${stub_dir}/_fake_loki.py" <<'PY'
import http.server
import json
import sys

host = sys.argv[1]
port = int(sys.argv[2])

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/ready"):
            body = b"ready\n"
        elif self.path.startswith("/loki/api/v1/labels"):
            body = json.dumps({"status": "success", "data": ["service"]}).encode()
        elif self.path.startswith("/loki/api/v1/label/service/values"):
            body = json.dumps({"status": "success", "data": ["backend"]}).encode()
        else:
            body = json.dumps({"status": "success", "data": {"result": []}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        return

server = http.server.ThreadingHTTPServer((host, port), Handler)
server.serve_forever()
PY

  cat > "${stub_dir}/ssh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
local_forward=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -L)
      local_forward="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
local_host="${local_forward%%:*}"
rest="${local_forward#*:}"
local_port="${rest%%:*}"
exec python3 "$(dirname "$0")/_fake_loki.py" "${local_host}" "${local_port}" "${local_forward}"
EOF
  chmod +x "${stub_dir}/ssh"
}

test_start_status_stop_with_stub_ssh() {
  local temp_dir stub_dir state_root endpoint_file bind_port raw_port output old_path
  temp_dir="$(mktemp -d)"
  stub_dir="${temp_dir}/bin"
  state_root="${temp_dir}/state"
  endpoint_file="${temp_dir}/prod_loki_endpoint.env"
  bind_port="$(free_port)"
  raw_port="$(free_port)"
  write_stub_ssh "${stub_dir}"
  : > "${temp_dir}/AGR-ssl3.pem"

  old_path="${PATH}"
  export PATH="${stub_dir}:${PATH}"
  export SYMPHONY_PROD_LOKI_STATE_ROOT="${state_root}"
  export SYMPHONY_PROD_LOKI_ENDPOINT_FILE="${endpoint_file}"
  export SYMPHONY_PROD_LOKI_ALLOW_LOCALHOST_BIND=1
  export SYMPHONY_PROD_LOKI_SKIP_VM_ENDPOINT_SYNC=1
  export SYMPHONY_PROD_LOKI_SSH_KEY="${temp_dir}/AGR-ssl3.pem"

  output="$(bash "${START_HELPER}" --bind-ip 127.0.0.1 --port "${bind_port}" --raw-port "${raw_port}" --remote-host 127.0.0.1)"
  assert_contains "Production Loki read-only endpoint is ready" "${output}"
  assert_contains "LOKI_URL=http://127.0.0.1:${bind_port}" "$(cat "${endpoint_file}")"

  output="$(bash "${STATUS_HELPER}")"
  assert_contains "status=online" "${output}"
  assert_contains "raw_tunnel_status=running" "${output}"
  assert_contains "proxy_status=running" "${output}"

  curl -fsS "http://127.0.0.1:${bind_port}/loki/api/v1/label/service/values" >/dev/null

  bash "${STOP_HELPER}" >/dev/null
  [[ ! -e "${endpoint_file}" ]] || {
    echo "Expected endpoint file to be removed by stop" >&2
    exit 1
  }

  export PATH="${old_path}"
  unset SYMPHONY_PROD_LOKI_STATE_ROOT SYMPHONY_PROD_LOKI_ENDPOINT_FILE \
    SYMPHONY_PROD_LOKI_ALLOW_LOCALHOST_BIND SYMPHONY_PROD_LOKI_SKIP_VM_ENDPOINT_SYNC \
    SYMPHONY_PROD_LOKI_SSH_KEY
}

test_foreground_records_proxy_and_cleans_up_children() {
  local temp_dir stub_dir state_root endpoint_file bind_port raw_port old_path launcher_pid output
  temp_dir="$(mktemp -d)"
  stub_dir="${temp_dir}/bin"
  state_root="${temp_dir}/state"
  endpoint_file="${temp_dir}/prod_loki_endpoint.env"
  bind_port="$(free_port)"
  raw_port="$(free_port)"
  write_stub_ssh "${stub_dir}"
  : > "${temp_dir}/AGR-ssl3.pem"

  old_path="${PATH}"
  export PATH="${stub_dir}:${PATH}"
  export SYMPHONY_PROD_LOKI_STATE_ROOT="${state_root}"
  export SYMPHONY_PROD_LOKI_ENDPOINT_FILE="${endpoint_file}"
  export SYMPHONY_PROD_LOKI_ALLOW_LOCALHOST_BIND=1
  export SYMPHONY_PROD_LOKI_SKIP_VM_ENDPOINT_SYNC=1
  export SYMPHONY_PROD_LOKI_SSH_KEY="${temp_dir}/AGR-ssl3.pem"

  bash "${START_HELPER}" --foreground --bind-ip 127.0.0.1 --port "${bind_port}" --raw-port "${raw_port}" --remote-host 127.0.0.1 >"${temp_dir}/foreground.log" 2>&1 &
  launcher_pid="$!"

  for _ in $(seq 1 40); do
    if [[ -f "${state_root}/tunnel.state" ]] && curl -fsS "http://127.0.0.1:${bind_port}/ready" >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done

  output="$(bash "${STATUS_HELPER}")"
  assert_contains "status=online" "${output}"
  assert_contains "proxy_status=running" "${output}"
  assert_contains "raw_tunnel_status=running" "${output}"

  # shellcheck disable=SC1090
  source "${state_root}/tunnel.state"
  [[ -n "${PROXY_PID:-}" ]] || {
    echo "Expected PROXY_PID in foreground state" >&2
    exit 1
  }

  kill "${launcher_pid}" 2>/dev/null || true
  wait "${launcher_pid}" 2>/dev/null || true
  sleep 0.3

  if kill -0 "${PROXY_PID}" 2>/dev/null; then
    echo "Expected foreground proxy child to be stopped" >&2
    exit 1
  fi
  if kill -0 "${SSH_PID}" 2>/dev/null; then
    echo "Expected foreground raw tunnel child to be stopped" >&2
    exit 1
  fi

  export PATH="${old_path}"
  unset SYMPHONY_PROD_LOKI_STATE_ROOT SYMPHONY_PROD_LOKI_ENDPOINT_FILE \
    SYMPHONY_PROD_LOKI_ALLOW_LOCALHOST_BIND SYMPHONY_PROD_LOKI_SKIP_VM_ENDPOINT_SYNC \
    SYMPHONY_PROD_LOKI_SSH_KEY
}

test_start_status_stop_with_stub_ssh
test_foreground_records_proxy_and_cleans_up_children

echo "symphony_prod_loki_host_lifecycle tests passed"
