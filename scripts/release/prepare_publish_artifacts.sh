#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
# shellcheck source=scripts/install/lib/common.sh
source "${repo_root}/scripts/install/lib/common.sh"
env_template_path="${repo_root}/scripts/install/lib/templates/env.standalone"

lane=""
output_dir=""
ref_name=""
short_sha=""
source_date_epoch="${SOURCE_DATE_EPOCH:-}"
temp_dir=""

usage() {
  cat <<'USAGE'
Usage: scripts/release/prepare_publish_artifacts.sh [options]

Options:
  --lane LANE                Publish lane: main or release
  --output-dir DIR           Directory for generated artifacts
  --ref-name NAME            Source ref name (main or vX.Y.Z)
  --short-sha SHA            Short git SHA for naming non-release artifacts
  --source-date-epoch EPOCH  Override reproducible archive timestamp
  -h, --help                 Show this help text
USAGE
}

require_value() {
  local option_name="$1"
  local option_value="$2"

  if [[ -z "${option_value}" ]]; then
    echo "Missing required value for ${option_name}" >&2
    exit 1
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --lane)
        shift
        lane="${1:-}"
        ;;
      --output-dir)
        shift
        output_dir="${1:-}"
        ;;
      --ref-name)
        shift
        ref_name="${1:-}"
        ;;
      --short-sha)
        shift
        short_sha="${1:-}"
        ;;
      --source-date-epoch)
        shift
        source_date_epoch="${1:-}"
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        usage
        exit 1
        ;;
    esac
    shift
  done
}

resolve_names() {
  local artifact_label=""
  local image_tag=""

  case "${lane}" in
    release)
      if [[ ! "${ref_name}" =~ ^v[0-9] ]]; then
        echo "Release lane requires --ref-name vX.Y.Z" >&2
        exit 1
      fi
      artifact_label="${ref_name}"
      image_tag="${ref_name}"
      ;;
    main)
      require_value "--short-sha" "${short_sha}"
      artifact_label="main-sha-${short_sha}"
      image_tag="sha-${short_sha}"
      ;;
    *)
      echo "Unsupported lane: ${lane}" >&2
      exit 1
      ;;
  esac

  core_artifact_name="core-${artifact_label}.tar.gz"
  standalone_env_name="env.standalone-${artifact_label}"
  metadata_name="publish-artifacts-metadata-${artifact_label}.json"
  resolved_image_tag="${image_tag}"
}

render_env_template() {
  local output_path="$1"

  sed \
    -e "s/^BACKEND_IMAGE_TAG=.*/BACKEND_IMAGE_TAG=${resolved_image_tag}/" \
    -e "s/^FRONTEND_IMAGE_TAG=.*/FRONTEND_IMAGE_TAG=${resolved_image_tag}/" \
    -e "s/^TRACE_REVIEW_BACKEND_IMAGE_TAG=.*/TRACE_REVIEW_BACKEND_IMAGE_TAG=${resolved_image_tag}/" \
    "${env_template_path}" > "${output_path}"
}

write_metadata() {
  local metadata_path="$1"
  local core_sha256="$2"
  local env_sha256="$3"

  jq -n \
    --arg lane "${lane}" \
    --arg ref_name "${ref_name}" \
    --arg short_sha "${short_sha}" \
    --arg image_tag "${resolved_image_tag}" \
    --argjson source_date_epoch "${source_date_epoch}" \
    --arg core_name "${core_artifact_name}" \
    --arg core_sha256 "${core_sha256}" \
    --arg env_name "${standalone_env_name}" \
    --arg env_sha256 "${env_sha256}" \
    '{
      lane: $lane,
      ref_name: $ref_name,
      short_sha: $short_sha,
      image_tag: $image_tag,
      source_date_epoch: $source_date_epoch,
      core_artifact: {
        name: $core_name,
        sha256: $core_sha256
      },
      standalone_env: {
        name: $env_name,
        sha256: $env_sha256,
        image_tag: $image_tag
      }
    }' > "${metadata_path}"
}

cleanup() {
  if [[ -n "${temp_dir}" && -d "${temp_dir}" ]]; then
    rm -rf "${temp_dir}"
  fi
}

main() {
  parse_args "$@"

  require_value "--lane" "${lane}"
  require_value "--output-dir" "${output_dir}"
  require_value "--ref-name" "${ref_name}"

  if [[ -z "${source_date_epoch}" ]]; then
    source_date_epoch="$(git -C "${repo_root}" log -1 --format=%ct HEAD)"
  fi

  if [[ ! "${source_date_epoch}" =~ ^[0-9]+$ ]]; then
    echo "--source-date-epoch must be an integer" >&2
    exit 1
  fi

  if [[ ! -f "${env_template_path}" ]]; then
    echo "Missing env template: ${env_template_path}" >&2
    exit 1
  fi

  local package_name=""
  while IFS= read -r package_name; do
    [[ -n "$package_name" ]] || continue
    if [[ ! -f "${repo_root}/packages/${package_name}/package.yaml" ]]; then
      echo "Missing packages/${package_name}/package.yaml in checkout; cannot build bundled runtime artifact" >&2
      exit 1
    fi
  done < <(install_shipped_package_names)

  resolve_names
  mkdir -p "${output_dir}"

  temp_dir="$(mktemp -d)"

  local bundled_package_dirs=()
  while IFS= read -r package_name; do
    [[ -n "$package_name" ]] || continue
    bundled_package_dirs+=("${package_name}")
    cp -a "${repo_root}/packages/${package_name}" "${temp_dir}/${package_name}"
  done < <(install_shipped_package_names)

  local core_output_path="${output_dir}/${core_artifact_name}"
  local env_output_path="${output_dir}/${standalone_env_name}"
  local metadata_output_path="${output_dir}/${metadata_name}"

  render_env_template "${env_output_path}"

  tar \
    --sort=name \
    --mtime="@${source_date_epoch}" \
    --owner=0 \
    --group=0 \
    --numeric-owner \
    --pax-option=delete=atime,delete=ctime \
    -C "${temp_dir}" \
    -cf - "${bundled_package_dirs[@]}" | gzip -n > "${core_output_path}"

  local core_sha256
  local env_sha256
  core_sha256="$(sha256sum "${core_output_path}" | awk '{print $1}')"
  env_sha256="$(sha256sum "${env_output_path}" | awk '{print $1}')"

  write_metadata "${metadata_output_path}" "${core_sha256}" "${env_sha256}"

  printf 'CORE_ARTIFACT=%s\n' "${core_artifact_name}"
  printf 'PINNED_ENV_TEMPLATE=%s\n' "${standalone_env_name}"
  printf 'PUBLISH_METADATA=%s\n' "${metadata_name}"
  printf 'PUBLISHED_IMAGE_TAG=%s\n' "${resolved_image_tag}"
}

trap cleanup EXIT

main "$@"
