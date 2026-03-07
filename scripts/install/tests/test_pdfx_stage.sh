#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
core_config_script="${repo_root}/scripts/install/02_core_config.sh"
pdfx_setup_script="${repo_root}/scripts/install/05_pdfx_setup.sh"

export NO_COLOR=1

assert_contains() {
  local needle="$1"
  local file_path="$2"
  if ! grep -q "$needle" "$file_path"; then
    echo "Expected to find '$needle' in $file_path" >&2
    cat "$file_path" >&2
    exit 1
  fi
}

assert_not_contains() {
  local needle="$1"
  local file_path="$2"
  if grep -q "$needle" "$file_path"; then
    echo "Did not expect to find '$needle' in $file_path" >&2
    cat "$file_path" >&2
    exit 1
  fi
}

run_core_config() {
  local home_dir="$1"
  local input_text="$2"
  HOME="$home_dir" bash "$core_config_script" <<<"$input_text"
}

make_stub_tools() {
  local stub_dir="$1"
  local lsof_mode="${2:-free}"

  cat >"${stub_dir}/git" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "clone" ]]; then
  target_path="${3:-}"
  if [[ -z "$target_path" ]]; then
    echo "missing clone target" >&2
    exit 2
  fi
  mkdir -p "$target_path"
  printf 'stub clone\n' >"${target_path}/.git-cloned"
  exit 0
fi
if [[ "${1:-}" == "--version" ]]; then
  echo "git version 2.48.1"
  exit 0
fi
echo "unexpected git args: $*" >&2
exit 2
EOF

  if [[ "$lsof_mode" == "conflict-8501" ]]; then
    cat >"${stub_dir}/lsof" <<'EOF'
#!/usr/bin/env bash
if [[ "$*" == *"-iTCP:8501"* ]]; then
  echo "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME"
  echo "python 4321 codex 11u IPv4 0t0 TCP *:8501 (LISTEN)"
  exit 0
fi
exit 1
EOF
  else
    cat >"${stub_dir}/lsof" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
  fi

  chmod +x "${stub_dir}/git" "${stub_dir}/lsof"
}

run_pdfx_setup() {
  local home_dir="$1"
  local stub_dir="$2"
  local input_text="$3"
  local output_path="$4"
  local rc=0

  set +e
  HOME="$home_dir" \
  PATH="${stub_dir}:${PATH}" \
  INSTALL_GIT_CMD="git" \
  INSTALL_LSOF_CMD="lsof" \
  INSTALL_PDFX_REPO_URL="https://example.invalid/agr_pdf_extraction_service.git" \
  bash "$pdfx_setup_script" <<<"$input_text" >"$output_path" 2>&1
  rc=$?
  set -e

  if [[ "$rc" -ne 0 ]]; then
    echo "Expected PDFX setup to succeed, got rc=$rc" >&2
    cat "$output_path" >&2
    exit 1
  fi
}

test_pdfx_setup_clones_and_generates_env() {
  local temp_home
  local stub_dir
  local output_path
  temp_home="$(mktemp -d)"
  stub_dir="$(mktemp -d)"
  output_path="$(mktemp)"
  trap 'rm -rf "$temp_home" "$stub_dir" "$output_path"' RETURN

  run_core_config "$temp_home" $'sk-openai-test\n\n\n\n'
  make_stub_tools "$stub_dir" "free"

  local clone_path="${temp_home}/pdfx-service"
  run_pdfx_setup "$temp_home" "$stub_dir" $'y\n'"${clone_path}"$'\n3\ny\ny\n' "$output_path"

  local pdfx_env="${clone_path}/.env"
  local main_env="${temp_home}/.agr_ai_curation/.env"

  [[ -f "${clone_path}/.git-cloned" ]] || {
    echo "Expected stub clone artifact at ${clone_path}/.git-cloned" >&2
    cat "$output_path" >&2
    exit 1
  }
  [[ -f "$pdfx_env" ]] || {
    echo "Expected PDFX env file at ${pdfx_env}" >&2
    cat "$output_path" >&2
    exit 1
  }
  [[ "$(stat -c '%a' "$pdfx_env")" == "600" ]] || {
    echo "Expected PDFX env permissions 600, got $(stat -c '%a' "$pdfx_env")" >&2
    cat "$output_path" >&2
    exit 1
  }

  assert_contains '^OPENAI_API_KEY=sk-openai-test$' "$pdfx_env"
  assert_contains '^DOCLING_DEVICE=cuda$' "$pdfx_env"
  assert_contains '^MARKER_DEVICE=auto$' "$pdfx_env"
  assert_contains '^CONSENSUS_ENABLED=true$' "$pdfx_env"
  assert_contains '^PDFX_SELECTED_METHODS=grobid,marker$' "$pdfx_env"
  assert_contains '^PDFX_DEFAULT_MERGE=true$' "$pdfx_env"
  assert_contains '^PDFX_GPU_ENABLED=true$' "$pdfx_env"

  assert_contains '^PDF_EXTRACTION_SERVICE_URL=http://localhost:8501$' "$main_env"
  assert_contains '^PDF_EXTRACTION_METHODS=grobid,marker$' "$main_env"
  assert_contains '^PDF_EXTRACTION_MERGE=true$' "$main_env"
}

test_pdfx_setup_handles_port_conflict() {
  local temp_home
  local stub_dir
  local output_path
  temp_home="$(mktemp -d)"
  stub_dir="$(mktemp -d)"
  output_path="$(mktemp)"
  trap 'rm -rf "$temp_home" "$stub_dir" "$output_path"' RETURN

  run_core_config "$temp_home" $'sk-openai-test\n\n\n\n'
  make_stub_tools "$stub_dir" "conflict-8501"

  local clone_path="${temp_home}/pdfx-port-override"
  run_pdfx_setup "$temp_home" "$stub_dir" $'y\n8511\n'"${clone_path}"$'\n1\nn\n' "$output_path"

  local main_env="${temp_home}/.agr_ai_curation/.env"

  assert_contains '^PDF_EXTRACTION_SERVICE_URL=http://localhost:8511$' "$main_env"
  assert_contains '^PDF_EXTRACTION_METHODS=grobid$' "$main_env"
  assert_contains '^PDF_EXTRACTION_MERGE=false$' "$main_env"
}

test_pdfx_setup_skip_removes_main_env_vars() {
  local temp_home
  local stub_dir
  local output_path
  temp_home="$(mktemp -d)"
  stub_dir="$(mktemp -d)"
  output_path="$(mktemp)"
  trap 'rm -rf "$temp_home" "$stub_dir" "$output_path"' RETURN

  run_core_config "$temp_home" $'sk-openai-test\n\n\n\n'
  make_stub_tools "$stub_dir" "free"

  local clone_path="${temp_home}/pdfx-skip-reset"
  run_pdfx_setup "$temp_home" "$stub_dir" $'y\n'"${clone_path}"$'\n2\nn\n' "$output_path"
  run_pdfx_setup "$temp_home" "$stub_dir" $'n\n' "$output_path"

  local main_env="${temp_home}/.agr_ai_curation/.env"
  assert_not_contains '^PDF_EXTRACTION_SERVICE_URL=' "$main_env"
  assert_not_contains '^PDF_EXTRACTION_METHODS=' "$main_env"
  assert_not_contains '^PDF_EXTRACTION_MERGE=' "$main_env"
}

test_pdfx_setup_clones_and_generates_env
test_pdfx_setup_handles_port_conflict
test_pdfx_setup_skip_removes_main_env_vars

echo "pdfx installer stage checks passed"
