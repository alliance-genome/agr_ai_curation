#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  ensure_python_tools_venv.sh [--repo-root DIR] [--venv-dir DIR] [--requirements-file PATH] [--python-bin BIN]

Behavior:
  - Creates or updates a persistent repo-local Python tools venv.
  - Installs the packages listed in scripts/requirements/python-tools.txt by default.
  - Reuses the venv when the requirements hash is unchanged and required tools are available.
  - Emits machine-parsable summary lines:
      PYTHON_TOOLS_VENV_STATUS=already_ready|created|updated|error
      PYTHON_TOOLS_VENV_DIR=<path>
      PYTHON_TOOLS_PYTHON=<path or none>
      PYTHON_TOOLS_REQUIREMENTS=<path>
USAGE
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
venv_dir=""
requirements_file=""
python_bin="${PYTHON_TOOLS_BOOTSTRAP_PYTHON:-python3}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)
      repo_root="${2:-}"
      shift 2
      ;;
    --venv-dir)
      venv_dir="${2:-}"
      shift 2
      ;;
    --requirements-file)
      requirements_file="${2:-}"
      shift 2
      ;;
    --python-bin)
      python_bin="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

repo_root="$(cd "${repo_root}" && pwd -P)"
venv_dir="${venv_dir:-${repo_root}/.venv}"
requirements_file="${requirements_file:-${repo_root}/scripts/requirements/python-tools.txt}"
venv_python="${venv_dir}/bin/python"
hash_marker="${venv_dir}/.python-tools-requirements.sha256"

emit_summary() {
  local status="$1"
  local python_path="none"
  if [[ -x "${venv_python}" ]]; then
    python_path="${venv_python}"
  fi

  echo "PYTHON_TOOLS_VENV_STATUS=${status}"
  echo "PYTHON_TOOLS_VENV_DIR=${venv_dir}"
  echo "PYTHON_TOOLS_PYTHON=${python_path}"
  echo "PYTHON_TOOLS_REQUIREMENTS=${requirements_file}"
}

requirements_hash() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${path}" | awk '{print $1}'
    return 0
  fi

  shasum -a 256 "${path}" | awk '{print $1}'
}

if [[ ! -f "${requirements_file}" ]]; then
  emit_summary "error"
  echo "Missing requirements file: ${requirements_file}" >&2
  exit 3
fi

if ! command -v "${python_bin}" >/dev/null 2>&1; then
  emit_summary "error"
  echo "Python bootstrap interpreter not found: ${python_bin}" >&2
  exit 3
fi

current_hash="$(requirements_hash "${requirements_file}")"
current_marker=""
if [[ -f "${hash_marker}" ]]; then
  current_marker="$(tr -d '[:space:]' < "${hash_marker}")"
fi

if [[ -x "${venv_python}" ]] \
  && [[ "${current_marker}" == "${current_hash}" ]] \
  && "${venv_python}" -m ruff --version >/dev/null 2>&1 \
  && "${venv_python}" - <<'PY' >/dev/null 2>&1
import yaml
PY
then
  emit_summary "already_ready"
  exit 0
fi

status="updated"
if [[ ! -x "${venv_python}" ]]; then
  "${python_bin}" -m venv "${venv_dir}"
  status="created"
fi

"${venv_python}" -m pip install --quiet --upgrade pip
"${venv_python}" -m pip install --quiet -r "${requirements_file}"
printf '%s\n' "${current_hash}" > "${hash_marker}"

emit_summary "${status}"
