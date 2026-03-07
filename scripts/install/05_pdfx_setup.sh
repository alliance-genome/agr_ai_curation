#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
# shellcheck source=scripts/install/lib/common.sh
source "${repo_root}/scripts/install/lib/common.sh"

install_home_dir="${INSTALL_HOME_DIR:-${HOME}/.agr_ai_curation}"
env_output_path="${INSTALL_ENV_PATH:-${install_home_dir}/.env}"
git_cmd="${INSTALL_GIT_CMD:-git}"
pdfx_repo_url="${INSTALL_PDFX_REPO_URL:-https://github.com/alliance-genome/agr_pdf_extraction_service.git}"
default_clone_path="${INSTALL_PDFX_CLONE_PATH_DEFAULT:-${repo_root}/../agr_pdf_extraction_service}"

validate_port_number() {
  local port="$1"
  [[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 ))
}

resolve_pdfx_port() {
  local port="8501"
  local owner=""
  local response=""

  if ! has_port_probe_command; then
    log_error "Port checks require either '${INSTALL_LSOF_CMD:-lsof}' or '${INSTALL_SS_CMD:-ss}'."
    exit 1
  fi

  while true; do
    owner="$(find_listening_port_owner "$port" || true)"
    if [[ -z "$owner" ]]; then
      printf '%s\n' "$port"
      return 0
    fi

    log_warn "Port ${port} is in use by ${owner}." >&2
    read -r -p "Enter an alternative PDF extraction service port: " response

    if ! validate_port_number "$response"; then
      log_warn "Please enter a valid TCP port (1-65535)." >&2
      continue
    fi

    port="$response"
  done
}

resolve_clone_path() {
  local response=""
  local clone_path=""
  local parent_dir=""

  while true; do
    read -r -p "PDF extraction service clone path (default ${default_clone_path}): " response
    clone_path="${response:-$default_clone_path}"
    parent_dir="$(dirname "$clone_path")"

    if [[ ! -d "$parent_dir" ]]; then
      log_warn "Parent directory does not exist: ${parent_dir}" >&2
      continue
    fi

    if [[ ! -w "$parent_dir" ]]; then
      log_warn "Parent directory is not writable: ${parent_dir}" >&2
      continue
    fi

    if [[ -e "$clone_path" && ! -d "$clone_path" ]]; then
      log_warn "Clone path exists and is not a directory: ${clone_path}" >&2
      continue
    fi

    if [[ -d "$clone_path" ]] && [[ -n "$(ls -A "$clone_path")" ]]; then
      log_warn "Clone path already exists and is not empty: ${clone_path}" >&2
      continue
    fi

    printf '%s\n' "$clone_path"
    return 0
  done
}

prompt_extractor_methods() {
  local response=""

  while true; do
    echo "Extractor selection:" >&2
    echo "  1) GROBID only (CPU-friendly, recommended)" >&2
    echo "  2) Marker only (GPU-intensive)" >&2
    echo "  3) Both GROBID and Marker (best quality)" >&2
    read -r -p "Choose extractor mode (default 1): " response
    response="${response:-1}"

    case "$response" in
      1)
        printf 'grobid\n'
        return 0
        ;;
      2)
        printf 'marker\n'
        return 0
        ;;
      3)
        printf 'grobid,marker\n'
        return 0
        ;;
      *)
        log_warn "Please choose 1, 2, or 3." >&2
        ;;
    esac
  done
}

read_env_value() {
  local env_file="$1"
  local key="$2"

  awk -v key="$key" '
    index($0, key "=") == 1 {
      sub("^[^=]*=", "", $0)
      print
      exit
    }
  ' "$env_file"
}

remove_pdf_env_vars_from_main_env() {
  local env_file="$1"
  remove_env_var "$env_file" "PDF_EXTRACTION_SERVICE_URL"
  remove_env_var "$env_file" "PDF_EXTRACTION_METHODS"
  remove_env_var "$env_file" "PDF_EXTRACTION_MERGE"
}

main() {
  require_file_exists "$env_output_path"
  require_command "$git_cmd"

  log_info "Stage 5: PDF extraction setup"

  if ! prompt_yes_no "Install PDF extraction service? (enables document upload)" "yes"; then
    backup_file_with_timestamp "$env_output_path"
    remove_pdf_env_vars_from_main_env "$env_output_path"
    chmod 600 "$env_output_path"
    log_success "Skipped PDF extraction setup. Main .env PDF extraction keys removed."
    exit 0
  fi

  local pdfx_port
  local clone_path
  local methods
  local gpu_available="false"
  local merge_enabled="false"
  local marker_device="cpu"
  local docling_device="cpu"
  local consensus_enabled="false"
  local main_openai_key=""

  pdfx_port="$(resolve_pdfx_port)"
  clone_path="$(resolve_clone_path)"
  methods="$(prompt_extractor_methods)"

  if prompt_yes_no "GPU available? (enables CUDA if yes + Marker selected)" "no"; then
    gpu_available="true"
  fi

  if [[ "$methods" == *"marker"* ]]; then
    if [[ "$gpu_available" == "true" ]]; then
      marker_device="auto"
      docling_device="cuda"
    fi
  fi

  if [[ "$methods" == "grobid,marker" ]]; then
    if prompt_yes_no "Enable LLM merge? (uses same OpenAI key)" "yes"; then
      merge_enabled="true"
      consensus_enabled="true"
    fi
  fi

  log_info "Cloning PDF extraction service into ${clone_path}"
  "$git_cmd" clone "$pdfx_repo_url" "$clone_path"

  main_openai_key="$(read_env_value "$env_output_path" "OPENAI_API_KEY")"
  if [[ -z "$main_openai_key" ]]; then
    log_warn "OPENAI_API_KEY is empty in main .env; PDFX merge will fail unless key is added later." >&2
  fi

  local pdfx_env_path="${clone_path}/.env"
  cat >"$pdfx_env_path" <<EOF
# Generated by agr_ai_curation installer Stage 5
OPENAI_API_KEY=${main_openai_key}
DOCLING_DEVICE=${docling_device}
MARKER_DEVICE=${marker_device}
CONSENSUS_ENABLED=${consensus_enabled}
PDFX_SELECTED_METHODS=${methods}
PDFX_DEFAULT_MERGE=${merge_enabled}
PDFX_GPU_ENABLED=${gpu_available}
EOF
  chmod 600 "$pdfx_env_path"

  backup_file_with_timestamp "$env_output_path"
  upsert_env_var "$env_output_path" "PDF_EXTRACTION_SERVICE_URL" "http://localhost:${pdfx_port}"
  upsert_env_var "$env_output_path" "PDF_EXTRACTION_METHODS" "$methods"
  upsert_env_var "$env_output_path" "PDF_EXTRACTION_MERGE" "$merge_enabled"
  chmod 600 "$env_output_path"

  log_success "PDF extraction service cloned to ${clone_path}"
  log_success "Generated PDFX config at ${pdfx_env_path}"
  log_success "Main .env updated with PDF extraction service settings"
}

main "$@"
