#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
helper_script="${repo_root}/scripts/install/migrate_repo_install.sh"

export NO_COLOR=1

assert_contains() {
  local needle="$1"
  local haystack_path="$2"

  if ! grep -Fq "$needle" "$haystack_path"; then
    echo "Expected to find '${needle}' in ${haystack_path}" >&2
    echo "--- file contents ---" >&2
    cat "$haystack_path" >&2
    echo "---------------------" >&2
    exit 1
  fi
}

assert_not_contains() {
  local needle="$1"
  local haystack_path="$2"

  if grep -Fq "$needle" "$haystack_path"; then
    echo "Did not expect to find '${needle}' in ${haystack_path}" >&2
    echo "--- file contents ---" >&2
    cat "$haystack_path" >&2
    echo "---------------------" >&2
    exit 1
  fi
}

assert_file_exists() {
  local file_path="$1"

  if [[ ! -f "$file_path" ]]; then
    echo "Expected file to exist: ${file_path}" >&2
    exit 1
  fi
}

assert_dir_not_exists() {
  local dir_path="$1"

  if [[ -d "$dir_path" ]]; then
    echo "Expected directory to be absent: ${dir_path}" >&2
    exit 1
  fi
}

run_helper() {
  local output_path="$1"
  shift
  local script_path="${MIGRATION_HELPER_SCRIPT:-$helper_script}"

  set +e
  bash "$script_path" "$@" >"$output_path" 2>&1
  local status=$?
  set -e

  printf '%s\n' "$status"
}

write_source_config() {
  local source_repo="$1"

  mkdir -p "${source_repo}/config"
  cat >"${source_repo}/config/groups.yaml" <<'EOF'
identity_provider:
  type: oidc
  group_claim: groups
groups:
  FB:
    name: FlyBase
    provider_groups:
      - flybase-curators
EOF

  cat >"${source_repo}/config/connections.yaml" <<'EOF'
databases:
  primary:
    type: postgresql
    host: localhost
EOF

  cat >"${source_repo}/config/providers.yaml" <<'EOF'
providers:
  openai:
    driver: openai_native
    api_key_env: OPENAI_API_KEY
    api_mode: responses
    default_for_runner: true
EOF

  cat >"${source_repo}/config/models.yaml" <<'EOF'
models:
  - model_id: gpt-test
    name: GPT Test
    provider: openai
EOF

  cat >"${source_repo}/config/tool_policy_defaults.yaml" <<'EOF'
tool_policies:
  demo_tool:
    display_name: Demo Tool
    allow_execute: true
EOF

  cat >"${source_repo}/config/maintenance_message.txt" <<'EOF'
All systems normal.
EOF
}

write_source_data() {
  local source_repo="$1"

  mkdir -p "${source_repo}/pdf_storage/user-1"
  printf 'pdf bytes\n' >"${source_repo}/pdf_storage/user-1/paper.pdf"

  mkdir -p "${source_repo}/file_outputs"
  printf 'gene_id\nGENE:1\n' >"${source_repo}/file_outputs/export.tsv"

  mkdir -p "${source_repo}/weaviate_data"
  printf 'weaviate\n' >"${source_repo}/weaviate_data/segments.db"

  mkdir -p "${source_repo}/runtime_state/identifier_prefixes"
  printf '{"prefixes":["WB"]}\n' >"${source_repo}/runtime_state/identifier_prefixes/identifier_prefixes.json"
}

write_source_env() {
  local source_repo="$1"

  cat >"${source_repo}/.env" <<'EOF'
OPENAI_API_KEY=test-openai-key
AGENTS_CONFIG_PATH=config/agents
GROUPS_CONFIG_PATH=config/groups.yaml
EOF
}

prepare_source_repo() {
  local source_repo="$1"

  mkdir -p "${source_repo}/packages"
  cp -a "${repo_root}/packages/core" "${source_repo}/packages/core"
  write_source_config "$source_repo"
  write_source_data "$source_repo"
  write_source_env "$source_repo"
}

prepare_helper_repo() {
  local helper_repo="$1"

  mkdir -p "${helper_repo}/scripts/install/lib" "${helper_repo}/packages"
  cp -a "${repo_root}/scripts/install/migrate_repo_install.sh" "${helper_repo}/scripts/install/migrate_repo_install.sh"
  cp -a "${repo_root}/scripts/install/lib/common.sh" "${helper_repo}/scripts/install/lib/common.sh"
  cp -a "${repo_root}/packages/core" "${helper_repo}/packages/core"

  git -C "$helper_repo" init -q
  git -C "$helper_repo" config user.name 'Repo Migration Test'
  git -C "$helper_repo" config user.email 'repo-migration-test@example.org'
  git -C "$helper_repo" add scripts/install/migrate_repo_install.sh scripts/install/lib/common.sh packages/core
  git -C "$helper_repo" commit -qm 'baseline helper repo'
}

test_standard_repo_install_apply() {
  local temp_root
  temp_root="$(mktemp -d)"
  trap 'rm -rf "${temp_root}"' RETURN

  local source_repo="${temp_root}/source-standard"
  local install_home="${temp_root}/install-standard"
  local output_path="${temp_root}/standard-apply.out"

  prepare_source_repo "$source_repo"

  local status
  status="$(run_helper "$output_path" --apply --source-repo "$source_repo" --install-home "$install_home")"
  if [[ "$status" != "0" ]]; then
    echo "Expected standard apply migration to exit 0, got ${status}" >&2
    cat "$output_path" >&2
    exit 1
  fi

  assert_contains 'MIGRATION_STATUS=ready' "$output_path"
  assert_contains 'extra migrated packages: 0' "$output_path"
  assert_file_exists "${install_home}/runtime/config/groups.yaml"
  assert_file_exists "${install_home}/runtime/packages/core/package.yaml"
  assert_file_exists "${install_home}/runtime/state/identifier_prefixes/identifier_prefixes.json"
  assert_file_exists "${install_home}/data/pdf_storage/user-1/paper.pdf"
  assert_file_exists "${install_home}/data/file_outputs/export.tsv"
  assert_file_exists "${install_home}/data/weaviate/segments.db"
  assert_file_exists "${install_home}/.env"
  assert_contains "AGR_RUNTIME_CONFIG_HOST_DIR=${install_home}/runtime/config" "${install_home}/.env"
  assert_contains "AGR_RUNTIME_PACKAGES_HOST_DIR=${install_home}/runtime/packages" "${install_home}/.env"
  assert_contains "WEAVIATE_DATA_HOST_DIR=${install_home}/data/weaviate" "${install_home}/.env"
  assert_not_contains 'AGENTS_CONFIG_PATH=' "${install_home}/.env"
  assert_not_contains 'GROUPS_CONFIG_PATH=' "${install_home}/.env"
  assert_dir_not_exists "${install_home}/migration/legacy_local"

  rm -rf "${temp_root}"
  trap - RETURN
}

test_dry_run_allows_missing_data_dirs() {
  local temp_root
  temp_root="$(mktemp -d)"
  trap 'rm -rf "${temp_root}"' RETURN

  local source_repo="${temp_root}/source-missing-data"
  local install_home="${temp_root}/install-missing-data"
  local output_path="${temp_root}/missing-data-dry-run.out"

  prepare_source_repo "$source_repo"
  rm -rf \
    "${source_repo}/pdf_storage" \
    "${source_repo}/file_outputs" \
    "${source_repo}/weaviate_data"

  local status
  status="$(run_helper "$output_path" --dry-run --source-repo "$source_repo" --install-home "$install_home")"
  if [[ "$status" != "0" ]]; then
    echo "Expected missing-data dry-run migration to exit 0, got ${status}" >&2
    cat "$output_path" >&2
    exit 1
  fi

  assert_contains 'MIGRATION_STATUS=ready' "$output_path"
  assert_contains "skipped missing source dir: ${source_repo}/pdf_storage" "$output_path"
  assert_contains "skipped missing source dir: ${source_repo}/file_outputs" "$output_path"
  assert_contains "skipped missing source dir: ${source_repo}/weaviate_data" "$output_path"
  assert_contains 'Installed env migration' "$output_path"
  assert_contains 'Migration summary' "$output_path"
  assert_dir_not_exists "${install_home}/runtime"
  assert_dir_not_exists "${install_home}/data"

  rm -rf "${temp_root}"
  trap - RETURN
}

test_non_git_source_ignores_dirty_helper_core() {
  local temp_root
  temp_root="$(mktemp -d)"
  trap 'rm -rf "${temp_root}"' RETURN

  local helper_repo="${temp_root}/helper-repo"
  local source_repo="${temp_root}/source-clean-copy"
  local install_home="${temp_root}/install-clean-copy"
  local output_path="${temp_root}/non-git-source.out"
  local helper_marker='# helper checkout customization'

  prepare_helper_repo "$helper_repo"
  prepare_source_repo "$source_repo"
  printf '\n%s\n' "$helper_marker" >>"${helper_repo}/packages/core/agents/gene/prompt.yaml"

  local status
  status="$(MIGRATION_HELPER_SCRIPT="${helper_repo}/scripts/install/migrate_repo_install.sh" run_helper "$output_path" --apply --source-repo "$source_repo" --install-home "$install_home")"
  if [[ "$status" != "0" ]]; then
    echo "Expected non-git source migration to exit 0, got ${status}" >&2
    cat "$output_path" >&2
    exit 1
  fi

  assert_contains 'MIGRATION_STATUS=ready' "$output_path"
  assert_contains 'modified shipped core package preserved: 0' "$output_path"
  assert_file_exists "${install_home}/runtime/packages/core/agents/gene/prompt.yaml"
  assert_not_contains "$helper_marker" "${install_home}/runtime/packages/core/agents/gene/prompt.yaml"
  assert_dir_not_exists "${install_home}/migration/legacy_local"

  rm -rf "${temp_root}"
  trap - RETURN
}

test_git_source_without_upstream_can_still_be_ready() {
  local temp_root
  temp_root="$(mktemp -d)"
  trap 'rm -rf "${temp_root}"' RETURN

  local source_repo="${temp_root}/source-clean-no-upstream"
  local install_home="${temp_root}/install-clean-no-upstream"
  local output_path="${temp_root}/clean-no-upstream.out"

  prepare_source_repo "$source_repo"
  git -C "$source_repo" init -q
  git -C "$source_repo" config user.name 'Repo Migration Test'
  git -C "$source_repo" config user.email 'repo-migration-test@example.org'
  git -C "$source_repo" add .
  git -C "$source_repo" commit -qm 'baseline source repo'

  local status
  status="$(run_helper "$output_path" --apply --source-repo "$source_repo" --install-home "$install_home")"
  if [[ "$status" != "0" ]]; then
    echo "Expected clean no-upstream migration to exit 0, got ${status}" >&2
    cat "$output_path" >&2
    exit 1
  fi

  assert_contains 'MIGRATION_STATUS=ready' "$output_path"
  assert_contains 'shipped core baseline unresolved: 0' "$output_path"
  assert_contains 'modified shipped core package preserved: 0' "$output_path"
  assert_file_exists "${install_home}/runtime/packages/core/package.yaml"
  assert_dir_not_exists "${install_home}/migration/legacy_local"

  rm -rf "${temp_root}"
  trap - RETURN
}

test_git_source_without_upstream_reports_manual_review() {
  local temp_root
  temp_root="$(mktemp -d)"
  trap 'rm -rf "${temp_root}"' RETURN

  local source_repo="${temp_root}/source-no-upstream"
  local install_home="${temp_root}/install-no-upstream"
  local dry_run_output="${temp_root}/no-upstream-dry-run.out"
  local apply_output="${temp_root}/no-upstream-apply.out"
  local local_marker='# committed local customization without upstream'

  prepare_source_repo "$source_repo"
  git -C "$source_repo" init -q
  git -C "$source_repo" config user.name 'Repo Migration Test'
  git -C "$source_repo" config user.email 'repo-migration-test@example.org'
  git -C "$source_repo" add .
  git -C "$source_repo" commit -qm 'baseline source repo'

  printf '\n%s\n' "$local_marker" >>"${source_repo}/packages/core/agents/gene/prompt.yaml"
  git -C "$source_repo" add packages/core/agents/gene/prompt.yaml
  git -C "$source_repo" commit -qm 'customized shipped core'

  local dry_run_status
  dry_run_status="$(run_helper "$dry_run_output" --dry-run --source-repo "$source_repo" --install-home "$install_home")"
  if [[ "$dry_run_status" != "0" ]]; then
    echo "Expected no-upstream dry-run migration to exit 0, got ${dry_run_status}" >&2
    cat "$dry_run_output" >&2
    exit 1
  fi

  assert_contains 'MIGRATION_STATUS=manual_review_required' "$dry_run_output"
  assert_contains 'shipped core baseline unresolved: 1' "$dry_run_output"
  assert_contains 'modified shipped core package preserved: 1' "$dry_run_output"

  local apply_status
  apply_status="$(run_helper "$apply_output" --apply --source-repo "$source_repo" --install-home "$install_home")"
  if [[ "$apply_status" != "3" ]]; then
    echo "Expected no-upstream apply migration to exit 3, got ${apply_status}" >&2
    cat "$apply_output" >&2
    exit 1
  fi

  assert_contains 'MIGRATION_STATUS=manual_review_required' "$apply_output"
  assert_contains 'shipped core baseline unresolved: 1' "$apply_output"
  assert_file_exists "${install_home}/runtime/packages/core/agents/gene/prompt.yaml"
  assert_file_exists "${install_home}/migration/legacy_local/packages/core_repo_snapshot/agents/gene/prompt.yaml"
  assert_not_contains "$local_marker" "${install_home}/runtime/packages/core/agents/gene/prompt.yaml"
  assert_contains "$local_marker" "${install_home}/migration/legacy_local/packages/core_repo_snapshot/agents/gene/prompt.yaml"

  rm -rf "${temp_root}"
  trap - RETURN
}

test_modified_core_package_reports_manual_review() {
  local temp_root
  temp_root="$(mktemp -d)"
  trap 'rm -rf "${temp_root}"' RETURN

  local source_repo="${temp_root}/source-modified-core"
  local install_home="${temp_root}/install-modified-core"
  local dry_run_output="${temp_root}/modified-core-dry-run.out"
  local apply_output="${temp_root}/modified-core-apply.out"
  local local_marker='# repo-local core customization'

  prepare_source_repo "$source_repo"
  printf '\n%s\n' "$local_marker" >>"${source_repo}/packages/core/agents/gene/prompt.yaml"

  local dry_run_status
  dry_run_status="$(run_helper "$dry_run_output" --dry-run --source-repo "$source_repo" --install-home "$install_home")"
  if [[ "$dry_run_status" != "0" ]]; then
    echo "Expected modified-core dry-run migration to exit 0, got ${dry_run_status}" >&2
    cat "$dry_run_output" >&2
    exit 1
  fi

  assert_contains 'MIGRATION_STATUS=manual_review_required' "$dry_run_output"
  assert_contains 'modified shipped core package preserved: 1' "$dry_run_output"
  assert_contains 'Legacy local code detected' "$dry_run_output"

  local apply_status
  apply_status="$(run_helper "$apply_output" --apply --source-repo "$source_repo" --install-home "$install_home")"
  if [[ "$apply_status" != "3" ]]; then
    echo "Expected modified-core apply migration to exit 3, got ${apply_status}" >&2
    cat "$apply_output" >&2
    exit 1
  fi

  assert_contains 'MIGRATION_STATUS=manual_review_required' "$apply_output"
  assert_contains 'modified shipped core package preserved: 1' "$apply_output"
  assert_file_exists "${install_home}/runtime/packages/core/agents/gene/prompt.yaml"
  assert_file_exists "${install_home}/migration/legacy_local/packages/core_repo_snapshot/agents/gene/prompt.yaml"
  assert_not_contains "$local_marker" "${install_home}/runtime/packages/core/agents/gene/prompt.yaml"
  assert_contains "$local_marker" "${install_home}/migration/legacy_local/packages/core_repo_snapshot/agents/gene/prompt.yaml"
  assert_contains 'core_repo_snapshot' "${install_home}/migration/legacy_local/README.md"

  rm -rf "${temp_root}"
  trap - RETURN
}

test_custom_repo_install_reports_manual_review() {
  local temp_root
  temp_root="$(mktemp -d)"
  trap 'rm -rf "${temp_root}"' RETURN

  local source_repo="${temp_root}/source-custom"
  local install_home="${temp_root}/install-custom"
  local dry_run_output="${temp_root}/custom-dry-run.out"
  local apply_output="${temp_root}/custom-apply.out"

  prepare_source_repo "$source_repo"

  mkdir -p "${source_repo}/config/agents"
  cp -a "${repo_root}/packages/core/agents/gene" "${source_repo}/config/agents/gene"
  printf '\n# local override\n' >>"${source_repo}/config/agents/gene/prompt.yaml"

  mkdir -p "${source_repo}/config/agents/custom_local"
  cat >"${source_repo}/config/agents/custom_local/agent.yaml" <<'EOF'
agent_id: custom_local
name: Custom Local
tools:
  - save_json_file
EOF
  cat >"${source_repo}/config/agents/custom_local/prompt.yaml" <<'EOF'
system_prompt: |
  You are a custom local agent.
EOF

  mkdir -p "${source_repo}/backend/tools/custom"
  cat >"${source_repo}/backend/tools/custom/my_tool.py" <<'EOF'
from agents import function_tool

@function_tool
def my_tool(query: str) -> dict:
    return {"query": query}
EOF

  mkdir -p "${source_repo}/packages/local_notes"
  printf 'notes\n' >"${source_repo}/packages/local_notes/README.txt"

  local dry_run_status
  dry_run_status="$(run_helper "$dry_run_output" --dry-run --source-repo "$source_repo" --install-home "$install_home")"
  if [[ "$dry_run_status" != "0" ]]; then
    echo "Expected custom dry-run migration to exit 0, got ${dry_run_status}" >&2
    cat "$dry_run_output" >&2
    exit 1
  fi

  assert_contains 'MIGRATION_STATUS=manual_review_required' "$dry_run_output"
  assert_contains 'custom agents preserved: 1' "$dry_run_output"
  assert_contains 'modified shipped agents preserved: 1' "$dry_run_output"
  assert_contains 'custom tool files preserved: 1' "$dry_run_output"
  assert_contains 'extra non-package dirs preserved: 1' "$dry_run_output"
  assert_dir_not_exists "${install_home}/migration/legacy_local"

  local apply_status
  apply_status="$(run_helper "$apply_output" --apply --source-repo "$source_repo" --install-home "$install_home")"
  if [[ "$apply_status" != "3" ]]; then
    echo "Expected custom apply migration to exit 3, got ${apply_status}" >&2
    cat "$apply_output" >&2
    exit 1
  fi

  assert_contains 'MIGRATION_STATUS=manual_review_required' "$apply_output"
  assert_contains 'Manual review is required' "$apply_output"
  assert_file_exists "${install_home}/runtime/packages/core/package.yaml"
  assert_file_exists "${install_home}/migration/legacy_local/README.md"
  assert_file_exists "${install_home}/migration/legacy_local/package.yaml.template"
  assert_file_exists "${install_home}/migration/legacy_local/tools/bindings.yaml.template"
  assert_file_exists "${install_home}/migration/legacy_local/agents/custom/custom_local/agent.yaml"
  assert_file_exists "${install_home}/migration/legacy_local/agents/modified_core/gene/prompt.yaml"
  assert_file_exists "${install_home}/migration/legacy_local/python/src/legacy_local/custom_tools/my_tool.py"
  assert_file_exists "${install_home}/migration/legacy_local/packages/local_notes/README.txt"
  assert_contains 'legacy_local' "${install_home}/migration/legacy_local/README.md"
  assert_contains 'custom_local' "${install_home}/migration/legacy_local/package.yaml.template"

  rm -rf "${temp_root}"
  trap - RETURN
}

test_standard_repo_install_apply
test_dry_run_allows_missing_data_dirs
test_non_git_source_ignores_dirty_helper_core
test_git_source_without_upstream_can_still_be_ready
test_git_source_without_upstream_reports_manual_review
test_modified_core_package_reports_manual_review
test_custom_repo_install_reports_manual_review

echo "repo migration helper checks passed"
