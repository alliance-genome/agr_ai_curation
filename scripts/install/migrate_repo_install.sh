#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
helper_repo_root="$(cd "${script_dir}/../.." && pwd)"
# shellcheck source=scripts/install/lib/common.sh
source "${helper_repo_root}/scripts/install/lib/common.sh"

deployment_config_filenames=()
while IFS= read -r filename; do
  deployment_config_filenames+=("$filename")
done < <(install_deployment_config_filenames)

readonly EXIT_MANUAL_REVIEW_REQUIRED=3

mode="dry-run"
apply_mode=0
source_repo="${helper_repo_root}"
install_home_dir="${INSTALL_HOME_DIR:-${HOME}/.agr_ai_curation}"

declare -a extra_package_dirs=()
declare -a non_package_extra_dirs=()
declare -a custom_agent_dirs=()
declare -a modified_core_agent_dirs=()
declare -a custom_tool_files=()
declare -a custom_tool_dirs=()
modified_core_package=0
core_baseline_unresolved=0
helper_canonical_core_dir_cache=""
canonical_core_dir_cache=""
helper_canonical_core_dir_temp_root=""
canonical_core_dir_temp_root=""

usage() {
  cat <<'EOF'
Usage: scripts/install/migrate_repo_install.sh [--dry-run|--apply] [options]

Migrate a repo-based install into the standalone modular runtime/data layout.

Options:
  --dry-run                Report what would be migrated (default)
  --apply                  Perform the migration
  --source-repo PATH       Repo checkout to migrate (default: current repo)
  --install-home PATH      Target install home (default: ~/.agr_ai_curation)
  --help                   Show this help text

Exit codes:
  0  Migration is complete or dry-run only
  3  Manual review is required because legacy local code was preserved
  1  Usage or runtime failure
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run)
        mode="dry-run"
        apply_mode=0
        ;;
      --apply)
        mode="apply"
        apply_mode=1
        ;;
      --source-repo)
        shift
        [[ $# -gt 0 ]] || {
          log_error "--source-repo requires a path value"
          usage
          exit 1
        }
        source_repo="$1"
        ;;
      --install-home)
        shift
        [[ $# -gt 0 ]] || {
          log_error "--install-home requires a path value"
          usage
          exit 1
        }
        install_home_dir="$1"
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        log_error "Unknown option: $1"
        usage
        exit 1
        ;;
    esac
    shift
  done
}

join_by() {
  local delimiter="$1"
  shift || true
  local result=""
  local item=""

  for item in "$@"; do
    if [[ -z "$result" ]]; then
      result="$item"
    else
      result="${result}${delimiter}${item}"
    fi
  done

  printf '%s\n' "$result"
}

directory_has_entries() {
  local dir_path="$1"
  [[ -d "$dir_path" ]] && find "$dir_path" -mindepth 1 -print -quit | grep -q .
}

backup_existing_path() {
  local path="$1"

  if [[ ! -e "$path" ]]; then
    return 0
  fi

  local backup_path
  backup_path="${path}.bak.$(date -u +%Y%m%d%H%M%S).$$"
  mv "$path" "$backup_path"
  log_info "Backed up ${path} -> ${backup_path}"
}

copy_file_exact() {
  local source_path="$1"
  local target_path="$2"

  if [[ "$apply_mode" -eq 0 ]]; then
    printf '  - copy file: %s -> %s\n' "$source_path" "$target_path"
    return 0
  fi

  mkdir -p "$(dirname "$target_path")"
  backup_existing_path "$target_path"
  cp -a "$source_path" "$target_path"
}

copy_tree_exact() {
  local source_path="$1"
  local target_path="$2"

  if [[ "$apply_mode" -eq 0 ]]; then
    printf '  - copy tree: %s -> %s\n' "$source_path" "$target_path"
    return 0
  fi

  mkdir -p "$(dirname "$target_path")"
  backup_existing_path "$target_path"
  cp -a "$source_path" "$target_path"
}

copy_tree_contents() {
  local source_path="$1"
  local target_path="$2"

  if [[ "$apply_mode" -eq 0 ]]; then
    printf '  - copy contents: %s -> %s\n' "$source_path" "$target_path"
    return 0
  fi

  mkdir -p "$target_path"
  cp -a "${source_path}/." "$target_path/"
}

agent_dirs_match() {
  local left_dir="$1"
  local right_dir="$2"

  diff -qr \
    -x '__pycache__' \
    -x '*.pyc' \
    -x '.DS_Store' \
    "$left_dir" \
    "$right_dir" >/dev/null 2>&1
}

repo_has_live_git_path_customizations() {
  local repo_root="$1"
  local repo_path="$2"

  if ! command -v git >/dev/null 2>&1; then
    return 1
  fi

  if ! git -C "$repo_root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    return 1
  fi

  if ! git -C "$repo_root" diff --no-ext-diff --quiet -- "$repo_path"; then
    return 0
  fi

  if ! git -C "$repo_root" diff --cached --no-ext-diff --quiet -- "$repo_path"; then
    return 0
  fi

  if git -C "$repo_root" ls-files --others --exclude-standard -- "$repo_path" | grep -q .; then
    return 0
  fi

  return 1
}

repo_has_git_path_customizations() {
  local repo_root="$1"
  local repo_path="$2"
  local baseline_commit=""

  if ! command -v git >/dev/null 2>&1; then
    return 1
  fi

  if ! git -C "$repo_root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    return 1
  fi

  if repo_has_live_git_path_customizations "$repo_root" "$repo_path"; then
    return 0
  fi

  baseline_commit="$(resolve_git_baseline_commit "$repo_root")" || return 1
  if ! git -C "$repo_root" diff --no-ext-diff --quiet "$baseline_commit" -- "$repo_path"; then
    return 0
  fi

  return 1
}

resolve_git_baseline_commit() {
  local repo_root="$1"
  local baseline_ref=""

  git -C "$repo_root" rev-parse --verify HEAD >/dev/null 2>&1 || return 1

  for baseline_ref in origin/main origin/master; do
    if git -C "$repo_root" rev-parse --verify "${baseline_ref}^{commit}" >/dev/null 2>&1; then
      git -C "$repo_root" merge-base HEAD "$baseline_ref"
      return 0
    fi
  done

  return 1
}

resolve_helper_canonical_core_dir() {
  local baseline_commit=""
  local head_commit=""

  if [[ -n "$helper_canonical_core_dir_cache" ]]; then
    printf '%s\n' "$helper_canonical_core_dir_cache"
    return 0
  fi

  helper_canonical_core_dir_cache="${helper_repo_root}/packages/core"

  if command -v git >/dev/null 2>&1 && git -C "$helper_repo_root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    baseline_commit="$(resolve_git_baseline_commit "$helper_repo_root")" || baseline_commit=""
    if [[ -z "$baseline_commit" ]] && repo_has_live_git_path_customizations "$helper_repo_root" "packages/core"; then
      head_commit="$(git -C "$helper_repo_root" rev-parse --verify HEAD 2>/dev/null)" || head_commit=""
      baseline_commit="$head_commit"
    fi
    if [[ -n "$baseline_commit" ]]; then
      helper_canonical_core_dir_temp_root="$(mktemp -d)"
      if git -C "$helper_repo_root" archive "$baseline_commit" packages/core | tar -x -C "$helper_canonical_core_dir_temp_root"; then
        helper_canonical_core_dir_cache="${helper_canonical_core_dir_temp_root}/packages/core"
      else
        rm -rf "$helper_canonical_core_dir_temp_root"
        helper_canonical_core_dir_temp_root=""
      fi
    fi
  fi

  printf '%s\n' "$helper_canonical_core_dir_cache"
}

resolve_canonical_core_dir() {
  local baseline_commit=""
  local helper_canonical_core_dir=""

  if [[ -n "$canonical_core_dir_cache" ]]; then
    printf '%s\n' "$canonical_core_dir_cache"
    return 0
  fi

  helper_canonical_core_dir="$(resolve_helper_canonical_core_dir)"
  canonical_core_dir_cache="$helper_canonical_core_dir"

  if command -v git >/dev/null 2>&1 && git -C "$source_repo" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    baseline_commit="$(resolve_git_baseline_commit "$source_repo")" || baseline_commit=""
    if [[ -n "$baseline_commit" ]]; then
      if repo_has_git_path_customizations "$source_repo" "packages/core"; then
        canonical_core_dir_temp_root="$(mktemp -d)"
        if git -C "$source_repo" archive "$baseline_commit" packages/core | tar -x -C "$canonical_core_dir_temp_root"; then
          canonical_core_dir_cache="${canonical_core_dir_temp_root}/packages/core"
        else
          rm -rf "$canonical_core_dir_temp_root"
          canonical_core_dir_temp_root=""
        fi
      else
        canonical_core_dir_cache="${source_repo}/packages/core"
      fi
    fi
  fi

  printf '%s\n' "$canonical_core_dir_cache"
}

record_source_scan() {
  local packages_dir="${source_repo}/packages"
  local source_core_dir="${packages_dir}/core"
  local canonical_core_dir
  local baseline_agents_dir=""
  local config_agents_dir="${source_repo}/config/agents"
  local tools_dir="${source_repo}/backend/tools/custom"
  local source_baseline_commit=""

  if command -v git >/dev/null 2>&1 && git -C "$source_repo" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    source_baseline_commit="$(resolve_git_baseline_commit "$source_repo")" || source_baseline_commit=""
  fi

  canonical_core_dir="$(resolve_canonical_core_dir)"
  baseline_agents_dir="${canonical_core_dir}/agents"
  if ! agent_dirs_match "$source_core_dir" "$canonical_core_dir"; then
    modified_core_package=1
    if [[ -z "$source_baseline_commit" ]]; then
      core_baseline_unresolved=1
    fi
  fi

  local dir_path=""
  for dir_path in "${packages_dir}"/*; do
    [[ -d "$dir_path" ]] || continue

    local dir_name
    dir_name="$(basename "$dir_path")"
    if [[ "$dir_name" == "core" ]]; then
      continue
    fi

    if [[ -f "${dir_path}/package.yaml" ]]; then
      extra_package_dirs+=("$dir_path")
    else
      non_package_extra_dirs+=("$dir_path")
    fi
  done

  if [[ -d "$config_agents_dir" ]]; then
    local agent_dir=""
    for agent_dir in "${config_agents_dir}"/*; do
      [[ -d "$agent_dir" ]] || continue

      local agent_name
      agent_name="$(basename "$agent_dir")"
      [[ "$agent_name" == _* ]] && continue

      local baseline_dir="${baseline_agents_dir}/${agent_name}"
      if [[ ! -d "$baseline_dir" ]]; then
        custom_agent_dirs+=("$agent_dir")
        continue
      fi

      if ! agent_dirs_match "$agent_dir" "$baseline_dir"; then
        modified_core_agent_dirs+=("$agent_dir")
      fi
    done
  fi

  if [[ -d "$tools_dir" ]]; then
    while IFS= read -r -d '' file_path; do
      custom_tool_files+=("$file_path")
    done < <(
      find "$tools_dir" \
        \( -type d \( -name '__pycache__' -o -name '.git' \) -prune \) -o \
        \( -type f ! -name '*.pyc' ! -name '.DS_Store' ! -name 'README.md' -print0 \)
    )

    while IFS= read -r -d '' dir_path; do
      custom_tool_dirs+=("$dir_path")
    done < <(
      find "$tools_dir" \
        \( -type d \( -name '__pycache__' -o -name '.git' \) -prune \) -o \
        \( -type d ! -path "$tools_dir" -print0 \)
    )
  fi
}

has_custom_code() {
  [[ "$modified_core_package" -eq 1 ]] \
    || [[ "${#non_package_extra_dirs[@]}" -gt 0 ]] \
    || [[ "${#custom_agent_dirs[@]}" -gt 0 ]] \
    || [[ "${#modified_core_agent_dirs[@]}" -gt 0 ]] \
    || [[ "${#custom_tool_files[@]}" -gt 0 ]]
}

copy_runtime_config() {
  local runtime_config_dir="$1"
  local config_dir="${source_repo}/config"
  local filename=""

  echo
  log_info "Runtime config migration"
  for filename in "${deployment_config_filenames[@]}"; do
    local source_path="${config_dir}/${filename}"
    local target_path="${runtime_config_dir}/${filename}"
    require_file_exists "$source_path"
    copy_file_exact "$source_path" "$target_path"
  done
}

copy_runtime_packages() {
  local runtime_packages_dir="$1"
  local packages_dir="${source_repo}/packages"
  local source_core_dir="${packages_dir}/core"
  local core_source_dir
  local extra_dir=""

  echo
  log_info "Runtime package migration"
  require_directory_exists "$source_core_dir"
  core_source_dir="$(resolve_canonical_core_dir)"
  copy_tree_exact "$core_source_dir" "${runtime_packages_dir}/core"

  for extra_dir in "${extra_package_dirs[@]}"; do
    local package_name
    package_name="$(basename "$extra_dir")"
    copy_tree_exact "$extra_dir" "${runtime_packages_dir}/${package_name}"
  done
}

copy_runtime_state() {
  local runtime_state_dir="$1"
  local source_state_dir="${source_repo}/runtime_state"
  local temp_dir=""
  local has_state_children=0

  echo
  log_info "Runtime state migration"

  if [[ ! -d "$source_state_dir" ]]; then
    if [[ "$apply_mode" -eq 1 ]]; then
      mkdir -p "$runtime_state_dir"
    fi
    printf '  - no repo runtime_state directory detected\n'
    return 0
  fi

  while IFS= read -r -d '' child_path; do
    local child_name
    child_name="$(basename "$child_path")"
    case "$child_name" in
      pdf_storage|file_outputs)
        continue
        ;;
    esac
    if [[ -z "$temp_dir" ]]; then
      temp_dir="$(mktemp -d)"
    fi
    has_state_children=1
    cp -a "$child_path" "${temp_dir}/${child_name}"
  done < <(find "$source_state_dir" -mindepth 1 -maxdepth 1 -print0)

  if [[ "$has_state_children" -eq 0 ]]; then
    if [[ "$apply_mode" -eq 1 ]]; then
      mkdir -p "$runtime_state_dir"
    fi
    printf '  - no standalone runtime_state content detected outside pdf/file mounts\n'
    return 0
  fi

  if [[ "$apply_mode" -eq 0 ]]; then
    printf '  - copy tree: %s -> %s\n' "$source_state_dir" "$runtime_state_dir"
    rm -rf "$temp_dir"
    return 0
  fi

  mkdir -p "$(dirname "$runtime_state_dir")"
  backup_existing_path "$runtime_state_dir"
  mkdir -p "$runtime_state_dir"
  cp -a "${temp_dir}/." "$runtime_state_dir/"
  rm -rf "$temp_dir"
}

copy_data_dirs() {
  local pdf_storage_dir="$1"
  local file_outputs_dir="$2"
  local weaviate_data_dir="$3"
  local source_pdf_dir="${source_repo}/pdf_storage"
  local source_outputs_dir="${source_repo}/file_outputs"
  local source_weaviate_dir="${source_repo}/weaviate_data"

  echo
  log_info "Data directory migration"

  if [[ -d "$source_pdf_dir" ]]; then
    copy_tree_exact "$source_pdf_dir" "$pdf_storage_dir"
  else
    printf '  - skipped missing source dir: %s\n' "$source_pdf_dir"
    if [[ "$apply_mode" -eq 1 ]]; then
      mkdir -p "$pdf_storage_dir"
    fi
  fi

  if [[ -d "$source_outputs_dir" ]]; then
    copy_tree_exact "$source_outputs_dir" "$file_outputs_dir"
  else
    printf '  - skipped missing source dir: %s\n' "$source_outputs_dir"
    if [[ "$apply_mode" -eq 1 ]]; then
      mkdir -p "$file_outputs_dir"
    fi
  fi

  if [[ -d "$source_weaviate_dir" ]]; then
    copy_tree_exact "$source_weaviate_dir" "$weaviate_data_dir"
  else
    printf '  - skipped missing source dir: %s\n' "$source_weaviate_dir"
    if [[ "$apply_mode" -eq 1 ]]; then
      mkdir -p "$weaviate_data_dir"
    fi
  fi
}

patch_installed_env() {
  local runtime_config_dir="$1"
  local runtime_packages_dir="$2"
  local runtime_state_dir="$3"
  local pdf_storage_dir="$4"
  local file_outputs_dir="$5"
  local weaviate_data_dir="$6"
  local source_env_path="${source_repo}/.env"
  local target_env_path="${install_home_dir}/.env"
  local copied_source_env=0

  echo
  log_info "Installed env migration"

  if [[ -f "$source_env_path" ]]; then
    copy_file_exact "$source_env_path" "$target_env_path"
    copied_source_env=1
  elif [[ "$apply_mode" -eq 0 ]]; then
    printf '  - no source .env found at %s\n' "$source_env_path"
  fi

  if [[ "$apply_mode" -eq 0 ]]; then
    if [[ "$copied_source_env" -eq 1 || -f "$target_env_path" ]]; then
      printf '  - update env vars in: %s\n' "$target_env_path"
    else
      printf '  - no target .env available to patch automatically\n'
    fi
    return 0
  fi

  if [[ ! -f "$target_env_path" ]]; then
    log_warn "No source or target .env found; create ${target_env_path} before using docker-compose.production.yml"
    return 0
  fi

  upsert_env_var "$target_env_path" "AGR_RUNTIME_CONFIG_HOST_DIR" "$runtime_config_dir"
  upsert_env_var "$target_env_path" "AGR_REPO_CONFIG_HOST_DIR" "${source_repo}/config"
  upsert_env_var "$target_env_path" "AGR_RUNTIME_PACKAGES_HOST_DIR" "$runtime_packages_dir"
  upsert_env_var "$target_env_path" "AGR_RUNTIME_STATE_HOST_DIR" "$runtime_state_dir"
  upsert_env_var "$target_env_path" "PDF_STORAGE_HOST_DIR" "$pdf_storage_dir"
  upsert_env_var "$target_env_path" "FILE_OUTPUT_STORAGE_HOST_DIR" "$file_outputs_dir"
  upsert_env_var "$target_env_path" "WEAVIATE_DATA_HOST_DIR" "$weaviate_data_dir"

  local obsolete_key=""
  for obsolete_key in \
    AGENTS_CONFIG_PATH \
    GROUPS_CONFIG_PATH \
    CONNECTIONS_CONFIG_PATH \
    MODELS_CONFIG_PATH \
    PROVIDERS_CONFIG_PATH \
    TOOL_POLICY_DEFAULTS_CONFIG_PATH \
    AGR_RUNTIME_ROOT \
    AGR_RUNTIME_CONFIG_DIR \
    AGR_RUNTIME_PACKAGES_DIR \
    AGR_RUNTIME_STATE_DIR \
    AGR_RUNTIME_OVERRIDES_PATH; do
    remove_env_var "$target_env_path" "$obsolete_key"
  done

  chmod 600 "$target_env_path"
  log_success "Updated installed env at ${target_env_path}"
}

agent_has_schema() {
  local agent_dir="$1"
  [[ -f "${agent_dir}/schema.py" ]]
}

agent_group_rule_names() {
  local agent_dir="$1"
  local group_rules_dir="${agent_dir}/group_rules"

  if [[ ! -d "$group_rules_dir" ]]; then
    return 0
  fi

  find "$group_rules_dir" -maxdepth 1 -type f -name '*.yaml' \
    ! -name 'example.yaml' ! -name '_*.yaml' -exec basename {} .yaml \; \
    | sort
}

write_legacy_local_readme() {
  local scaffold_dir="$1"
  local readme_path="${scaffold_dir}/README.md"
  local custom_agent_names=()
  local modified_agent_names=()
  local non_package_names=()
  local tool_rel_paths=()
  local agent_dir=""
  local path=""

  for agent_dir in "${custom_agent_dirs[@]}"; do
    custom_agent_names+=("$(basename "$agent_dir")")
  done
  for agent_dir in "${modified_core_agent_dirs[@]}"; do
    modified_agent_names+=("$(basename "$agent_dir")")
  done
  for path in "${non_package_extra_dirs[@]}"; do
    non_package_names+=("$(basename "$path")")
  done
  for path in "${custom_tool_files[@]}"; do
    tool_rel_paths+=("${path#${source_repo}/}")
  done

  cat >"$readme_path" <<EOF
# legacy_local migration scaffold

This directory preserves repo-local code that is not safe to activate automatically
inside the modular runtime layout.

Created by:
- script: scripts/install/migrate_repo_install.sh
- mode: ${mode}
- source repo: ${source_repo}
- install home: ${install_home_dir}

Why this exists:
- Standard config, packages, and data were migrated into the installed runtime layout.
- Legacy local code was detected, so manual review is required before switching the
  deployment to the published-image runtime.

What was preserved here:
EOF

  if [[ "$core_baseline_unresolved" -eq 1 ]]; then
    printf -- '- shipped core baseline could not be verified automatically; preserved source snapshot: packages/core_repo_snapshot\n' >>"$readme_path"
  fi
  if [[ "$modified_core_package" -eq 1 ]]; then
    printf -- '- modified shipped core package snapshot: packages/core_repo_snapshot\n' >>"$readme_path"
  fi
  if [[ "${#custom_agent_names[@]}" -gt 0 ]]; then
    printf -- '- custom agent bundles: %s\n' "$(join_by ", " "${custom_agent_names[@]}")" >>"$readme_path"
  fi
  if [[ "${#modified_agent_names[@]}" -gt 0 ]]; then
    printf -- '- modified shipped agent bundles: %s\n' "$(join_by ", " "${modified_agent_names[@]}")" >>"$readme_path"
  fi
  if [[ "${#tool_rel_paths[@]}" -gt 0 ]]; then
    printf -- '- custom tool files: %s\n' "$(join_by ", " "${tool_rel_paths[@]}")" >>"$readme_path"
  fi
  if [[ "${#non_package_names[@]}" -gt 0 ]]; then
    printf -- '- extra repo package directories without package.yaml: %s\n' "$(join_by ", " "${non_package_names[@]}")" >>"$readme_path"
  fi

  cat >>"$readme_path" <<'EOF'

Next steps:
1. Review the preserved sources in this directory.
2. Decide which custom agents should become package-owned runtime bundles.
3. Fill in `package.yaml.template` and, when custom tools are present, `tools/bindings.yaml.template`.
4. Move the completed package into `runtime/packages/` only after the manifest and bindings are valid.
5. If you changed shipped agent bundles or package-local core files, reconcile those changes manually against the canonical `runtime/packages/core`.
EOF
}

write_legacy_local_package_template() {
  local scaffold_dir="$1"
  local template_path="${scaffold_dir}/package.yaml.template"
  local has_custom_agents=0
  local agent_dir=""

  cat >"$template_path" <<'EOF'
package_id: legacy_local
display_name: Legacy Local Migration Package
version: 0.1.0
package_api_version: 1.0.0
min_runtime_version: 1.0.0
max_runtime_version: 2.0.0
python_package_root: python/src/legacy_local
requirements_file: requirements/runtime.txt
exports:
  # TODO: replace these placeholder exports with the bundles you decide to keep.
  - kind: provider
    name: replace_me
    path: config/placeholder.yaml
    description: Replace this placeholder before activating the package
EOF

  if [[ "${#custom_agent_dirs[@]}" -gt 0 ]]; then
    has_custom_agents=1
    {
      printf '\nagent_bundles:\n'
      for agent_dir in "${custom_agent_dirs[@]}"; do
        local agent_name
        agent_name="$(basename "$agent_dir")"
        printf '  - name: %s\n' "$agent_name"
        printf '    agents_dir: agents/custom\n'
        if agent_has_schema "$agent_dir"; then
          printf '    has_schema: true\n'
        fi

        local group_rules=()
        local group_rule=""
        while IFS= read -r group_rule; do
          [[ -n "$group_rule" ]] || continue
          group_rules+=("$group_rule")
        done < <(agent_group_rule_names "$agent_dir")
        if [[ "${#group_rules[@]}" -gt 0 ]]; then
          printf '    group_rules: [%s]\n' "$(join_by ", " "${group_rules[@]}")"
        fi
      done
    } >>"$template_path"
  fi

  if [[ "$has_custom_agents" -eq 0 ]]; then
    cat >>"$template_path" <<'EOF'

# No brand-new custom agent bundles were detected.
# Modified shipped agents were preserved under agents/modified_core/ for manual review.
EOF
  fi
}

write_legacy_local_tool_template() {
  local scaffold_dir="$1"
  local template_path="${scaffold_dir}/tools/bindings.yaml.template"

  mkdir -p "${scaffold_dir}/tools"
  cat >"$template_path" <<'EOF'
package_id: legacy_local
bindings_api_version: 1.0.0
tools:
  - tool_id: replace_me
    binding_kind: static
    callable: legacy_local.custom_tools.replace_me:replace_me
    required_context: []
    description: Replace this placeholder with a migrated legacy tool binding
EOF
}

create_legacy_local_scaffold() {
  local scaffold_dir="${install_home_dir}/migration/legacy_local"
  local source_core_dir="${source_repo}/packages/core"
  local custom_tools_source_dir="${source_repo}/backend/tools/custom"
  local custom_tools_target_dir="${scaffold_dir}/python/src/legacy_local/custom_tools"
  local agent_dir=""
  local dir_path=""

  echo
  log_warn "Legacy local code detected; preserving a manual-review scaffold"
  printf '  - scaffold dir: %s\n' "$scaffold_dir"

  if [[ "$apply_mode" -eq 0 ]]; then
    printf '  - shipped core baseline unresolved: %s\n' "$core_baseline_unresolved"
    printf '  - modified shipped core package: %s\n' "$modified_core_package"
    printf '  - custom agents: %s\n' "${#custom_agent_dirs[@]}"
    printf '  - modified shipped agents: %s\n' "${#modified_core_agent_dirs[@]}"
    printf '  - custom tool files: %s\n' "${#custom_tool_files[@]}"
    printf '  - extra non-package dirs: %s\n' "${#non_package_extra_dirs[@]}"
    return 0
  fi

  mkdir -p "$(dirname "$scaffold_dir")"
  backup_existing_path "$scaffold_dir"
  mkdir -p \
    "${scaffold_dir}/agents/custom" \
    "${scaffold_dir}/agents/modified_core" \
    "${scaffold_dir}/python/src/legacy_local" \
    "${scaffold_dir}/requirements"

  printf '%s\n' '"""Legacy local migration scaffold."""' >"${scaffold_dir}/python/src/legacy_local/__init__.py"
  : >"${scaffold_dir}/requirements/runtime.txt"

  if [[ "$core_baseline_unresolved" -eq 1 || "$modified_core_package" -eq 1 ]]; then
    mkdir -p "${scaffold_dir}/packages"
    copy_tree_exact "$source_core_dir" "${scaffold_dir}/packages/core_repo_snapshot"
  fi

  for agent_dir in "${custom_agent_dirs[@]}"; do
    copy_tree_exact "$agent_dir" "${scaffold_dir}/agents/custom/$(basename "$agent_dir")"
  done

  for agent_dir in "${modified_core_agent_dirs[@]}"; do
    copy_tree_exact "$agent_dir" "${scaffold_dir}/agents/modified_core/$(basename "$agent_dir")"
  done

  if [[ "${#custom_tool_files[@]}" -gt 0 || "${#custom_tool_dirs[@]}" -gt 0 ]]; then
    mkdir -p "$custom_tools_target_dir"
    copy_tree_contents "$custom_tools_source_dir" "$custom_tools_target_dir"
  fi

  if [[ "${#non_package_extra_dirs[@]}" -gt 0 ]]; then
    mkdir -p "${scaffold_dir}/packages"
    for dir_path in "${non_package_extra_dirs[@]}"; do
      copy_tree_exact "$dir_path" "${scaffold_dir}/packages/$(basename "$dir_path")"
    done
  fi

  write_legacy_local_readme "$scaffold_dir"
  write_legacy_local_package_template "$scaffold_dir"
  if [[ "${#custom_tool_files[@]}" -gt 0 ]]; then
    write_legacy_local_tool_template "$scaffold_dir"
  fi
}

print_summary() {
  local runtime_config_dir="$1"
  local runtime_packages_dir="$2"
  local runtime_state_dir="$3"
  local pdf_storage_dir="$4"
  local file_outputs_dir="$5"
  local weaviate_data_dir="$6"
  local status="ready"
  local next_step="Standalone migration inputs are ready."

  if has_custom_code; then
    status="manual_review_required"
    next_step="Review ${install_home_dir}/migration/legacy_local before switching to docker-compose.production.yml."
  fi

  echo
  log_info "Migration summary"
  printf '  - mode: %s\n' "$mode"
  printf '  - source repo: %s\n' "$source_repo"
  printf '  - install home: %s\n' "$install_home_dir"
  printf '  - runtime config: %s\n' "$runtime_config_dir"
  printf '  - runtime packages: %s\n' "$runtime_packages_dir"
  printf '  - runtime state: %s\n' "$runtime_state_dir"
  printf '  - pdf storage: %s\n' "$pdf_storage_dir"
  printf '  - file outputs: %s\n' "$file_outputs_dir"
  printf '  - weaviate data: %s\n' "$weaviate_data_dir"
  printf '  - extra migrated packages: %s\n' "${#extra_package_dirs[@]}"
  printf '  - shipped core baseline unresolved: %s\n' "$core_baseline_unresolved"
  printf '  - modified shipped core package preserved: %s\n' "$modified_core_package"
  printf '  - custom agents preserved: %s\n' "${#custom_agent_dirs[@]}"
  printf '  - modified shipped agents preserved: %s\n' "${#modified_core_agent_dirs[@]}"
  printf '  - custom tool files preserved: %s\n' "${#custom_tool_files[@]}"
  printf '  - extra non-package dirs preserved: %s\n' "${#non_package_extra_dirs[@]}"
  printf '  - next step: %s\n' "$next_step"
  printf 'MIGRATION_STATUS=%s\n' "$status"
}

main() {
  trap '[[ -n "$helper_canonical_core_dir_temp_root" ]] && rm -rf "$helper_canonical_core_dir_temp_root"; [[ -n "$canonical_core_dir_temp_root" ]] && rm -rf "$canonical_core_dir_temp_root"' EXIT

  parse_args "$@"

  require_directory_exists "$source_repo"
  source_repo="$(cd "$source_repo" && pwd)"
  require_directory_exists "${source_repo}/config"
  require_directory_exists "${source_repo}/packages"
  require_directory_exists "${source_repo}/packages/core"
  require_file_exists "${source_repo}/packages/core/package.yaml"

  local runtime_root_dir
  local runtime_config_dir
  local runtime_packages_dir
  local runtime_state_dir
  local data_root_dir
  local pdf_storage_dir
  local file_outputs_dir
  local weaviate_data_dir

  runtime_root_dir="$(install_runtime_root_dir "$install_home_dir")"
  runtime_config_dir="$(install_runtime_config_dir "$install_home_dir")"
  runtime_packages_dir="$(install_runtime_packages_dir "$install_home_dir")"
  runtime_state_dir="$(install_runtime_state_dir "$install_home_dir")"
  data_root_dir="$(install_data_root_dir "$install_home_dir")"
  pdf_storage_dir="$(install_pdf_storage_dir "$install_home_dir")"
  file_outputs_dir="$(install_file_outputs_dir "$install_home_dir")"
  weaviate_data_dir="$(install_weaviate_data_dir "$install_home_dir")"

  echo
  log_info "Repo install migration helper"
  printf '  Source repo: %s\n' "$source_repo"
  printf '  Install home: %s\n' "$install_home_dir"
  printf '  Mode: %s\n' "$mode"

  record_source_scan

  if [[ "$apply_mode" -eq 1 ]]; then
    mkdir -p "$install_home_dir" "$runtime_root_dir" "$data_root_dir"
  fi

  copy_runtime_config "$runtime_config_dir"
  copy_runtime_packages "$runtime_packages_dir"
  copy_runtime_state "$runtime_state_dir"
  copy_data_dirs "$pdf_storage_dir" "$file_outputs_dir" "$weaviate_data_dir"
  patch_installed_env \
    "$runtime_config_dir" \
    "$runtime_packages_dir" \
    "$runtime_state_dir" \
    "$pdf_storage_dir" \
    "$file_outputs_dir" \
    "$weaviate_data_dir"

  if has_custom_code; then
    create_legacy_local_scaffold
  fi

  print_summary \
    "$runtime_config_dir" \
    "$runtime_packages_dir" \
    "$runtime_state_dir" \
    "$pdf_storage_dir" \
    "$file_outputs_dir" \
    "$weaviate_data_dir"

  if has_custom_code && [[ "$apply_mode" -eq 1 ]]; then
    log_warn "Manual review is required before you can safely complete the standalone upgrade."
    exit "$EXIT_MANUAL_REVIEW_REQUIRED"
  fi
}

main "$@"
