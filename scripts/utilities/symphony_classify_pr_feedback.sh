#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF' >&2
Usage:
  symphony_classify_pr_feedback.sh --report-file PATH [options]

Options:
  --report-file PATH        Claude review report file to classify. Required.
  --model VALUE             Override classifier model.
  --reasoning-effort VALUE  Override classifier reasoning effort.
  --timeout-seconds N       Codex timeout in seconds (default: 120).
  --codex-bin PATH          Codex executable (default: codex).
  --overrides-file PATH     Config override JSON (default: .symphony/codex-overrides.json).
  --fixture-output-file PATH
                            Testing override: parse this model output instead of running Codex.

Exit codes:
  0   clean: no implementation work required
  10  actionable: implementation work required
  11  uncertain: classify conservatively as needing implementation
  2   error: classifier failed; caller should treat as actionable
EOF
}

report_file=""
default_model="gpt-5.4-mini"
default_reasoning_effort="high"
model="${SYMPHONY_PR_FEEDBACK_CLASSIFIER_MODEL:-}"
reasoning_effort="${SYMPHONY_PR_FEEDBACK_CLASSIFIER_REASONING_EFFORT:-}"
timeout_seconds="${SYMPHONY_PR_FEEDBACK_CLASSIFIER_TIMEOUT_SECONDS:-120}"
codex_bin="${SYMPHONY_PR_FEEDBACK_CLASSIFIER_CODEX_BIN:-codex}"
overrides_file="${SYMPHONY_PR_FEEDBACK_CLASSIFIER_OVERRIDES_FILE:-}"
fixture_output_file=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --report-file)
      report_file="${2:-}"
      shift 2
      ;;
    --model)
      model="${2:-}"
      shift 2
      ;;
    --reasoning-effort)
      reasoning_effort="${2:-}"
      shift 2
      ;;
    --timeout-seconds)
      timeout_seconds="${2:-}"
      shift 2
      ;;
    --codex-bin)
      codex_bin="${2:-}"
      shift 2
      ;;
    --overrides-file)
      overrides_file="${2:-}"
      shift 2
      ;;
    --fixture-output-file)
      fixture_output_file="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "${report_file}" || ! -s "${report_file}" ]]; then
  echo "PR_FEEDBACK_CLASSIFIER_STATUS=error"
  echo "PR_FEEDBACK_CLASSIFIER_ERROR=Missing or empty --report-file"
  exit 2
fi

if ! [[ "${timeout_seconds}" =~ ^[0-9]+$ ]] || (( timeout_seconds == 0 )); then
  echo "PR_FEEDBACK_CLASSIFIER_STATUS=error"
  echo "PR_FEEDBACK_CLASSIFIER_ERROR=timeout-seconds must be a positive integer"
  exit 2
fi

if [[ -z "${overrides_file}" ]]; then
  overrides_file=".symphony/codex-overrides.json"
  if [[ ! -f "${overrides_file}" && -n "${SYMPHONY_LOCAL_SOURCE_ROOT:-}" ]]; then
    source_overrides="${SYMPHONY_LOCAL_SOURCE_ROOT}/.symphony/codex-overrides.json"
    if [[ -f "${source_overrides}" ]]; then
      overrides_file="${source_overrides}"
    fi
  fi
fi

resolve_classifier_config() {
  python3 - "${overrides_file}" "${model}" "${reasoning_effort}" "${default_model}" "${default_reasoning_effort}" <<'PY'
import json
import sys
from pathlib import Path

overrides_path = Path(sys.argv[1])
model_override = sys.argv[2].strip()
reasoning_override = sys.argv[3].strip()
default_model = sys.argv[4].strip()
default_reasoning = sys.argv[5].strip()

config = {
    "model": default_model,
    "reasoning_effort": default_reasoning,
}

if overrides_path.exists():
    try:
        payload = json.loads(overrides_path.read_text(encoding="utf-8") or "{}")
        classifier = payload.get("pr_feedback_classifier")
        if isinstance(classifier, dict):
            if isinstance(classifier.get("model"), str) and classifier["model"].strip():
                config["model"] = classifier["model"].strip()
            if isinstance(classifier.get("reasoning_effort"), str) and classifier["reasoning_effort"].strip():
                config["reasoning_effort"] = classifier["reasoning_effort"].strip()
    except Exception as exc:
        print(
            f"PR_FEEDBACK_CLASSIFIER_CONFIG_WARNING=Failed to read overrides file {overrides_path}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

if model_override:
    config["model"] = model_override
if reasoning_override:
    config["reasoning_effort"] = reasoning_override

print(config["model"])
print(config["reasoning_effort"])
PY
}

mapfile -t classifier_config < <(resolve_classifier_config)
model="${classifier_config[0]:-${default_model}}"
reasoning_effort="${classifier_config[1]:-${default_reasoning_effort}}"

prompt_file="$(mktemp "${TMPDIR:-/tmp}/symphony-pr-feedback-prompt-XXXXXX.md")"
schema_file="$(mktemp "${TMPDIR:-/tmp}/symphony-pr-feedback-schema-XXXXXX.json")"
response_file="$(mktemp "${TMPDIR:-/tmp}/symphony-pr-feedback-response-XXXXXX.json")"
stdout_file="$(mktemp "${TMPDIR:-/tmp}/symphony-pr-feedback-stdout-XXXXXX.log")"
stderr_file="$(mktemp "${TMPDIR:-/tmp}/symphony-pr-feedback-stderr-XXXXXX.log")"

cleanup() {
  rm -f "${prompt_file}" "${schema_file}" "${response_file}" "${stdout_file}" "${stderr_file}"
}
trap cleanup EXIT

cat > "${schema_file}" <<'EOF'
{
  "type": "object",
  "additionalProperties": false,
  "required": ["classification", "reason", "action_items"],
  "properties": {
    "classification": {
      "type": "string",
      "enum": ["clean", "actionable", "uncertain"]
    },
    "reason": {
      "type": "string"
    },
    "action_items": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  }
}
EOF

cat > "${prompt_file}" <<'EOF'
You are classifying a Claude Code pull request review for Symphony automation.

Decide whether the review contains implementation work that must be addressed before the issue can move forward.

Return JSON only with this exact shape:
{
  "classification": "clean" | "actionable" | "uncertain",
  "reason": "...",
  "action_items": ["..."]
}

Rules:
- Use "clean" only when the review clearly approves or confirms that no blocking, non-blocking, follow-up, or actionable implementation work remains.
- Use "actionable" when the review asks for code, tests, docs, config, behavior changes, verification, cleanup, or follow-up implementation before the issue advances.
- Use "uncertain" when the review is mixed, ambiguous, truncated, mostly metadata, or does not contain enough information to decide safely.
- Do not classify as clean just because the review says LGTM, approve, or previous approval stands if it also includes suggestions, warnings, concerns, non-blocking issues, follow-ups, or requests.
- If unsure, choose "uncertain".

Claude review report:
EOF
sed 's/^/> /' "${report_file}" >> "${prompt_file}"

if [[ -n "${fixture_output_file}" ]]; then
  if [[ ! -f "${fixture_output_file}" ]]; then
    echo "PR_FEEDBACK_CLASSIFIER_STATUS=error"
    echo "PR_FEEDBACK_CLASSIFIER_MODEL=${model}"
    echo "PR_FEEDBACK_CLASSIFIER_REASONING_EFFORT=${reasoning_effort}"
    echo "PR_FEEDBACK_CLASSIFIER_ERROR=Fixture output file not found"
    exit 2
  fi
  cp "${fixture_output_file}" "${response_file}"
else
  if ! command -v "${codex_bin}" >/dev/null 2>&1; then
    echo "PR_FEEDBACK_CLASSIFIER_STATUS=error"
    echo "PR_FEEDBACK_CLASSIFIER_MODEL=${model}"
    echo "PR_FEEDBACK_CLASSIFIER_REASONING_EFFORT=${reasoning_effort}"
    echo "PR_FEEDBACK_CLASSIFIER_ERROR=Codex executable not found: ${codex_bin}"
    exit 2
  fi

  if ! command -v timeout >/dev/null 2>&1; then
    echo "PR_FEEDBACK_CLASSIFIER_STATUS=error"
    echo "PR_FEEDBACK_CLASSIFIER_MODEL=${model}"
    echo "PR_FEEDBACK_CLASSIFIER_REASONING_EFFORT=${reasoning_effort}"
    echo "PR_FEEDBACK_CLASSIFIER_ERROR=timeout executable not found; cannot enforce ${timeout_seconds}s classifier bound"
    exit 2
  fi

  cmd=(
    "${codex_bin}" exec
    --ephemeral
    --sandbox read-only
    -m "${model}"
    -c "model_reasoning_effort=\"${reasoning_effort}\""
    --output-schema "${schema_file}"
    -o "${response_file}"
    -
  )

  set +e
  timeout "${timeout_seconds}s" "${cmd[@]}" < "${prompt_file}" > "${stdout_file}" 2> "${stderr_file}"
  codex_rc=$?
  set -e

  if (( codex_rc != 0 )); then
    error_preview="$(tr '\n' ' ' < "${stderr_file}" | cut -c1-400)"
    if [[ -z "${error_preview}" ]]; then
      error_preview="$(tr '\n' ' ' < "${stdout_file}" | cut -c1-400)"
    fi
    echo "PR_FEEDBACK_CLASSIFIER_STATUS=error"
    echo "PR_FEEDBACK_CLASSIFIER_MODEL=${model}"
    echo "PR_FEEDBACK_CLASSIFIER_REASONING_EFFORT=${reasoning_effort}"
    echo "PR_FEEDBACK_CLASSIFIER_ERROR=Codex classifier failed with exit ${codex_rc}: ${error_preview}"
    exit 2
  fi
fi

python3 - "${response_file}" "${model}" "${reasoning_effort}" <<'PY'
import json
import re
import sys
from pathlib import Path

response_path = Path(sys.argv[1])
model = sys.argv[2]
reasoning = sys.argv[3]

raw = response_path.read_text(encoding="utf-8", errors="replace").strip()
if raw.startswith("```"):
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

try:
    payload = json.loads(raw)
except Exception as exc:
    print("PR_FEEDBACK_CLASSIFIER_STATUS=error")
    print(f"PR_FEEDBACK_CLASSIFIER_MODEL={model}")
    print(f"PR_FEEDBACK_CLASSIFIER_REASONING_EFFORT={reasoning}")
    print(f"PR_FEEDBACK_CLASSIFIER_ERROR=Invalid classifier JSON: {type(exc).__name__}: {exc}")
    sys.exit(2)

classification = str(payload.get("classification", "")).strip().lower()
reason = " ".join(str(payload.get("reason", "")).split())[:800]
items = payload.get("action_items", [])
if not isinstance(items, list):
    items = []
items = [" ".join(str(item).split())[:300] for item in items if str(item).strip()]

if classification not in {"clean", "actionable", "uncertain"}:
    print("PR_FEEDBACK_CLASSIFIER_STATUS=error")
    print(f"PR_FEEDBACK_CLASSIFIER_MODEL={model}")
    print(f"PR_FEEDBACK_CLASSIFIER_REASONING_EFFORT={reasoning}")
    print(f"PR_FEEDBACK_CLASSIFIER_ERROR=Invalid classifier value: {classification}")
    sys.exit(2)

if classification == "clean" and items:
    classification = "uncertain"
    reason = (
        "Classifier returned clean but also supplied action items; "
        "routing conservatively. "
        + reason
    ).strip()[:800]

print(f"PR_FEEDBACK_CLASSIFIER_STATUS={classification}")
print(f"PR_FEEDBACK_CLASSIFIER_CLASSIFICATION={classification}")
print(f"PR_FEEDBACK_CLASSIFIER_MODEL={model}")
print(f"PR_FEEDBACK_CLASSIFIER_REASONING_EFFORT={reasoning}")
print(f"PR_FEEDBACK_CLASSIFIER_REASON={reason}")
for index, item in enumerate(items, start=1):
    print(f"PR_FEEDBACK_CLASSIFIER_ACTION_ITEM_{index}={item}")

if classification == "clean":
    sys.exit(0)
if classification == "actionable":
    sys.exit(10)
sys.exit(11)
PY
