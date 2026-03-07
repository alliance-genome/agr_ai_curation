#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
preflight_script="${repo_root}/scripts/install/01_preflight.sh"

export NO_COLOR=1

assert_contains() {
  local needle="$1"
  local file_path="$2"
  if ! grep -q "$needle" "$file_path"; then
    echo "Expected to find '$needle' in output" >&2
    cat "$file_path" >&2
    exit 1
  fi
}

run_and_capture() {
  local output_file="$1"
  shift
  set +e
  "$@" >"$output_file" 2>&1
  local rc=$?
  set -e
  echo "$rc"
}

make_common_stubs() {
  local stub_dir="$1"

  cat >"${stub_dir}/docker" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "info" ]]; then
  exit 0
fi
if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then
  echo "Docker Compose version v2.28.1"
  exit 0
fi
echo "unexpected docker args: $*" >&2
exit 2
EOF

  cat >"${stub_dir}/git" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then
  echo "git version 2.48.1"
  exit 0
fi
echo "unexpected git args: $*" >&2
exit 2
EOF

  cat >"${stub_dir}/ss" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF

  chmod +x "${stub_dir}/docker" "${stub_dir}/git" "${stub_dir}/ss"
}

test_detects_missing_docker() {
  local stub_dir
  local output_file
  stub_dir="$(mktemp -d)"
  output_file="$(mktemp)"
  trap 'rm -rf "$stub_dir" "$output_file"' RETURN

  make_common_stubs "$stub_dir"
  cat >"${stub_dir}/lsof" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
  chmod +x "${stub_dir}/lsof"

  local rc
  rc="$(
    run_and_capture "$output_file" env \
      PATH="${stub_dir}:${PATH}" \
      PREFLIGHT_DOCKER_CMD="missing-docker" \
      PREFLIGHT_GIT_CMD="git" \
      PREFLIGHT_LSOF_CMD="lsof" \
      PREFLIGHT_MEMORY_BYTES_OVERRIDE="$((16 * 1024 * 1024 * 1024))" \
      PREFLIGHT_DISK_BYTES_OVERRIDE="$((20 * 1024 * 1024 * 1024))" \
      bash "$preflight_script"
  )"

  if [[ "$rc" -ne 10 ]]; then
    echo "Expected exit code 10 for missing Docker, got $rc" >&2
    cat "$output_file" >&2
    exit 1
  fi

  assert_contains "Docker CLI not found" "$output_file"
  assert_contains "PREFLIGHT_RESULT exit_code=10" "$output_file"
}

test_detects_port_conflict() {
  local stub_dir
  local output_file
  stub_dir="$(mktemp -d)"
  output_file="$(mktemp)"
  trap 'rm -rf "$stub_dir" "$output_file"' RETURN

  make_common_stubs "$stub_dir"
  cat >"${stub_dir}/lsof" <<'EOF'
#!/usr/bin/env bash
if [[ "$*" == *"-iTCP:8000"* ]]; then
  echo "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME"
  echo "python 4321 codex 11u IPv4 0t0 TCP *:8000 (LISTEN)"
  exit 0
fi
exit 1
EOF
  chmod +x "${stub_dir}/lsof"

  local rc
  rc="$(
    run_and_capture "$output_file" env \
      PATH="${stub_dir}:${PATH}" \
      PREFLIGHT_DOCKER_CMD="docker" \
      PREFLIGHT_GIT_CMD="git" \
      PREFLIGHT_LSOF_CMD="lsof" \
      PREFLIGHT_MEMORY_BYTES_OVERRIDE="$((16 * 1024 * 1024 * 1024))" \
      PREFLIGHT_DISK_BYTES_OVERRIDE="$((20 * 1024 * 1024 * 1024))" \
      bash "$preflight_script"
  )"

  if [[ "$rc" -ne 11 ]]; then
    echo "Expected exit code 11 for port conflict, got $rc" >&2
    cat "$output_file" >&2
    exit 1
  fi

  assert_contains "Port 8000 in use by python/4321" "$output_file"
  assert_contains "PREFLIGHT_RESULT exit_code=11" "$output_file"
}

test_passes_on_clean_system() {
  local stub_dir
  local output_file
  stub_dir="$(mktemp -d)"
  output_file="$(mktemp)"
  trap 'rm -rf "$stub_dir" "$output_file"' RETURN

  make_common_stubs "$stub_dir"
  cat >"${stub_dir}/lsof" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
  chmod +x "${stub_dir}/lsof"

  local rc
  rc="$(
    run_and_capture "$output_file" env \
      PATH="${stub_dir}:${PATH}" \
      PREFLIGHT_DOCKER_CMD="docker" \
      PREFLIGHT_GIT_CMD="git" \
      PREFLIGHT_LSOF_CMD="lsof" \
      PREFLIGHT_MEMORY_BYTES_OVERRIDE="$((16 * 1024 * 1024 * 1024))" \
      PREFLIGHT_DISK_BYTES_OVERRIDE="$((20 * 1024 * 1024 * 1024))" \
      bash "$preflight_script"
  )"

  if [[ "$rc" -ne 0 ]]; then
    echo "Expected clean preflight to pass, got $rc" >&2
    cat "$output_file" >&2
    exit 1
  fi

  assert_contains "Preflight passed with 0 warning" "$output_file"
  assert_contains "PREFLIGHT_RESULT exit_code=0" "$output_file"
}

test_warnings_do_not_block_installation() {
  local stub_dir
  local output_file
  stub_dir="$(mktemp -d)"
  output_file="$(mktemp)"
  trap 'rm -rf "$stub_dir" "$output_file"' RETURN

  make_common_stubs "$stub_dir"
  cat >"${stub_dir}/lsof" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
  chmod +x "${stub_dir}/lsof"

  local rc
  rc="$(
    run_and_capture "$output_file" env \
      PATH="${stub_dir}:${PATH}" \
      PREFLIGHT_DOCKER_CMD="docker" \
      PREFLIGHT_GIT_CMD="git" \
      PREFLIGHT_LSOF_CMD="lsof" \
      PREFLIGHT_MEMORY_BYTES_OVERRIDE="$((4 * 1024 * 1024 * 1024))" \
      PREFLIGHT_DISK_BYTES_OVERRIDE="$((5 * 1024 * 1024 * 1024))" \
      bash "$preflight_script"
  )"

  if [[ "$rc" -ne 0 ]]; then
    echo "Expected warnings to be non-blocking, got $rc" >&2
    cat "$output_file" >&2
    exit 1
  fi

  assert_contains "reranker model is memory-intensive" "$output_file"
  assert_contains "for Docker images" "$output_file"
  assert_contains "PREFLIGHT_RESULT exit_code=0" "$output_file"
}

test_detects_missing_docker
test_detects_port_conflict
test_passes_on_clean_system
test_warnings_do_not_block_installation

echo "preflight.sh checks passed"
