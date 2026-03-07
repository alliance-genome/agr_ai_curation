#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
# shellcheck source=scripts/install/lib/common.sh
source "${repo_root}/scripts/install/lib/common.sh"

skip_preflight=0
skip_core_config=0
skip_auth_setup=0
skip_group_setup=0
skip_pdfx_setup=0

print_usage() {
  cat <<USAGE
Usage: scripts/install/install.sh [options]

Options:
  --skip-preflight     Skip Stage 1 (01_preflight.sh)
  --skip-core-config   Skip Stage 2 (02_core_config.sh)
  --skip-auth-setup    Skip Stage 3 (03_auth_setup.sh)
  --skip-group-setup   Skip Stage 4 (04_group_setup.sh)
  --skip-pdfx-setup    Skip Stage 5 (05_pdfx_setup.sh)
  -h, --help           Show this help message
USAGE
}

run_stage() {
  local stage_label="$1"
  local stage_script="$2"

  require_file_exists "$stage_script"
  log_info "Running ${stage_label}"
  bash "$stage_script"
  log_success "Completed ${stage_label}"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --skip-preflight)
        skip_preflight=1
        ;;
      --skip-core-config)
        skip_core_config=1
        ;;
      --skip-auth-setup)
        skip_auth_setup=1
        ;;
      --skip-group-setup)
        skip_group_setup=1
        ;;
      --skip-pdfx-setup)
        skip_pdfx_setup=1
        ;;
      -h|--help)
        print_usage
        exit 0
        ;;
      *)
        log_error "Unknown option: $1"
        print_usage
        exit 1
        ;;
    esac
    shift
  done
}

main() {
  parse_args "$@"

  local stage1="${script_dir}/01_preflight.sh"
  local stage2="${script_dir}/02_core_config.sh"
  local stage3="${script_dir}/03_auth_setup.sh"
  local stage4="${script_dir}/04_group_setup.sh"
  local stage5="${script_dir}/05_pdfx_setup.sh"

  if (( skip_preflight == 0 )); then
    run_stage "Stage 1 - Preflight" "$stage1"
  else
    log_warn "Skipping Stage 1 - Preflight"
  fi

  if (( skip_core_config == 0 )); then
    run_stage "Stage 2 - Core config" "$stage2"
  else
    log_warn "Skipping Stage 2 - Core config"
  fi

  if (( skip_auth_setup == 0 )); then
    run_stage "Stage 3 - Auth setup" "$stage3"
  else
    log_warn "Skipping Stage 3 - Auth setup"
  fi

  if (( skip_group_setup == 0 )); then
    run_stage "Stage 4 - Group setup" "$stage4"
  else
    log_warn "Skipping Stage 4 - Group setup"
  fi

  if (( skip_pdfx_setup == 0 )); then
    run_stage "Stage 5 - PDF extraction setup" "$stage5"
  else
    log_warn "Skipping Stage 5 - PDF extraction setup"
  fi

  log_success "Installer completed"
}

main "$@"
