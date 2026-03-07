#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=scripts/install/lib/common.sh
source "${repo_root}/scripts/install/lib/common.sh"

export NO_COLOR=1

require_non_empty "TEST_KEY" "value"
if require_non_empty "TEST_KEY" ""; then
  echo "require_non_empty should fail for empty value" >&2
  exit 1
fi

require_file_exists "${repo_root}/scripts/install/lib/common.sh"
if require_file_exists "${repo_root}/scripts/install/lib/missing.sh"; then
  echo "require_file_exists should fail for missing file" >&2
  exit 1
fi

require_command "bash"
if require_command "definitely-not-a-real-command"; then
  echo "require_command should fail for missing command" >&2
  exit 1
fi

valid_hex="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
require_hex_64 "ENCRYPTION_KEY" "$valid_hex"
if require_hex_64 "ENCRYPTION_KEY" "1234"; then
  echo "require_hex_64 should fail for invalid hex" >&2
  exit 1
fi

if ! prompt_yes_no "Continue" "yes" <<< "y"; then
  echo "prompt_yes_no should accept yes input" >&2
  exit 1
fi

if prompt_yes_no "Continue" "yes" <<< "n"; then
  echo "prompt_yes_no should reject no input" >&2
  exit 1
fi

if ! prompt_yes_no "Continue" "yes" <<< ""; then
  echo "prompt_yes_no should use yes default" >&2
  exit 1
fi

prompt_output="$(prompt_required_value "Enter value" <<< "provided")"
if [[ "$prompt_output" != "provided" ]]; then
  echo "prompt_required_value did not return expected value" >&2
  exit 1
fi

warning_output_file="$(mktemp)"
prompt_output="$(
  prompt_required_value "Enter value" <<< $'\nprovided' 2>"$warning_output_file"
)"
if [[ "$prompt_output" != "provided" ]]; then
  echo "prompt_required_value should keep stdout clean when warning first" >&2
  rm -f "$warning_output_file"
  exit 1
fi
if ! grep -q "Value is required." "$warning_output_file"; then
  echo "prompt_required_value should emit required-value warning to stderr" >&2
  rm -f "$warning_output_file"
  exit 1
fi
rm -f "$warning_output_file"

env_file="$(mktemp)"
cat >"$env_file" <<'EOF'
ONE=1
TWO=2
EOF
remove_env_var "$env_file" "TWO"
if grep -q '^TWO=' "$env_file"; then
  echo "remove_env_var should remove matching key" >&2
  rm -f "$env_file"
  exit 1
fi
remove_env_var "$env_file" "MISSING"
if ! grep -q '^ONE=1$' "$env_file"; then
  echo "remove_env_var should leave file unchanged for missing key" >&2
  rm -f "$env_file"
  exit 1
fi
rm -f "$env_file"

port_stub_dir="$(mktemp -d)"
cat >"${port_stub_dir}/lsof" <<'EOF'
#!/usr/bin/env bash
if [[ "$*" == *"-iTCP:8501"* ]]; then
  echo "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME"
  echo "python 1234 codex 11u IPv4 0t0 TCP *:8501 (LISTEN)"
  exit 0
fi
exit 1
EOF
chmod +x "${port_stub_dir}/lsof"

owner="$(INSTALL_LSOF_CMD="lsof" PATH="${port_stub_dir}:${PATH}" find_listening_port_owner "8501" || true)"
if [[ "$owner" != "python/1234" ]]; then
  echo "find_listening_port_owner should return detected process owner" >&2
  rm -rf "$port_stub_dir"
  exit 1
fi

missing_owner="$(INSTALL_LSOF_CMD="lsof" PATH="${port_stub_dir}:${PATH}" find_listening_port_owner "8511" || true)"
if [[ -n "$missing_owner" ]]; then
  echo "find_listening_port_owner should return empty output for free port" >&2
  rm -rf "$port_stub_dir"
  exit 1
fi

INSTALL_LSOF_CMD="lsof" PATH="${port_stub_dir}:${PATH}" has_port_probe_command || {
  echo "has_port_probe_command should detect available probe command" >&2
  rm -rf "$port_stub_dir"
  exit 1
}
rm -rf "$port_stub_dir"

echo "common.sh checks passed"
