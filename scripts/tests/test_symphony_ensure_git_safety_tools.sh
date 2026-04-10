#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_ensure_git_safety_tools.sh"

assert_contains() {
  local pattern="$1"
  local output="$2"
  if ! printf '%s\n' "${output}" | rg -n --fixed-strings "${pattern}" >/dev/null 2>&1; then
    echo "Expected to find '${pattern}' in output:" >&2
    printf '%s\n' "${output}" >&2
    exit 1
  fi
}

test_check_mode_succeeds_when_tools_are_present() {
  local temp_dir output
  temp_dir="$(mktemp -d)"
  mkdir -p "${temp_dir}/bin"

  cat > "${temp_dir}/bin/gitleaks" <<'EOF'
#!/usr/bin/env bash
echo "8.99.0"
EOF

  cat > "${temp_dir}/bin/trufflehog" <<'EOF'
#!/usr/bin/env bash
echo "3.88.0"
EOF

  chmod +x "${temp_dir}/bin/gitleaks" "${temp_dir}/bin/trufflehog"

  output="$(
    PATH="${temp_dir}/bin:${PATH}" \
      /bin/bash "${SCRIPT_PATH}" --check
  )"

  assert_contains "[ok] gitleaks: 8.99.0" "${output}"
  assert_contains "[ok] trufflehog: 3.88.0" "${output}"
}

test_check_mode_fails_when_tools_are_missing() {
  local temp_dir output
  temp_dir="$(mktemp -d)"
  mkdir -p "${temp_dir}/empty"

  if output="$(
    PATH="${temp_dir}/empty" \
      /bin/bash "${SCRIPT_PATH}" --check 2>&1
  )"; then
    echo "Expected check mode to fail when tools are missing" >&2
    exit 1
  fi

  assert_contains "[missing] gitleaks" "${output}"
  assert_contains "[missing] trufflehog" "${output}"
}

test_check_mode_succeeds_when_tools_are_present
test_check_mode_fails_when_tools_are_missing

echo "symphony_ensure_git_safety_tools tests passed"
