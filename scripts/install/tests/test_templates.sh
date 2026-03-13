#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
env_template="${repo_root}/scripts/install/lib/templates/env.standalone"
groups_template="${repo_root}/scripts/install/lib/templates/groups.standalone.yaml"

required_env_keys=(
  OPENAI_API_KEY
  GROQ_API_KEY
  ANTHROPIC_API_KEY
  GEMINI_API_KEY
  AUTH_PROVIDER
  DEV_MODE
  OIDC_ISSUER_URL
  OIDC_CLIENT_ID
  OIDC_CLIENT_SECRET
  OIDC_REDIRECT_URI
  OIDC_GROUP_CLAIM
  POSTGRES_PASSWORD
  REDIS_AUTH
  DATABASE_URL
  NEXTAUTH_SECRET
  SALT
  ENCRYPTION_KEY
  LANGFUSE_LOCAL_NEXTAUTH_SECRET
  LANGFUSE_LOCAL_SALT
  LANGFUSE_LOCAL_ENCRYPTION_KEY
  CLICKHOUSE_PASSWORD
  MINIO_ROOT_PASSWORD
  LANGFUSE_INIT_PROJECT_PUBLIC_KEY
  LANGFUSE_INIT_PROJECT_SECRET_KEY
  LANGFUSE_INIT_USER_PASSWORD
  LANGFUSE_PUBLIC_KEY
  LANGFUSE_SECRET_KEY
  LANGFUSE_LOCAL_PUBLIC_KEY
  LANGFUSE_LOCAL_SECRET_KEY
  LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY
  LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY
  LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY
  LLM_PROVIDER_STRICT_MODE
  RUN_DB_BOOTSTRAP_ON_START
  RUN_DB_MIGRATIONS_ON_START
  HEALTH_CHECK_STRICT_MODE
  LANGFUSE_DATABASE_URL
  LANGFUSE_LOCAL_DATABASE_URL
  BACKEND_IMAGE
  BACKEND_IMAGE_TAG
  FRONTEND_IMAGE
  FRONTEND_IMAGE_TAG
  TRACE_REVIEW_BACKEND_IMAGE
  TRACE_REVIEW_BACKEND_IMAGE_TAG
  TRACE_REVIEW_URL
  TRACE_REVIEW_DEV_MODE
  TRACE_REVIEW_LANGFUSE_HOST
  TRACE_REVIEW_LANGFUSE_PUBLIC_KEY
  TRACE_REVIEW_LANGFUSE_SECRET_KEY
  TRACE_REVIEW_LANGFUSE_LOCAL_HOST
  TRACE_REVIEW_LANGFUSE_LOCAL_PUBLIC_KEY
  TRACE_REVIEW_LANGFUSE_LOCAL_SECRET_KEY
  TRACE_REVIEW_BACKEND_HOST
  TRACE_REVIEW_BACKEND_HOST_PORT
  TRACE_REVIEW_BACKEND_PORT
  TRACE_REVIEW_FRONTEND_URL
  TRACE_REVIEW_CACHE_TTL_HOURS
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
grep -q '^BACKEND_IMAGE=public.ecr.aws/v4p5b7m9/agr-ai-curation-backend$' "$env_template"
grep -q '^BACKEND_IMAGE_TAG=smoke-20260310-final$' "$env_template"
grep -q '^FRONTEND_IMAGE=public.ecr.aws/v4p5b7m9/agr-ai-curation-frontend$' "$env_template"
grep -q '^FRONTEND_IMAGE_TAG=smoke-20260310-final$' "$env_template"
grep -q '^TRACE_REVIEW_BACKEND_IMAGE=public.ecr.aws/v4p5b7m9/agr-ai-curation-trace-review-backend$' "$env_template"
grep -q '^TRACE_REVIEW_URL=http://trace_review_backend:8001$' "$env_template"
grep -q '^TRACE_REVIEW_LANGFUSE_HOST=http://langfuse:3000$' "$env_template"
grep -q '^TRACE_REVIEW_LANGFUSE_LOCAL_HOST=http://langfuse:3000$' "$env_template"
grep -q '^TRACE_REVIEW_BACKEND_HOST=0.0.0.0$' "$env_template"

grep -q '__AUTH_TYPE__' "$groups_template"
grep -q '__GROUP_CLAIM__' "$groups_template"

echo "Template checks passed"
