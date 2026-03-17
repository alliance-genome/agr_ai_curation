#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  symphony_microvm_guest_run.sh <manifest-path> <result-path> [bundle-path] [source-ref] [snapshot-path] [snapshot-manifest-path] [codex-home]

Behavior:
  - Materializes the repo into a persistent guest workspace on first run.
  - Executes `codex exec` inside that workspace using the manifest prompt.
  - Writes a JSON result file with the execution outcome.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

manifest_path="${1:-}"
result_path="${2:-}"
bundle_path="${3:-}"
source_ref="${4:-main}"
snapshot_path="${5:-}"
snapshot_manifest_path="${6:-}"
codex_home_path="${7:-}"

if [[ -z "${manifest_path}" || -z "${result_path}" ]]; then
  usage >&2
  exit 2
fi

if [[ ! -f "${manifest_path}" ]]; then
  echo "Manifest does not exist: ${manifest_path}" >&2
  exit 2
fi

python3 - "${manifest_path}" "${result_path}" "${bundle_path}" "${source_ref}" "${snapshot_path}" "${snapshot_manifest_path}" "${codex_home_path}" <<'PY'
import json
import os
import pathlib
import subprocess
import shutil
import socket
import sys
import tarfile
import fcntl

manifest_path, result_path, bundle_path, source_ref, snapshot_path, snapshot_manifest_path, codex_home_path = sys.argv[1:8]
manifest_file = pathlib.Path(manifest_path)
result_file = pathlib.Path(result_path)
changes_archive_path = result_file.with_suffix(".changes.tgz")
deleted_paths_path = result_file.with_suffix(".deleted.json")

with manifest_file.open("r", encoding="utf-8") as handle:
    manifest = json.load(handle)

workspace_dir = pathlib.Path("/root/workspace/repo")
workspace_parent = workspace_dir.parent
workspace_parent.mkdir(parents=True, exist_ok=True)
lock_file_path = pathlib.Path("/root/symphony/active-run.lock")

materialization_method = "existing_workspace"
lock_handle = None


def run(cmd, *, cwd=None, input_text=None, check=True):
    return subprocess.run(
        cmd,
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
        check=check,
    )


def run_bytes(cmd, *, cwd=None, check=True):
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        check=check,
    )


def acquire_run_lock():
    global lock_handle

    lock_file_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = lock_file_path.open("a+", encoding="utf-8")

    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_handle.seek(0)
        active = lock_handle.read().strip()
        write_result(
            "failed",
            setup_stage="acquire_run_lock",
            setup_exit_code=75,
            stdout_tail="",
            stderr_tail=f"another guest run is already active: {active or 'unknown'}",
            final_message="",
            codex_exit_code=None,
        )
        raise SystemExit(75)

    lock_handle.seek(0)
    lock_handle.truncate(0)
    lock_handle.write(manifest.get("run_id", "unknown"))
    lock_handle.flush()


def release_run_lock():
    global lock_handle

    if lock_handle is None:
        return

    try:
        lock_handle.seek(0)
        lock_handle.truncate(0)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock_handle.close()
        lock_handle = None


def ensure_git_repo():
    git_dir = workspace_dir / ".git"
    if git_dir.exists():
        return

    init_proc = run(["git", "init", "-b", source_ref], cwd=workspace_dir, check=False)
    if init_proc.returncode != 0:
        write_result(
            "failed",
            setup_stage="init_workspace_git",
            setup_exit_code=init_proc.returncode,
            stdout_tail=init_proc.stdout[-4000:],
            stderr_tail=init_proc.stderr[-4000:],
            final_message="",
            codex_exit_code=None,
        )
        raise SystemExit(init_proc.returncode)

    run(["git", "config", "user.name", "Symphony Worker"], cwd=workspace_dir, check=False)
    run(["git", "config", "user.email", "symphony-worker@local"], cwd=workspace_dir, check=False)


def load_snapshot_paths():
    if not snapshot_manifest_path or not pathlib.Path(snapshot_manifest_path).exists():
        return None

    entries = pathlib.Path(snapshot_manifest_path).read_bytes().split(b"\0")
    normalized = set()

    for raw_entry in entries:
        if not raw_entry:
            continue
        rel_path = normalized_rel_path(raw_entry.decode("utf-8", errors="replace"))
        if rel_path is not None:
            normalized.add(rel_path)

    return normalized


def extract_snapshot():
    global materialization_method

    if not snapshot_path or not pathlib.Path(snapshot_path).exists():
        return

    with tarfile.open(snapshot_path, "r:gz") as archive:
        for member in archive.getmembers():
            rel_path = normalized_rel_path(member.name)
            if rel_path is None or member.issym() or member.islnk():
                write_result(
                    "failed",
                    setup_stage="validate_snapshot",
                    setup_exit_code=2,
                    stdout_tail="",
                    stderr_tail=f"unsafe snapshot member: {member.name}",
                    final_message="",
                    codex_exit_code=None,
                )
                raise SystemExit(2)

    proc = run(["tar", "-xzf", snapshot_path, "-C", str(workspace_dir)], check=False)
    if proc.returncode != 0:
        write_result(
            "failed",
            setup_stage="extract_snapshot",
            setup_exit_code=proc.returncode,
            stdout_tail=proc.stdout[-4000:],
            stderr_tail=proc.stderr[-4000:],
            final_message="",
            codex_exit_code=None,
        )
        raise SystemExit(proc.returncode)

    materialization_method = "snapshot" if materialization_method == "existing_workspace" else materialization_method


def reconcile_snapshot_paths(snapshot_paths):
    if snapshot_paths is None:
        return

    keep_paths = set(snapshot_paths)
    keep_paths.add(".git")

    existing_files = sorted(
        path.relative_to(workspace_dir).as_posix()
        for path in workspace_dir.rglob("*")
        if path.exists() or path.is_symlink()
        if ".git" not in path.relative_to(workspace_dir).parts
    )

    for rel_path in reversed(existing_files):
        if rel_path in keep_paths:
            continue

        candidate = workspace_dir / rel_path
        if candidate.is_symlink() or candidate.is_file():
            candidate.unlink(missing_ok=True)
        elif candidate.is_dir():
            try:
                candidate.rmdir()
            except OSError:
                pass


def refresh_host_baseline():
    ensure_git_repo()
    run(["git", "add", "-A"], cwd=workspace_dir, check=False)
    status_proc = run(
        ["git", "status", "--porcelain=v1", "--ignore-submodules=dirty"],
        cwd=workspace_dir,
        check=False,
    )
    if not status_proc.stdout.strip():
        return

    commit_proc = run(
        ["git", "commit", "-m", f"Symphony host baseline {manifest.get('run_id')}"],
        cwd=workspace_dir,
        check=False,
    )
    if commit_proc.returncode != 0:
        write_result(
            "failed",
            setup_stage="commit_host_baseline",
            setup_exit_code=commit_proc.returncode,
            stdout_tail=commit_proc.stdout[-4000:],
            stderr_tail=commit_proc.stderr[-4000:],
            final_message="",
            codex_exit_code=None,
        )
        raise SystemExit(commit_proc.returncode)

def write_result(status, **extra):
    payload = {
        "status": status,
        "hostname": socket.gethostname(),
        "manifest_issue_identifier": manifest.get("issue", {}).get("identifier"),
        "manifest_run_id": manifest.get("run_id"),
        "workspace_dir": str(workspace_dir),
        "materialization_method": materialization_method,
        "has_codex": shutil.which("codex") is not None,
        "host_sync": {
            "workspace_authoritative": True,
            "remote_authoritative": "origin",
            "host_branch": os.environ.get("SYMPHONY_HOST_BRANCH"),
            "host_head_sha": os.environ.get("SYMPHONY_HOST_HEAD_SHA"),
            "origin_branch_sha": os.environ.get("SYMPHONY_ORIGIN_BRANCH_SHA"),
            "origin_main_sha": os.environ.get("SYMPHONY_ORIGIN_MAIN_SHA"),
        },
    }
    payload.update(extra)
    with result_file.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def normalized_rel_path(value):
    candidate = pathlib.PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    return candidate.as_posix()


def collect_workspace_changes():
    status_proc = run_bytes(
        ["git", "status", "--porcelain=v1", "-z", "--ignore-submodules=dirty"],
        cwd=workspace_dir,
        check=False,
    )
    changed_paths = []
    deleted_paths = []
    entries = status_proc.stdout.split(b"\0")
    index = 0

    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue

        line = entry.decode("utf-8", errors="surrogateescape")
        status = line[:2]
        path_info = line[3:]

        if ("R" in status or "C" in status) and index < len(entries):
            old_path = path_info
            new_path = entries[index].decode("utf-8", errors="surrogateescape")
            index += 1
            if (normalized_old := normalized_rel_path(old_path)):
                deleted_paths.append(normalized_old)
            if (normalized_new := normalized_rel_path(new_path)):
                changed_paths.append(normalized_new)
            continue

        normalized_path = normalized_rel_path(path_info)
        if normalized_path is None:
            continue

        if status == "??" or any(flag in status for flag in ("A", "M", "T")):
            changed_paths.append(normalized_path)
        elif "D" in status:
            deleted_paths.append(normalized_path)
        elif status.strip():
            changed_paths.append(normalized_path)

    changed_paths = list(dict.fromkeys(changed_paths))
    deleted_paths = list(dict.fromkeys(deleted_paths))

    if changed_paths:
        with tarfile.open(changes_archive_path, "w:gz") as archive:
            for rel_path in changed_paths:
                full_path = workspace_dir / rel_path
                if full_path.exists() or full_path.is_symlink():
                    archive.add(full_path, arcname=rel_path, recursive=True)

    with deleted_paths_path.open("w", encoding="utf-8") as handle:
        json.dump(deleted_paths, handle)

    return changed_paths, deleted_paths


acquire_run_lock()

try:
    if not workspace_dir.exists():
        workspace_dir.mkdir(parents=True, exist_ok=True)
        if snapshot_path and pathlib.Path(snapshot_path).exists():
            materialization_method = "snapshot"
        elif bundle_path:
            proc = run(["git", "clone", bundle_path, str(workspace_dir)], check=False)
            if proc.returncode != 0:
                write_result(
                    "failed",
                    setup_stage="clone_bundle",
                    setup_exit_code=proc.returncode,
                    stdout_tail=proc.stdout[-4000:],
                    stderr_tail=proc.stderr[-4000:],
                    final_message="",
                    codex_exit_code=None,
                )
                raise SystemExit(proc.returncode)
            checkout_proc = run(["git", "checkout", "-B", source_ref], cwd=workspace_dir, check=False)
            if checkout_proc.returncode != 0:
                write_result(
                    "failed",
                    setup_stage="checkout_bundle_ref",
                    setup_exit_code=checkout_proc.returncode,
                    stdout_tail=checkout_proc.stdout[-4000:],
                    stderr_tail=checkout_proc.stderr[-4000:],
                    final_message="",
                    codex_exit_code=None,
                )
                raise SystemExit(checkout_proc.returncode)
            materialization_method = "bundle"
        else:
            write_result(
                "failed",
                setup_stage="materialize_workspace",
                setup_exit_code=2,
                stdout_tail="",
                stderr_tail="bundle path or snapshot path required for first-run repo materialization",
                final_message="",
                codex_exit_code=None,
            )
            raise SystemExit(2)

    extract_snapshot()
    reconcile_snapshot_paths(load_snapshot_paths())
    refresh_host_baseline()

    prompt = manifest.get("prompt", "")
    last_message_path = result_file.with_suffix(".last_message.txt")

    env = os.environ.copy()
    env.setdefault("HOME", "/root")
    if codex_home_path:
        env["CODEX_HOME"] = codex_home_path

    proc = subprocess.run(
        [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--output-last-message",
            str(last_message_path),
            "-C",
            str(workspace_dir),
            "-",
        ],
        cwd=workspace_dir,
        input=prompt,
        text=True,
        capture_output=True,
        env=env,
    )

    changed_paths, deleted_paths = collect_workspace_changes()

    payload = {
        "status": "completed" if proc.returncode == 0 else "failed",
        "hostname": socket.gethostname(),
        "manifest_issue_identifier": manifest.get("issue", {}).get("identifier"),
        "manifest_run_id": manifest.get("run_id"),
        "workspace_dir": str(workspace_dir),
        "materialization_method": materialization_method,
        "codex_exit_code": proc.returncode,
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
        "final_message": last_message_path.read_text(encoding="utf-8", errors="replace") if last_message_path.exists() else "",
        "has_codex": shutil.which("codex") is not None,
        "changed_paths": changed_paths,
        "deleted_paths": deleted_paths,
        "changes_archive_path": str(changes_archive_path) if changes_archive_path.exists() else "",
        "deleted_paths_json": str(deleted_paths_path),
    }

    with result_file.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle)

    raise SystemExit(proc.returncode)
finally:
    release_run_lock()
PY
