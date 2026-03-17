#!/usr/bin/env bash
set -u

usage() {
  cat <<'USAGE'
Usage:
  symphony_pre_merge_cleanup.sh [--workspace-dir DIR] [--compose-project NAME] [--env-file FILE] [--remove-workspace] [--max-attempts N]

Behavior:
  - Attempts issue-local docker teardown before merge.
  - Applies bounded self-healing (ownership fix + docker config fallback).
  - Emits machine-parsable summary lines:
      CLEANUP_STATUS=success|partial
      CLEANUP_ATTEMPTS=<n>
      CLEANUP_PROJECT=<name>
      CLEANUP_REMOVE_WORKSPACE_REQUESTED=true|false
      CLEANUP_WORKSPACE_REMOVED=true|false
      CLEANUP_LEFTOVER_CONTAINERS=<n>
      CLEANUP_LEFTOVER_VOLUMES=<n>
      CLEANUP_LEFTOVER_NETWORKS=<n>
      CLEANUP_FIXES=<comma-separated or none>
      CLEANUP_FIRST_ERROR=<single-line message or none>
  - Exits 0 on success, 42 on partial cleanup.
USAGE
}

workspace_dir="${PWD}"
compose_project=""
env_file=""
remove_workspace=0
max_attempts=2
retry_sleep_seconds="${SYMPHONY_CLEANUP_RETRY_SLEEP_SECONDS:-5}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      workspace_dir="${2:-}"
      shift 2
      ;;
    --compose-project)
      compose_project="${2:-}"
      shift 2
      ;;
    --env-file)
      env_file="${2:-}"
      shift 2
      ;;
    --remove-workspace)
      remove_workspace=1
      shift
      ;;
    --max-attempts)
      max_attempts="${2:-2}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "${compose_project}" ]]; then
  compose_project="$(basename "${workspace_dir}" | tr '[:upper:]' '[:lower:]')"
fi

if [[ -z "${env_file}" && -f "${HOME}/.agr_ai_curation/.env" ]]; then
  env_file="${HOME}/.agr_ai_curation/.env"
fi

sanitize_one_line() {
  printf '%s' "$1" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//'
}

unique_nonempty_lines() {
  awk 'NF && !seen[$0]++'
}

first_error=""
workspace_removed="false"
remove_workspace_requested="false"
attempt=0
applied_docker_config_fix=0
applied_owner_fix=0
applied_sudo_rm_fix=0
status="partial"
leftover_containers_count=0
leftover_volumes_count=0
leftover_networks_count=0
cleanup_projects_cache=()

record_first_error() {
  local text="$1"
  if [[ -n "${first_error}" ]]; then
    return 0
  fi
  local line
  line="$(printf '%s\n' "${text}" | sed -n '/./{p;q;}')"
  if [[ -n "${line}" ]]; then
    first_error="$(sanitize_one_line "${line}")"
  fi
}

ensure_docker_config_fix() {
  local fallback="/tmp/symphony-docker-config-${UID}"
  mkdir -p "${fallback}" >/dev/null 2>&1 || true
  export DOCKER_CONFIG="${fallback}"
  applied_docker_config_fix=1
}

attempt_owner_fix() {
  if [[ ! -d "${workspace_dir}" ]]; then
    return 0
  fi
  applied_owner_fix=1
  docker run --rm -v "${workspace_dir}:/target" alpine:3.20 sh -lc \
    "chown -R $(id -u):$(id -g) /target || true" >/dev/null 2>&1 || true
}

list_workspace_project_names() {
  docker ps -a --format '{{.Label "com.docker.compose.project"}}	{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null \
    | awk -F '\t' -v workspace="${workspace_dir}" '$2 == workspace && $1 != "" { print $1 }' \
    | unique_nonempty_lines
}

list_cleanup_projects() {
  {
    printf '%s\n' "${compose_project}"
    list_workspace_project_names || true
  } | unique_nonempty_lines
}

load_cleanup_projects() {
  mapfile -t cleanup_projects_cache < <(list_cleanup_projects || true)
  if [[ ${#cleanup_projects_cache[@]} -eq 0 ]]; then
    cleanup_projects_cache=("${compose_project}")
  fi
}

list_project_container_ids_for_project() {
  local project="$1"
  {
    docker ps -a --filter "label=com.docker.compose.project=${project}" --format '{{.ID}}' 2>/dev/null
    docker ps -a --format '{{.ID}}	{{.Names}}	{{.Label "com.docker.compose.project"}}' 2>/dev/null \
      | awk -F '\t' -v project="${project}" '$3 == project || index($2, project "-") == 1 { print $1 }'
  } | unique_nonempty_lines
}

list_project_containers_for_project() {
  local project="$1"
  {
    docker ps -a --filter "label=com.docker.compose.project=${project}" --format '{{.Names}}' 2>/dev/null
    docker ps -a --format '{{.Names}}	{{.Label "com.docker.compose.project"}}' 2>/dev/null \
      | awk -F '\t' -v project="${project}" '$2 == project || index($1, project "-") == 1 { print $1 }'
  } | unique_nonempty_lines
}

list_project_volumes_for_project() {
  local project="$1"
  docker volume ls --format '{{.Name}}' 2>/dev/null | awk -v prefix="${project}_" 'index($0, prefix) == 1'
}

list_project_networks_for_project() {
  local project="$1"
  docker network ls --format '{{.Name}}' 2>/dev/null | awk -v prefix="${project}_" 'index($0, prefix) == 1'
}

inspect_project_label() {
  local project="$1"
  local label="$2"
  local container_id
  container_id="$(list_project_container_ids_for_project "${project}" | head -n 1)"
  if [[ -z "${container_id}" ]]; then
    return 1
  fi

  docker inspect "${container_id}" --format "{{ index .Config.Labels \"${label}\" }}" 2>/dev/null
}

prune_project_resources_for_project() {
  local project="$1"
  local -a volumes
  local -a networks

  mapfile -t volumes < <(list_project_volumes_for_project "${project}" || true)
  if [[ ${#volumes[@]} -gt 0 ]]; then
    docker volume rm "${volumes[@]}" >/dev/null 2>&1 || true
  fi

  mapfile -t networks < <(list_project_networks_for_project "${project}" || true)
  if [[ ${#networks[@]} -gt 0 ]]; then
    docker network rm "${networks[@]}" >/dev/null 2>&1 || true
  fi
}

force_cleanup_project() {
  local project="$1"
  local -a container_ids

  mapfile -t container_ids < <(list_project_container_ids_for_project "${project}" || true)
  if [[ ${#container_ids[@]} -gt 0 ]]; then
    docker rm -f "${container_ids[@]}" >/dev/null 2>&1 || true
  fi

  prune_project_resources_for_project "${project}"
}

compose_file_args_for_project() {
  local project="$1"
  local config_files=""
  local config_path=""
  local found=0

  config_files="$(inspect_project_label "${project}" "com.docker.compose.project.config_files" || true)"
  if [[ -n "${config_files}" ]]; then
    local -a config_paths=()
    IFS=',' read -r -a config_paths <<< "${config_files}"
    for config_path in "${config_paths[@]}"; do
      if [[ -n "${config_path}" && -f "${config_path}" ]]; then
        printf '%s\0%s\0' "-f" "${config_path}"
        found=1
      fi
    done
  fi

  if [[ "${found}" -eq 0 && -d "${workspace_dir}" ]]; then
    for config_path in \
      "${workspace_dir}/docker-compose.yml" \
      "${workspace_dir}/docker-compose.production.yml" \
      "${workspace_dir}/compose.yml"
    do
      if [[ -f "${config_path}" ]]; then
        printf '%s\0%s\0' "-f" "${config_path}"
        found=1
      fi
    done
  fi

  return "${found}"
}

docker_teardown_project() {
  local project="$1"
  local output rc
  local -a cmd=(docker compose -p "${project}")
  local -a compose_file_args=()
  if [[ -n "${env_file}" && -f "${env_file}" ]]; then
    cmd+=(--env-file "${env_file}")
  fi

  while IFS= read -r -d '' arg; do
    compose_file_args+=("${arg}")
  done < <(compose_file_args_for_project "${project}" 2>/dev/null || true)

  if [[ ${#compose_file_args[@]} -eq 0 ]]; then
    return 99
  fi

  cmd+=("${compose_file_args[@]}")
  cmd+=(down --remove-orphans --volumes)

  output="$("${cmd[@]}" 2>&1)"
  rc=$?

  printf '%s' "${output}"
  return "${rc}"
}

refresh_leftover_counts() {
  local project
  local -a containers=()
  local -a volumes=()
  local -a networks=()

  leftover_containers_count=0
  leftover_volumes_count=0
  leftover_networks_count=0

  if [[ ${#cleanup_projects_cache[@]} -eq 0 ]]; then
    load_cleanup_projects
  fi

  for project in "${cleanup_projects_cache[@]}"; do
    mapfile -t containers < <(list_project_containers_for_project "${project}" || true)
    mapfile -t volumes < <(list_project_volumes_for_project "${project}" || true)
    mapfile -t networks < <(list_project_networks_for_project "${project}" || true)

    leftover_containers_count=$((leftover_containers_count + ${#containers[@]}))
    leftover_volumes_count=$((leftover_volumes_count + ${#volumes[@]}))
    leftover_networks_count=$((leftover_networks_count + ${#networks[@]}))
  done
}

docker_teardown_once() {
  local project=""
  local project_output=""
  local project_rc=0
  local combined_output=""

  if [[ ${#cleanup_projects_cache[@]} -eq 0 ]]; then
    load_cleanup_projects
  fi

  for project in "${cleanup_projects_cache[@]}"; do
    project_output="$(docker_teardown_project "${project}")"
    project_rc=$?

    if [[ "${project_rc}" -eq 99 ]]; then
      project_output="compose config unavailable for ${project}; using direct docker cleanup"
    fi

    if [[ "${project_rc}" -ne 0 ]]; then
      if [[ -n "${combined_output}" ]]; then
        combined_output="${combined_output}"$'\n'
      fi
      combined_output="${combined_output}${project_output}"
      force_cleanup_project "${project}"
    fi

    prune_project_resources_for_project "${project}"
  done

  refresh_leftover_counts

  if [[ "${leftover_containers_count}" -eq 0 && "${leftover_volumes_count}" -eq 0 && "${leftover_networks_count}" -eq 0 ]]; then
    printf '%s' "${combined_output}"
    return 0
  fi

  if [[ -n "${combined_output}" ]]; then
    combined_output="${combined_output}"$'\n'
  fi
  combined_output="${combined_output}leftover docker resources for ${compose_project}: containers=${leftover_containers_count} volumes=${leftover_volumes_count} networks=${leftover_networks_count}"
  printf '%s' "${combined_output}"
  return 1
}

if ! command -v docker >/dev/null 2>&1; then
  record_first_error "docker command not found"
else
  while [[ "${attempt}" -lt "${max_attempts}" ]]; do
    attempt=$((attempt + 1))
    load_cleanup_projects

    teardown_output="$(docker_teardown_once)"
    teardown_rc=$?

    if [[ "${teardown_rc}" -eq 0 ]]; then
      break
    fi

    record_first_error "${teardown_output}"

    if [[ "${applied_docker_config_fix}" -eq 0 ]]; then
      ensure_docker_config_fix
    fi

    if [[ "${applied_owner_fix}" -eq 0 ]]; then
      attempt_owner_fix
    fi

    if [[ "${attempt}" -lt "${max_attempts}" && "${retry_sleep_seconds}" -gt 0 ]]; then
      sleep "${retry_sleep_seconds}"
    fi
  done

  refresh_leftover_counts

  if [[ "${teardown_rc:-1}" -eq 0 ]]; then
    if [[ "${leftover_containers_count}" -eq 0 && "${leftover_volumes_count}" -eq 0 && "${leftover_networks_count}" -eq 0 ]]; then
      status="success"
    else
      status="partial"
      record_first_error "leftover docker resources for ${compose_project}: containers=${leftover_containers_count} volumes=${leftover_volumes_count} networks=${leftover_networks_count}"
    fi
  else
    status="partial"
  fi
fi

if [[ "${remove_workspace}" -eq 1 ]]; then
  remove_workspace_requested="true"
  if [[ -d "${workspace_dir}" ]]; then
    current_dir="$(pwd -P 2>/dev/null || pwd)"
    case "${current_dir}" in
      "${workspace_dir}"|${workspace_dir}/*)
        cd "$(dirname "${workspace_dir}")" || true
        ;;
    esac

    rm -rf "${workspace_dir}" >/dev/null 2>&1 || true

    if [[ -d "${workspace_dir}" ]]; then
      attempt_owner_fix
      rm -rf "${workspace_dir}" >/dev/null 2>&1 || true
    fi

    if [[ -d "${workspace_dir}" ]] && command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
      sudo -n rm -rf "${workspace_dir}" >/dev/null 2>&1 || true
      applied_sudo_rm_fix=1
    fi
  fi

  if [[ -d "${workspace_dir}" ]]; then
    workspace_removed="false"
    status="partial"
    record_first_error "failed to remove workspace ${workspace_dir}"
  else
    workspace_removed="true"
  fi
fi

fixes="none"
if [[ "${applied_docker_config_fix}" -eq 1 || "${applied_owner_fix}" -eq 1 || "${applied_sudo_rm_fix}" -eq 1 ]]; then
  fixes=""
  if [[ "${applied_docker_config_fix}" -eq 1 ]]; then
    fixes="docker_config_fallback"
  fi
  if [[ "${applied_owner_fix}" -eq 1 ]]; then
    if [[ -n "${fixes}" ]]; then
      fixes="${fixes},owner_fix"
    else
      fixes="owner_fix"
    fi
  fi
  if [[ "${applied_sudo_rm_fix}" -eq 1 ]]; then
    if [[ -n "${fixes}" ]]; then
      fixes="${fixes},sudo_rm_fallback"
    else
      fixes="sudo_rm_fallback"
    fi
  fi
fi

if [[ -z "${first_error}" ]]; then
  first_error="none"
fi

cleanup_projects_joined="none"
if [[ ${#cleanup_projects_cache[@]} -gt 0 ]]; then
  cleanup_projects_joined="$(IFS=,; echo "${cleanup_projects_cache[*]}")"
fi

echo "CLEANUP_STATUS=${status}"
echo "CLEANUP_ATTEMPTS=${attempt}"
echo "CLEANUP_PROJECT=${compose_project}"
echo "CLEANUP_PROJECTS=${cleanup_projects_joined}"
echo "CLEANUP_REMOVE_WORKSPACE_REQUESTED=${remove_workspace_requested}"
echo "CLEANUP_WORKSPACE_REMOVED=${workspace_removed}"
echo "CLEANUP_LEFTOVER_CONTAINERS=${leftover_containers_count}"
echo "CLEANUP_LEFTOVER_VOLUMES=${leftover_volumes_count}"
echo "CLEANUP_LEFTOVER_NETWORKS=${leftover_networks_count}"
echo "CLEANUP_FIXES=${fixes}"
echo "CLEANUP_FIRST_ERROR=${first_error}"

if [[ "${status}" == "success" ]]; then
  exit 0
fi

exit 42
