#!/usr/bin/env bash
set -euo pipefail

readonly DEFAULT_REPO_URL="https://github.com/alliance-genome/agr_curation_schema.git"
readonly DEFAULT_COMMIT="1b11d0888f19eba4ca72022200bb7d96b30d4a52"
readonly DEFAULT_CACHE_ROOT="${AGR_CURATION_SCHEMA_CACHE_ROOT:-/tmp/agr_curation_schema_cache}"

usage() {
  cat <<'USAGE'
Usage: cache_agr_curation_schema.sh [--cache-root PATH] [--repo-url URL] [--commit SHA]

Clone or reuse the pinned Alliance LinkML schema repository cache and print
shell-compatible environment assignments on stdout:

  AGR_CURATION_SCHEMA_CACHE_DIR
  AGR_CURATION_SCHEMA_COMMIT
  AGR_CURATION_SCHEMA_REPO_URL
  AGR_CURATION_SCHEMA_CACHE_STATUS
USAGE
}

cache_root="${DEFAULT_CACHE_ROOT}"
repo_url="${AGR_CURATION_SCHEMA_REPO_URL:-${DEFAULT_REPO_URL}}"
commit="${AGR_CURATION_SCHEMA_COMMIT:-${DEFAULT_COMMIT}}"

while (($#)); do
  case "$1" in
    --cache-root)
      cache_root="${2:?--cache-root requires a path}"
      shift 2
      ;;
    --repo-url)
      repo_url="${2:?--repo-url requires a URL}"
      shift 2
      ;;
    --commit)
      commit="${2:?--commit requires a commit SHA}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cache_dir="${cache_root%/}/agr_curation_schema/${commit}"
status="reused"

mkdir -p "$(dirname "$cache_dir")"

if [[ -d "$cache_dir/.git" ]]; then
  if [[ -n "$(git -C "$cache_dir" status --porcelain)" ]]; then
    printf 'Schema cache has local modifications: %s\n' "$cache_dir" >&2
    exit 1
  fi
  git -C "$cache_dir" fetch --quiet origin "$commit" >&2
  git -C "$cache_dir" checkout --quiet --detach "$commit" >&2
else
  if [[ -e "$cache_dir" ]]; then
    printf 'Schema cache path exists but is not a git checkout: %s\n' "$cache_dir" >&2
    exit 1
  fi
  git clone --quiet "$repo_url" "$cache_dir" >&2
  git -C "$cache_dir" checkout --quiet --detach "$commit" >&2
  status="created"
fi

actual_commit="$(git -C "$cache_dir" rev-parse HEAD)"
if [[ "$actual_commit" != "$commit" ]]; then
  printf 'Schema cache checked out %s, expected %s\n' "$actual_commit" "$commit" >&2
  exit 1
fi

if [[ ! -f "$cache_dir/model/schema/allianceModel.yaml" ]]; then
  printf 'Schema cache is missing model/schema/allianceModel.yaml: %s\n' "$cache_dir" >&2
  exit 1
fi

printf 'AGR_CURATION_SCHEMA_CACHE_DIR=%q\n' "$cache_dir"
printf 'AGR_CURATION_SCHEMA_COMMIT=%q\n' "$commit"
printf 'AGR_CURATION_SCHEMA_REPO_URL=%q\n' "$repo_url"
printf 'AGR_CURATION_SCHEMA_CACHE_STATUS=%q\n' "$status"
