#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
# shellcheck source=scripts/install/lib/common.sh
source "${repo_root}/scripts/install/lib/common.sh"

install_home_dir="${INSTALL_HOME_DIR:-${HOME}/.agr_ai_curation}"
env_output_path="${INSTALL_ENV_PATH:-${install_home_dir}/.env}"
pdfx_state_path="${INSTALL_PDFX_STATE_PATH:-${install_home_dir}/.install_pdfx.env}"
git_cmd="${INSTALL_GIT_CMD:-git}"
pdfx_repo_url="${INSTALL_PDFX_REPO_URL:-https://github.com/alliance-genome/agr_pdf_extraction_service.git}"
default_clone_path="${INSTALL_PDFX_CLONE_PATH_DEFAULT:-${repo_root}/../agr_pdf_extraction_service}"
PDFX_SKIP_CLONE="false"

validate_port_number() {
  local port="$1"
  [[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 ))
}

resolve_pdfx_port() {
  local port="5000"
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
      if prompt_yes_no "Wipe and re-clone?" "no" >&2; then
        rm -rf "$clone_path"
      else
        if prompt_yes_no "Reuse existing directory as-is? (only the .env will be regenerated)" "yes" >&2; then
          PDFX_SKIP_CLONE="true"
        else
          continue
        fi
      fi
    fi

    printf '%s\n' "$clone_path"
    return 0
  done
}

prompt_extractor_methods() {
  local use_grobid="y"
  local use_docling="n"
  local use_marker="n"
  local methods=""

  echo "Select which PDF extractors to enable." >&2
  echo "You can enable any combination. At least one is required." >&2
  echo >&2

  while true; do
    read -r -p "  Enable GROBID?  (CPU-friendly, fast, good quality) [Y/n]: " use_grobid
    use_grobid="${use_grobid:-y}"
    case "$use_grobid" in [yYnN]) break ;; *) log_warn "Please enter y or n." >&2 ;; esac
  done

  while true; do
    read -r -p "  Enable Docling? (CPU-friendly, good table extraction) [y/N]: " use_docling
    use_docling="${use_docling:-n}"
    case "$use_docling" in [yYnN]) break ;; *) log_warn "Please enter y or n." >&2 ;; esac
  done

  while true; do
    read -r -p "  Enable Marker?  (GPU-intensive, high quality) [y/N]: " use_marker
    use_marker="${use_marker:-n}"
    case "$use_marker" in [yYnN]) break ;; *) log_warn "Please enter y or n." >&2 ;; esac
  done

  methods=""
  [[ "$use_grobid" == [yY] ]] && methods="grobid"
  [[ "$use_docling" == [yY] ]] && methods="${methods:+${methods},}docling"
  [[ "$use_marker" == [yY] ]] && methods="${methods:+${methods},}marker"

  if [[ -z "$methods" ]]; then
    log_warn "At least one extractor is required. Defaulting to GROBID." >&2
    methods="grobid"
  fi

  printf '%s\n' "$methods"
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

write_pdfx_state() {
  local clone_path="$1"
  local port="$2"
  local gpu="$3"

  cat >"$pdfx_state_path" <<STATE
INSTALL_PDFX_CLONE_PATH=${clone_path}
INSTALL_PDFX_PORT=${port}
INSTALL_PDFX_GPU_ENABLED=${gpu}
STATE
  chmod 600 "$pdfx_state_path"
}

main() {
  require_file_exists "$env_output_path"
  require_command "$git_cmd"

  echo
  log_info "=== Stage 5 of 6: PDF Extraction Setup ==="
  echo
  echo "  The PDF extraction service lets curators upload research papers and"
  echo "  have them automatically parsed into text that the AI agents can read."
  echo
  echo "  This is a separate service (agr_pdf_extraction_service) that will be"
  echo "  cloned alongside this repository and run its own Docker containers."
  echo
  echo "  Three extractors are available (enable any combination):"
  echo
  echo "    GROBID  -- CPU-friendly, fast, good quality. Recommended default."
  echo "    Docling -- CPU-friendly, good table/figure extraction."
  echo "    Marker  -- Higher quality, but GPU-intensive (needs CUDA)."
  echo
  echo "  When two or more extractors are enabled, an LLM-based consensus"
  echo "  merge can combine their outputs for the best overall quality."
  echo
  echo "  You can change extractor settings later by editing the .env files"
  echo "  in both repositories and restarting the services."
  echo
  echo "  If you skip this stage, everything else works -- curators just won't"
  echo "  be able to upload PDFs directly."
  echo

  if ! prompt_yes_no "Install PDF extraction service? (enables document upload)" "yes"; then
    backup_file_with_timestamp "$env_output_path"
    remove_pdf_env_vars_from_main_env "$env_output_path"
    rm -f "$pdfx_state_path"
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

  if prompt_yes_no "GPU available? (enables CUDA for Marker/Docling if selected)" "no"; then
    gpu_available="true"
  fi

  if [[ "$gpu_available" == "true" ]]; then
    if [[ "$methods" == *"marker"* ]]; then
      marker_device="auto"
    fi
    if [[ "$methods" == *"docling"* ]]; then
      docling_device="cuda"
    fi
  fi

  if [[ "$methods" == *","* ]]; then
    if prompt_yes_no "Enable LLM merge? (combines outputs from multiple extractors, uses same OpenAI key)" "yes"; then
      merge_enabled="true"
      consensus_enabled="true"
    fi
  fi

  if [[ "$PDFX_SKIP_CLONE" == "true" ]]; then
    log_info "Reusing existing PDF extraction service at ${clone_path}"
  else
    log_info "Cloning PDF extraction service into ${clone_path}"
    "$git_cmd" clone "$pdfx_repo_url" "$clone_path"
  fi

  # Detect system resources and set PDFX worker limits
  local total_cpus
  local total_mem_gb
  local worker_cpus
  local worker_mem

  total_cpus="$(nproc 2>/dev/null || echo 4)"
  total_mem_gb="$(awk '/^MemTotal:/ { printf "%d", $2 / 1048576 }' /proc/meminfo 2>/dev/null || echo 16)"

  # Reserve 1 CPU for the OS and other containers; minimum 1 for the worker
  worker_cpus="$(( total_cpus > 2 ? total_cpus - 1 : total_cpus ))"
  if (( worker_cpus < 1 )); then worker_cpus=1; fi

  # Give the worker ~half the system RAM (other containers need some too)
  worker_mem="$(( total_mem_gb / 2 ))"
  if (( worker_mem < 2 )); then worker_mem=2; fi

  log_info "Detected ${total_cpus} CPUs and ${total_mem_gb}GB RAM"
  log_info "PDFX worker limits: ${worker_cpus} CPUs, ${worker_mem}GB RAM"

  main_openai_key="$(read_env_value "$env_output_path" "OPENAI_API_KEY")"
  if [[ -z "$main_openai_key" ]]; then
    log_warn "OPENAI_API_KEY is empty in main .env; PDFX merge will fail unless key is added later." >&2
  fi

  local pdfx_env_path="${clone_path}/.env"
  (
    umask 077
    cat >"$pdfx_env_path" <<EOF
# Generated by agr_ai_curation installer Stage 5
OPENAI_API_KEY=${main_openai_key}
DOCLING_DEVICE=${docling_device}
MARKER_DEVICE=${marker_device}
CONSENSUS_ENABLED=${consensus_enabled}
PDFX_SELECTED_METHODS=${methods}
PDFX_DEFAULT_MERGE=${merge_enabled}
PDFX_GPU_ENABLED=${gpu_available}
PDFX_WORKER_CPUS=${worker_cpus}.0
PDFX_WORKER_MEM_LIMIT=${worker_mem}g
EOF
  )
  chmod 600 "$pdfx_env_path"

  backup_file_with_timestamp "$env_output_path"
  upsert_env_var "$env_output_path" "PDF_EXTRACTION_SERVICE_URL" "http://localhost:${pdfx_port}"
  upsert_env_var "$env_output_path" "PDF_EXTRACTION_METHODS" "$methods"
  upsert_env_var "$env_output_path" "PDF_EXTRACTION_MERGE" "$merge_enabled"
  chmod 600 "$env_output_path"
  write_pdfx_state "$clone_path" "$pdfx_port" "$gpu_available"

  log_success "PDF extraction service cloned to ${clone_path}"
  log_success "Generated PDFX config at ${pdfx_env_path}"
  log_success "Saved PDFX state to ${pdfx_state_path}"
  log_success "Main .env updated with PDF extraction service settings"
}

main "$@"
