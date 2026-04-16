#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SERVICE="backend"
OUT_DIR="${EXPORT_STORAGE_PREFLIGHT_OUT_DIR:-/tmp/agr_ai_curation_export_storage_preflight}"

usage() {
  cat <<'EOF'
Usage: scripts/testing/file_output_storage_preflight.sh [--service SERVICE]

Runs an export-storage preflight against the live backend container by:
- probing direct write access to outputs/temp directories
- exercising FileOutputStorageService.save_output() end to end
- writing a JSON evidence file under /tmp by default

Environment:
  EXPORT_STORAGE_PREFLIGHT_OUT_DIR   Override evidence output directory
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service)
      SERVICE="${2:?--service requires a value}"
      shift 2
      ;;
    --help|-h)
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

mkdir -p "${OUT_DIR}"
STAMP_FILE="$(date -u +"%Y%m%dT%H%M%SZ")"
OUT_FILE="${OUT_DIR}/file_output_storage_preflight_${STAMP_FILE}.json"
TMP_FILE="$(mktemp)"

cleanup() {
  rm -f "${TMP_FILE}"
}
trap cleanup EXIT

status=0
docker compose exec -T "${SERVICE}" python - <<'PY' > "${TMP_FILE}" || status=$?
import json
from datetime import datetime, timezone
from pathlib import Path

from src.lib.file_outputs.storage import FileOutputStorageService


def mode_string(path: Path) -> str | None:
    try:
        return oct(path.stat().st_mode & 0o777)
    except FileNotFoundError:
        return None


def uid_gid(path: Path) -> dict[str, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return {"uid": stat.st_uid, "gid": stat.st_gid}


service = FileOutputStorageService()
timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
result: dict[str, object] = {
    "timestamp_utc": timestamp,
    "status": "pass",
    "base_path": str(service.base_path),
    "probes": [],
    "save_output": {},
    "errors": [],
}
errors: list[str] = []

probe_targets = [
    ("outputs", service.outputs_path),
    ("temp_processing", service.temp_processing_path),
    ("temp_failed", service.temp_failed_path),
]

for name, path in probe_targets:
    probe: dict[str, object] = {
        "name": name,
        "path": str(path),
        "exists": path.exists(),
        "mode": mode_string(path),
        "owner": uid_gid(path),
    }
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe_file = path / f".codex_export_preflight_{name}.tmp"
        probe_file.write_text("ok\n", encoding="utf-8")
        probe["write_result"] = "pass"
        probe_file.unlink()
    except Exception as exc:
        probe["write_result"] = "fail"
        probe["error"] = f"{type(exc).__name__}: {exc}"
        errors.append(f"{name}: {type(exc).__name__}: {exc}")
    result["probes"].append(probe)

session_id = "codex-export-preflight-session"
saved_path: Path | None = None
session_dir: Path | None = None
try:
    final_path, file_hash, file_size, warnings = service.save_output(
        trace_id="0123456789abcdef0123456789abcdef",
        session_id=session_id,
        content="col1,col2\nalpha,beta\n",
        file_type="csv",
        descriptor="export_storage_preflight",
    )
    saved_path = final_path
    session_dir = final_path.parent
    result["save_output"] = {
        "result": "pass",
        "path": str(final_path),
        "exists": final_path.exists(),
        "size_bytes": file_size,
        "file_hash_prefix": file_hash[:16],
        "warnings": warnings,
    }
except Exception as exc:
    result["save_output"] = {
        "result": "fail",
        "error": f"{type(exc).__name__}: {exc}",
    }
    errors.append(f"save_output: {type(exc).__name__}: {exc}")
finally:
    if saved_path is not None and saved_path.exists():
        saved_path.unlink()
    if session_dir is not None and session_dir.exists() and not any(session_dir.iterdir()):
        session_dir.rmdir()

if errors:
    result["status"] = "fail"
    result["errors"] = errors

print(json.dumps(result, indent=2, sort_keys=True))
raise SystemExit(1 if errors else 0)
PY

mv "${TMP_FILE}" "${OUT_FILE}"

if command -v python3 >/dev/null 2>&1; then
  python3 - "${OUT_FILE}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)

print(
    "Export storage preflight complete: "
    f"{payload.get('status', 'unknown')} "
    f"(base_path={payload.get('base_path', 'unknown')})"
)
PY
fi

echo "Evidence file: ${OUT_FILE}"
exit "${status}"
