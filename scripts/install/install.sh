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
skip_start_verify=0
from_stage=0
image_tag=""
package_profile=""

print_usage() {
  cat <<USAGE
Usage: scripts/install/install.sh [options]

Options:
  --from-stage N       Start from stage N (1-6), skipping all earlier stages
  --image-tag TAG      Override backend/frontend/trace-review image tags in Stage 2
  --package-profile P  Package profile for Stage 2: core-only (default) or core-plus-alliance
  --skip-preflight     Skip Stage 1 (01_preflight.sh)
  --skip-core-config   Skip Stage 2 (02_core_config.sh)
  --skip-auth-setup    Skip Stage 3 (03_auth_setup.sh)
  --skip-group-setup   Skip Stage 4 (04_group_setup.sh)
  --skip-pdfx-setup    Skip Stage 5 (05_pdfx_setup.sh)
  --skip-start-verify  Skip Stage 6 (06_start_verify.sh)
  -h, --help           Show this help message

Examples:
  scripts/install/install.sh                  # Run all stages
  scripts/install/install.sh --image-tag v0.3.0  # Pin all published app images to one tag
  scripts/install/install.sh --package-profile core-plus-alliance
  scripts/install/install.sh --from-stage 5   # Re-run from PDF extraction onward
  scripts/install/install.sh --from-stage 6   # Just start & verify
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

maybe_run_stage() {
  local stage_num="$1"
  local stage_label="$2"
  local stage_script="$3"
  local skip="$4"

  if (( skip == 0 )); then
    run_stage "$stage_label" "$stage_script"
  elif (( from_stage == 0 )); then
    # Only show individual skip warnings when using --skip-* flags,
    # not when using --from-stage (the banner already explains it)
    log_warn "Skipping ${stage_label}"
  fi
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
      --skip-start-verify)
        skip_start_verify=1
        ;;
      --from-stage)
        shift
        if [[ -z "${1:-}" ]] || ! [[ "$1" =~ ^[1-6]$ ]]; then
          log_error "--from-stage requires a number between 1 and 6"
          print_usage
          exit 1
        fi
        from_stage="$1"
        ;;
      --image-tag)
        shift
        if [[ -z "${1:-}" ]]; then
          log_error "--image-tag requires a non-empty tag value"
          print_usage
          exit 1
        fi
        image_tag="$1"
        ;;
      --package-profile)
        shift
        if [[ -z "${1:-}" ]]; then
          log_error "--package-profile requires a value: core-only or core-plus-alliance"
          print_usage
          exit 1
        fi
        if ! package_profile="$(normalize_install_package_profile "$1")"; then
          log_error "Unsupported package profile: $1"
          print_usage
          exit 1
        fi
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

print_welcome() {
  local cyan='\033[0;36m'
  local green='\033[0;32m'
  local yellow='\033[1;33m'
  local bold='\033[1m'
  local dim='\033[2m'
  local reset='\033[0m'
  local package_profile_label=""

  if ! supports_color; then
    cyan="" green="" yellow="" bold="" dim="" reset=""
  fi

  cat <<'BANNER'

     _    ___    ____                _   _
    / \  |_ _|  / ___|   _ _ __ __ _| |_(_) ___  _ __
   / _ \  | |  | |  | | | | '__/ _` | __| |/ _ \| '_ \
  / ___ \ | |  | |__| |_| | | | (_| | |_| | (_) | | | |
 /_/   \_\___|  \____\__,_|_|  \__,_|\__|_|\___/|_| |_|

BANNER

  printf "${bold}  Alliance of Genome Resources -- AI Curation Platform${reset}\n"
  printf "${dim}  https://github.com/alliance-genome/agr_ai_curation${reset}\n"
  printf "\n"
  printf "${cyan}  This installer will set up a standalone instance of the${reset}\n"
  printf "${cyan}  AI Curation system on this machine.${reset}\n"
  printf "\n"
  printf "${bold}  What it does:${reset}\n"
  printf "\n"
  printf "    ${green}Stage 1${reset}  Preflight checks    -- Docker, disk space, ports, memory\n"
  printf "    ${green}Stage 2${reset}  Core configuration  -- .env file, API keys, database secrets\n"
  printf "    ${green}Stage 3${reset}  Auth setup           -- OIDC / authentication provider\n"
  printf "    ${green}Stage 4${reset}  Group mapping        -- curator group assignments\n"
  printf "    ${green}Stage 5${reset}  PDF extraction       -- PDF processing service config\n"
  printf "    ${green}Stage 6${reset}  Start & verify       -- docker compose up + health checks\n"
  printf "\n"
  printf "${bold}  Prerequisites:${reset}\n"
  printf "\n"
  printf "    - Docker & Docker Compose (v2)\n"
  printf "    - OpenAI API key (required for embeddings)\n"
  printf "    - Optional: Anthropic, Gemini, or Groq API keys for additional models\n"
  printf "    - ~8 GiB RAM recommended, ~10 GiB free disk minimum\n"
  printf "\n"
  if [[ -n "$image_tag" ]]; then
    printf "${yellow}  Published image tag override: %s${reset}\n" "$image_tag"
    printf "\n"
  fi
  if [[ -n "$package_profile" ]]; then
    package_profile_label="$(install_package_profile_label "$package_profile")"
    printf "${yellow}  Package profile override: %s${reset}\n" "$package_profile_label"
    printf "\n"
  fi
  if (( from_stage > 1 )); then
    local red='\033[1;31m'
    if ! supports_color; then red=""; fi
    local stage_names=("" "Preflight" "Core config" "Auth setup" "Group setup" "PDF extraction" "Start & verify")
    printf "${red}  NOTE: Starting from Stage %d (%s) -- stages 1-%d skipped${reset}\n" \
      "$from_stage" "${stage_names[$from_stage]}" "$((from_stage - 1))"
    printf "\n"
  else
    printf "${yellow}  Tip: re-run with --from-stage N to resume from a specific stage.${reset}\n"
    printf "${dim}  Run with --help for all options.${reset}\n"
    printf "\n"

    # Pause so the user can read before output scrolls
    if [[ -t 0 ]]; then
      read -r -p "  Press Enter to continue (or Ctrl-C to abort)... "
      printf "\n"
    fi
  fi
}

apply_from_stage() {
  if (( from_stage >= 2 )); then skip_preflight=1; fi
  if (( from_stage >= 3 )); then skip_core_config=1; fi
  if (( from_stage >= 4 )); then skip_auth_setup=1; fi
  if (( from_stage >= 5 )); then skip_group_setup=1; fi
  if (( from_stage >= 6 )); then skip_pdfx_setup=1; fi
}

main() {
  parse_args "$@"
  if [[ -n "$image_tag" ]]; then
    export INSTALL_IMAGE_TAG="$image_tag"
  fi
  if [[ -n "$package_profile" ]]; then
    export INSTALL_PACKAGE_PROFILE="$package_profile"
  fi
  if (( from_stage > 0 )); then
    apply_from_stage
  fi
  if [[ -n "$package_profile" ]] && (( skip_core_config == 1 )); then
    log_warn "Package profile selection only applies when Stage 2 runs; this invocation skips Stage 2."
  fi
  print_welcome

  local stage1="${script_dir}/01_preflight.sh"
  local stage2="${script_dir}/02_core_config.sh"
  local stage3="${script_dir}/03_auth_setup.sh"
  local stage4="${script_dir}/04_group_setup.sh"
  local stage5="${script_dir}/05_pdfx_setup.sh"
  local stage6="${script_dir}/06_start_verify.sh"

  maybe_run_stage 1 "Stage 1 - Preflight" "$stage1" "$skip_preflight"
  maybe_run_stage 2 "Stage 2 - Core config" "$stage2" "$skip_core_config"
  maybe_run_stage 3 "Stage 3 - Auth setup" "$stage3" "$skip_auth_setup"
  maybe_run_stage 4 "Stage 4 - Group setup" "$stage4" "$skip_group_setup"
  maybe_run_stage 5 "Stage 5 - PDF extraction setup" "$stage5" "$skip_pdfx_setup"
  maybe_run_stage 6 "Stage 6 - Start and verify services" "$stage6" "$skip_start_verify"

  log_success "Installer completed"
}

main "$@"
