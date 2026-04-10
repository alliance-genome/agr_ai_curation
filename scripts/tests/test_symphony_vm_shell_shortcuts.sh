#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
INSTALLER="${REPO_ROOT}/scripts/utilities/symphony_install_vm_shell_shortcuts.sh"
SHORTCUTS="${REPO_ROOT}/scripts/utilities/symphony_vm_shell_shortcuts.sh"

assert_contains() {
  local pattern="$1"
  local haystack="$2"

  if ! printf '%s\n' "${haystack}" | rg -n --fixed-strings "${pattern}" >/dev/null 2>&1; then
    echo "Expected to find '${pattern}' in output:" >&2
    printf '%s\n' "${haystack}" >&2
    exit 1
  fi
}

test_installer_replaces_existing_block() {
  local temp_home aliases_file output
  temp_home="$(mktemp -d)"
  aliases_file="${temp_home}/.bash_aliases"

  cat > "${aliases_file}" <<'EOF'
alias ll='ls -alF'
# >>> symphony codex shortcuts >>>
alias codex-high='codex -m gpt-5.4 -c model_reasoning_effort="high" --yolo'
alias co='codex-xhigh'
# <<< symphony codex shortcuts <<<
EOF

  output="$(HOME="${temp_home}" bash "${INSTALLER}" --repo-root "${REPO_ROOT}")"

  assert_contains "Installed Symphony Codex shortcuts" "${output}"
  assert_contains "alias ll='ls -alF'" "$(cat "${aliases_file}")"
  assert_contains "source \"${REPO_ROOT}/scripts/utilities/symphony_vm_shell_shortcuts.sh\"" "$(cat "${aliases_file}")"

  if rg -n --fixed-strings "alias co='codex-xhigh'" "${aliases_file}" >/dev/null 2>&1; then
    echo "Expected old inline co alias to be replaced by the managed source block" >&2
    exit 1
  fi
}

test_shortcuts_use_repo_launcher_and_overrides() {
  local temp_home temp_repo temp_workdir temp_sandbox output
  temp_home="$(mktemp -d)"
  temp_repo="$(mktemp -d)"
  temp_workdir="${temp_repo}/workdir"
  temp_sandbox="${temp_repo}/main-sandbox"

  mkdir -p "${temp_repo}/scripts/utilities" "${temp_repo}/.symphony" "${temp_workdir}" "${temp_sandbox}" "${temp_home}/.agr_ai_curation/shell"

  cat > "${temp_repo}/.symphony/github_pat_env.sh" <<'EOF'
#!/usr/bin/env bash
symphony_load_github_pat_env() {
  export GH_TOKEN="test-gh-token"
  export GITHUB_TOKEN="${GH_TOKEN}"
}
EOF

  cat > "${temp_repo}/scripts/utilities/codex_with_repo_pat.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
# shellcheck disable=SC1090
source "${REPO_ROOT}/.symphony/github_pat_env.sh"
symphony_load_github_pat_env --require
printf 'GH_TOKEN=%s\n' "${GH_TOKEN:-}"
printf 'ARGS=%s\n' "$*"
EOF
  chmod +x "${temp_repo}/scripts/utilities/codex_with_repo_pat.sh"

  cat > "${temp_home}/.agr_ai_curation/shell/codex_shortcuts.env" <<'EOF'
SYMPHONY_CODEX_DEFAULT_MODEL=gpt-test
SYMPHONY_CODEX_USE_NO_ALT_SCREEN=1
EOF

  output="$(
    HOME="${temp_home}" \
    AGR_AI_CURATION_REPO_ROOT="${temp_repo}" \
    SYMPHONY_CODEX_MAIN_SANDBOX_DIR="${temp_sandbox}" \
    bash -lc '
      source "'"${SHORTCUTS}"'"
      cd "'"${temp_workdir}"'"
      co inspect-me
      comain check-main
      cor resume-me
    '
  )"

  assert_contains "GH_TOKEN=test-gh-token" "${output}"
  assert_contains "ARGS=-C ${temp_workdir} -m gpt-test -c model_reasoning_effort=\"xhigh\" --yolo --no-alt-screen inspect-me" "${output}"
  assert_contains "ARGS=-C ${temp_sandbox} -m gpt-test -c model_reasoning_effort=\"xhigh\" --yolo --no-alt-screen check-main" "${output}"
  assert_contains "ARGS=resume resume-me" "${output}"
}

test_installer_replaces_existing_block
test_shortcuts_use_repo_launcher_and_overrides

echo "symphony_vm_shell_shortcuts tests passed"
