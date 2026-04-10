#!/usr/bin/env bash

set -euo pipefail

case "${BASH_SOURCE[0]}" in
  */*)
    SCRIPT_DIR="$(cd "${BASH_SOURCE[0]%/*}" && pwd -P)"
    ;;
  *)
    SCRIPT_DIR="$(pwd -P)"
    ;;
esac
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/symphony_git_safety_tool_versions.sh"

MODE="ensure"
INSTALL_DIR="${HOME}/.local/bin"
QUIET=0

usage() {
  cat <<'EOF'
Usage:
  symphony_ensure_git_safety_tools.sh [options]

Options:
  --check              Only verify whether gitleaks and trufflehog are installed
  --install-user       Install missing tools into a user-writable bin dir
  --install-dir DIR    Override the user install directory (default: ~/.local/bin)
  --quiet              Reduce status output
  -h, --help           Show this help

Default mode is --install-user.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      MODE="check"
      shift
      ;;
    --install-user)
      MODE="ensure"
      shift
      ;;
    --install-dir)
      INSTALL_DIR="${2:?--install-dir requires a path}"
      shift 2
      ;;
    --quiet)
      QUIET=1
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

case "${INSTALL_DIR}" in
  "~")
    INSTALL_DIR="${HOME}"
    ;;
  "~/"*)
    INSTALL_DIR="${HOME}/${INSTALL_DIR#~/}"
    ;;
esac

if [[ "${INSTALL_DIR}" != /* ]]; then
  INSTALL_DIR="${PWD}/${INSTALL_DIR}"
fi

export PATH="${INSTALL_DIR}:${HOME}/.local/bin:${HOME}/bin:${PATH}"

log() {
  if [[ "${QUIET}" != "1" ]]; then
    printf '%s\n' "$*"
  fi
}

tool_installed() {
  command -v "$1" >/dev/null 2>&1
}

tool_version() {
  local tool="$1"

  case "${tool}" in
    gitleaks)
      "${tool}" version 2>&1 | head -n 1
      ;;
    trufflehog)
      "${tool}" --version 2>&1 | head -n 1
      ;;
    *)
      return 1
      ;;
  esac
}

report_tool() {
  local tool="$1"

  if tool_installed "${tool}"; then
    log "[ok] ${tool}: $(tool_version "${tool}")"
    return 0
  fi

  log "[missing] ${tool}"
  return 1
}

normalized_arch() {
  case "$(uname -m)" in
    x86_64|amd64)
      printf 'amd64\n'
      ;;
    aarch64|arm64)
      printf 'arm64\n'
      ;;
    *)
      echo "Unsupported architecture: $(uname -m)" >&2
      return 1
      ;;
  esac
}

verify_sha256() {
  local expected_sha="$1"
  local archive_path="$2"

  if ! command -v sha256sum >/dev/null 2>&1; then
    echo "sha256sum is required to verify downloaded git safety tools." >&2
    return 1
  fi

  printf '%s  %s\n' "${expected_sha}" "${archive_path}" | sha256sum -c - >/dev/null
}

install_linux_tarball() {
  local binary_name="$1"
  local repo=""
  local version=""
  local archive_name=""
  local archive_path=""
  local tmp_dir=""
  local source_path=""
  local expected_sha=""
  local arch=""

  arch="$(normalized_arch)"

  case "${binary_name}" in
    gitleaks)
      repo="gitleaks/gitleaks"
      version="${SYMPHONY_GITLEAKS_VERSION}"
      case "${arch}" in
        amd64)
          archive_name="gitleaks_${version#v}_linux_x64.tar.gz"
          expected_sha="${SYMPHONY_GITLEAKS_SHA256_LINUX_AMD64}"
          ;;
        arm64)
          archive_name="gitleaks_${version#v}_linux_arm64.tar.gz"
          expected_sha="${SYMPHONY_GITLEAKS_SHA256_LINUX_ARM64}"
          ;;
      esac
      ;;
    trufflehog)
      repo="trufflesecurity/trufflehog"
      version="${SYMPHONY_TRUFFLEHOG_VERSION}"
      case "${arch}" in
        amd64)
          archive_name="trufflehog_${version#v}_linux_amd64.tar.gz"
          expected_sha="${SYMPHONY_TRUFFLEHOG_SHA256_LINUX_AMD64}"
          ;;
        arm64)
          archive_name="trufflehog_${version#v}_linux_arm64.tar.gz"
          expected_sha="${SYMPHONY_TRUFFLEHOG_SHA256_LINUX_ARM64}"
          ;;
      esac
      ;;
    *)
      echo "Unsupported binary: ${binary_name}" >&2
      return 1
      ;;
  esac

  mkdir -p "${INSTALL_DIR}"
  tmp_dir="$(mktemp -d)"
  archive_path="${tmp_dir}/${binary_name}.tar.gz"

  log "Installing ${binary_name} ${version} into ${INSTALL_DIR}..."
  curl -fsSL -o "${archive_path}" "https://github.com/${repo}/releases/download/${version}/${archive_name}"
  verify_sha256 "${expected_sha}" "${archive_path}"
  tar -xzf "${archive_path}" -C "${tmp_dir}"

  source_path="$(find "${tmp_dir}" -type f -name "${binary_name}" | head -n 1 || true)"
  if [[ -z "${source_path}" ]]; then
    rm -rf "${tmp_dir}"
    echo "Could not locate ${binary_name} in downloaded archive" >&2
    return 1
  fi

  cp "${source_path}" "${INSTALL_DIR}/${binary_name}"
  chmod +x "${INSTALL_DIR}/${binary_name}"
  rm -rf "${tmp_dir}"
}

ensure_installed() {
  local tool="$1"

  if tool_installed "${tool}"; then
    return 0
  fi

  case "${tool}" in
    gitleaks)
      install_linux_tarball "gitleaks"
      ;;
    trufflehog)
      install_linux_tarball "trufflehog"
      ;;
    *)
      echo "Unsupported tool: ${tool}" >&2
      return 1
      ;;
  esac
}

if [[ "${MODE}" == "check" ]]; then
  missing=0
  report_tool gitleaks || missing=1
  report_tool trufflehog || missing=1
  exit "${missing}"
fi

if [[ "${OSTYPE:-}" != linux* ]]; then
  echo "Automatic git safety tool installation is only supported on Linux." >&2
  echo "Install gitleaks and trufflehog manually on this platform." >&2
  exit 1
fi

report_tool gitleaks || true
report_tool trufflehog || true

ensure_installed gitleaks
ensure_installed trufflehog
hash -r

report_tool gitleaks
report_tool trufflehog

log "Git safety tools are installed and ready."
