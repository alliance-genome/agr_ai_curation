#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${REPO_ROOT}/scripts/lib/symphony_microvm_common.sh"

usage() {
  cat <<'EOF'
Usage:
  symphony_microvm_prepare_assets.sh [--assets-root DIR] [--release TAG] [--force] [--dry-run]

Behavior:
  - Downloads Firecracker and jailer binaries for the current architecture.
  - Downloads the latest kernel and Ubuntu squashfs guest assets from Firecracker CI.
  - Generates an SSH keypair for root login into the guest.
  - Builds an ext4 rootfs image with authorized_keys injected.
  - Writes machine-readable summary lines on success.
EOF
}

assets_root="$(symphony_microvm_assets_root)"
release_tag="${SYMPHONY_FIRECRACKER_VERSION:-}"
force=0
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --assets-root)
      assets_root="${2:-}"
      shift 2
      ;;
    --release)
      release_tag="${2:-}"
      shift 2
      ;;
    --force)
      force=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
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

arch="$(symphony_microvm_arch)"

for cmd in curl jq wget tar unsquashfs mkfs.ext4 ssh-keygen; do
  symphony_microvm_require_cmd "${cmd}"
done

if [[ -z "${release_tag}" ]]; then
  release_tag="$(symphony_microvm_latest_firecracker_version)"
fi

ci_version="$(symphony_microvm_ci_version_for_release "${release_tag}")"
bin_dir="${assets_root}/bin/${release_tag}/${arch}"
images_dir="${assets_root}/images/${ci_version}/${arch}"
build_dir="${assets_root}/build/${ci_version}/${arch}"
ssh_key_dir="${assets_root}/ssh"
ssh_private_key="${ssh_key_dir}/id_rsa"
ssh_public_key="${ssh_key_dir}/id_rsa.pub"
release_archive="${build_dir}/firecracker-${release_tag}-${arch}.tgz"
rootfs_squashfs="${images_dir}/ubuntu.squashfs"
rootfs_dir="${build_dir}/squashfs-root"
rootfs_ext4="${images_dir}/ubuntu.ext4"
firecracker_bin="${bin_dir}/firecracker"
jailer_bin="${bin_dir}/jailer"
kernel_path="${images_dir}/vmlinux.bin"
rootfs_size_mb="${SYMPHONY_MICROVM_ROOTFS_SIZE_MB:-8192}"

mkdir -p "${bin_dir}" "${images_dir}" "${build_dir}" "${ssh_key_dir}"

kernel_key="$(curl -fsSL --max-time 20 "https://s3.amazonaws.com/spec.ccfc.min/?prefix=firecracker-ci/${ci_version}/${arch}/vmlinux-&list-type=2" \
  | grep -oP "(?<=<Key>)(firecracker-ci/${ci_version}/${arch}/vmlinux-[0-9]+\.[0-9]+\.[0-9]{1,3})(?=</Key>)" \
  | sort -V | tail -1)"

ubuntu_key="$(curl -fsSL --max-time 20 "https://s3.amazonaws.com/spec.ccfc.min/?prefix=firecracker-ci/${ci_version}/${arch}/ubuntu-&list-type=2" \
  | grep -oP "(?<=<Key>)(firecracker-ci/${ci_version}/${arch}/ubuntu-[0-9]+\.[0-9]+\.squashfs)(?=</Key>)" \
  | sort -V | tail -1)"

if [[ -z "${kernel_key}" || -z "${ubuntu_key}" ]]; then
  echo "Unable to resolve Firecracker CI assets for ${ci_version}/${arch}" >&2
  exit 1
fi

if [[ "${dry_run}" -eq 1 ]]; then
  symphony_microvm_output_kv "MICROVM_ASSETS_STATUS" "dry_run"
  symphony_microvm_output_kv "MICROVM_ASSETS_RELEASE" "${release_tag}"
  symphony_microvm_output_kv "MICROVM_ASSETS_ROOT" "${assets_root}"
  symphony_microvm_output_kv "MICROVM_ASSETS_KERNEL_URL" "https://s3.amazonaws.com/spec.ccfc.min/${kernel_key}"
  symphony_microvm_output_kv "MICROVM_ASSETS_ROOTFS_URL" "https://s3.amazonaws.com/spec.ccfc.min/${ubuntu_key}"
  exit 0
fi

if [[ ! -f "${release_archive}" || "${force}" -eq 1 ]]; then
  wget -q -O "${release_archive}" "https://github.com/firecracker-microvm/firecracker/releases/download/${release_tag}/firecracker-${release_tag}-${arch}.tgz"
fi

tmp_extract="${build_dir}/release-extract"
rm -rf "${tmp_extract}"
mkdir -p "${tmp_extract}"
tar -xzf "${release_archive}" -C "${tmp_extract}"

cp -f "${tmp_extract}/release-${release_tag}-${arch}/firecracker-${release_tag}-${arch}" "${firecracker_bin}"
cp -f "${tmp_extract}/release-${release_tag}-${arch}/jailer-${release_tag}-${arch}" "${jailer_bin}"
chmod +x "${firecracker_bin}" "${jailer_bin}"

if [[ ! -f "${kernel_path}" || "${force}" -eq 1 ]]; then
  wget -q -O "${kernel_path}" "https://s3.amazonaws.com/spec.ccfc.min/${kernel_key}"
fi

if [[ ! -f "${rootfs_squashfs}" || "${force}" -eq 1 ]]; then
  wget -q -O "${rootfs_squashfs}" "https://s3.amazonaws.com/spec.ccfc.min/${ubuntu_key}"
fi

if [[ ! -f "${ssh_private_key}" || ! -f "${ssh_public_key}" || "${force}" -eq 1 ]]; then
  rm -f "${ssh_private_key}" "${ssh_public_key}"
  ssh-keygen -q -t rsa -b 4096 -f "${ssh_private_key}" -N ""
fi

if [[ ! -f "${rootfs_ext4}" || "${force}" -eq 1 ]]; then
  symphony_microvm_require_sudo_noninteractive
  sudo -n rm -rf "${rootfs_dir}"
  unsquashfs -d "${rootfs_dir}" "${rootfs_squashfs}" >/dev/null
  sudo -n mkdir -p "${rootfs_dir}/root/.ssh"
  cp -f "${ssh_public_key}" "${rootfs_dir}/root/.ssh/authorized_keys"
  sudo -n chmod 700 "${rootfs_dir}/root/.ssh"
  sudo -n chmod 600 "${rootfs_dir}/root/.ssh/authorized_keys"
  sudo -n chown -R root:root "${rootfs_dir}"
  rm -f "${rootfs_ext4}"
  truncate -s "${rootfs_size_mb}M" "${rootfs_ext4}"
  sudo -n mkfs.ext4 -q -d "${rootfs_dir}" -F "${rootfs_ext4}"
fi

symphony_microvm_output_kv "MICROVM_ASSETS_STATUS" "prepared"
symphony_microvm_output_kv "MICROVM_ASSETS_RELEASE" "${release_tag}"
symphony_microvm_output_kv "MICROVM_ASSETS_ROOT" "${assets_root}"
symphony_microvm_output_kv "MICROVM_ASSETS_FIRECRACKER_BIN" "${firecracker_bin}"
symphony_microvm_output_kv "MICROVM_ASSETS_JAILER_BIN" "${jailer_bin}"
symphony_microvm_output_kv "MICROVM_ASSETS_KERNEL_PATH" "${kernel_path}"
symphony_microvm_output_kv "MICROVM_ASSETS_ROOTFS_PATH" "${rootfs_ext4}"
symphony_microvm_output_kv "MICROVM_ASSETS_SSH_KEY" "${ssh_private_key}"
