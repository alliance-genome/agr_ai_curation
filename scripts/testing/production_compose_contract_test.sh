#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
compose_file="${repo_root}/docker-compose.production.yml"
preflight="${script_dir}/production_compose_preflight.py"
fixture_dir="${script_dir}/fixtures"
temp_dir="$(mktemp -d)"
trap 'rm -rf "${temp_dir}"' EXIT

env_file="${temp_dir}/production.env"
rendered_file="${temp_dir}/rendered.json"
development_env_file="${temp_dir}/development.env"
development_rendered_file="${temp_dir}/development-rendered.json"
frontend_build_metadata_file="${temp_dir}/frontend-build-metadata.json"
unsafe_frontend_build_metadata_file="${temp_dir}/unsafe-frontend-build-metadata.json"

write_test_env() {
  {
    printf '%s\n' \
      'BACKEND_IMAGE_TAG=v0.9.0' \
      'FRONTEND_IMAGE_TAG=v0.9.0' \
      'TRACE_REVIEW_BACKEND_IMAGE_TAG=v0.9.0' \
      'DATABASE_URL=postgresql://postgres@postgres:5432/ai_curation' \
      'LANGFUSE_LOCAL_DATABASE_URL=postgresql://postgres@postgres:5432/postgres' \
      'SALT=test-salt' \
      'ENCRYPTION_KEY=test-only' \
      'NEXTAUTH_SECRET=test-nextauth-secret' \
      'LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-test-public' \
      'LANGFUSE_INIT_PROJECT_SECRET_KEY=sk-lf-test-secret' \
      'REDIS_AUTH=test-redis-auth' \
      'WEAVIATE_API_KEY=test-weaviate-key' \
      'AUTH_PROVIDER=oidc' \
      'OIDC_ISSUER_URL=https://issuer.example.org' \
      'OIDC_CLIENT_ID=curation-production' \
      'OIDC_REDIRECT_URI=https://curation.example.org/auth/callback'
  } >"${env_file}"
}

render_with_override() {
  local override_file="$1"
  local output_file="$2"
  docker compose \
    --env-file "${env_file}" \
    -f "${compose_file}" \
    -f "${override_file}" \
    config --format json >"${output_file}"
}

assert_rejected_with() {
  local config_json="$1"
  local build_metadata_json="$2"
  shift 2
  local output="${temp_dir}/preflight.out"
  if "${preflight}" --env-file "${env_file}" --config-json "${config_json}" \
    --frontend-build-metadata-json "${build_metadata_json}" >"${output}" 2>&1; then
    echo "Expected production preflight to reject ${config_json}" >&2
    return 1
  fi
  local expected
  for expected in "$@"; do
    if ! grep -Fq -- "${expected}" "${output}"; then
      echo "Expected rejection containing '${expected}'" >&2
      sed -n '1,160p' "${output}" >&2
      return 1
    fi
  done
}

write_test_env
printf '%s\n' '{"schema_version":1,"vite_dev_mode":false,"git_sha":"abcdef1"}' \
  >"${frontend_build_metadata_file}"
printf '%s\n' '{"schema_version":1,"vite_dev_mode":true,"git_sha":"abcdef1"}' \
  >"${unsafe_frontend_build_metadata_file}"

publish_workflow="${repo_root}/.github/workflows/publish-images.yml"
grep -Fq 'Verify frontend compiled mode artifact' "${publish_workflow}"
grep -Fq '/usr/share/nginx/html/build-metadata.json' "${publish_workflow}"
grep -Fq '.vite_dev_mode == false' "${publish_workflow}"

# Exercise the environment created by a fresh `make setup` and the effective
# development Compose interpolation, without starting containers.
cp "${repo_root}/.env.example" "${development_env_file}"
docker compose \
  --env-file "${development_env_file}" \
  -f "${repo_root}/docker-compose.yml" \
  config --format json >"${development_rendered_file}"
python3 - "${development_rendered_file}" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
services = config["services"]
backend_env = services["backend"]["environment"]
frontend_env = services["frontend"]["environment"]
trace_review = services["trace_review_backend"]
trace_review_env = trace_review["environment"]
weaviate = services["weaviate"]
weaviate_env = weaviate["environment"]

assert trace_review["image"].endswith(":latest")
assert str(trace_review_env["DEV_MODE"]).lower() == "true"
assert str(trace_review_env["SECURE_COOKIES"]).lower() == "false"
assert str(backend_env["HEALTH_CHECK_REQUIRE_EXTERNAL_VALIDATION_DEPS"]).lower() == "false"
assert str(backend_env["HEALTH_CHECK_REQUIRE_LITERATURE_DB"]).lower() == "false"
assert backend_env["ELASTICSEARCH_HOST"] == ""
assert backend_env["ELASTICSEARCH_SCHEME"] == "https"
assert str(backend_env["PDF_MAX_FILE_SIZE_BYTES"]) == "524288000"
assert str(frontend_env["PDF_MAX_FILE_SIZE_BYTES"]) == "524288000"
assert "backup-filesystem" in str(weaviate_env["ENABLE_MODULES"]).split(",")
assert weaviate_env["BACKUP_FILESYSTEM_PATH"] == "/var/lib/weaviate/backups"
assert any(
    mount.get("target") == "/var/lib/weaviate/backups"
    for mount in weaviate["volumes"]
)
PY

docker compose --env-file "${env_file}" -f "${compose_file}" config --format json >"${rendered_file}"
# Exercise the same validator with deterministic image metadata while the
# installer tests cover its real image-inspection invocation.
"${preflight}" --env-file "${env_file}" --config-json "${rendered_file}" \
  --frontend-build-metadata-json "${frontend_build_metadata_file}"
python3 - "${rendered_file}" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
backend_env = config["services"]["backend"]["environment"]
frontend_env = config["services"]["frontend"]["environment"]
weaviate_env = config["services"]["weaviate"]["environment"]
weaviate = config["services"]["weaviate"]
assert backend_env["SENTRY_LOG_EVENT_LEVEL"] == ""
assert str(backend_env["SENTRY_AI_CONTENT_PREVIEW_MAX_CHARS"]) == "2000"
assert str(backend_env["SENTRY_TRANSACTION_RETAINED_SPANS_MAX"]) == "50"
assert str(backend_env["PDF_MAX_FILE_SIZE_BYTES"]) == "524288000"
assert str(frontend_env["PDF_MAX_FILE_SIZE_BYTES"]) == "524288000"
assert weaviate_env["AUTHORIZATION_ADMINLIST_USERS"] == "curation-backend"
assert "backup-filesystem" in str(weaviate_env["ENABLE_MODULES"]).split(",")
assert weaviate_env["BACKUP_FILESYSTEM_PATH"] == "/var/lib/weaviate/backups"
backup_mounts = [
    mount
    for mount in weaviate["volumes"]
    if mount.get("target") == "/var/lib/weaviate/backups"
]
assert len(backup_mounts) == 1
assert backup_mounts[0]["type"] == "bind"
PY

python3 - "${repo_root}" <<'PY'
from pathlib import Path
import re
import sys

repo_root = Path(sys.argv[1])
doc_paths = [repo_root / "README.md"]
for docs_root in (repo_root / "config", repo_root / "docs", repo_root / "packages"):
    doc_paths.extend(docs_root.rglob("*.md"))
for doc_path in doc_paths:
    text = doc_path.read_text(encoding="utf-8")
    for command_block in re.findall(r"```(?:bash|sh)\s*(.*?)```", text, re.DOTALL):
        if "docker-compose.production.yml" not in command_block:
            continue
        if re.search(r"\b(?:up|start|restart)\b", command_block):
            raise AssertionError(
                f"{doc_path} documents an unvalidated production Compose operation"
            )
PY

unsafe_environment="${temp_dir}/unsafe-environment.json"
render_with_override \
  "${fixture_dir}/production-compose-unsafe-environment.yml" \
  "${unsafe_environment}"
assert_rejected_with "${unsafe_environment}" "${frontend_build_metadata_file}" \
  'backend.AUTH_PROVIDER' \
  'backend.DEBUG' \
  'backend.DEV_MODE' \
  'backend.SECURE_COOKIES' \
  'backend.HEALTH_CHECK_STRICT_MODE' \
  'backend.HEALTH_CHECK_REQUIRE_EXTERNAL_VALIDATION_DEPS' \
  'backend.HEALTH_CHECK_REQUIRE_LITERATURE_DB' \
  'trace_review_backend.DEV_MODE' \
  'trace_review_backend.SECURE_COOKIES' \
  'weaviate.AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED' \
  'weaviate.AUTHENTICATION_APIKEY_ENABLED' \
  'weaviate.AUTHENTICATION_APIKEY_ALLOWED_KEYS' \
  'weaviate.AUTHORIZATION_ADMINLIST_USERS'

unsafe_images="${temp_dir}/unsafe-images-and-ports.json"
render_with_override \
  "${fixture_dir}/production-compose-unsafe-images-and-ports.yml" \
  "${unsafe_images}"
assert_rejected_with "${unsafe_images}" "${frontend_build_metadata_file}" \
  'backend.image must use a vX.Y.Z or sha-<shortsha> tag' \
  'frontend.image must use a vX.Y.Z or sha-<shortsha> tag' \
  'trace_review_backend.image must use a vX.Y.Z or sha-<shortsha> tag' \
  'postgres.image must not use the mutable latest tag' \
  'postgres.image must be pinned by digest' \
  'weaviate must not publish data ports in production'

assert_rejected_with "${rendered_file}" "${unsafe_frontend_build_metadata_file}" \
  'frontend image must be compiled with vite_dev_mode=false'

echo "Production Compose render-backed contract tests passed"
