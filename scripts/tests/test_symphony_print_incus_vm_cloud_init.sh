#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_print_incus_vm_cloud_init.sh"

assert_contains() {
  local pattern="$1"
  local output="$2"
  if ! printf '%s\n' "${output}" | rg -n --fixed-strings "${pattern}" >/dev/null 2>&1; then
    echo "Expected to find '${pattern}' in output:" >&2
    printf '%s\n' "${output}" >&2
    exit 1
  fi
}

test_cloud_init_contains_user_and_pinned_scanners() {
  local temp_home output key_file
  temp_home="$(mktemp -d)"
  mkdir -p "${temp_home}/.ssh"
  key_file="${temp_home}/.ssh/id_ed25519.pub"

  cat > "${key_file}" <<'EOF'
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyForCloudInitOnly user@example.test
EOF

  output="$(
    HOME="${temp_home}" \
      bash "${SCRIPT_PATH}" --user symphony --gecos "Symphony User"
  )"

  assert_contains "#cloud-config" "${output}"
  assert_contains "name: symphony" "${output}"
  assert_contains "gecos: Symphony User" "${output}"
  assert_contains "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyForCloudInitOnly user@example.test" "${output}"
  assert_contains "path: /usr/local/sbin/symphony-install-git-safety-tools.sh" "${output}"
  assert_contains "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/" "${output}"
  assert_contains "https://github.com/trufflesecurity/trufflehog/releases/download/v3.94.3/" "${output}"
  assert_contains "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb" "${output}"
  assert_contains "7cc45010bfac7258a23731bc3ab4371abdbf20ffc705075066971e5aa8ebda7f" "${output}"
}

test_cloud_init_contains_user_and_pinned_scanners

echo "symphony_print_incus_vm_cloud_init tests passed"
