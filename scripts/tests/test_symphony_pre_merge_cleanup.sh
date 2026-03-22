#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_pre_merge_cleanup.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

with_fake_docker() {
  local temp_dir="$1"
  local docker_stub="${temp_dir}/docker"

  cat > "${docker_stub}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${TEST_STATE_DIR:?}"

read_lines() {
  local path="$1"
  if [[ -f "${path}" ]]; then
    cat "${path}"
  fi
}

write_lines() {
  local path="$1"
  shift || true
  : > "${path}"
  if [[ "$#" -gt 0 ]]; then
    printf '%s\n' "$@" > "${path}"
  fi
}

remove_matches() {
  local path="$1"
  shift || true
  if [[ ! -f "${path}" ]]; then
    return 0
  fi
  local tmp
  tmp="$(mktemp)"
  cp "${path}" "${tmp}"
  for needle in "$@"; do
    grep -F -v -x "${needle}" "${tmp}" > "${tmp}.next" || true
    mv "${tmp}.next" "${tmp}"
  done
  cat "${tmp}" > "${path}"
  rm -f "${tmp}"
}

first_line() {
  local path="$1"
  if [[ -f "${path}" ]]; then
    head -n 1 "${path}"
  fi
}

case "${1:-}" in
  info)
    if [[ -f "${STATE_DIR}/docker_info_fail_count" ]]; then
      count="$(cat "${STATE_DIR}/docker_info_fail_count")"
      if [[ "${count}" -gt 0 ]]; then
        echo $((count - 1)) > "${STATE_DIR}/docker_info_fail_count"
        exit 1
      fi
    fi
    exit 0
    ;;
  run)
    exit 0
    ;;
  inspect)
    shift
    container_id="$1"
    shift
    if [[ "${1:-}" != "--format" ]]; then
      exit 1
    fi
    format="$2"
    while IFS='|' read -r id project working_dir config_files name; do
      [[ "${id}" == "${container_id}" ]] || continue
      if [[ "${format}" == *"com.docker.compose.project.config_files"* ]]; then
        printf '%s\n' "${config_files}"
        exit 0
      fi
      if [[ "${format}" == *"com.docker.compose.project.working_dir"* ]]; then
        printf '%s\n' "${working_dir}"
        exit 0
      fi
    done < <(read_lines "${STATE_DIR}/containers")
    exit 1
    ;;
  ps)
    shift
    mode="running"
    project_filter=""
    format=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -a)
          mode="all"
          shift
          ;;
        --filter)
          project_filter="${2#label=com.docker.compose.project=}"
          shift 2
          ;;
        --format)
          format="$2"
          shift 2
          ;;
        *)
          shift
          ;;
      esac
    done

    while IFS='|' read -r id project working_dir config_files name; do
      [[ -n "${id}" ]] || continue
      if [[ -n "${project_filter}" && "${project}" != "${project_filter}" ]]; then
        continue
      fi
      if [[ "${format}" == "{{.ID}}" ]]; then
        printf '%s\n' "${id}"
      elif [[ "${format}" == "{{.Names}}" ]]; then
        printf '%s\n' "${name}"
      elif [[ "${format}" == *'{{.ID}}'* && "${format}" == *'{{.Names}}'* && "${format}" == *'com.docker.compose.project'* ]]; then
        printf '%s\t%s\t%s\n' "${id}" "${name}" "${project}"
      elif [[ "${format}" == *'{{.Names}}'* && "${format}" == *'com.docker.compose.project'* && "${format}" != *'working_dir'* ]]; then
        printf '%s\t%s\n' "${name}" "${project}"
      elif [[ "${format}" == *'com.docker.compose.project.working_dir'* ]]; then
        printf '%s\t%s\n' "${project}" "${working_dir}"
      fi
    done < <(read_lines "${STATE_DIR}/containers")
    exit 0
    ;;
  volume)
    shift
    case "${1:-}" in
      ls)
        shift
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --format)
              shift 2
              ;;
            *)
              shift
              ;;
          esac
        done
        read_lines "${STATE_DIR}/volumes"
        ;;
      rm)
        shift
        remove_matches "${STATE_DIR}/volumes" "$@"
        ;;
    esac
    exit 0
    ;;
  image)
    shift
    case "${1:-}" in
      ls)
        shift
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --format)
              shift 2
              ;;
            *)
              shift
              ;;
          esac
        done
        read_lines "${STATE_DIR}/images"
        ;;
      rm)
        shift
        remove_matches "${STATE_DIR}/images" "$@"
        ;;
    esac
    exit 0
    ;;
  network)
    shift
    case "${1:-}" in
      ls)
        shift
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --format)
              shift 2
              ;;
            *)
              shift
              ;;
          esac
        done
        read_lines "${STATE_DIR}/networks"
        ;;
      rm)
        shift
        remove_matches "${STATE_DIR}/networks" "$@"
        ;;
    esac
    exit 0
    ;;
  rm)
    shift
    if [[ "${1:-}" == "-f" ]]; then
      shift
    fi
    while [[ $# -gt 0 ]]; do
      awk -F '|' -v id="$1" '$1 != id { print }' "${STATE_DIR}/containers" > "${STATE_DIR}/containers.next" || true
      mv "${STATE_DIR}/containers.next" "${STATE_DIR}/containers"
      shift
    done
    exit 0
    ;;
  compose)
    shift
    project=""
    config_paths=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -p)
          project="$2"
          shift 2
          ;;
        -f)
          config_paths+=("$2")
          shift 2
          ;;
        --env-file)
          shift 2
          ;;
        down)
          shift
          break
          ;;
        *)
          shift
          ;;
      esac
    done

    if [[ -f "${STATE_DIR}/compose_fail_once_project" ]]; then
      fail_project="$(cat "${STATE_DIR}/compose_fail_once_project")"
      if [[ "${fail_project}" == "${project}" ]]; then
        rm -f "${STATE_DIR}/compose_fail_once_project"
        echo "daemon unavailable" >&2
        exit 1
      fi
    fi

    if [[ ${#config_paths[@]} -eq 0 ]]; then
      echo "missing compose file" >&2
      exit 1
    fi

    for config_path in "${config_paths[@]}"; do
      if [[ ! -f "${config_path}" ]]; then
        echo "missing compose file" >&2
        exit 1
      fi
    done

    if [[ -f "${STATE_DIR}/containers" ]]; then
      grep -F -v "|${project}|" "${STATE_DIR}/containers" > "${STATE_DIR}/containers.next" || true
      mv "${STATE_DIR}/containers.next" "${STATE_DIR}/containers"
    fi
    remove_matches "${STATE_DIR}/volumes" "${project}_postgres_data" "${project}_default"
    remove_matches "${STATE_DIR}/networks" "${project}_default"
    exit 0
    ;;
esac

echo "Unexpected docker invocation: $*" >&2
exit 1
EOF

  chmod +x "${docker_stub}"
  shift
  TEST_STATE_DIR="${temp_dir}/state" PATH="${temp_dir}:${PATH}" "$@"
}

test_force_cleanup_uses_workspace_project_discovery() {
  local temp_dir workspace output
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/ALL-58"
  mkdir -p "${temp_dir}/state" "${workspace}"

  cat > "${temp_dir}/state/containers" <<EOF
cid-proof-1|all58proof|${workspace}|${workspace}/docker-compose.production.yml|all58proof-frontend-1
cid-proof-2|all58proof|${workspace}|${workspace}/docker-compose.production.yml|all58proof-postgres-1
EOF
  printf '%s\n' "all58proof_postgres_data" > "${temp_dir}/state/volumes"
  printf '%s\n' "all58proof_default" > "${temp_dir}/state/networks"
  cat > "${temp_dir}/state/images" <<'EOF'
all58-backend
all58-frontend
all58proof-backend
all58proof-frontend
all58proof-frontend-builder
keep-me
EOF

  output="$(
    with_fake_docker "${temp_dir}" \
      bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" --compose-project all58 --max-attempts 1
  )"

  assert_contains "CLEANUP_STATUS=success" "${output}"
  assert_contains "CLEANUP_PROJECTS=all58,all58proof" "${output}"
  assert_contains "CLEANUP_LEFTOVER_CONTAINERS=0" "${output}"
  assert_contains "CLEANUP_LEFTOVER_IMAGES=0" "${output}"
  [[ ! -s "${temp_dir}/state/containers" ]]
  [[ ! -s "${temp_dir}/state/volumes" ]]
  [[ ! -s "${temp_dir}/state/networks" ]]
  [[ "$(cat "${temp_dir}/state/images")" == "keep-me" ]]
}

test_compose_failure_falls_back_to_direct_cleanup() {
  local temp_dir workspace output
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/ALL-77"
  mkdir -p "${temp_dir}/state" "${workspace}"
  touch "${workspace}/docker-compose.yml"
  printf '%s\n' "all77" > "${temp_dir}/state/compose_fail_once_project"
  cat > "${temp_dir}/state/containers" <<EOF
cid-main-1|all77|${workspace}|${workspace}/docker-compose.yml|all77-frontend-1
EOF
  printf '%s\n' "all77_postgres_data" > "${temp_dir}/state/volumes"
  printf '%s\n' "all77_default" > "${temp_dir}/state/networks"
  cat > "${temp_dir}/state/images" <<'EOF'
all77-backend
all77-frontend
all77-frontend-builder
keep-me
EOF

  output="$(
    with_fake_docker "${temp_dir}" \
      bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" --compose-project all77 --max-attempts 2
  )"

  assert_contains "CLEANUP_STATUS=success" "${output}"
  assert_contains "CLEANUP_ATTEMPTS=1" "${output}"
  assert_contains "CLEANUP_LEFTOVER_CONTAINERS=0" "${output}"
  assert_contains "CLEANUP_LEFTOVER_IMAGES=0" "${output}"
  [[ ! -s "${temp_dir}/state/containers" ]]
  [[ ! -s "${temp_dir}/state/volumes" ]]
  [[ ! -s "${temp_dir}/state/networks" ]]
  [[ "$(cat "${temp_dir}/state/images")" == "keep-me" ]]
}

test_cleanup_matches_sanitized_issue_image_names() {
  local temp_dir workspace output
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/ALL-107"
  mkdir -p "${temp_dir}/state" "${workspace}"
  touch "${workspace}/docker-compose.yml"

  cat > "${temp_dir}/state/images" <<'EOF'
all107-backend
all107-frontend
all107-frontend-builder
all-107-unrelated
keep-me
EOF

  output="$(
    with_fake_docker "${temp_dir}" \
      bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" --compose-project all-107 --max-attempts 1
  )"

  assert_contains "CLEANUP_STATUS=success" "${output}"
  assert_contains "CLEANUP_LEFTOVER_IMAGES=0" "${output}"
  [[ "$(cat "${temp_dir}/state/images")" == $'all-107-unrelated\nkeep-me' ]]
}

test_force_cleanup_uses_workspace_project_discovery
test_compose_failure_falls_back_to_direct_cleanup
test_cleanup_matches_sanitized_issue_image_names

echo "symphony_pre_merge_cleanup tests passed"
