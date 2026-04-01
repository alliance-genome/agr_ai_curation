#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

assert_file_exists() {
  local path="$1"
  [[ -f "${path}" ]] || {
    echo "Expected file to exist: ${path}" >&2
    exit 1
  }
}

assert_file_missing() {
  local path="$1"
  [[ ! -e "${path}" ]] || {
    echo "Expected path to be missing: ${path}" >&2
    exit 1
  }
}

assert_contains() {
  local pattern="$1"
  local file="$2"
  if ! rg -n --fixed-strings "$pattern" "$file" >/dev/null 2>&1; then
    echo "Expected to find '$pattern' in $file" >&2
    exit 1
  fi
}

assert_sourced_env_value() {
  local env_file="$1"
  local key="$2"
  local expected="$3"
  local actual
  actual="$(bash -c "set -euo pipefail; source \"$env_file\"; printf '%s' \"\${$key}\"")"
  [[ "${actual}" == "${expected}" ]] || {
    echo "Expected ${key}='${expected}', got '${actual}'" >&2
    exit 1
  }
}

assert_sourced_env_url_components() {
  local env_file="$1"
  bash -c "set -euo pipefail; source \"$env_file\"; python3 - <<'PY'
import os
from urllib.parse import urlparse, unquote

url = os.environ['CURATION_DB_URL']
parsed = urlparse(url)

assert parsed.scheme == 'postgresql', parsed
assert parsed.username == 'readonly_user', parsed
assert unquote(parsed.password) == 'fixture pass!', parsed
assert parsed.hostname == 'host.docker.internal', parsed
assert parsed.path == '/curation', parsed
assert parsed.port is not None, parsed
PY"
}

make_stub_bin() {
  local stub_dir="$1"
  mkdir -p "${stub_dir}"

  cat > "${stub_dir}/session-manager-plugin" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF

  cat > "${stub_dir}/timeout" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "--foreground" ]]; then
  shift
fi
shift
exec "$@"
EOF

  cat > "${stub_dir}/psql" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF

  cat > "${stub_dir}/pg_isready" <<'EOF'
#!/usr/bin/env bash
host="127.0.0.1"
port=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h)
      host="$2"
      shift 2
      ;;
    -p)
      port="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
python3 - "$host" "$port" <<'PY'
import socket
import sys
host = sys.argv[1]
port = int(sys.argv[2])
s = socket.socket()
s.settimeout(0.2)
try:
    s.connect((host, port))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
EOF

  cat > "${stub_dir}/_listen.py" <<'EOF'
import signal
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind((host, port))
sock.listen(5)

def stop(_signum, _frame):
    sock.close()
    sys.exit(0)

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

while True:
    try:
        conn, _ = sock.accept()
        conn.close()
    except OSError:
        time.sleep(0.05)
EOF

  cat > "${stub_dir}/aws" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
sub="${2:-}"
shift 2 || true
if [[ "${cmd}" == "secretsmanager" && "${sub}" == "get-secret-value" ]]; then
  args="$*"
  if [[ "${args}" == *"/claude-code-pr/config"* ]]; then
    echo '{"ssm_instance_id":"i-test123"}'
    exit 0
  fi
  if [[ "${args}" == *"ai-curation/db/curation-readonly"* ]]; then
    printf -v fixture_password 'fixture pass\x21'
    jq -cn \
      --arg host "db.internal" \
      --arg port "5432" \
      --arg dbname "curation" \
      --arg username "readonly_user" \
      --arg password "${fixture_password}" \
      '{host:$host, port:$port, dbname:$dbname, username:$username, password:$password}'
    exit 0
  fi
fi

if [[ "${cmd}" == "ssm" && "${sub}" == "start-session" ]]; then
  args="$*"
  local_port="$(printf '%s' "${args}" | sed -n 's/.*"localPortNumber":\["\([0-9]*\)"\].*/\1/p')"
  echo "SessionId: test-session-123"
  python3 "$(dirname "$0")/_listen.py" 127.0.0.1 "${local_port}"
  exit 0
fi

if [[ "${cmd}" == "ssm" && "${sub}" == "terminate-session" ]]; then
  exit 0
fi

echo "unexpected aws invocation: ${cmd} ${sub} $*" >&2
exit 1
EOF

  cat > "${stub_dir}/socat" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
arg="${1:-}"
port="$(printf '%s' "${arg}" | sed -n 's/.*TCP-LISTEN:\([0-9]*\).*/\1/p')"
bind_ip="$(printf '%s' "${arg}" | sed -n 's/.*bind=\([^,]*\).*/\1/p')"
python3 "$(dirname "$0")/_listen.py" "${bind_ip}" "${port}"
EOF

  chmod +x "${stub_dir}/session-manager-plugin" "${stub_dir}/timeout" "${stub_dir}/psql" \
    "${stub_dir}/pg_isready" "${stub_dir}/aws" "${stub_dir}/socat"
}

test_successful_start_status_stop() {
  local temp_root workspace state_root stub_dir env_file state_dir old_path
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/workspace"
  state_root="${temp_root}/state"
  stub_dir="${temp_root}/stubbin"
  mkdir -p "${workspace}/scripts"
  make_stub_bin "${stub_dir}"

  old_path="${PATH}"
  export PATH="${stub_dir}:${PATH}"
  export CURATION_DB_TUNNEL_BIND_IP="127.0.0.1"
  export CURATION_DB_TUNNEL_FORWARD_HOST="host.docker.internal"
  export SYMPHONY_LOCAL_DB_TUNNEL_STATE_ROOT="${state_root}"

  env_file="${workspace}/scripts/local_db_tunnel_env.sh"
  state_dir="${state_root}/$(basename "${workspace}" | tr -cs 'A-Za-z0-9._-' '-')-"*

  "${REPO_ROOT}/scripts/utilities/symphony_local_db_tunnel_start.sh" --workspace-dir "${workspace}"
  assert_file_exists "${env_file}"
  assert_contains "export PERSISTENT_STORE_DB_PASSWORD=fixture\\ pass\\!" "${env_file}"
  assert_sourced_env_value "${env_file}" "PERSISTENT_STORE_DB_PASSWORD" "fixture pass!"
  assert_sourced_env_url_components "${env_file}"
  if ! compgen -G "${state_dir}/watchdog.pid" >/dev/null; then
    echo "Expected watchdog.pid to be created" >&2
    exit 1
  fi

  "${REPO_ROOT}/scripts/utilities/symphony_local_db_tunnel_status.sh" --workspace-dir "${workspace}"
  "${REPO_ROOT}/scripts/utilities/symphony_local_db_tunnel_stop.sh" --workspace-dir "${workspace}"

  assert_file_missing "${env_file}"
  if compgen -G "${state_dir}" >/dev/null; then
    echo "Expected tunnel state directory to be removed" >&2
    exit 1
  fi

  export PATH="${old_path}"
  unset CURATION_DB_TUNNEL_BIND_IP CURATION_DB_TUNNEL_FORWARD_HOST SYMPHONY_LOCAL_DB_TUNNEL_STATE_ROOT
}

test_failed_start_cleans_stale_env() {
  local temp_root workspace state_root stub_dir env_file state_dir old_path
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/workspace"
  state_root="${temp_root}/state"
  stub_dir="${temp_root}/stubbin"
  mkdir -p "${workspace}/scripts"
  make_stub_bin "${stub_dir}"

  cat > "${stub_dir}/aws" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
sub="${2:-}"
shift 2 || true
if [[ "${cmd}" == "secretsmanager" && "${sub}" == "get-secret-value" ]]; then
  args="$*"
  if [[ "${args}" == *"/claude-code-pr/config"* ]]; then
    echo '{"ssm_instance_id":"i-test123"}'
    exit 0
  fi
  if [[ "${args}" == *"ai-curation/db/curation-readonly"* ]]; then
    printf -v fixture_password 'fixture pass\x21'
    jq -cn \
      --arg host "db.internal" \
      --arg port "5432" \
      --arg dbname "curation" \
      --arg username "readonly_user" \
      --arg password "${fixture_password}" \
      '{host:$host, port:$port, dbname:$dbname, username:$username, password:$password}'
    exit 0
  fi
fi
if [[ "${cmd}" == "ssm" && "${sub}" == "start-session" ]]; then
  echo "SessionId: test-session-123"
  sleep 5
  exit 0
fi
if [[ "${cmd}" == "ssm" && "${sub}" == "terminate-session" ]]; then
  exit 0
fi
exit 1
EOF
  chmod +x "${stub_dir}/aws"

  old_path="${PATH}"
  export PATH="${stub_dir}:${PATH}"
  export CURATION_DB_TUNNEL_BIND_IP="127.0.0.1"
  export CURATION_DB_TUNNEL_FORWARD_HOST="host.docker.internal"
  export SYMPHONY_LOCAL_DB_TUNNEL_STATE_ROOT="${state_root}"
  export LOCAL_DB_TUNNEL_WAIT_ITERATIONS=1
  export LOCAL_DB_TUNNEL_WAIT_SLEEP_SECONDS=0

  env_file="${workspace}/scripts/local_db_tunnel_env.sh"
  state_dir="${state_root}/$(basename "${workspace}" | tr -cs 'A-Za-z0-9._-' '-')-"*

  if "${REPO_ROOT}/scripts/utilities/symphony_local_db_tunnel_start.sh" --workspace-dir "${workspace}"; then
    echo "Expected tunnel start to fail" >&2
    exit 1
  fi

  assert_file_missing "${env_file}"
  if compgen -G "${state_dir}" >/dev/null; then
    echo "Expected tunnel state directory to be removed after failure" >&2
    exit 1
  fi

  export PATH="${old_path}"
  unset CURATION_DB_TUNNEL_BIND_IP CURATION_DB_TUNNEL_FORWARD_HOST SYMPHONY_LOCAL_DB_TUNNEL_STATE_ROOT
  unset LOCAL_DB_TUNNEL_WAIT_ITERATIONS LOCAL_DB_TUNNEL_WAIT_SLEEP_SECONDS
}

test_successful_start_status_stop
test_failed_start_cleans_stale_env

echo "symphony_local_db_tunnel lifecycle tests passed"
