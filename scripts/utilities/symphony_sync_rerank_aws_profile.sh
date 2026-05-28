#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: symphony_sync_rerank_aws_profile.sh [options]

Copies the dedicated Bedrock rerank AWS profile files into the Symphony Incus VM.
The source directory must contain AWS CLI-compatible `credentials` and `config`
files. Secret file contents are never printed. By default, the helper also
updates the VM's AI Curation env file with the rerank-only AWS settings needed
by docker-compose.yml.

Options:
  --source-dir DIR         Host source directory for credentials/config
  --dest-dir DIR           VM destination directory for credentials/config
  --vm NAME                Incus VM name
  --project NAME           Incus project
  --profile NAME           AWS profile name inside the credentials/config files
  --region REGION          AWS region for Bedrock reranking
  --env-file PATH          VM env file to update
  --host-aws-profile NAME  VM host-tooling AWS_PROFILE to preserve/update
  --skip-env-update        Copy profile files only

Environment defaults:
  AI_CURATION_RERANK_AWS_SECRET_DIR   Host source directory
  AI_CURATION_RERANK_AWS_PROFILE      Rerank AWS profile name
  AI_CURATION_RERANK_AWS_REGION       Rerank AWS region
  AI_CURATION_HOST_AWS_PROFILE        VM host-tooling AWS profile
  AI_CURATION_VM_ENV_FILE             VM env file to update
  SYMPHONY_INCUS_VM                   Incus VM name (default: symphony-main)
  SYMPHONY_INCUS_PROJECT              Incus project (default: default)
EOF
}

source_dir="${AI_CURATION_RERANK_AWS_SECRET_DIR:-${HOME}/.symphony/secrets/agr_ai_curation/aws-rerank}"
dest_dir="/home/ctabone/.symphony/secrets/agr_ai_curation/aws-rerank"
vm_name="${SYMPHONY_INCUS_VM:-symphony-main}"
project="${SYMPHONY_INCUS_PROJECT:-default}"
profile_name="${AI_CURATION_RERANK_AWS_PROFILE:-ai-curation-rerank-local}"
region="${AI_CURATION_RERANK_AWS_REGION:-us-east-1}"
env_file="${AI_CURATION_VM_ENV_FILE:-/home/ctabone/.agr_ai_curation/.env}"
host_aws_profile="${AI_CURATION_HOST_AWS_PROFILE:-ctabone}"
update_env=1

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
    --profile)
      profile_name="${2:-}"
      shift 2
      ;;
    --region)
      region="${2:-}"
      shift 2
      ;;
    --env-file)
      env_file="${2:-}"
      shift 2
      ;;
    --host-aws-profile)
      host_aws_profile="${2:-}"
      shift 2
      ;;
    --skip-env-update)
      update_env=0
      shift
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

if [[ -z "${source_dir}" || -z "${dest_dir}" || -z "${vm_name}" || -z "${project}" || -z "${profile_name}" || -z "${region}" ]]; then
  echo "source-dir, dest-dir, vm, project, profile, and region must be non-empty." >&2
  exit 2
fi

if [[ "${update_env}" == "1" && -z "${env_file}" ]]; then
  echo "env-file must be non-empty unless --skip-env-update is used." >&2
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
  "mkdir -p '${dest_dir}' && chmod 711 '${dest_dir}'"

incus --project "${project}" file push "${credentials_file}" "${vm_name}${dest_dir}/credentials"
incus --project "${project}" file push "${config_file}" "${vm_name}${dest_dir}/config"

incus --project "${project}" exec "${vm_name}" -- sudo --login --user ctabone bash -lc \
  "chmod 711 '${dest_dir}' && chmod 644 '${dest_dir}/credentials' '${dest_dir}/config'"

echo "Synced credentials and config with Docker-readable VM permissions."

if [[ "${update_env}" == "1" ]]; then
  incus --project "${project}" exec "${vm_name}" -- sudo --login --user ctabone bash -s -- \
    "${env_file}" "${host_aws_profile}" "${region}" "${profile_name}" "${dest_dir}" <<'REMOTE'
set -euo pipefail
env_file="$1"
host_aws_profile="$2"
region="$3"
profile_name="$4"
dest_dir="$5"

mkdir -p "$(dirname "${env_file}")"
if [[ ! -f "${env_file}" ]]; then
  touch "${env_file}"
fi

tmp_file="$(mktemp)"
grep -Ev '^(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|AWS_DEFAULT_PROFILE|AWS_PROFILE|AWS_CREDENTIALS_MOUNT_DIR|AWS_SHARED_CREDENTIALS_FILE|AWS_CONFIG_FILE|AWS_REGION|RERANK_PROVIDER|RERANK_AWS_PROFILE|RERANK_AWS_CREDENTIALS_MOUNT_DIR|RERANK_AWS_SHARED_CREDENTIALS_FILE|RERANK_AWS_CONFIG_FILE)=' "${env_file}" >"${tmp_file}" || true
{
  if [[ -n "${host_aws_profile}" ]]; then
    printf 'AWS_PROFILE=%s\n' "${host_aws_profile}"
  fi
  printf 'AWS_REGION=%s\n' "${region}"
  printf 'RERANK_PROVIDER=bedrock_cohere\n'
  printf 'RERANK_AWS_PROFILE=%s\n' "${profile_name}"
  printf 'RERANK_AWS_CREDENTIALS_MOUNT_DIR=%s\n' "${dest_dir}"
  printf 'RERANK_AWS_SHARED_CREDENTIALS_FILE=/runtime/secrets/aws-rerank/credentials\n'
  printf 'RERANK_AWS_CONFIG_FILE=/runtime/secrets/aws-rerank/config\n'
} >>"${tmp_file}"
mv "${tmp_file}" "${env_file}"
chmod 600 "${env_file}"
REMOTE
  echo "Updated VM env file with rerank-only AWS settings: ${env_file}"
fi
