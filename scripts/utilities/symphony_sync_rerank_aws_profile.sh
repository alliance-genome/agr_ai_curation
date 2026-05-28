#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: symphony_sync_rerank_aws_profile.sh [--source-dir DIR] [--dest-dir DIR] [--vm NAME] [--project NAME]

Copies the dedicated Bedrock rerank AWS profile files into the Symphony Incus VM.
The source directory must contain AWS CLI-compatible `credentials` and `config`
files. Secret file contents are never printed.

Environment defaults:
  AI_CURATION_RERANK_AWS_SECRET_DIR  Host source directory
  SYMPHONY_INCUS_VM                  Incus VM name (default: symphony-main)
  SYMPHONY_INCUS_PROJECT             Incus project (default: default)
EOF
}

source_dir="${AI_CURATION_RERANK_AWS_SECRET_DIR:-${HOME}/.symphony/secrets/agr_ai_curation/aws-rerank}"
dest_dir="/home/ctabone/.symphony/secrets/agr_ai_curation/aws-rerank"
vm_name="${SYMPHONY_INCUS_VM:-symphony-main}"
project="${SYMPHONY_INCUS_PROJECT:-default}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir)
      source_dir="${2:-}"
      shift 2
      ;;
    --dest-dir)
      dest_dir="${2:-}"
      shift 2
      ;;
    --vm)
      vm_name="${2:-}"
      shift 2
      ;;
    --project)
      project="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${source_dir}" || -z "${dest_dir}" || -z "${vm_name}" || -z "${project}" ]]; then
  echo "source-dir, dest-dir, vm, and project must be non-empty." >&2
  exit 2
fi

credentials_file="${source_dir}/credentials"
config_file="${source_dir}/config"

if [[ ! -f "${credentials_file}" ]]; then
  echo "Missing credentials file: ${credentials_file}" >&2
  exit 1
fi

if [[ ! -f "${config_file}" ]]; then
  echo "Missing config file: ${config_file}" >&2
  exit 1
fi

echo "Syncing Bedrock rerank AWS profile files into ${vm_name}:${dest_dir}"

incus --project "${project}" exec "${vm_name}" -- sudo --login --user ctabone bash -lc \
  "mkdir -p '${dest_dir}' && chmod 700 '${dest_dir}'"

incus --project "${project}" file push "${credentials_file}" "${vm_name}${dest_dir}/credentials"
incus --project "${project}" file push "${config_file}" "${vm_name}${dest_dir}/config"

incus --project "${project}" exec "${vm_name}" -- sudo --login --user ctabone bash -lc \
  "chmod 700 '${dest_dir}' && chmod 600 '${dest_dir}/credentials' '${dest_dir}/config'"

echo "Synced credentials and config with restricted permissions."
