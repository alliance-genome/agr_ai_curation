#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PACKAGE_ROOT="${REPO_ROOT}/agent_tests/midscene"

usage() {
  cat <<'EOF'
Usage: scripts/testing/agent_ui_smoke.sh [options]

Same-host Midscene curator-agent smoke pilot.

Options:
  --case NAME             all, create, edit, upload, run, or canonical case name
  --tag TAG               include a Midscene tag; repeatable
  --headed                show Chromium
  --headless              run Chromium headlessly (default)
  --provider NAME         codex (default) or openai
  --cost-warning-usd USD  after-run OpenAI API cost warning (default 5)
  --app-auth MODE         api-key (default) or cookie
  --url URL               loopback application URL (default http://localhost:3002)
  --retain-resources      retain prefixed app resources for debugging
  --run-id ID             explicit safe run/evidence identifier
  --preflight-only        run strict app/model/PDF/browser checks only
  --offline               run typecheck, offline tests, and YAML validation only
  -h, --help              show this help

Secrets are accepted only through the selected environment variable:
TESTING_API_KEY for api-key auth, CURATOR_COOKIE for cookie auth, and
OPENAI_API_KEY only when --provider openai is explicitly selected.
EOF
}

case_name="all"
provider="${AGENT_UI_SMOKE_PROVIDER:-codex}"
app_auth="${AGENT_UI_SMOKE_APP_AUTH:-api-key}"
app_url="${AGENT_UI_SMOKE_APP_URL:-http://localhost:3002}"
headless="${AGENT_UI_SMOKE_HEADLESS:-true}"
retain="${AGENT_UI_SMOKE_RETAIN_RESOURCES:-false}"
cost_warning_usd="${AGENT_UI_SMOKE_OPENAI_COST_WARNING_USD:-5}"
run_id="${AGENT_UI_SMOKE_RUN_ID:-}"
preflight_only=false
offline=false
tags=()

while (($#)); do
  case "$1" in
    --case)
      [[ $# -ge 2 ]] || { echo "--case requires a value" >&2; exit 2; }
      case "$2" in
        all) case_name="all" ;;
        create) case_name="create-connect-save" ;;
        edit) case_name="edit-rewire" ;;
        upload) case_name="upload-ask" ;;
        run) case_name="run-saved-flow" ;;
        create-connect-save|edit-rewire|upload-ask|run-saved-flow) case_name="$2" ;;
        *) echo "Unknown case: $2" >&2; exit 2 ;;
      esac
      shift 2
      ;;
    --tag)
      [[ $# -ge 2 ]] || { echo "--tag requires a value" >&2; exit 2; }
      tags+=("$2")
      shift 2
      ;;
    --headed) headless=false; shift ;;
    --headless) headless=true; shift ;;
    --provider)
      [[ $# -ge 2 ]] || { echo "--provider requires a value" >&2; exit 2; }
      provider="$2"; shift 2
      ;;
    --cost-warning-usd)
      [[ $# -ge 2 ]] || { echo "--cost-warning-usd requires a value" >&2; exit 2; }
      cost_warning_usd="$2"; shift 2
      ;;
    --app-auth)
      [[ $# -ge 2 ]] || { echo "--app-auth requires a value" >&2; exit 2; }
      app_auth="$2"; shift 2
      ;;
    --url)
      [[ $# -ge 2 ]] || { echo "--url requires a value" >&2; exit 2; }
      app_url="$2"; shift 2
      ;;
    --retain-resources) retain=true; shift ;;
    --run-id)
      [[ $# -ge 2 ]] || { echo "--run-id requires a value" >&2; exit 2; }
      run_id="$2"; shift 2
      ;;
    --preflight-only) preflight_only=true; shift ;;
    --offline) offline=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "$provider" == "codex" || "$provider" == "openai" ]] || { echo "Provider must be codex or openai" >&2; exit 2; }
[[ "$app_auth" == "api-key" || "$app_auth" == "cookie" ]] || { echo "App auth must be api-key or cookie" >&2; exit 2; }

if [[ ! -d "${PACKAGE_ROOT}/node_modules" ]]; then
  echo "Missing locked dependencies. Run: cd agent_tests/midscene && npm ci" >&2
  exit 2
fi

if [[ "$offline" == true ]]; then
  cd "$PACKAGE_ROOT"
  exec npm run check
fi

export AGENT_UI_SMOKE_APP_URL="$app_url"
export AGENT_UI_SMOKE_APP_AUTH="$app_auth"
export AGENT_UI_SMOKE_PROVIDER="$provider"
export AGENT_UI_SMOKE_HEADLESS="$headless"
export AGENT_UI_SMOKE_RETAIN_RESOURCES="$retain"
export AGENT_UI_SMOKE_OPENAI_COST_WARNING_USD="$cost_warning_usd"
export AGENT_UI_SMOKE_CASE="$case_name"
if ((${#tags[@]})); then
  tag_csv="$(IFS=,; echo "${tags[*]}")"
  export AGENT_UI_SMOKE_TAGS="$tag_csv"
fi
if [[ -n "$run_id" ]]; then
  export AGENT_UI_SMOKE_RUN_ID="$run_id"
fi

if [[ "$app_auth" == "api-key" ]]; then
  key_env="${AGENT_UI_SMOKE_API_KEY_ENV:-TESTING_API_KEY}"
else
  key_env="${AGENT_UI_SMOKE_COOKIE_ENV:-CURATOR_COOKIE}"
fi
[[ -n "${!key_env:-}" ]] || { echo "${key_env} must be set for ${app_auth} app authentication" >&2; exit 2; }

# The pilot validates and reports one model/provider slot. Remove inherited
# intent-specific Midscene slots before Node imports Midscene so planning and
# insight calls cannot bypass that selection or use a separate billing key.
while IFS= read -r env_name; do
  case "$env_name" in
    MIDSCENE_PLANNING_MODEL_*|MIDSCENE_INSIGHT_MODEL_*) unset "$env_name" ;;
  esac
done < <(compgen -e)

if [[ "$provider" == "codex" ]]; then
  unset MIDSCENE_MODEL_API_KEY
  unset OPENAI_API_KEY
else
  [[ -n "${OPENAI_API_KEY:-}" ]] || { echo "OPENAI_API_KEY is required for the explicitly selected OpenAI provider" >&2; exit 2; }
fi

cd "$PACKAGE_ROOT"
if [[ "$preflight_only" == true ]]; then
  exec npm run preflight
fi
exec npm run run
