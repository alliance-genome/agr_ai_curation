#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: check_openai_agents_upgrade_gate.sh --diff-range RANGE --pr-body-file PATH [--repo-root PATH]

Fails when an openai-agents pin version changes without PR-body evidence that the
deep dev-release smoke passed. The required evidence line is:

  SDK-Smoke-Evidence: dev_release_smoke PASS <evidence-link-or-path>
USAGE
}

repo_root="$(pwd)"
diff_range=""
pr_body_file=""

while (($#)); do
  case "$1" in
    --repo-root)
      repo_root="${2:?--repo-root requires a value}"
      shift 2
      ;;
    --diff-range)
      diff_range="${2:?--diff-range requires a value}"
      shift 2
      ;;
    --pr-body-file)
      pr_body_file="${2:?--pr-body-file requires a value}"
      shift 2
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

if [[ -z "${diff_range}" ]]; then
  echo "Missing required --diff-range" >&2
  usage >&2
  exit 2
fi

if [[ -z "${pr_body_file}" ]]; then
  echo "Missing required --pr-body-file" >&2
  usage >&2
  exit 2
fi

cd "${repo_root}"

if [[ ! -f "${pr_body_file}" ]]; then
  echo "PR body file not found: ${pr_body_file}" >&2
  exit 2
fi

if ! diff_output="$(
  git diff --unified=0 --no-ext-diff "${diff_range}" -- \
    backend/requirements.txt backend/requirements.lock.txt 2>&1
)"; then
  echo "Invalid --diff-range or git diff failed: ${diff_range}" >&2
  printf '%s\n' "${diff_output}" >&2
  exit 2
fi

old_versions="$(
  printf '%s\n' "${diff_output}" \
    | sed -En 's/^-[[:space:]]*openai-agents(\[[^]]+\])?[[:space:]]*==[[:space:]]*([^[:space:]#;]+).*/\2/p' \
    | sort -u \
    | paste -sd, -
)"
new_versions="$(
  printf '%s\n' "${diff_output}" \
    | sed -En 's/^\+[[:space:]]*openai-agents(\[[^]]+\])?[[:space:]]*==[[:space:]]*([^[:space:]#;]+).*/\2/p' \
    | sort -u \
    | paste -sd, -
)"

if [[ -z "${old_versions}${new_versions}" ]]; then
  echo "SDK_UPGRADE_GATE_STATUS=skipped"
  echo "SDK_UPGRADE_GATE_REASON=openai-agents pin unchanged"
  exit 0
fi

if [[ "${old_versions}" == "${new_versions}" ]]; then
  echo "SDK_UPGRADE_GATE_STATUS=skipped"
  echo "SDK_UPGRADE_GATE_REASON=openai-agents pin line touched but version unchanged"
  echo "SDK_UPGRADE_GATE_VERSION=${new_versions}"
  exit 0
fi

if grep -Eiq '^SDK-Smoke-Evidence:[[:space:]]*.*dev_release_smoke.*PASS' "${pr_body_file}"; then
  echo "SDK_UPGRADE_GATE_STATUS=pass"
  echo "SDK_UPGRADE_GATE_OLD_VERSION=${old_versions:-none}"
  echo "SDK_UPGRADE_GATE_NEW_VERSION=${new_versions:-none}"
  echo "SDK_UPGRADE_GATE_REASON=dev_release_smoke PASS evidence marker present"
  exit 0
fi

echo "SDK_UPGRADE_GATE_STATUS=fail"
echo "SDK_UPGRADE_GATE_OLD_VERSION=${old_versions:-none}"
echo "SDK_UPGRADE_GATE_NEW_VERSION=${new_versions:-none}"
echo "SDK_UPGRADE_GATE_REASON=openai-agents pin changed without dev_release_smoke PASS evidence"
echo "SDK_UPGRADE_GATE_REQUIRED_MARKER=SDK-Smoke-Evidence: dev_release_smoke PASS <evidence-link-or-path>"
exit 1
