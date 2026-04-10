#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
SHELL_FILE="${HOME}/.bash_aliases"
QUIET=0

BEGIN_MARKER="# >>> symphony codex shortcuts >>>"
END_MARKER="# <<< symphony codex shortcuts <<<"

usage() {
  cat <<'EOF'
Usage:
  symphony_install_vm_shell_shortcuts.sh [options]

Options:
  --repo-root DIR    Repo root containing scripts/utilities/symphony_vm_shell_shortcuts.sh
  --shell-file PATH  Shell aliases file to update (default: ~/.bash_aliases)
  --quiet            Suppress success output
  -h, --help         Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)
      REPO_ROOT="${2:-}"
      shift 2
      ;;
    --shell-file)
      SHELL_FILE="${2:-}"
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

REPO_ROOT="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "${REPO_ROOT}")"
SHELL_FILE="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "${SHELL_FILE}")"

SHORTCUTS_FILE="${REPO_ROOT}/scripts/utilities/symphony_vm_shell_shortcuts.sh"
if [[ ! -r "${SHORTCUTS_FILE}" ]]; then
  echo "Missing shell shortcuts file: ${SHORTCUTS_FILE}" >&2
  exit 1
fi

mkdir -p "$(dirname "${SHELL_FILE}")"
touch "${SHELL_FILE}"

TEMP_FILE="$(mktemp)"
trap 'rm -f "${TEMP_FILE}"' EXIT

awk -v begin="${BEGIN_MARKER}" -v end="${END_MARKER}" '
  $0 == begin { skip = 1; next }
  $0 == end { skip = 0; next }
  skip != 1 { print }
' "${SHELL_FILE}" > "${TEMP_FILE}"

cat >> "${TEMP_FILE}" <<EOF
${BEGIN_MARKER}
export AGR_AI_CURATION_REPO_ROOT="${REPO_ROOT}"
if [[ -f "${SHORTCUTS_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${SHORTCUTS_FILE}"
fi
${END_MARKER}
EOF

mv "${TEMP_FILE}" "${SHELL_FILE}"
trap - EXIT

if [[ "${QUIET}" != "1" ]]; then
  echo "Installed Symphony Codex shortcuts in ${SHELL_FILE}"
fi
