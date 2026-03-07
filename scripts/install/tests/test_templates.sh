#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
env_template="${repo_root}/scripts/install/lib/templates/env.standalone"
groups_template="${repo_root}/scripts/install/lib/templates/groups.standalone.yaml"

required_env_keys=(
  OPENAI_API_KEY
  AUTH_PROVIDER
  DEV_MODE
  POSTGRES_PASSWORD
  REDIS_AUTH
  DATABASE_URL
  NEXTAUTH_SECRET
  SALT
  ENCRYPTION_KEY
  CLICKHOUSE_PASSWORD
  MINIO_ROOT_PASSWORD
  LANGFUSE_INIT_PROJECT_PUBLIC_KEY
  LANGFUSE_INIT_PROJECT_SECRET_KEY
  LANGFUSE_INIT_USER_PASSWORD
  LANGFUSE_PUBLIC_KEY
  LANGFUSE_SECRET_KEY
  LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY
  LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY
  LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY
  LLM_PROVIDER_STRICT_MODE
  RUN_DB_BOOTSTRAP_ON_START
  RUN_DB_MIGRATIONS_ON_START
  HEALTH_CHECK_STRICT_MODE
  LANGFUSE_DATABASE_URL
)

for key in "${required_env_keys[@]}"; do
  if ! grep -q "^${key}=" "$env_template"; then
    echo "Missing required env key: $key" >&2
    exit 1
  fi
done

grep -q '^LANGFUSE_PUBLIC_KEY=${LANGFUSE_INIT_PROJECT_PUBLIC_KEY}$' "$env_template"
grep -q '^LANGFUSE_SECRET_KEY=${LANGFUSE_INIT_PROJECT_SECRET_KEY}$' "$env_template"
grep -q '^LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=${MINIO_ROOT_PASSWORD}$' "$env_template"
grep -q '^LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY=${MINIO_ROOT_PASSWORD}$' "$env_template"
grep -q '^LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY=${MINIO_ROOT_PASSWORD}$' "$env_template"
grep -q '^LLM_PROVIDER_STRICT_MODE=false$' "$env_template"
grep -q '^RUN_DB_BOOTSTRAP_ON_START=true$' "$env_template"
grep -q '^RUN_DB_MIGRATIONS_ON_START=true$' "$env_template"
grep -q '^HEALTH_CHECK_STRICT_MODE=true$' "$env_template"

grep -q '__AUTH_TYPE__' "$groups_template"
grep -q '__GROUP_CLAIM__' "$groups_template"

echo "Template checks passed"
