#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${1:-file_outputs/evidence/agent_evidence_${STAMP}}"
mkdir -p "${OUT_DIR}"
FAILURES=0

{
  echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "repo_root=${ROOT_DIR}"
  echo "git_branch=$(git branch --show-current || true)"
  echo "git_head=$(git rev-parse --short HEAD || true)"
} > "${OUT_DIR}/meta.env"

if ! docker compose ps > "${OUT_DIR}/docker_compose_ps.txt" 2>&1; then
  FAILURES=$((FAILURES + 1))
fi
if ! docker compose logs --tail=300 backend > "${OUT_DIR}/backend_logs_tail.txt" 2>&1; then
  FAILURES=$((FAILURES + 1))
fi
if ! docker compose logs --tail=200 frontend > "${OUT_DIR}/frontend_logs_tail.txt" 2>&1; then
  FAILURES=$((FAILURES + 1))
fi
docker compose logs --tail=200 trace_review > "${OUT_DIR}/trace_review_logs_tail.txt" 2>&1 || true
docker compose logs --tail=200 langfuse > "${OUT_DIR}/langfuse_logs_tail.txt" 2>&1 || true

if ! curl -sS --fail --max-time 8 http://localhost:8000/health > "${OUT_DIR}/backend_health.json" 2>"${OUT_DIR}/backend_health.err"; then
  FAILURES=$((FAILURES + 1))
fi
if ! curl -sS --fail --max-time 8 http://localhost:8000/api/admin/health/llm-providers > "${OUT_DIR}/llm_provider_health.json" 2>"${OUT_DIR}/llm_provider_health.err"; then
  FAILURES=$((FAILURES + 1))
fi

if [[ -d ".symphony/log" ]]; then
  mkdir -p "${OUT_DIR}/symphony_log"
  cp -R .symphony/log/. "${OUT_DIR}/symphony_log/" 2>/dev/null || true
fi

if [[ -x "./scripts/testing/llm_provider_smoke_local.sh" ]]; then
  if ! ./scripts/testing/llm_provider_smoke_local.sh > "${OUT_DIR}/llm_provider_smoke.stdout" 2>"${OUT_DIR}/llm_provider_smoke.stderr"; then
    FAILURES=$((FAILURES + 1))
  fi
fi

cat > "${OUT_DIR}/README.md" <<EOF
# Agent Evidence Bundle

- Captured: $(date -u +%Y-%m-%dT%H:%M:%SZ)
- Repository: ${ROOT_DIR}

## Included Artifacts

- \`meta.env\` - run metadata
- \`docker_compose_ps.txt\` - container/service status
- \`backend_logs_tail.txt\` - backend log tail
- \`frontend_logs_tail.txt\` - frontend log tail
- \`trace_review_logs_tail.txt\` - trace review log tail (best effort)
- \`langfuse_logs_tail.txt\` - langfuse log tail (best effort)
- \`backend_health.json\` - backend health endpoint response
- \`llm_provider_health.json\` - provider health endpoint response
- \`llm_provider_smoke.*\` - local smoke script output (best effort)
- \`symphony_log/\` - Symphony logs when present
EOF

echo "Agent evidence collected: ${OUT_DIR}"
echo "failures=${FAILURES}" > "${OUT_DIR}/capture_status.txt"

if (( FAILURES > 0 )); then
  echo "Agent evidence capture is partial (${FAILURES} failures)." >&2
  if [[ "${EVIDENCE_ALLOW_PARTIAL:-0}" != "1" ]]; then
    exit 1
  fi
fi
