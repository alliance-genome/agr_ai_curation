#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/symphony_git_safety_tool_versions.sh"

VM_USER="ctabone"
VM_GECOS="Christopher Tabone"
SSH_KEY_FILE=""

usage() {
  cat <<'EOF'
Usage:
  symphony_print_incus_vm_cloud_init.sh [options]

Options:
  --user USER          Login user to create in the VM (default: ctabone)
  --gecos TEXT         GECOS/full-name field for the VM user
  --ssh-key-file PATH  Public SSH key to authorize for the VM user
  -h, --help           Show this help

If --ssh-key-file is omitted, the script looks for ~/.ssh/id_ed25519.pub and
then ~/.ssh/id_rsa.pub.
EOF
}

pick_default_ssh_key_file() {
  local candidate=""

  for candidate in \
    "${HOME}/.ssh/id_ed25519.pub" \
    "${HOME}/.ssh/id_rsa.pub"
  do
    if [[ -r "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      VM_USER="${2:?--user requires a value}"
      shift 2
      ;;
    --gecos)
      VM_GECOS="${2:?--gecos requires a value}"
      shift 2
      ;;
    --ssh-key-file)
      SSH_KEY_FILE="${2:?--ssh-key-file requires a path}"
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

if [[ -z "${SSH_KEY_FILE}" ]]; then
  SSH_KEY_FILE="$(pick_default_ssh_key_file || true)"
fi

if [[ -z "${SSH_KEY_FILE}" || ! -r "${SSH_KEY_FILE}" ]]; then
  echo "Missing readable SSH public key. Pass --ssh-key-file PATH." >&2
  exit 1
fi

SSH_PUBLIC_KEY="$(tr -d '\r' < "${SSH_KEY_FILE}" | sed 's/[[:space:]]*$//')"
if [[ -z "${SSH_PUBLIC_KEY}" ]]; then
  echo "SSH public key file is empty: ${SSH_KEY_FILE}" >&2
  exit 1
fi

cat <<EOF
#cloud-config
users:
  - name: ${VM_USER}
    gecos: ${VM_GECOS}
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: [adm, sudo]
    lock_passwd: true
    ssh_authorized_keys:
      - ${SSH_PUBLIC_KEY}
ssh_pwauth: false
disable_root: true
package_update: true
package_upgrade: false
packages:
  - ca-certificates
  - curl
write_files:
  - path: /usr/local/sbin/symphony-install-git-safety-tools.sh
    owner: root:root
    permissions: "0755"
    content: |
      #!/usr/bin/env bash
      set -euo pipefail

      install_archive() {
        local url="\$1"
        local expected_sha="\$2"
        local binary_name="\$3"
        local archive_path="\$4"
        local tmp_dir="\$5"
        local extracted_path=""

        curl -fsSL -o "\${archive_path}" "\${url}"
        printf '%s  %s\n' "\${expected_sha}" "\${archive_path}" | sha256sum -c -
        tar -xzf "\${archive_path}" -C "\${tmp_dir}"

        extracted_path="\$(find "\${tmp_dir}" -type f -name "\${binary_name}" | head -n 1 || true)"
        if [[ -z "\${extracted_path}" ]]; then
          echo "Could not locate \${binary_name} after extracting \${url}" >&2
          return 1
        fi

        install -m 0755 "\${extracted_path}" "/usr/local/bin/\${binary_name}"
      }

      arch="\$(uname -m)"
      case "\${arch}" in
        x86_64|amd64)
          gitleaks_archive="gitleaks_${SYMPHONY_GITLEAKS_VERSION#v}_linux_x64.tar.gz"
          gitleaks_sha="${SYMPHONY_GITLEAKS_SHA256_LINUX_AMD64}"
          trufflehog_archive="trufflehog_${SYMPHONY_TRUFFLEHOG_VERSION#v}_linux_amd64.tar.gz"
          trufflehog_sha="${SYMPHONY_TRUFFLEHOG_SHA256_LINUX_AMD64}"
          ;;
        aarch64|arm64)
          gitleaks_archive="gitleaks_${SYMPHONY_GITLEAKS_VERSION#v}_linux_arm64.tar.gz"
          gitleaks_sha="${SYMPHONY_GITLEAKS_SHA256_LINUX_ARM64}"
          trufflehog_archive="trufflehog_${SYMPHONY_TRUFFLEHOG_VERSION#v}_linux_arm64.tar.gz"
          trufflehog_sha="${SYMPHONY_TRUFFLEHOG_SHA256_LINUX_ARM64}"
          ;;
        *)
          echo "Unsupported architecture for git safety tools: \${arch}" >&2
          exit 1
          ;;
      esac

      tmp_dir="\$(mktemp -d)"
      trap 'rm -rf "\${tmp_dir}"' EXIT

      install_archive \
        "https://github.com/gitleaks/gitleaks/releases/download/${SYMPHONY_GITLEAKS_VERSION}/\${gitleaks_archive}" \
        "\${gitleaks_sha}" \
        "gitleaks" \
        "\${tmp_dir}/gitleaks.tar.gz" \
        "\${tmp_dir}"

      install_archive \
        "https://github.com/trufflesecurity/trufflehog/releases/download/${SYMPHONY_TRUFFLEHOG_VERSION}/\${trufflehog_archive}" \
        "\${trufflehog_sha}" \
        "trufflehog" \
        "\${tmp_dir}/trufflehog.tar.gz" \
        "\${tmp_dir}"
runcmd:
  - [/usr/local/sbin/symphony-install-git-safety-tools.sh]
EOF
