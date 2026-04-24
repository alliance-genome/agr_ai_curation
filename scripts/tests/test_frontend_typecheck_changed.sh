#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TYPECHECK_SCRIPT="${REPO_ROOT}/frontend/scripts/typecheck-changed.mjs"

source "${SCRIPT_DIR}/lib/assertions.sh"

assert_equals() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "Expected '${expected}', got '${actual}'" >&2
    exit 1
  fi
}

assert_text_contains() {
  local needle="$1"
  local haystack="$2"
  if [[ "${haystack}" != *"${needle}"* ]]; then
    echo "Expected output to contain '${needle}'" >&2
    echo "${haystack}" >&2
    exit 1
  fi
}

create_fixture_repo() {
  local fixture_dir="$1"
  mkdir -p "${fixture_dir}/frontend/src"
  cat > "${fixture_dir}/frontend/package.json" <<'JSON'
{"name":"ai-curation-frontend","scripts":{"type-check":"tsc --noEmit"}}
JSON
  cat > "${fixture_dir}/frontend/src/Changed.tsx" <<'EOF'
export const changed = 'before';
EOF
  cat > "${fixture_dir}/frontend/src/Unchanged.tsx" <<'EOF'
export const unchanged = 'before';
EOF
  git -C "${fixture_dir}" init -q
  git -C "${fixture_dir}" config user.email test@example.org
  git -C "${fixture_dir}" config user.name "Test User"
  git -C "${fixture_dir}" add frontend
  git -C "${fixture_dir}" commit -q -m "initial frontend"
  cat > "${fixture_dir}/frontend/src/Changed.tsx" <<'EOF'
export const changed = 'after';
EOF
}

run_typecheck_fixture() {
  local fixture_dir="$1"
  local output_file="$2"
  local command_output status
  set +e
  command_output="$(
    cd "${fixture_dir}/frontend" &&
      node "${TYPECHECK_SCRIPT}" \
        --base HEAD \
        --tsc-output-file "${output_file}" \
        --tsc-exit-code 2 2>&1
  )"
  status=$?
  set -e
  printf '%s\n%s\n' "${status}" "${command_output}"
}

test_baseline_errors_are_non_blocking() {
  local temp_dir tsc_output result status output
  temp_dir="$(mktemp -d)"
  create_fixture_repo "${temp_dir}"
  tsc_output="${temp_dir}/tsc.out"
  cat > "${tsc_output}" <<'EOF'
src/Unchanged.tsx(1,7): error TS2322: Type 'string' is not assignable to type 'number'.
EOF

  result="$(run_typecheck_fixture "${temp_dir}" "${tsc_output}")"
  status="$(sed -n '1p' <<< "${result}")"
  output="$(sed '1d' <<< "${result}")"

  assert_equals "0" "${status}"
  assert_text_contains "FRONTEND_TYPECHECK_STATUS=baseline_only" "${output}"
  assert_text_contains "FRONTEND_TYPECHECK_BASELINE_ERRORS=1" "${output}"
  rm -rf "${temp_dir}"
}

test_changed_file_errors_are_blocking() {
  local temp_dir tsc_output result status output
  temp_dir="$(mktemp -d)"
  create_fixture_repo "${temp_dir}"
  tsc_output="${temp_dir}/tsc.out"
  cat > "${tsc_output}" <<'EOF'
src/Changed.tsx(1,7): error TS2322: Type 'string' is not assignable to type 'number'.
EOF

  result="$(run_typecheck_fixture "${temp_dir}" "${tsc_output}")"
  status="$(sed -n '1p' <<< "${result}")"
  output="$(sed '1d' <<< "${result}")"

  assert_equals "2" "${status}"
  assert_text_contains "FRONTEND_TYPECHECK_STATUS=failed_changed_files" "${output}"
  assert_text_contains "src/Changed.tsx" "${output}"
  rm -rf "${temp_dir}"
}

test_unscoped_errors_are_blocking() {
  local temp_dir tsc_output result status output
  temp_dir="$(mktemp -d)"
  create_fixture_repo "${temp_dir}"
  tsc_output="${temp_dir}/tsc.out"
  cat > "${tsc_output}" <<'EOF'
error TS18003: No inputs were found in config file.
EOF

  result="$(run_typecheck_fixture "${temp_dir}" "${tsc_output}")"
  status="$(sed -n '1p' <<< "${result}")"
  output="$(sed '1d' <<< "${result}")"

  assert_equals "2" "${status}"
  assert_text_contains "FRONTEND_TYPECHECK_STATUS=failed_unscoped_errors" "${output}"
  rm -rf "${temp_dir}"
}

test_unparseable_failures_are_blocking() {
  local temp_dir tsc_output result status output
  temp_dir="$(mktemp -d)"
  create_fixture_repo "${temp_dir}"
  tsc_output="${temp_dir}/tsc.out"
  cat > "${tsc_output}" <<'EOF'
tsc crashed before reporting structured errors
EOF

  result="$(run_typecheck_fixture "${temp_dir}" "${tsc_output}")"
  status="$(sed -n '1p' <<< "${result}")"
  output="$(sed '1d' <<< "${result}")"

  assert_equals "2" "${status}"
  assert_text_contains "FRONTEND_TYPECHECK_STATUS=failed_unscoped_errors" "${output}"
  assert_text_contains "without parseable error locations" "${output}"
  rm -rf "${temp_dir}"
}

test_baseline_errors_are_non_blocking
test_changed_file_errors_are_blocking
test_unscoped_errors_are_blocking
test_unparseable_failures_are_blocking

echo "frontend type-check changed tests passed"
