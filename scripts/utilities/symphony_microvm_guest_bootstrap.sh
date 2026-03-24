#!/usr/bin/env bash

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export DPKG_FORCE=confdef,confold

BOOTSTRAP_MARKER="/root/.symphony_bootstrap_complete"
APT_PACKAGES=(
  ca-certificates
  curl
  docker.io
  git
  jq
  lsb-release
  nodejs
  npm
  python3
  python3-venv
  ripgrep
  unzip
  wget
)

# Erlang/OTP and Elixir versions for Symphony's Elixir dashboard
# Update checksums when bumping versions (sha256sum the downloaded file)
ERLANG_ESL_VERSION="27.3-1"
ERLANG_SHA256="d0ca69c38ea41a83d3d6616b980ae70f83452ebb57f848930df3f8d01c09520b"  # noble/amd64
ELIXIR_VERSION="1.19.1"
ELIXIR_OTP_MAJOR="27"
ELIXIR_SHA256="a7642130f2c4a66ddf5922cf9289144665c2de7f2a0eae3a6490bade5fafe33e"  # elixir-otp-27.zip

if [[ -f "${BOOTSTRAP_MARKER}" ]]; then
  echo "GUEST_BOOTSTRAP_STATUS=already_complete"
  exit 0
fi

apt_get_install_with_recovery() {
  local attempt

  apt-get update

  for attempt in 1 2; do
    if apt-get install -y "${APT_PACKAGES[@]}"; then
      return 0
    fi

    dpkg --configure -a || true
    apt-get install -f -y || true
    sleep 2
  done

  return 1
}

apt_get_install_with_recovery

npm install -g @openai/codex

mkdir -p /root/.codex /root/symphony/runs /root/symphony/bundles /root/workspace

# --- Erlang/OTP (ESL package) ---
arch="$(dpkg --print-architecture)"
erlang_deb="esl-erlang_${ERLANG_ESL_VERSION}~ubuntu~$(lsb_release -cs)_${arch}.deb"
erlang_url="https://binaries2.erlang-solutions.com/ubuntu/pool/contrib/e/esl-erlang/${erlang_deb}"

if ! erl -eval 'halt().' -noshell >/dev/null 2>&1; then
  wget -q "${erlang_url}" -O "/tmp/${erlang_deb}"
  echo "${ERLANG_SHA256}  /tmp/${erlang_deb}" | sha256sum -c - || {
    echo "SECURITY: Erlang checksum mismatch — aborting" >&2; exit 1
  }
  dpkg -i "/tmp/${erlang_deb}" || apt-get install -f -y
  rm -f "/tmp/${erlang_deb}"
fi

# --- Elixir (precompiled release) ---
elixir_zip="elixir-otp-${ELIXIR_OTP_MAJOR}.zip"
elixir_url="https://github.com/elixir-lang/elixir/releases/download/v${ELIXIR_VERSION}/${elixir_zip}"

if ! command -v elixir >/dev/null 2>&1; then
  wget -q "${elixir_url}" -O "/tmp/${elixir_zip}"
  echo "${ELIXIR_SHA256}  /tmp/${elixir_zip}" | sha256sum -c - || {
    echo "SECURITY: Elixir checksum mismatch — aborting" >&2; exit 1
  }
  mkdir -p /usr/local/elixir
  unzip -o -q "/tmp/${elixir_zip}" -d /usr/local/elixir
  for bin in elixir elixirc mix iex; do
    ln -sf "/usr/local/elixir/bin/${bin}" "/usr/local/bin/${bin}"
  done
  rm -f "/tmp/${elixir_zip}"
fi

# Ensure hex is installed (needed for mix deps)
if ! ELIXIR_ERL_OPTIONS="+fnu" mix local.hex --force 2>&1; then
  echo "WARNING: mix local.hex failed — mix deps.get will fail without hex" >&2
fi

apt-get clean || true
rm -rf /var/lib/apt/lists/*

if command -v systemctl >/dev/null 2>&1; then
  systemctl enable docker >/dev/null 2>&1 || true
  systemctl start docker >/dev/null 2>&1 || true
fi

if command -v service >/dev/null 2>&1; then
  service docker start >/dev/null 2>&1 || true
fi

date -u +%Y-%m-%dT%H:%M:%SZ > "${BOOTSTRAP_MARKER}"
echo "GUEST_BOOTSTRAP_STATUS=completed"
