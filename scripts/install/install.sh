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

print_usage() {
  cat <<USAGE
Usage: scripts/install/install.sh [options]

Options:
  --skip-preflight     Skip Stage 1 (01_preflight.sh)
  --skip-core-config   Skip Stage 2 (02_core_config.sh)
  --skip-auth-setup    Skip Stage 3 (03_auth_setup.sh)
  --skip-group-setup   Skip Stage 4 (04_group_setup.sh)
  --skip-pdfx-setup    Skip Stage 5 (05_pdfx_setup.sh)
  --skip-start-verify  Skip Stage 6 (06_start_verify.sh)
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
      --skip-start-verify)
        skip_start_verify=1
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
  printf "    ${green}Stage 4${reset}  Group mapping        -- MOD curator group assignments\n"
  printf "    ${green}Stage 5${reset}  PDF extraction       -- PDF processing service config\n"
  printf "    ${green}Stage 6${reset}  Start & verify       -- docker compose up + health checks\n"
  printf "\n"
  printf "${bold}  Prerequisites:${reset}\n"
  printf "\n"
  printf "    - Docker & Docker Compose (v2)\n"
  printf "    - At least one LLM API key (OpenAI, Anthropic, Gemini, or Groq)\n"
  printf "    - ~8 GiB RAM recommended, ~10 GiB free disk minimum\n"
  printf "\n"
  printf "${yellow}  Tip: re-run with --skip-<stage> flags to skip completed stages.${reset}\n"
  printf "${dim}  Run with --help for all options.${reset}\n"
  printf "\n"

  # Pause so the user can read before output scrolls
  if [[ -t 0 ]]; then
    read -r -p "  Press Enter to continue (or Ctrl-C to abort)... "
    printf "\n"
  fi
}

main() {
  parse_args "$@"
  print_welcome

  local stage1="${script_dir}/01_preflight.sh"
  local stage2="${script_dir}/02_core_config.sh"
  local stage3="${script_dir}/03_auth_setup.sh"
  local stage4="${script_dir}/04_group_setup.sh"
  local stage5="${script_dir}/05_pdfx_setup.sh"
  local stage6="${script_dir}/06_start_verify.sh"

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

  if (( skip_start_verify == 0 )); then
    run_stage "Stage 6 - Start and verify services" "$stage6"
  else
    log_warn "Skipping Stage 6 - Start and verify services"
  fi

  log_success "Installer completed"
}

main "$@"
