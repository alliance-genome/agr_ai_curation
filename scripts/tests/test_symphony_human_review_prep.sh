#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/assertions.sh"

make_workspace_tunnel_helpers() {
  local workspace="$1"
  mkdir -p "${workspace}/scripts/utilities"

  cat > "${workspace}/scripts/utilities/symphony_local_db_tunnel_start.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
workspace_dir="${PWD}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      workspace_dir="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
mkdir -p "${workspace_dir}/scripts"
cat > "${workspace_dir}/scripts/local_db_tunnel_env.sh" <<'ENV'
export CURATION_DB_TUNNEL_FORWARD_HOST=host.docker.internal
export CURATION_DB_TUNNEL_DOCKER_PORT=6139
_proto="postgresql://"
export CURATION_DB_URL="${_proto}readonly:pw@host.docker.internal:6139/curation"
ENV
echo "stub tunnel started for ${workspace_dir}"
EOF

  cat > "${workspace}/scripts/utilities/symphony_local_db_tunnel_status.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
workspace_dir="${PWD}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      workspace_dir="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
echo "workspace=${workspace_dir}"
echo "ssm_status=running"
echo "socat_status=running"
echo "docker_listener=ready"
EOF

  chmod +x "${workspace}/scripts/utilities/symphony_local_db_tunnel_start.sh" \
    "${workspace}/scripts/utilities/symphony_local_db_tunnel_status.sh"
}

make_workspace_langfuse_repair_helper() {
  local workspace="$1"
  mkdir -p "${workspace}/scripts/utilities"

  cat > "${workspace}/scripts/utilities/ensure_local_langfuse_env.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
env_file="${1:?env file required}"
python3 - "$env_file" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
old_url = "postgresql://" + "langfuse_user:langfuse_pass" + "@langfuse-db:5432/langfuse"
new_url = "postgresql://" + "postgres:postgres" + "@postgres:5432/postgres"
text = text.replace(
    "export LANGFUSE_LOCAL_DATABASE_URL=" + old_url,
    "export LANGFUSE_LOCAL_DATABASE_URL=" + new_url,
)
text = text.replace(
    "export LANGFUSE_LOCAL_ENCRYPTION_KEY=CHANGE_ME_64_CHAR_HEX",
    "export LANGFUSE_LOCAL_ENCRYPTION_KEY=TEST_DUMMY_ENCRYPTION_KEY_not_real_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
)
path.write_text(text)
PY
echo "normalized_langfuse_env=${env_file}"
EOF

  chmod +x "${workspace}/scripts/utilities/ensure_local_langfuse_env.sh"
}

make_stub_bin() {
  local stub_dir="$1"
  local behavior="$2"
  mkdir -p "${stub_dir}"

  cat > "${stub_dir}/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
log_file="${DOCKER_STUB_LOG:?}"
printf 'docker|cwd=%s|CURATION_DB_URL=%s|LANGFUSE_LOCAL_DATABASE_URL=%s|LANGFUSE_LOCAL_ENCRYPTION_KEY=%s|LANGFUSE_HOST_PORT=%s|PDF_STORAGE_PATH=%s|FILE_OUTPUT_STORAGE_PATH=%s|NEXTAUTH_URL=%s|args=%s\n' \
  "$PWD" \
  "${CURATION_DB_URL:-}" \
  "${LANGFUSE_LOCAL_DATABASE_URL:-}" \
  "${LANGFUSE_LOCAL_ENCRYPTION_KEY:-}" \
  "${LANGFUSE_HOST_PORT:-}" \
  "${PDF_STORAGE_PATH:-}" \
  "${FILE_OUTPUT_STORAGE_PATH:-}" \
  "${NEXTAUTH_URL:-}" \
  "$*" >> "${log_file}"
if [[ -n "${STUB_DOCKER_FAIL_ONCE_MATCH:-}" && "$*" == *"${STUB_DOCKER_FAIL_ONCE_MATCH}"* ]]; then
  count_file="${STUB_DOCKER_FAIL_ONCE_COUNT_FILE:?}"
  count=0
  if [[ -f "${count_file}" ]]; then
    count="$(cat "${count_file}")"
  fi
  if [[ "${count}" == "0" ]]; then
    echo "1" > "${count_file}"
    echo "simulated docker failure for: $*" >&2
    exit 1
  fi
fi
if [[ "$*" == *"config --services"* ]]; then
  printf '%s\n' postgres redis reranker-transformers weaviate clickhouse minio langfuse langfuse-worker backend frontend
  exit 0
fi
if [[ "$*" == *" ps"* ]]; then
  cat <<'PS'
NAME                STATUS                SERVICE
stub-backend-1      running(healthy)      backend
stub-weaviate-1     running(unhealthy)    weaviate
PS
  exit 0
fi
if [[ "$*" == *"logs backend"* ]]; then
  echo 'sqlalchemy.exc.ProgrammingError: (psycopg2.errors.UndefinedColumn) column "mod_prompt_overrides" of relation "agents" does not exist'
  exit 0
fi
if [[ "$*" == *"logs "* ]]; then
  echo "stub logs for $*"
  exit 0
fi
if [[ "$*" == *"port postgres-test 5432"* ]]; then
  echo "127.0.0.1:15434"
  exit 0
fi
if [[ "$*" == *"port weaviate-test 8080"* ]]; then
  echo "127.0.0.1:18080"
  exit 0
fi
if [[ "$*" == *"port weaviate-test 50051"* ]]; then
  echo "127.0.0.1:15051"
fi
EOF

  cat > "${stub_dir}/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
url="${@: -1}"
behavior="${STUB_CURL_BEHAVIOR:-healthy}"
case "${behavior}:${url}" in
  healthy:http://127.0.0.1:3049/)
    echo '<html>ok</html>'
    ;;
  healthy:http://127.0.0.1:8049/health)
    echo '{"status":"healthy","services":{"app":"running","curation_db":"connected"}}'
    ;;
  healthy:http://127.0.0.1:8049/api/admin/health/connections/curation_db)
    echo '{"status":"healthy","service":"curation_db"}'
    ;;
  healthy:http://pdf.example/api/v1/health)
    echo '{"status":"ok"}'
    ;;
  backend_down:http://127.0.0.1:3049/)
    echo '<html>ok</html>'
    ;;
  *)
    exit 22
    ;;
esac
EOF

  cat > "${stub_dir}/hostname" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "-I" ]]; then
  echo "192.168.86.44"
  exit 0
fi
exec /usr/bin/hostname "$@"
EOF

  chmod +x "${stub_dir}/docker" "${stub_dir}/curl" "${stub_dir}/hostname"
  export STUB_CURL_BEHAVIOR="${behavior}"
}

test_review_prep_default_skips_stack_startup() {
  local temp_root workspace stub_dir output docker_log old_path env_file
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/ALL-49"
  stub_dir="${temp_root}/stubbin"
  output="${temp_root}/output.txt"
  docker_log="${temp_root}/docker.log"
  env_file="${temp_root}/private.env"

  mkdir -p "${workspace}/scripts"
  : > "${workspace}/docker-compose.yml"
  cat > "${env_file}" <<'EOF'
export OPENAI_API_KEY=test-openai
export GROQ_API_KEY=test-groq
export POSTGRES_PASSWORD=postgres
EOF

  make_stub_bin "${stub_dir}" "healthy"

  old_path="${PATH}"
  export PATH="${stub_dir}:${PATH}"
  export DOCKER_STUB_LOG="${docker_log}"
  export SYMPHONY_REVIEW_PREP_REFRESH_MANAGED=0

  "${REPO_ROOT}/scripts/utilities/symphony_human_review_prep.sh" \
    --workspace-dir "${workspace}" \
    --env-file "${env_file}" \
    > "${output}"

  assert_contains "human_review_prep_wrapper_status=skipped" "${output}"
  assert_contains "human_review_prep_wrapper_reason=start_test_containers_false" "${output}"
  assert_contains "start_test_containers=0" "${output}"
  assert_contains "stack_startup=skipped_by_flag" "${output}"
  assert_contains "dependency_start_status=skipped_by_flag" "${output}"
  assert_contains "frontend_health=skipped_by_flag" "${output}"
  assert_contains "backend_health=skipped_by_flag" "${output}"
  if [[ -f "${docker_log}" ]]; then
    echo "Expected docker not to be called when stack startup is skipped" >&2
    exit 1
  fi

  export PATH="${old_path}"
  unset DOCKER_STUB_LOG STUB_CURL_BEHAVIOR
  unset SYMPHONY_REVIEW_PREP_REFRESH_MANAGED
}

test_review_prep_happy_path() {
  local temp_root workspace stub_dir output docker_log old_path env_file
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/ALL-49"
  stub_dir="${temp_root}/stubbin"
  output="${temp_root}/output.txt"
  docker_log="${temp_root}/docker.log"
  env_file="${temp_root}/private.env"

  mkdir -p "${workspace}/scripts"
  : > "${workspace}/docker-compose.yml"
  cat > "${env_file}" <<'EOF'
export OPENAI_API_KEY=test-openai
export GROQ_API_KEY=test-groq
export POSTGRES_PASSWORD=postgres
export PDF_EXTRACTION_SERVICE_URL=http://pdf.example
export PDF_STORAGE_PATH=pdf_storage
export FILE_OUTPUT_STORAGE_PATH=file_outputs
EOF

  make_workspace_tunnel_helpers "${workspace}"
  make_stub_bin "${stub_dir}" "healthy"

  old_path="${PATH}"
  export PATH="${stub_dir}:${PATH}"
  export DOCKER_STUB_LOG="${docker_log}"
  export SYMPHONY_REVIEW_FRONTEND_HEALTH_ATTEMPTS=2
  export SYMPHONY_REVIEW_FRONTEND_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_BACKEND_HEALTH_ATTEMPTS=2
  export SYMPHONY_REVIEW_BACKEND_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_CURATION_HEALTH_ATTEMPTS=2
  export SYMPHONY_REVIEW_CURATION_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_PDF_HEALTH_ATTEMPTS=1
  export SYMPHONY_REVIEW_PDF_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_PREP_REFRESH_MANAGED=0

  "${REPO_ROOT}/scripts/utilities/symphony_human_review_prep.sh" \
    --workspace-dir "${workspace}" \
    --env-file "${env_file}" \
    --start-test-containers true \
    > "${output}"

  assert_contains "human_review_prep_wrapper_status=ready" "${output}"
  assert_contains "human_review_prep_wrapper_reason=healthy" "${output}"
  assert_contains "compose_project=all49" "${output}"
  assert_contains "compose_file=${workspace}/docker-compose.yml" "${output}"
  assert_contains "frontend_host_port=3049" "${output}"
  assert_contains "backend_host_port=8049" "${output}"
  assert_contains "build_backend=1" "${output}"
  assert_contains "build_frontend=1" "${output}"
  assert_contains "rerank_provider=none" "${output}"
  assert_contains "reranker_dependency_required=0" "${output}"
  assert_contains "dependency_services=postgres,redis,weaviate" "${output}"
  assert_contains "dependency_start_status=ready" "${output}"
  assert_contains "tunnel_env_file=${workspace}/scripts/local_db_tunnel_env.sh" "${output}"
  assert_contains "backend_health={\"status\":\"healthy\",\"services\":{\"app\":\"running\",\"curation_db\":\"connected\"}}" "${output}"
  assert_contains "curation_db_health={\"status\":\"healthy\",\"service\":\"curation_db\"}" "${output}"
  assert_contains "pdf_extraction_health={\"status\":\"ok\"}" "${output}"
  assert_contains "review_frontend_url=http://192.168.86.44:3049/" "${output}"
  assert_contains "review_backend_url=http://192.168.86.44:8049/health" "${output}"

  assert_contains "args=compose --env-file ${env_file} -f ${workspace}/docker-compose.yml -p all49 up -d --wait postgres redis weaviate" "${docker_log}"
  assert_not_contains "args=compose --env-file ${env_file} -f ${workspace}/docker-compose.yml -p all49 up -d --wait postgres redis reranker-transformers weaviate" "${docker_log}"
  assert_contains "args=compose --env-file ${env_file} -f ${workspace}/docker-compose.yml -p all49 build backend frontend" "${docker_log}"
  assert_contains "args=compose --env-file ${env_file} -f ${workspace}/docker-compose.yml -p all49 up -d backend frontend" "${docker_log}"
  assert_contains "args=compose --env-file ${env_file} -f ${workspace}/docker-compose.yml -p all49 up -d --force-recreate backend" "${docker_log}"
  # Built from parts to avoid secret-scanner false positives on test fixture URIs
  _db_url="postgresql://""readonly:pw@host.docker.internal:6139/curation"
  assert_contains "CURATION_DB_URL=${_db_url}" "${docker_log}"
  assert_contains "PDF_STORAGE_PATH=/app/pdf_storage" "${docker_log}"
  assert_contains "FILE_OUTPUT_STORAGE_PATH=/app/file_outputs" "${docker_log}"

  export PATH="${old_path}"
  unset DOCKER_STUB_LOG STUB_CURL_BEHAVIOR
  unset SYMPHONY_REVIEW_FRONTEND_HEALTH_ATTEMPTS SYMPHONY_REVIEW_FRONTEND_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_BACKEND_HEALTH_ATTEMPTS SYMPHONY_REVIEW_BACKEND_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_CURATION_HEALTH_ATTEMPTS SYMPHONY_REVIEW_CURATION_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_PDF_HEALTH_ATTEMPTS SYMPHONY_REVIEW_PDF_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_PREP_REFRESH_MANAGED
}

test_review_prep_retries_dependency_start_once() {
  local temp_root workspace stub_dir output docker_log old_path env_file fail_once_count
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/ALL-49"
  stub_dir="${temp_root}/stubbin"
  output="${temp_root}/output.txt"
  docker_log="${temp_root}/docker.log"
  env_file="${temp_root}/private.env"
  fail_once_count="${temp_root}/docker-fail-once.count"

  mkdir -p "${workspace}/scripts"
  : > "${workspace}/docker-compose.yml"
  cat > "${env_file}" <<'EOF'
export OPENAI_API_KEY=test-openai
export GROQ_API_KEY=test-groq
export POSTGRES_PASSWORD=postgres
export RERANK_PROVIDER=local_transformers
EOF

  make_workspace_tunnel_helpers "${workspace}"
  make_stub_bin "${stub_dir}" "healthy"

  old_path="${PATH}"
  export PATH="${stub_dir}:${PATH}"
  export DOCKER_STUB_LOG="${docker_log}"
  export STUB_DOCKER_FAIL_ONCE_MATCH="up -d --wait postgres redis reranker-transformers weaviate"
  export STUB_DOCKER_FAIL_ONCE_COUNT_FILE="${fail_once_count}"
  export SYMPHONY_REVIEW_FRONTEND_HEALTH_ATTEMPTS=1
  export SYMPHONY_REVIEW_FRONTEND_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_BACKEND_HEALTH_ATTEMPTS=1
  export SYMPHONY_REVIEW_BACKEND_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_CURATION_HEALTH_ATTEMPTS=1
  export SYMPHONY_REVIEW_CURATION_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_DEPENDENCY_START_RETRY_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_PREP_REFRESH_MANAGED=0

  "${REPO_ROOT}/scripts/utilities/symphony_human_review_prep.sh" \
    --workspace-dir "${workspace}" \
    --env-file "${env_file}" \
    --start-test-containers true \
    > "${output}"

  assert_contains "human_review_prep_wrapper_status=ready" "${output}"
  assert_contains "rerank_provider=local_transformers" "${output}"
  assert_contains "reranker_dependency_required=1" "${output}"
  assert_contains "dependency_start_attempt_failed=1" "${output}"
  assert_contains "dependency_start_retry_success_attempt=2" "${output}"
  assert_contains "compose_ps_begin" "${output}"
  assert_contains "service_logs_begin=weaviate" "${output}"
  assert_contains "args=compose --env-file ${env_file} -f ${workspace}/docker-compose.yml -p all49 --profile local-reranker config --services" "${docker_log}"
  assert_count "2" "args=compose --env-file ${env_file} -f ${workspace}/docker-compose.yml -p all49 --profile local-reranker up -d --wait postgres redis reranker-transformers weaviate" "${docker_log}"

  export PATH="${old_path}"
  unset DOCKER_STUB_LOG STUB_CURL_BEHAVIOR
  unset STUB_DOCKER_FAIL_ONCE_MATCH STUB_DOCKER_FAIL_ONCE_COUNT_FILE
  unset SYMPHONY_REVIEW_FRONTEND_HEALTH_ATTEMPTS SYMPHONY_REVIEW_FRONTEND_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_BACKEND_HEALTH_ATTEMPTS SYMPHONY_REVIEW_BACKEND_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_CURATION_HEALTH_ATTEMPTS SYMPHONY_REVIEW_CURATION_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_DEPENDENCY_START_RETRY_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_PREP_REFRESH_MANAGED
}

test_review_prep_can_include_langfuse_stack() {
  local temp_root workspace stub_dir output docker_log old_path env_file
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/ALL-49"
  stub_dir="${temp_root}/stubbin"
  output="${temp_root}/output.txt"
  docker_log="${temp_root}/docker.log"
  env_file="${temp_root}/private.env"

  mkdir -p "${workspace}/scripts"
  : > "${workspace}/docker-compose.yml"
  cat > "${env_file}" <<'EOF'
export OPENAI_API_KEY=test-openai
export GROQ_API_KEY=test-groq
export POSTGRES_PASSWORD=postgres
EOF

  make_workspace_tunnel_helpers "${workspace}"
  make_stub_bin "${stub_dir}" "healthy"

  old_path="${PATH}"
  export PATH="${stub_dir}:${PATH}"
  export DOCKER_STUB_LOG="${docker_log}"
  export SYMPHONY_REVIEW_FRONTEND_HEALTH_ATTEMPTS=1
  export SYMPHONY_REVIEW_FRONTEND_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_BACKEND_HEALTH_ATTEMPTS=1
  export SYMPHONY_REVIEW_BACKEND_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_CURATION_HEALTH_ATTEMPTS=1
  export SYMPHONY_REVIEW_CURATION_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_PREP_REFRESH_MANAGED=0
  export SYMPHONY_REVIEW_INCLUDE_LANGFUSE_STACK=1

  "${REPO_ROOT}/scripts/utilities/symphony_human_review_prep.sh" \
    --workspace-dir "${workspace}" \
    --env-file "${env_file}" \
    --start-test-containers true \
    > "${output}"

  assert_contains "human_review_prep_wrapper_status=ready" "${output}"
  assert_contains "include_langfuse_stack=1" "${output}"
  assert_contains "dependency_services=postgres,redis,weaviate,clickhouse,minio,langfuse,langfuse-worker" "${output}"
  assert_contains "args=compose --env-file ${env_file} -f ${workspace}/docker-compose.yml -p all49 up -d --wait postgres redis weaviate clickhouse minio langfuse langfuse-worker" "${docker_log}"

  export PATH="${old_path}"
  unset DOCKER_STUB_LOG STUB_CURL_BEHAVIOR
  unset SYMPHONY_REVIEW_FRONTEND_HEALTH_ATTEMPTS SYMPHONY_REVIEW_FRONTEND_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_BACKEND_HEALTH_ATTEMPTS SYMPHONY_REVIEW_BACKEND_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_CURATION_HEALTH_ATTEMPTS SYMPHONY_REVIEW_CURATION_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_PREP_REFRESH_MANAGED
  unset SYMPHONY_REVIEW_INCLUDE_LANGFUSE_STACK
}

test_review_prep_preserves_review_ports_over_private_env() {
  local temp_root workspace stub_dir output docker_log old_path env_file
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/ALL-49"
  stub_dir="${temp_root}/stubbin"
  output="${temp_root}/output.txt"
  docker_log="${temp_root}/docker.log"
  env_file="${temp_root}/private.env"

  mkdir -p "${workspace}/scripts"
  : > "${workspace}/docker-compose.yml"
  cat > "${env_file}" <<'EOF'
export OPENAI_API_KEY=test-openai
export GROQ_API_KEY=test-groq
export POSTGRES_PASSWORD=postgres
export LANGFUSE_HOST_PORT=127.0.0.1:3000
export NEXTAUTH_URL=http://localhost:3000
EOF

  make_workspace_tunnel_helpers "${workspace}"
  make_stub_bin "${stub_dir}" "healthy"

  old_path="${PATH}"
  export PATH="${stub_dir}:${PATH}"
  export DOCKER_STUB_LOG="${docker_log}"
  export SYMPHONY_REVIEW_FRONTEND_HEALTH_ATTEMPTS=1
  export SYMPHONY_REVIEW_FRONTEND_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_BACKEND_HEALTH_ATTEMPTS=1
  export SYMPHONY_REVIEW_BACKEND_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_CURATION_HEALTH_ATTEMPTS=1
  export SYMPHONY_REVIEW_CURATION_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_PREP_REFRESH_MANAGED=0
  export SYMPHONY_REVIEW_INCLUDE_LANGFUSE_STACK=1

  "${REPO_ROOT}/scripts/utilities/symphony_human_review_prep.sh" \
    --workspace-dir "${workspace}" \
    --env-file "${env_file}" \
    --start-test-containers true \
    > "${output}"

  assert_contains "human_review_prep_wrapper_status=ready" "${output}"
  assert_contains "langfuse_host_port=3449" "${output}"
  assert_contains "LANGFUSE_HOST_PORT=3449" "${docker_log}"
  assert_contains "NEXTAUTH_URL=http://192.168.86.44:3449" "${docker_log}"

  export PATH="${old_path}"
  unset DOCKER_STUB_LOG STUB_CURL_BEHAVIOR
  unset SYMPHONY_REVIEW_FRONTEND_HEALTH_ATTEMPTS SYMPHONY_REVIEW_FRONTEND_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_BACKEND_HEALTH_ATTEMPTS SYMPHONY_REVIEW_BACKEND_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_CURATION_HEALTH_ATTEMPTS SYMPHONY_REVIEW_CURATION_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_PREP_REFRESH_MANAGED
  unset SYMPHONY_REVIEW_INCLUDE_LANGFUSE_STACK
}

test_review_prep_reports_backend_root_cause() {
  local temp_root workspace stub_dir output docker_log old_path env_file
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/ALL-49"
  stub_dir="${temp_root}/stubbin"
  output="${temp_root}/output.txt"
  docker_log="${temp_root}/docker.log"
  env_file="${temp_root}/private.env"

  mkdir -p "${workspace}/scripts"
  : > "${workspace}/docker-compose.yml"
  cat > "${env_file}" <<'EOF'
export OPENAI_API_KEY=test-openai
export GROQ_API_KEY=test-groq
export POSTGRES_PASSWORD=postgres
EOF

  make_workspace_tunnel_helpers "${workspace}"
  make_stub_bin "${stub_dir}" "backend_down"

  old_path="${PATH}"
  export PATH="${stub_dir}:${PATH}"
  export DOCKER_STUB_LOG="${docker_log}"
  export SYMPHONY_REVIEW_FRONTEND_HEALTH_ATTEMPTS=2
  export SYMPHONY_REVIEW_FRONTEND_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_BACKEND_HEALTH_ATTEMPTS=2
  export SYMPHONY_REVIEW_BACKEND_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_CURATION_HEALTH_ATTEMPTS=1
  export SYMPHONY_REVIEW_CURATION_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_PREP_REFRESH_MANAGED=0

  if "${REPO_ROOT}/scripts/utilities/symphony_human_review_prep.sh" \
    --workspace-dir "${workspace}" \
    --env-file "${env_file}" \
    --start-test-containers true \
    > "${output}" 2>&1; then
    echo "Expected review prep to fail when backend health never comes up" >&2
    exit 1
  fi

  assert_contains "backend_health=unreachable" "${output}"
  assert_contains "backend_root_cause=sqlalchemy.exc.ProgrammingError: (psycopg2.errors.UndefinedColumn) column \"mod_prompt_overrides\" of relation \"agents\" does not exist" "${output}"
  assert_contains "human_review_prep_wrapper_status=partial" "${output}"
  assert_contains "human_review_prep_wrapper_reason=backend_unreachable" "${output}"

  export PATH="${old_path}"
  unset DOCKER_STUB_LOG STUB_CURL_BEHAVIOR
  unset SYMPHONY_REVIEW_FRONTEND_HEALTH_ATTEMPTS SYMPHONY_REVIEW_FRONTEND_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_BACKEND_HEALTH_ATTEMPTS SYMPHONY_REVIEW_BACKEND_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_CURATION_HEALTH_ATTEMPTS SYMPHONY_REVIEW_CURATION_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_PREP_REFRESH_MANAGED
}

test_review_prep_normalizes_langfuse_env_before_compose() {
  local temp_root workspace stub_dir output docker_log old_path env_file
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/ALL-49"
  stub_dir="${temp_root}/stubbin"
  output="${temp_root}/output.txt"
  docker_log="${temp_root}/docker.log"
  env_file="${temp_root}/private.env"

  mkdir -p "${workspace}/scripts"
  : > "${workspace}/docker-compose.yml"
  # Langfuse URL built from parts to avoid secret-scanner false positives
  _lf_old_url="postgresql://""langfuse_user:langfuse_pass@langfuse-db:5432/langfuse"
  cat > "${env_file}" <<EOF
export OPENAI_API_KEY=test-openai
export GROQ_API_KEY=test-groq
export POSTGRES_PASSWORD=postgres
export LANGFUSE_LOCAL_DATABASE_URL=${_lf_old_url}
export LANGFUSE_LOCAL_ENCRYPTION_KEY=CHANGE_ME_64_CHAR_HEX
EOF

  make_workspace_tunnel_helpers "${workspace}"
  make_workspace_langfuse_repair_helper "${workspace}"
  make_stub_bin "${stub_dir}" "healthy"

  old_path="${PATH}"
  export PATH="${stub_dir}:${PATH}"
  export DOCKER_STUB_LOG="${docker_log}"
  export SYMPHONY_REVIEW_FRONTEND_HEALTH_ATTEMPTS=1
  export SYMPHONY_REVIEW_FRONTEND_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_BACKEND_HEALTH_ATTEMPTS=1
  export SYMPHONY_REVIEW_BACKEND_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_CURATION_HEALTH_ATTEMPTS=1
  export SYMPHONY_REVIEW_CURATION_HEALTH_SLEEP_SECONDS=0
  export SYMPHONY_REVIEW_PREP_REFRESH_MANAGED=0

  "${REPO_ROOT}/scripts/utilities/symphony_human_review_prep.sh" \
    --workspace-dir "${workspace}" \
    --env-file "${env_file}" \
    --start-test-containers true \
    > "${output}"

  assert_contains "human_review_prep_wrapper_status=ready" "${output}"
  assert_contains "normalized_langfuse_env=${env_file}" "${output}"
  # Assertions use variable-built URIs to avoid secret-scanner false positives
  _lf_new_url="postgresql://""postgres:postgres@postgres:5432/postgres"
  assert_contains "export LANGFUSE_LOCAL_DATABASE_URL=${_lf_new_url}" "${env_file}"
  assert_contains "export LANGFUSE_LOCAL_ENCRYPTION_KEY=TEST_DUMMY_ENCRYPTION_KEY_not_real_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" "${env_file}"
  assert_contains "LANGFUSE_LOCAL_DATABASE_URL=${_lf_new_url}" "${docker_log}"
  assert_contains "LANGFUSE_LOCAL_ENCRYPTION_KEY=TEST_DUMMY_ENCRYPTION_KEY_not_real_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" "${docker_log}"

  export PATH="${old_path}"
  unset DOCKER_STUB_LOG STUB_CURL_BEHAVIOR
  unset SYMPHONY_REVIEW_FRONTEND_HEALTH_ATTEMPTS SYMPHONY_REVIEW_FRONTEND_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_BACKEND_HEALTH_ATTEMPTS SYMPHONY_REVIEW_BACKEND_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_CURATION_HEALTH_ATTEMPTS SYMPHONY_REVIEW_CURATION_HEALTH_SLEEP_SECONDS
  unset SYMPHONY_REVIEW_PREP_REFRESH_MANAGED
}

test_review_prep_default_skips_stack_startup
test_review_prep_happy_path
test_review_prep_retries_dependency_start_once
test_review_prep_preserves_review_ports_over_private_env
test_review_prep_reports_backend_root_cause
test_review_prep_normalizes_langfuse_env_before_compose
test_review_prep_can_include_langfuse_stack

echo "symphony_human_review_prep tests passed"
