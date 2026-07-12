#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_backend_test.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "FAIL: Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

write_compose_stub() {
  local path="$1"
  cat > "${path}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
log="${TEST_COMPOSE_LOG:?}"
mode="${TEST_COMPOSE_MODE:-pass}"
original_args="$*"
if [[ "${1:-}" == "--rootful" || "${1:-}" == "--rootless" ]]; then
  shift
fi

case "${mode}" in
  pass)
    printf 'pwd=%s\nargs=%s\n' "${PWD}" "$*" >> "${log}"
    ;;
  serialize)
    printf 'start:%s\n' "${TEST_LABEL:?}" >> "${log}"
    sleep "${TEST_DELAY_SECONDS:-0.2}"
    printf 'end:%s\n' "${TEST_LABEL}" >> "${log}"
    ;;
  collision)
    state="${TEST_COMPOSE_STATE:?}"
    if [[ "${1:-}" == "down" ]]; then
      printf 'down:%s\n' "${original_args}" >> "${log}"
      exit "${TEST_CLEANUP_EXIT_STATUS:-0}"
    fi
    if [[ ! -e "${state}" ]]; then
      touch "${state}"
      printf 'run-fail:%s\n' "${original_args}" >> "${log}"
      echo 'Error response from daemon: Conflict. The container name "/all-555-redis-test-1" is already in use.'
      exit 17
    fi
    printf 'run-success:%s\n' "${original_args}" >> "${log}"
    ;;
  persistent_collision)
    if [[ "${1:-}" == "down" ]]; then
      printf 'down:%s\n' "${original_args}" >> "${log}"
      exit 0
    fi
    printf 'run-fail:%s\n' "${original_args}" >> "${log}"
    echo 'failed to create network all-555_test-network: network already exists'
    exit 17
    ;;
  unrelated_failure)
    printf 'unrelated-fail:%s\n' "$*" >> "${log}"
    echo 'pytest collection failed'
    exit 23
    ;;
  *)
    echo "unknown test mode: ${mode}" >&2
    exit 99
    ;;
esac
EOF
  chmod +x "${path}"
}

test_passes_compose_args_from_workspace() {
  local temp_dir workspace helper log output
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  helper="${temp_dir}/compose-helper"
  log="${temp_dir}/compose.log"
  mkdir -p "${workspace}"
  write_compose_stub "${helper}"

  output="$(
    TEST_COMPOSE_MODE=pass \
    TEST_COMPOSE_LOG="${log}" \
    SYMPHONY_BACKEND_TEST_COMPOSE_HELPER="${helper}" \
    SYMPHONY_BACKEND_TEST_LOCK_ROOT="${temp_dir}/locks" \
      bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" -- run --rm backend-unit-tests 2>&1
  )"

  assert_contains "SYMPHONY_BACKEND_TEST_STATUS=ok" "${output}"
  assert_contains "pwd=${workspace}" "$(cat "${log}")"
  assert_contains "args=run --rm backend-unit-tests" "$(cat "${log}")"
  rm -rf "${temp_dir}"
}

test_serializes_commands_for_the_same_workspace() {
  local temp_dir workspace helper log first_output second_output
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  helper="${temp_dir}/compose-helper"
  log="${temp_dir}/compose.log"
  first_output="${temp_dir}/first.out"
  second_output="${temp_dir}/second.out"
  mkdir -p "${workspace}"
  write_compose_stub "${helper}"

  TEST_COMPOSE_MODE=serialize TEST_LABEL=first TEST_DELAY_SECONDS=0.3 \
  TEST_COMPOSE_LOG="${log}" SYMPHONY_BACKEND_TEST_COMPOSE_HELPER="${helper}" \
  SYMPHONY_BACKEND_TEST_LOCK_ROOT="${temp_dir}/locks" \
    bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" -- run --rm backend-unit-tests > "${first_output}" 2>&1 &
  first_pid=$!

  for _ in $(seq 1 100); do
    [[ -f "${log}" ]] && rg -q '^start:first$' "${log}" && break
    sleep 0.01
  done

  TEST_COMPOSE_MODE=serialize TEST_LABEL=second TEST_DELAY_SECONDS=0 \
  TEST_COMPOSE_LOG="${log}" SYMPHONY_BACKEND_TEST_COMPOSE_HELPER="${helper}" \
  SYMPHONY_BACKEND_TEST_LOCK_ROOT="${temp_dir}/locks" \
    bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" -- run --rm backend-contract-tests > "${second_output}" 2>&1 &
  second_pid=$!

  wait "${first_pid}"
  wait "${second_pid}"

  expected=$'start:first\nend:first\nstart:second\nend:second'
  actual="$(cat "${log}")"
  [[ "${actual}" == "${expected}" ]] || {
    printf 'FAIL: Commands were not serialized.\nExpected:\n%s\nActual:\n%s\n' "${expected}" "${actual}" >&2
    exit 1
  }
  rm -rf "${temp_dir}"
}

test_lock_timeout_is_explicit() {
  local temp_dir workspace helper log holder_output timeout_output rc
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  helper="${temp_dir}/compose-helper"
  log="${temp_dir}/compose.log"
  holder_output="${temp_dir}/holder.out"
  timeout_output="${temp_dir}/timeout.out"
  mkdir -p "${workspace}"
  write_compose_stub "${helper}"

  TEST_COMPOSE_MODE=serialize TEST_LABEL=holder TEST_DELAY_SECONDS=0.5 \
  TEST_COMPOSE_LOG="${log}" SYMPHONY_BACKEND_TEST_COMPOSE_HELPER="${helper}" \
  SYMPHONY_BACKEND_TEST_LOCK_ROOT="${temp_dir}/locks" \
    bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" -- run --rm backend-unit-tests > "${holder_output}" 2>&1 &
  holder_pid=$!

  for _ in $(seq 1 100); do
    [[ -f "${log}" ]] && rg -q '^start:holder$' "${log}" && break
    sleep 0.01
  done

  set +e
  TEST_COMPOSE_MODE=serialize TEST_LABEL=timeout TEST_DELAY_SECONDS=0 \
  TEST_COMPOSE_LOG="${log}" SYMPHONY_BACKEND_TEST_COMPOSE_HELPER="${helper}" \
  SYMPHONY_BACKEND_TEST_LOCK_ROOT="${temp_dir}/locks" \
    bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" --lock-timeout-seconds 0 -- run --rm backend-contract-tests > "${timeout_output}" 2>&1
  rc=$?
  set -e

  [[ "${rc}" == "75" ]] || {
    echo "FAIL: Expected lock timeout exit 75, got ${rc}" >&2
    exit 1
  }
  assert_contains "SYMPHONY_BACKEND_TEST_STATUS=lock_timeout" "$(cat "${timeout_output}")"
  wait "${holder_pid}"
  rm -rf "${temp_dir}"
}

test_collision_cleanup_is_opt_in_and_bounded() {
  local temp_dir workspace helper log state output rc
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  helper="${temp_dir}/compose-helper"
  log="${temp_dir}/compose.log"
  state="${temp_dir}/state"
  mkdir -p "${workspace}"
  write_compose_stub "${helper}"

  set +e
  output="$(
    TEST_COMPOSE_MODE=collision TEST_COMPOSE_LOG="${log}" TEST_COMPOSE_STATE="${state}" \
    SYMPHONY_BACKEND_TEST_COMPOSE_HELPER="${helper}" SYMPHONY_BACKEND_TEST_LOCK_ROOT="${temp_dir}/locks" \
      bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" -- run --rm backend-unit-tests 2>&1
  )"
  rc=$?
  set -e
  [[ "${rc}" == "17" ]] || {
    echo "FAIL: Expected original collision exit 17, got ${rc}" >&2
    exit 1
  }
  assert_contains "SYMPHONY_BACKEND_TEST_COLLISION_RECOGNIZED=true" "${output}"
  assert_contains "SYMPHONY_BACKEND_TEST_REPAIR_HINT=" "${output}"
  if rg -q '^down:' "${log}"; then
    echo "FAIL: Collision cleanup ran without explicit opt-in." >&2
    exit 1
  fi

  rm -f "${state}"
  : > "${log}"
  output="$(
    TEST_COMPOSE_MODE=collision TEST_COMPOSE_LOG="${log}" TEST_COMPOSE_STATE="${state}" \
    SYMPHONY_BACKEND_TEST_COMPOSE_HELPER="${helper}" SYMPHONY_BACKEND_TEST_LOCK_ROOT="${temp_dir}/locks" \
    SYMPHONY_BACKEND_TEST_COLLISION_RETRY_COUNT=1 \
      bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" --repair-known-collision -- run --rm backend-unit-tests 2>&1
  )"
  assert_contains "SYMPHONY_BACKEND_TEST_STATUS=repairing_known_collision" "${output}"
  assert_contains "SYMPHONY_BACKEND_TEST_RETRY=1/1" "${output}"
  assert_contains "SYMPHONY_BACKEND_TEST_STATUS=ok" "${output}"
  expected=$'run-fail:run --rm backend-unit-tests\ndown:down --remove-orphans\nrun-success:run --rm backend-unit-tests'
  [[ "$(cat "${log}")" == "${expected}" ]] || {
    echo "FAIL: Repair sequence was not fail/down/retry." >&2
    cat "${log}" >&2
    exit 1
  }
  rm -rf "${temp_dir}"
}

test_unrelated_failure_never_cleans_up() {
  local temp_dir workspace helper log output rc
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  helper="${temp_dir}/compose-helper"
  log="${temp_dir}/compose.log"
  mkdir -p "${workspace}"
  write_compose_stub "${helper}"

  set +e
  output="$(
    TEST_COMPOSE_MODE=unrelated_failure TEST_COMPOSE_LOG="${log}" \
    SYMPHONY_BACKEND_TEST_COMPOSE_HELPER="${helper}" SYMPHONY_BACKEND_TEST_LOCK_ROOT="${temp_dir}/locks" \
      bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" --repair-known-collision -- run --rm backend-unit-tests 2>&1
  )"
  rc=$?
  set -e

  [[ "${rc}" == "23" ]] || {
    echo "FAIL: Expected unrelated failure exit 23, got ${rc}" >&2
    exit 1
  }
  assert_contains "SYMPHONY_BACKEND_TEST_COLLISION_RECOGNIZED=false" "${output}"
  if rg -q '^down:' "${log}"; then
    echo "FAIL: Unrelated failure triggered cleanup." >&2
    exit 1
  fi
  rm -rf "${temp_dir}"
}

test_persistent_collision_uses_only_configured_retry() {
  local temp_dir workspace helper log output rc
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  helper="${temp_dir}/compose-helper"
  log="${temp_dir}/compose.log"
  mkdir -p "${workspace}"
  write_compose_stub "${helper}"

  set +e
  output="$(
    TEST_COMPOSE_MODE=persistent_collision TEST_COMPOSE_LOG="${log}" SYMPHONY_BACKEND_TEST_COMPOSE_HELPER="${helper}" SYMPHONY_BACKEND_TEST_LOCK_ROOT="${temp_dir}/locks" SYMPHONY_BACKEND_TEST_COLLISION_RETRY_COUNT=1 bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" --repair-known-collision -- run --rm backend-unit-tests 2>&1
  )"
  rc=$?
  set -e

  [[ "${rc}" == "17" ]] || {
    echo "FAIL: Expected persistent collision exit 17, got ${rc}" >&2
    exit 1
  }
  assert_contains "SYMPHONY_BACKEND_TEST_RETRY=1/1" "${output}"
  [[ "$(rg -c '^run-fail:' "${log}")" == "2" ]] || {
    echo "FAIL: Persistent collision did not stop after one retry." >&2
    cat "${log}" >&2
    exit 1
  }
  [[ "$(rg -c '^down:' "${log}")" == "1" ]] || {
    echo "FAIL: Persistent collision cleanup was not bounded to one attempt." >&2
    cat "${log}" >&2
    exit 1
  }
  rm -rf "${temp_dir}"
}

test_repair_preserves_rootful_selector() {
  local temp_dir workspace helper log state output
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  helper="${temp_dir}/compose-helper"
  log="${temp_dir}/compose.log"
  state="${temp_dir}/state"
  mkdir -p "${workspace}"
  write_compose_stub "${helper}"

  output="$(
    TEST_COMPOSE_MODE=collision TEST_COMPOSE_LOG="${log}" TEST_COMPOSE_STATE="${state}" \
    SYMPHONY_BACKEND_TEST_COMPOSE_HELPER="${helper}" SYMPHONY_BACKEND_TEST_LOCK_ROOT="${temp_dir}/locks" \
      bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" --repair-known-collision -- --rootful run --rm backend-unit-tests 2>&1
  )"

  assert_contains "SYMPHONY_BACKEND_TEST_STATUS=ok" "${output}"
  assert_contains "run-fail:--rootful run --rm backend-unit-tests" "$(cat "${log}")"
  assert_contains "down:--rootful down --remove-orphans" "$(cat "${log}")"
  assert_contains "run-success:--rootful run --rm backend-unit-tests" "$(cat "${log}")"
  rm -rf "${temp_dir}"
}

test_rejects_custom_project_selector_before_compose() {
  local temp_dir workspace helper log output rc
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  helper="${temp_dir}/compose-helper"
  log="${temp_dir}/compose.log"
  mkdir -p "${workspace}"
  write_compose_stub "${helper}"

  set +e
  output="$(
    TEST_COMPOSE_MODE=pass TEST_COMPOSE_LOG="${log}" \
    SYMPHONY_BACKEND_TEST_COMPOSE_HELPER="${helper}" SYMPHONY_BACKEND_TEST_LOCK_ROOT="${temp_dir}/locks" \
      bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" -- -p shared-tests run --rm backend-unit-tests 2>&1
  )"
  rc=$?
  set -e

  [[ "${rc}" == "2" ]] || {
    echo "FAIL: Expected custom project selector to exit 2, got ${rc}" >&2
    exit 1
  }
  assert_contains "Unsupported Compose context selector" "${output}"
  [[ ! -e "${log}" ]] || {
    echo "FAIL: Compose ran despite rejected custom project selector." >&2
    exit 1
  }

  for attached_selector in "-pshared-tests" "-fdocker-compose.override.yml"; do
    set +e
    output="$(
      TEST_COMPOSE_MODE=pass TEST_COMPOSE_LOG="${log}" SYMPHONY_BACKEND_TEST_COMPOSE_HELPER="${helper}" SYMPHONY_BACKEND_TEST_LOCK_ROOT="${temp_dir}/locks" bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" -- "${attached_selector}" run --rm backend-unit-tests 2>&1
    )"
    rc=$?
    set -e
    [[ "${rc}" == "2" ]] || {
      echo "FAIL: Expected ${attached_selector} to exit 2, got ${rc}" >&2
      exit 1
    }
    assert_contains "Unsupported attached Compose context selector" "${output}"
    [[ ! -e "${log}" ]] || {
      echo "FAIL: Compose ran despite rejected selector ${attached_selector}." >&2
      exit 1
    }
  done
  rm -rf "${temp_dir}"
}

test_same_default_project_serializes_across_workspace_roots() {
  local temp_dir workspace_a workspace_b helper log output_a output_b
  temp_dir="$(mktemp -d)"
  workspace_a="${temp_dir}/a/same-name"
  workspace_b="${temp_dir}/b/same-name"
  helper="${temp_dir}/compose-helper"
  log="${temp_dir}/compose.log"
  output_a="${temp_dir}/a.out"
  output_b="${temp_dir}/b.out"
  mkdir -p "${workspace_a}" "${workspace_b}"
  write_compose_stub "${helper}"

  TEST_COMPOSE_MODE=serialize TEST_LABEL=first-project TEST_DELAY_SECONDS=0.3 \
  TEST_COMPOSE_LOG="${log}" SYMPHONY_BACKEND_TEST_COMPOSE_HELPER="${helper}" \
  SYMPHONY_BACKEND_TEST_LOCK_ROOT="${temp_dir}/locks" \
    bash "${SCRIPT_PATH}" --workspace-dir "${workspace_a}" -- run --rm backend-unit-tests > "${output_a}" 2>&1 &
  first_pid=$!

  for _ in $(seq 1 100); do
    [[ -f "${log}" ]] && rg -q '^start:first-project$' "${log}" && break
    sleep 0.01
  done

  TEST_COMPOSE_MODE=serialize TEST_LABEL=second-project TEST_DELAY_SECONDS=0 \
  TEST_COMPOSE_LOG="${log}" SYMPHONY_BACKEND_TEST_COMPOSE_HELPER="${helper}" \
  SYMPHONY_BACKEND_TEST_LOCK_ROOT="${temp_dir}/locks" \
    bash "${SCRIPT_PATH}" --workspace-dir "${workspace_b}" -- run --rm backend-contract-tests > "${output_b}" 2>&1 &
  second_pid=$!

  wait "${first_pid}"
  wait "${second_pid}"
  expected=$'start:first-project\nend:first-project\nstart:second-project\nend:second-project'
  [[ "$(cat "${log}")" == "${expected}" ]] || {
    echo "FAIL: Same Compose project name was not serialized across workspaces." >&2
    cat "${log}" >&2
    exit 1
  }
  rm -rf "${temp_dir}"
}

test_passes_compose_args_from_workspace
test_serializes_commands_for_the_same_workspace
test_lock_timeout_is_explicit
test_collision_cleanup_is_opt_in_and_bounded
test_unrelated_failure_never_cleans_up
test_persistent_collision_uses_only_configured_retry
test_repair_preserves_rootful_selector
test_rejects_custom_project_selector_before_compose
test_same_default_project_serializes_across_workspace_roots

echo "symphony_backend_test tests passed"
