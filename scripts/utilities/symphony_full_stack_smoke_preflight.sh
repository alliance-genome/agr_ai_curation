#!/usr/bin/env bash
# Evidence-ready full stack preflight for Symphony/Incus AI Curation smokes.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  symphony_full_stack_smoke_preflight.sh [options]

Purpose:
  Check whether a Symphony sandbox is ready for evidence-producing paper/chat/
  flow smoke tests. This is intentionally louder than ordinary docker health:
  it reports PASS/WARN/BLOCKER lines and exits nonzero when the run would miss
  required metrics or validation dependencies.

Options:
  --workspace-dir DIR           Workspace/sandbox checkout. Default: current directory.
  --compose-project NAME        Docker Compose project name. Default: COMPOSE_PROJECT_NAME
                                or the workspace basename.
  --backend-port PORT           Backend host port in the VM. Default: BACKEND_HOST_PORT or 8000.
  --langfuse-port PORT          Langfuse host port in the VM. Default: LANGFUSE_HOST_PORT or 3000.
  --require-langfuse            Require Langfuse for metrics/traces. Default.
  --allow-no-langfuse           Allow Langfuse to be absent/down for non-metric smoke only.
  --skip-literature-es-smoke    Only check literature ES env presence; do not run live lookup smoke.
  --help                        Show this help.

Notes:
  Run inside the Incus VM from the target sandbox/workspace. Do not paste env
  files or connection strings into chat; this helper prints only configured,
  missing, ok, warn, and error labels.
EOF
}

workspace_dir="${PWD}"
compose_project="${COMPOSE_PROJECT_NAME:-}"
backend_port="${BACKEND_HOST_PORT:-8000}"
langfuse_port="${LANGFUSE_HOST_PORT:-3000}"
require_langfuse=1
run_literature_es_smoke=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      workspace_dir="${2:-}"
      shift 2
      ;;
    --compose-project)
      compose_project="${2:-}"
      shift 2
      ;;
    --backend-port)
      backend_port="${2:-}"
      shift 2
      ;;
    --langfuse-port)
      langfuse_port="${2:-}"
      shift 2
      ;;
    --require-langfuse)
      require_langfuse=1
      shift
      ;;
    --allow-no-langfuse)
      require_langfuse=0
      shift
      ;;
    --skip-literature-es-smoke)
      run_literature_es_smoke=0
      shift
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

if [[ -z "${workspace_dir}" ]]; then
  echo "--workspace-dir cannot be empty" >&2
  exit 2
fi

workspace_dir="$(cd "${workspace_dir}" && pwd -P)"
if [[ -z "${compose_project}" ]]; then
  compose_project="$(basename "${workspace_dir}" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')"
fi

backend_container="${compose_project}-backend-1"
langfuse_container="${compose_project}-langfuse-1"
langfuse_worker_container="${compose_project}-langfuse-worker-1"
clickhouse_container="${compose_project}-clickhouse-1"
compose_network="${compose_project}_default"

blockers=0
warnings=0

pass() {
  printf 'PASS: %s\n' "$*"
}

warn() {
  warnings=$((warnings + 1))
  printf 'WARN: %s\n' "$*"
}

blocker() {
  blockers=$((blockers + 1))
  printf 'BLOCKER: %s\n' "$*"
}

emit_probe_output() {
  local output_file="$1"
  local nested_blockers
  local nested_warnings

  cat "${output_file}"
  nested_blockers="$(grep -c '^BLOCKER:' "${output_file}" || true)"
  nested_warnings="$(grep -c '^WARN:' "${output_file}" || true)"
  blockers=$((blockers + nested_blockers))
  warnings=$((warnings + nested_warnings))
}

container_exists() {
  docker inspect "$1" >/dev/null 2>&1
}

container_status() {
  docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$1" 2>/dev/null || true
}

container_running() {
  [[ "$(docker inspect --format '{{.State.Running}}' "$1" 2>/dev/null || true)" == "true" ]]
}

print_header() {
  echo "workspace=${workspace_dir}"
  echo "compose_project=${compose_project}"
  echo "compose_network=${compose_network}"
  echo "backend_url=http://127.0.0.1:${backend_port}"
  echo "langfuse_url=http://127.0.0.1:${langfuse_port}"
  echo "require_langfuse=$([[ "${require_langfuse}" -eq 1 ]] && echo true || echo false)"
}

check_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    blocker "docker is not installed or not in PATH"
    return
  fi
  if ! docker info >/dev/null 2>&1; then
    blocker "docker daemon is not responding"
    return
  fi
  pass "docker daemon is responding"
}

check_container() {
  local name="$1"
  local required="$2"
  local status

  if ! container_exists "${name}"; then
    if [[ "${required}" == "required" ]]; then
      blocker "container ${name} is missing"
    else
      warn "optional container ${name} is missing"
    fi
    return
  fi

  status="$(container_status "${name}")"
  if container_running "${name}"; then
    pass "container ${name} is running (${status})"
  elif [[ "${required}" == "required" ]]; then
    blocker "container ${name} is not running (${status})"
  else
    warn "optional container ${name} is not running (${status})"
  fi
}

check_stack_containers() {
  check_container "${backend_container}" required
  check_container "${compose_project}-postgres-1" required
  check_container "${compose_project}-redis-1" required
  check_container "${compose_project}-weaviate-1" required

  if [[ "${require_langfuse}" -eq 1 ]]; then
    check_container "${langfuse_container}" required
    check_container "${langfuse_worker_container}" required
    check_container "${clickhouse_container}" required
    check_container "${compose_project}-minio-1" required
  else
    check_container "${langfuse_container}" optional
    check_container "${langfuse_worker_container}" optional
    check_container "${clickhouse_container}" optional
    check_container "${compose_project}-minio-1" optional
  fi
}

check_network_alias() {
  local name="$1"
  local alias="$2"
  local why="$3"
  local info

  if ! container_exists "${name}"; then
    return
  fi

  info="$(docker inspect --format '{{with index .NetworkSettings.Networks "'"${compose_network}"'"}}{{.IPAddress}} aliases={{range .Aliases}}{{.}} {{end}}{{end}}' "${name}" 2>/dev/null || true)"
  if [[ -z "${info}" ]]; then
    blocker "container ${name} is not attached to ${compose_network} with service alias ${alias}"
    echo "WHY: ${why}"
    echo "REPAIR_HINT: from the sandbox, recreate the service with docker compose -p ${compose_project} up -d --force-recreate ${alias}; emergency repair is docker network connect --alias ${alias} ${compose_network} ${name}"
    return
  fi

  if [[ " ${info} " == *" ${alias} "* ]]; then
    pass "container ${name} is attached to ${compose_network} with alias ${alias}"
  else
    blocker "container ${name} is attached to ${compose_network} but missing service alias ${alias}"
    echo "WHY: ${why}"
    echo "NETWORK_DETAIL: ${name} ${info}"
    echo "REPAIR_HINT: recreate the service or reconnect it with docker network connect --alias ${alias} ${compose_network} ${name}"
  fi
}

check_compose_network_dns() {
  if ! docker network inspect "${compose_network}" >/dev/null 2>&1; then
    blocker "compose network ${compose_network} is missing"
    echo "WHY: Docker service-name DNS only works on the compose network; running containers without this network can look healthy while backend/Langfuse calls fail."
    return
  fi

  check_network_alias "${backend_container}" "backend" "TraceReview and other services call the backend by service name; without the backend alias, cross-container checks can fail."
  check_network_alias "${compose_project}-postgres-1" "postgres" "backend and Langfuse need Postgres service DNS for app state and Langfuse metadata."
  check_network_alias "${compose_project}-redis-1" "redis" "streaming, cancellation, queueing, and Langfuse worker paths use Redis service DNS."
  check_network_alias "${compose_project}-weaviate-1" "weaviate" "the backend retrieves paper chunks from Weaviate by service DNS."

  if [[ "${require_langfuse}" -eq 1 ]]; then
    check_network_alias "${langfuse_container}" "langfuse" "the backend and TraceReview send trace/metric traffic to Langfuse by service DNS."
    check_network_alias "${clickhouse_container}" "clickhouse" "Langfuse migrations and trace analytics use ClickHouse by service DNS; if this is missing, Langfuse restart-loops and TraceReview misses fresh traces."
    check_network_alias "${compose_project}-minio-1" "minio" "Langfuse event/media upload storage uses MinIO by service DNS; detached MinIO can break ingestion even when the container says healthy."
  fi
}

check_backend_http() {
  local health
  local ready

  if ! health="$(curl -fsS -m 10 "http://127.0.0.1:${backend_port}/health" 2>/dev/null)"; then
    blocker "backend /health is not reachable on port ${backend_port}"
    return
  fi
  pass "backend /health is reachable"

  ready="$(curl -sS -m 10 "http://127.0.0.1:${backend_port}/health/ready" 2>/dev/null || true)"
  if [[ -z "${ready}" ]]; then
    warn "backend /health/ready did not return a response"
    return
  fi

  printf '%s\n' "${ready}" >/tmp/symphony_backend_ready_payload.$$
  python - /tmp/symphony_backend_ready_payload.$$ <<'PY' >/tmp/symphony_backend_ready.$$ || true
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    raw = handle.read()
try:
    payload = json.loads(raw)
except Exception:
    print("WARN: backend /health/ready returned non-JSON")
    raise SystemExit(0)

ready = bool(payload.get("ready"))
services = payload.get("services") or {}

def status(name):
    value = services.get(name) or {}
    return value.get("status"), value.get("required")

def is_connected(service_status):
    return service_status in {"connected", "ok", "ready", "healthy", "running"}

if ready:
    print("PASS: backend /health/ready reports ready=true")
else:
    disconnected = []
    for service_name, value in services.items():
        if not isinstance(value, dict):
            continue
        service_status = value.get("status")
        required = bool(value.get("required"))
        if service_status is not None and not is_connected(service_status):
            disconnected.append((service_name, service_status, required))
    if (
        disconnected
        and all(name == "literature_db" and not required for name, _status, required in disconnected)
    ):
        print("PASS: backend /health/ready reports ready=false only because optional literature_db is disconnected")
        print("WHY: optional literature_db is not the evidence gate for real extraction smoke tests; the live literature ES/package smoke below validates the reference/literature lookup path.")
    else:
        print("WARN: backend /health/ready reports ready=false")
        print("WHY: readiness false can indicate a dependency issue; the dedicated curation DB and literature ES probes below decide whether evidence runs are blocked.")

for name in ("curation_db", "literature_search", "literature_db"):
    service_status, required = status(name)
    if service_status is not None:
        print(f"READY_DETAIL: {name} status={service_status} required={required}")
PY
  emit_probe_output /tmp/symphony_backend_ready.$$
  rm -f /tmp/symphony_backend_ready.$$ /tmp/symphony_backend_ready_payload.$$
}

check_backend_env() {
  if ! container_running "${backend_container}"; then
    blocker "cannot inspect backend env because ${backend_container} is not running"
    return
  fi

  docker exec -i "${backend_container}" python - <<'PY' >/tmp/symphony_backend_env.$$
import os

checks = [
    ("CURATION_DB_URL", True),
    ("ELASTICSEARCH_HOST", True),
    ("ELASTICSEARCH_SCHEME", True),
    ("ELASTICSEARCH_PORT", True),
    ("ELASTICSEARCH_INDEX", True),
    ("LANGFUSE_HOST", False),
    ("LANGFUSE_PUBLIC_KEY", False),
    ("LANGFUSE_SECRET_KEY", False),
    ("AGR_BOOTSTRAP_PACKAGE_ENVS_ON_START", False),
]
for name, required in checks:
    value = os.getenv(name)
    state = "configured" if value else "missing"
    level = "PASS" if value or not required else "BLOCKER"
    if name.startswith("LANGFUSE") and not value:
        level = "WARN"
    print(f"{level}: backend env {name}={state}")
    if level == "BLOCKER" and name == "CURATION_DB_URL":
        print("WHY: curation validation and identifier lookups need the read-only curation DB tunnel; without it, validator behavior and token counts are misleading.")
    elif level == "BLOCKER" and name.startswith("ELASTICSEARCH_"):
        print("WHY: literature/reference validation uses the AGR literature package backed by Elasticsearch/OpenSearch; without these env vars, paper extraction tests do not exercise the real lookup path.")
    elif level == "WARN" and name.startswith("LANGFUSE"):
        print("WHY: Langfuse env must be present for TraceReview/metrics evidence; warnings here become blockers when --require-langfuse is active.")

websocket_value = os.getenv("OPENAI_RESPONSES_WEBSOCKET_ENABLED")
if websocket_value is None or websocket_value.strip() == "":
    print("WARN: backend env OPENAI_RESPONSES_WEBSOCKET_ENABLED=missing_default_enabled")
    print("WHY: the runner defaults OpenAI Responses websocket transport on when this env var is unset, but compose should normally set OPENAI_RESPONSES_WEBSOCKET_ENABLED=true so production/default behavior is explicit.")
else:
    normalized = websocket_value.strip().lower()
    disabled_values = {"0", "false", "no", "off"}
    if normalized in disabled_values:
        print("WARN: backend env OPENAI_RESPONSES_WEBSOCKET_ENABLED=disabled")
        print("WHY: websocket transport is the intended default because it should make OpenAI Responses streaming faster. Disabled is acceptable for a narrow transport diagnostic or workaround, but do not leave production/default smoke stacks disabled by accident.")
    else:
        print("PASS: backend env OPENAI_RESPONSES_WEBSOCKET_ENABLED=enabled")
        print("WHY: websocket transport is the intended default for production/default smoke stacks; if a websocket hiccup happens, runner errors should surface as provider/transport failures rather than misleading flow missing-step evidence.")
PY
  emit_probe_output /tmp/symphony_backend_env.$$
  rm -f /tmp/symphony_backend_env.$$
}

check_curation_tunnel() {
  local helper="${workspace_dir}/scripts/utilities/symphony_local_db_tunnel_status.sh"
  if [[ ! -x "${helper}" ]]; then
    blocker "missing tunnel status helper at ${helper}"
    return
  fi

  if bash "${helper}" --workspace-dir "${workspace_dir}" >/tmp/symphony_tunnel_status.$$ 2>&1; then
    pass "curation DB tunnel status helper reports ready"
    echo "WHY: curation DB tunnel readiness matters because validation-heavy extractions use live curation lookups; a missing tunnel can turn a model/data issue into a fake token-budget issue."
    sed -E 's/(state_file|env_file)=.*/\1=configured/' /tmp/symphony_tunnel_status.$$ | grep -E '^(workspace|state_file|env_file|ssm_status|socat_status|watchdog_status|local_listener|docker_listener|local_port|docker_port|forward_host)=' || true
  else
    blocker "curation DB tunnel is not ready for this workspace. WHY: validation-heavy smokes require live curation lookups; do not run paper/chat/flow evidence tests until this is fixed."
    sed -E 's/(state_file|env_file)=.*/\1=configured/' /tmp/symphony_tunnel_status.$$ || true
  fi
  rm -f /tmp/symphony_tunnel_status.$$
}

check_backend_external_dependencies() {
  if ! container_running "${backend_container}"; then
    blocker "cannot probe backend dependencies because ${backend_container} is not running"
    return
  fi

  docker exec -i "${backend_container}" python - <<'PY' >/tmp/symphony_backend_external_deps.$$
import os
from sqlalchemy import create_engine, text

url = os.getenv("CURATION_DB_URL")
if not url:
    print("BLOCKER: curation DB URL missing inside backend")
    print("WHY: the backend container cannot validate against the live curation DB if this URL is absent, even if the host tunnel exists.")
else:
    try:
        engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
        with engine.connect() as conn:
            row = conn.execute(text("select current_database(), current_user")).first()
        print(f"PASS: curation DB SQL probe ok database={row[0]} user={row[1]}")
    except Exception as exc:
        print(f"BLOCKER: curation DB SQL probe failed error_type={type(exc).__name__}")
        print("WHY: this means validators may fail or skip live lookup work; do not treat downstream extraction/token evidence as representative.")

for name in ("ELASTICSEARCH_HOST", "ELASTICSEARCH_SCHEME", "ELASTICSEARCH_PORT", "ELASTICSEARCH_INDEX"):
    if os.getenv(name):
        print(f"PASS: literature ES env {name}=configured")
    else:
        print(f"BLOCKER: literature ES env {name}=missing")
        print("WHY: reference/literature validation uses the package-backed Elasticsearch/OpenSearch path; direct literature SQL is not a substitute for this smoke.")
PY
  emit_probe_output /tmp/symphony_backend_external_deps.$$
  rm -f /tmp/symphony_backend_external_deps.$$
}

check_literature_es_smoke() {
  if [[ "${run_literature_es_smoke}" -eq 0 ]]; then
    warn "skipping live literature ES smoke by request"
    return
  fi
  if ! container_running "${backend_container}"; then
    blocker "cannot run literature ES smoke because ${backend_container} is not running"
    return
  fi

  if docker exec -e RUN_LITERATURE_ES_SMOKE=1 -i "${backend_container}" bash -lc \
    'cd /app/backend && python -m pytest tests/unit/lib/packages/test_alliance_literature_reference_tool.py::test_live_literature_es_smoke_when_environment_is_available -q -s' >/tmp/symphony_literature_es_smoke.$$ 2>&1; then
    pass "live literature ES package smoke passed"
    echo "WHY: this confirms the real literature/reference lookup package path is available before model-driven paper tests spend tokens."
  else
    blocker "live literature ES package smoke failed. WHY: paper extraction and reference validation would not be testing the real literature lookup path."
    tail -80 /tmp/symphony_literature_es_smoke.$$
  fi
  rm -f /tmp/symphony_literature_es_smoke.$$
}

check_langfuse() {
  if [[ "${require_langfuse}" -ne 1 ]]; then
    warn "Langfuse is not required for this preflight; metric evidence will be incomplete. WHY: use this only for narrow functional debugging, never for TraceReview/token/metrics evidence."
    return
  fi

  if ! container_running "${langfuse_container}"; then
    blocker "Langfuse container is not running; metric/trace evidence will be missing. WHY: TraceReview, token analysis, and trace-level debugging depend on Langfuse; do not shortcut this for evidence runs."
    if container_exists "${langfuse_container}"; then
      echo "LANGFUSE_RECENT_ERRORS:"
      docker logs --tail 80 "${langfuse_container}" 2>&1 | grep -Ei 'clickhouse|error|failed|unavailable|dns|lookup' | tail -30 || true
    fi
    return
  fi

  if curl -fsS -m 10 "http://127.0.0.1:${langfuse_port}" >/dev/null 2>&1; then
    pass "Langfuse HTTP endpoint is reachable"
    echo "WHY: Langfuse reachability means traces/metrics can be inspected through TraceReview after the smoke."
  else
    blocker "Langfuse HTTP endpoint is not reachable on port ${langfuse_port}. WHY: backend spans may be emitted, but humans and TraceReview cannot reliably inspect them."
  fi

  if container_running "${clickhouse_container}"; then
    pass "ClickHouse container is running for Langfuse"
  else
    blocker "ClickHouse container is not running for Langfuse. WHY: Langfuse stores trace/event analytics in ClickHouse; without it metrics evidence is not trustworthy."
  fi

  if docker exec "${langfuse_container}" sh -lc 'getent hosts clickhouse >/dev/null 2>&1' >/dev/null 2>&1; then
    pass "Langfuse container can resolve clickhouse"
  else
    blocker "Langfuse container cannot resolve clickhouse; Langfuse migrations/metrics will fail. WHY: this exact failure causes Langfuse restart loops and leaves TraceReview without fresh traces."
    echo "LANGFUSE_RECENT_CLICKHOUSE_ERRORS:"
    docker logs --tail 120 "${langfuse_container}" 2>&1 | grep -Ei 'clickhouse|lookup|dns|failed to open database' | tail -30 || true
  fi

  if container_running "${langfuse_worker_container}"; then
    if docker exec "${langfuse_worker_container}" sh -lc 'getent hosts clickhouse >/dev/null 2>&1 && getent hosts minio >/dev/null 2>&1' >/dev/null 2>&1; then
      pass "Langfuse worker can resolve clickhouse and minio"
      echo "WHY: Langfuse ingestion uses the worker plus ClickHouse/object storage path; web UI health alone does not prove trace events are durable."
    else
      blocker "Langfuse worker cannot resolve clickhouse and/or minio. WHY: trace ingestion can be silently incomplete if the worker cannot reach analytics/storage dependencies."
      echo "LANGFUSE_WORKER_RECENT_DEPENDENCY_ERRORS:"
      docker logs --tail 120 "${langfuse_worker_container}" 2>&1 | grep -Ei 'clickhouse|minio|s3|lookup|dns|failed|unavailable' | tail -30 || true
    fi
  fi

  if ! container_running "${backend_container}"; then
    blocker "cannot verify backend-to-Langfuse reachability because ${backend_container} is not running"
    echo "WHY: backend spans are emitted from inside the backend container, so Langfuse UI health alone is not enough for evidence runs."
    return
  fi

  docker exec -i "${backend_container}" python - <<'PY' >/tmp/symphony_backend_langfuse.$$
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

missing = [name for name in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY") if not os.getenv(name)]
if missing:
    print("BLOCKER: backend Langfuse env missing values=" + ",".join(missing))
    print("WHY: disabling or blanking Langfuse hides the metrics and trace evidence this smoke is supposed to collect.")
else:
    print("PASS: backend Langfuse env is configured")

host = os.getenv("LANGFUSE_HOST")
if not host:
    sys.exit(0)

target = urllib.parse.urljoin(host.rstrip("/") + "/", "api/public/health")
try:
    with urllib.request.urlopen(target, timeout=10) as response:
        if 200 <= response.status < 500:
            print("PASS: backend container can reach configured LANGFUSE_HOST")
            print("WHY: host-side Langfuse reachability is not enough; spans are emitted from inside the backend container.")
        else:
            print(f"BLOCKER: backend LANGFUSE_HOST health returned status={response.status}")
            print("WHY: the backend may be configured, but traces/metrics are not trustworthy if it cannot reach Langfuse from inside Docker.")
except (urllib.error.URLError, TimeoutError, OSError) as exc:
    print(f"BLOCKER: backend container cannot reach configured LANGFUSE_HOST error_type={type(exc).__name__}")
    print("WHY: a reachable Langfuse UI does not help if backend spans cannot be delivered to Langfuse from inside the compose network.")
PY
  emit_probe_output /tmp/symphony_backend_langfuse.$$
  rm -f /tmp/symphony_backend_langfuse.$$
}

main() {
  print_header
  check_docker
  check_stack_containers
  check_compose_network_dns
  check_backend_http
  check_backend_env
  check_curation_tunnel
  check_backend_external_dependencies
  check_literature_es_smoke
  check_langfuse

  echo "summary: blockers=${blockers} warnings=${warnings}"
  if [[ "${blockers}" -gt 0 ]]; then
    echo "RESULT: not evidence-ready"
    exit 1
  fi
  echo "RESULT: evidence-ready"
}

main "$@"
