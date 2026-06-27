#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SMOKE_SCRIPT_PATH="${ABC_LITERATURE_READY_UPLOAD_SMOKE_SCRIPT_PATH:-/app/scripts/testing/abc_literature_ready_upload_smoke.py}"
IS_ADD_LITERATURE_UPLOAD_SMOKE=0
if [[ "${SMOKE_SCRIPT_PATH}" == *"/add_literature_upload_smoke.py" ]]; then
  IS_ADD_LITERATURE_UPLOAD_SMOKE=1
fi

if [[ "${IS_ADD_LITERATURE_UPLOAD_SMOKE}" == "1" ]]; then
  SMOKE_ENV_FILE="${ADD_LITERATURE_UPLOAD_SMOKE_ENV_FILE:-${ABC_LITERATURE_READY_UPLOAD_SMOKE_ENV_FILE:-${HOME}/.agr_ai_curation/.env}}"
else
  SMOKE_ENV_FILE="${ABC_LITERATURE_READY_UPLOAD_SMOKE_ENV_FILE:-${HOME}/.agr_ai_curation/.env}"
fi
SMOKE_ENV_FILE="$(python3 - "${SMOKE_ENV_FILE}" <<'PY'
import os
import sys
from pathlib import Path

print(Path(os.path.expandvars(sys.argv[1])).expanduser())
PY
)"

eval "$(
  python3 - "${SMOKE_ENV_FILE}" "${IS_ADD_LITERATURE_UPLOAD_SMOKE}" <<'PY'
import os
import shlex
import sys
from pathlib import Path

keys = {
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_COMPOSE_FILE": "COMPOSE_FILE",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_DOCKER_SERVICE": "SMOKE_SERVICE",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_BACKEND_SERVICE": "BACKEND_SERVICE",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_DOCKER_BACKEND_BASE_URL": "BACKEND_BASE_URL",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_DOCKER_USER": "DOCKER_USER",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_PROFILE": "AWS_PROFILE_NAME",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_DIR": "AWS_DIR",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_ID_TOKEN": "CURATOR_ID_TOKEN",
}

path = Path(sys.argv[1])
is_add_literature_upload_smoke = sys.argv[2] == "1"
values: dict[str, str] = {}
if path.is_file():
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value

if is_add_literature_upload_smoke:
    keys.update(
        {
            "ADD_LITERATURE_UPLOAD_SMOKE_AWS_PROFILE": "AWS_PROFILE_NAME",
            "ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_ID_TOKEN": "CURATOR_ID_TOKEN",
        }
    )

for env_name, shell_name in keys.items():
    value = os.environ.get(env_name)
    if value is None or not value.strip():
        value = values.get(env_name, "")
    if not value:
        continue
    if shell_name == "CURATOR_ID_TOKEN":
        print("CURATOR_ID_TOKEN_PRESENT=1")
        continue
    if shell_name in {"AWS_DIR"}:
        value = str(Path(os.path.expandvars(value)).expanduser())
    print(f"{shell_name}={shlex.quote(value)}")
PY
)"

TMP_RUNTIME_DIR="$(mktemp -d)"
TMP_AWS_DIR="${TMP_RUNTIME_DIR}/aws"
TMP_SMOKE_ENV_FILE="${TMP_RUNTIME_DIR}/ready-upload-smoke.env"
cleanup_tmp_runtime_dir() {
  rm -rf "${TMP_RUNTIME_DIR}"
}
trap cleanup_tmp_runtime_dir EXIT

python3 - "${SMOKE_ENV_FILE}" "${TMP_SMOKE_ENV_FILE}" <<'PY'
import os
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
keys = (
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_LITERATURE_BASE_URL",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_REGION",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_USER_POOL_ID",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_CLIENT_ID",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_CLIENT_SECRET",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_AUTHORIZED_GROUPS",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_USERNAME",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_PASSWORD",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_ID_TOKEN",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_EVIDENCE_DIR",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_KNOWN_MD5",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_PMID",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_REFERENCE",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_SOURCE_REFERENCEFILE_ID",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_CONVERTED_REFERENCEFILE_ID",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_SOURCE_PDF_FILENAME",
    "ABC_LITERATURE_IDENTIFIER_IMPORT_SMOKE_IDENTIFIER",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_HTTP_TIMEOUT_SECONDS",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_UPLOAD_TIMEOUT_SECONDS",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_PROCESSING_TIMEOUT_SECONDS",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_POLL_INTERVAL_SECONDS",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_API_TIMEOUT_SECONDS",
    "ABC_LITERATURE_READY_UPLOAD_SMOKE_EVIDENCE_TAIL_LIMIT",
    "ABC_LITERATURE_LIVE_BASE_URL",
    "ABC_LITERATURE_LIVE_KNOWN_MD5",
    "ABC_LITERATURE_LIVE_PMID",
    "ABC_LITERATURE_LIVE_REFERENCE",
    "ABC_LITERATURE_LIVE_CONVERTED_REFERENCEFILE_ID",
    "ABC_LITERATURE_SMOKE_AWS_REGION",
    "ABC_LITERATURE_SMOKE_USER_POOL_ID",
    "ABC_LITERATURE_SMOKE_CLIENT_ID",
    "ABC_LITERATURE_SMOKE_CLIENT_SECRET",
    "ABC_LITERATURE_SMOKE_BASE_URL",
    "ABC_LITERATURE_SMOKE_AUTHORIZED_GROUPS",
    "ABC_LITERATURE_SMOKE_EVIDENCE_DIR",
    "ABC_LITERATURE_SMOKE_KNOWN_MD5",
    "ABC_LITERATURE_SMOKE_PMID",
    "ABC_LITERATURE_SMOKE_REFERENCE",
    "ABC_LITERATURE_SMOKE_SOURCE_REFERENCEFILE_ID",
    "ABC_LITERATURE_SMOKE_CONVERTED_REFERENCEFILE_ID",
    "ADD_LITERATURE_UPLOAD_SMOKE_BACKEND_BASE_URL",
    "ADD_LITERATURE_UPLOAD_SMOKE_AWS_PROFILE",
    "ADD_LITERATURE_UPLOAD_SMOKE_AWS_REGION",
    "ADD_LITERATURE_UPLOAD_SMOKE_USER_POOL_ID",
    "ADD_LITERATURE_UPLOAD_SMOKE_CLIENT_ID",
    "ADD_LITERATURE_UPLOAD_SMOKE_CLIENT_SECRET",
    "ADD_LITERATURE_UPLOAD_SMOKE_AUTHORIZED_GROUPS",
    "ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_USERNAME",
    "ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_PASSWORD",
    "ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_ID_TOKEN",
    "ADD_LITERATURE_UPLOAD_SMOKE_EVIDENCE_DIR",
    "ADD_LITERATURE_UPLOAD_SMOKE_SAMPLE_PDF",
    "ADD_LITERATURE_UPLOAD_SMOKE_HTTP_TIMEOUT_SECONDS",
    "ADD_LITERATURE_UPLOAD_SMOKE_UPLOAD_TIMEOUT_SECONDS",
    "ADD_LITERATURE_UPLOAD_SMOKE_PROCESSING_TIMEOUT_SECONDS",
    "ADD_LITERATURE_UPLOAD_SMOKE_POLL_INTERVAL_SECONDS",
    "ADD_LITERATURE_UPLOAD_SMOKE_AWS_API_TIMEOUT_SECONDS",
    "ADD_LITERATURE_UPLOAD_SMOKE_EVIDENCE_TAIL_LIMIT",
    "ADD_LITERATURE_UPLOAD_SMOKE_JOB_LIST_WINDOW_DAYS",
    "ADD_LITERATURE_UPLOAD_SMOKE_JOB_LIST_LIMIT",
)
values: dict[str, str] = {}
if source_path.is_file():
    for raw_line in source_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value

target_path.parent.mkdir(parents=True, exist_ok=True)
with target_path.open("w", encoding="utf-8") as handle:
    handle.write("ABC_LITERATURE_READY_UPLOAD_SMOKE_ENV_FILE=/tmp/abc-ready-upload-smoke.env\n")
    handle.write("ABC_LITERATURE_READY_UPLOAD_SMOKE_BACKEND_BASE_URL=http://backend:8000\n")
    handle.write("ABC_LITERATURE_READY_UPLOAD_SMOKE_EVIDENCE_DIR=file_outputs/temp\n")
    for key in keys:
        value = os.environ.get(key)
        if value is None:
            value = values.get(key)
        if value is None or value == "":
            continue
        if key in {
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_ENV_FILE",
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_BACKEND_BASE_URL",
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_EVIDENCE_DIR",
        }:
            continue
        handle.write(f"{key}={value}\n")
target_path.chmod(0o600)
PY

COMPOSE_FILE="${ABC_LITERATURE_READY_UPLOAD_SMOKE_COMPOSE_FILE:-${COMPOSE_FILE:-docker-compose.yml}}"
SMOKE_SERVICE="${ABC_LITERATURE_READY_UPLOAD_SMOKE_DOCKER_SERVICE:-${SMOKE_SERVICE:-backend}}"
BACKEND_SERVICE="${ABC_LITERATURE_READY_UPLOAD_SMOKE_BACKEND_SERVICE:-${BACKEND_SERVICE:-backend}}"
BACKEND_BASE_URL="${ABC_LITERATURE_READY_UPLOAD_SMOKE_DOCKER_BACKEND_BASE_URL:-${BACKEND_BASE_URL:-http://backend:8000}}"
DEFAULT_DOCKER_USER="$(id -u):$(id -g)"
DOCKER_USER="${ABC_LITERATURE_READY_UPLOAD_SMOKE_DOCKER_USER:-${DOCKER_USER:-${DEFAULT_DOCKER_USER}}}"
if [[ "${IS_ADD_LITERATURE_UPLOAD_SMOKE}" == "1" ]]; then
  AWS_PROFILE_NAME="${ADD_LITERATURE_UPLOAD_SMOKE_AWS_PROFILE:-${AWS_PROFILE_NAME:-${AWS_PROFILE:-ctabone}}}"
else
  AWS_PROFILE_NAME="${ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_PROFILE:-${AWS_PROFILE_NAME:-${AWS_PROFILE:-ctabone}}}"
fi
AWS_DIR="${ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_DIR:-${AWS_DIR:-${HOME}/.aws}}"
AWS_DIR="$(python3 - "${AWS_DIR}" <<'PY'
import os
import sys
from pathlib import Path

print(Path(os.path.expandvars(sys.argv[1])).expanduser())
PY
)"
CURATOR_ID_TOKEN_PRESENT="${CURATOR_ID_TOKEN_PRESENT:-}"
if [[ "${COMPOSE_FILE}" = /* ]]; then
  COMPOSE_PATH="${COMPOSE_FILE}"
else
  COMPOSE_PATH="${REPO_ROOT}/${COMPOSE_FILE}"
fi

AWS_CREDENTIALS_FILE="${AWS_DIR}/credentials"
AWS_CONFIG_SOURCE_FILE="${AWS_DIR}/config"
if [[ -z "${CURATOR_ID_TOKEN_PRESENT}" && ! -r "${AWS_CREDENTIALS_FILE}" && ! -r "${AWS_CONFIG_SOURCE_FILE}" ]]; then
  echo "AWS credential directory not found: ${AWS_DIR}" >&2
  echo "Set ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_DIR, unlock the expected AWS profile, or set ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_ID_TOKEN." >&2
  exit 2
fi

if ! docker compose -f "${COMPOSE_PATH}" ps --services --status running | grep -qx "${BACKEND_SERVICE}"; then
  echo "The ${BACKEND_SERVICE} service is not running for ${COMPOSE_FILE}." >&2
  echo "Start a Cognito + ABC Literature configured Docker stack first, then rerun this smoke." >&2
  exit 2
fi

cd "${REPO_ROOT}"

if [[ -z "${CURATOR_ID_TOKEN_PRESENT}" ]]; then
  mkdir -p "${TMP_AWS_DIR}"
  python3 - "${AWS_PROFILE_NAME}" "${AWS_CREDENTIALS_FILE}" "${AWS_CONFIG_SOURCE_FILE}" "${TMP_AWS_DIR}" <<'PY'
import configparser
import sys
from pathlib import Path

profile, credentials_path, config_path, output_dir = sys.argv[1:]
credentials_file = Path(credentials_path)
config_file = Path(config_path)
output_path = Path(output_dir)


def read_ini(path: Path) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser()
    if path.is_file():
        parser.read(path)
    return parser


def config_section_name(name: str) -> str:
    return "default" if name == "default" else f"profile {name}"


def include_section(
    source: configparser.RawConfigParser,
    target: configparser.RawConfigParser,
    section: str,
) -> None:
    if not source.has_section(section):
        return
    if not target.has_section(section):
        target.add_section(section)
    for key, value in source.items(section):
        target.set(section, key, value)


source_credentials = read_ini(credentials_file)
source_config = read_ini(config_file)
target_credentials = configparser.RawConfigParser()
target_config = configparser.RawConfigParser()

pending = [profile]
seen: set[str] = set()
while pending:
    current = pending.pop()
    if current in seen:
        continue
    seen.add(current)

    include_section(source_credentials, target_credentials, current)
    section = config_section_name(current)
    include_section(source_config, target_config, section)

    if source_config.has_section(section):
        source_profile = source_config.get(section, "source_profile", fallback="").strip()
        if source_profile:
            pending.append(source_profile)

        sso_session = source_config.get(section, "sso_session", fallback="").strip()
        if sso_session:
            include_section(source_config, target_config, f"sso-session {sso_session}")

if not target_credentials.sections() and not target_config.sections():
    raise SystemExit(f"AWS profile {profile!r} was not found in {credentials_file} or {config_file}")

output_path.mkdir(parents=True, exist_ok=True)
credentials_output = output_path / "credentials"
config_output = output_path / "config"
with credentials_output.open("w") as handle:
    target_credentials.write(handle)
with config_output.open("w") as handle:
    target_config.write(handle)
credentials_output.chmod(0o600)
config_output.chmod(0o600)
PY
fi

mkdir -p "${REPO_ROOT}/file_outputs"

env_args=(
  -e "ABC_LITERATURE_READY_UPLOAD_SMOKE_BACKEND_BASE_URL=${BACKEND_BASE_URL}"
  -e "ABC_LITERATURE_READY_UPLOAD_SMOKE_ENV_FILE=/tmp/abc-ready-upload-smoke.env"
)
if [[ "${IS_ADD_LITERATURE_UPLOAD_SMOKE}" == "1" ]]; then
  env_args+=(
    -e "ADD_LITERATURE_UPLOAD_SMOKE_BACKEND_BASE_URL=${BACKEND_BASE_URL}"
    -e "ADD_LITERATURE_UPLOAD_SMOKE_ENV_FILE=/tmp/abc-ready-upload-smoke.env"
    -e "ADD_LITERATURE_UPLOAD_SMOKE_AWS_PROFILE=${AWS_PROFILE_NAME}"
  )
fi

volume_args=(
  -v "${REPO_ROOT}/scripts:/app/scripts:ro"
  -v "${REPO_ROOT}/file_outputs:/app/file_outputs"
  -v "${TMP_SMOKE_ENV_FILE}:/tmp/abc-ready-upload-smoke.env:ro"
)

if [[ -z "${CURATOR_ID_TOKEN_PRESENT}" ]]; then
  env_args+=(
    -e "AWS_PROFILE=${AWS_PROFILE_NAME}"
    -e "AWS_EC2_METADATA_DISABLED=true"
    -e "AWS_CONFIG_FILE=/tmp/abc-ready-upload-smoke-aws/config"
    -e "AWS_SHARED_CREDENTIALS_FILE=/tmp/abc-ready-upload-smoke-aws/credentials"
    -e "ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_PROFILE=${AWS_PROFILE_NAME}"
  )
  volume_args+=(-v "${TMP_AWS_DIR}:/tmp/abc-ready-upload-smoke-aws:ro")
fi

exec env \
  -u ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_PASSWORD \
  -u ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_ID_TOKEN \
  -u ABC_LITERATURE_READY_UPLOAD_SMOKE_CLIENT_SECRET \
  -u ABC_LITERATURE_SMOKE_CLIENT_SECRET \
  -u ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_PASSWORD \
  -u ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_ID_TOKEN \
  -u ADD_LITERATURE_UPLOAD_SMOKE_CLIENT_SECRET \
  docker compose -f "${COMPOSE_PATH}" run --rm --no-deps \
  --entrypoint python \
  --user "${DOCKER_USER}" \
  "${env_args[@]}" \
  "${volume_args[@]}" \
  "${SMOKE_SERVICE}" \
  "${SMOKE_SCRIPT_PATH}" "$@" \
  --evidence-dir file_outputs/temp
