#!/usr/bin/env python3
"""Small repo-local LSP helper for agent navigation.

This is intentionally narrower than a full MCP/LSP bridge. It gives agents a
stable CLI for cheap workspace freshness checks and a few semantic navigation
queries while keeping runtime state isolated per workspace root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


DEFAULT_CACHE_ROOT = Path.home() / ".cache" / "agr-ai-curation" / "agent-lsp"
SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}
# Symphony runs this helper from lightweight issue workspaces that often do not
# have the backend virtualenv installed. Treat missing-import/module-source
# Pyright rules as environment baseline noise so third-party dependency gaps do
# not hide actionable diagnostics in changed files. If diagnostics later run in
# a fully provisioned Python env, narrow this before relying on it for import
# contract coverage.
PYRIGHT_DEPENDENCY_RESOLUTION_RULES = {
    "reportMissingImports",
    "reportMissingModuleSource",
}


def run_command(
    args: list[str],
    *,
    cwd: Path,
    timeout: float = 10.0,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=check,
    )


def git_lines(root: Path, args: list[str]) -> list[str]:
    try:
        completed = run_command(["git", *args], cwd=root, timeout=10)
    except Exception:
        return []
    if completed.returncode != 0:
        return []
    return [line for line in completed.stdout.splitlines() if line.strip()]


def file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_hash(root: Path) -> str:
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:24]


def cache_dir_for(root: Path, cache_root: Path = DEFAULT_CACHE_ROOT) -> Path:
    return cache_root / workspace_hash(root)


def changed_source_files(root: Path) -> list[str]:
    names = set(git_lines(root, ["diff", "--name-only"]))
    names.update(git_lines(root, ["diff", "--name-only", "--cached"]))
    names.update(git_lines(root, ["ls-files", "--others", "--exclude-standard"]))
    return sorted(
        name
        for name in names
        if (root / name).suffix in SOURCE_EXTENSIONS and (root / name).exists()
    )


def workspace_fingerprint(root: Path) -> dict[str, Any]:
    config_paths = [
        ".gitignore",
        "backend/requirements.txt",
        "backend/requirements.lock.txt",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/tsconfig.json",
        "frontend/tsconfig.node.json",
        "pyrightconfig.json",
        "ruff.toml",
        "pyproject.toml",
    ]
    config_hashes = {
        rel: digest
        for rel in config_paths
        if (digest := file_hash(root / rel)) is not None
    }
    return {
        "workspace_root": str(root),
        "head": (git_lines(root, ["rev-parse", "HEAD"]) or ["unknown"])[0],
        "branch": (git_lines(root, ["branch", "--show-current"]) or ["unknown"])[0],
        "tracked_changes": git_lines(root, ["diff", "--name-only"]),
        "staged_changes": git_lines(root, ["diff", "--name-only", "--cached"]),
        "untracked_source_files": [
            name
            for name in git_lines(root, ["ls-files", "--others", "--exclude-standard"])
            if (root / name).suffix in SOURCE_EXTENSIONS
        ],
        "config_hashes": config_hashes,
    }


def fingerprint_digest(fingerprint: dict[str, Any]) -> str:
    raw = json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def workspace_lock(cache_dir: Path, timeout: float):
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_dir = cache_dir / "lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock_dir.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for LSP workspace lock: {lock_dir}")
            time.sleep(0.1)
    try:
        yield
    finally:
        try:
            lock_dir.rmdir()
        except OSError:
            pass


def command_version(command: str, args: list[str]) -> dict[str, Any]:
    path = shutil.which(command)
    if path is None:
        return {"command": command, "available": False, "path": None, "version": None}
    try:
        completed = subprocess.run(
            [command, *args],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        version = (completed.stdout or completed.stderr).strip().splitlines()
    except Exception as exc:
        return {
            "command": command,
            "available": True,
            "path": path,
            "version": None,
            "error": str(exc),
        }
    return {
        "command": command,
        "available": True,
        "path": path,
        "version": version[0] if version else "",
    }


def command_available(command: str, version: str | None = None) -> dict[str, Any]:
    path = shutil.which(command)
    return {
        "command": command,
        "available": path is not None,
        "path": path,
        "version": version if path is not None else None,
    }


def detect_languages(root: Path) -> list[str]:
    languages: list[str] = []
    if (root / "backend").is_dir() or any(root.glob("*.py")):
        languages.append("python")
    if (root / "frontend" / "tsconfig.json").is_file() or (root / "package.json").is_file():
        languages.append("typescript")
    return languages


def warm_workspace(root: Path, timeout: float) -> dict[str, Any]:
    root = root.resolve()
    cache_dir = cache_dir_for(root)
    state_file = cache_dir / "state.json"
    fingerprint = workspace_fingerprint(root)
    digest = fingerprint_digest(fingerprint)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    with workspace_lock(cache_dir, timeout=max(1.0, min(timeout, 30.0))):
        previous: dict[str, Any] = {}
        if state_file.is_file():
            try:
                previous = json.loads(state_file.read_text())
            except json.JSONDecodeError:
                previous = {}

        refreshed = previous.get("fingerprint_digest") != digest
        pyright_version = command_version("pyright", ["--version"])
        tool_versions = {
            "pyright": pyright_version,
            "pyright-langserver": command_available(
                "pyright-langserver",
                pyright_version.get("version") if pyright_version.get("available") else None,
            ),
            "ruff": command_version("ruff", ["--version"]),
            "typescript-language-server": command_version(
                "typescript-language-server", ["--version"]
            ),
        }

        state = {
            "status": "ready",
            "reason": "fingerprint_changed" if refreshed else "fingerprint_unchanged",
            "workspace_root": str(root),
            "cache_dir": str(cache_dir),
            "fingerprint_digest": digest,
            "fingerprint": fingerprint,
            "languages": detect_languages(root),
            "tool_versions": tool_versions,
            "refreshed": refreshed,
            "updated_at": now,
        }
        state_file.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        return state


class LspClient:
    def __init__(self, cmd: list[str], cwd: Path):
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._next_id = 0
        self.messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self.stderr: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr.append(line.decode("utf-8", "replace").rstrip())

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                return
            headers: dict[str, str] = {}
            while line not in (b"\r\n", b"\n", b""):
                key, _, value = line.decode("ascii", "replace").partition(":")
                headers[key.lower()] = value.strip()
                line = self.proc.stdout.readline()
            length = int(headers.get("content-length", "0"))
            if length <= 0:
                continue
            body = self.proc.stdout.read(length)
            self.messages.put(json.loads(body.decode("utf-8")))

    def send(self, payload: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.proc.stdin.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw)
        self.proc.stdin.flush()

    def request(self, method: str, params: Any, timeout: float) -> Any:
        self._next_id += 1
        request_id = self._next_id
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=0.25)
            except queue.Empty:
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(message["error"])
            return message.get("result")
        raise TimeoutError(f"Timed out waiting for {method}; stderr={self.stderr[-5:]}")

    def notify(self, method: str, params: Any) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def close(self) -> None:
        try:
            self.request("shutdown", None, timeout=5)
        except Exception:
            pass
        try:
            self.notify("exit", None)
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def language_for(path: Path) -> str:
    if path.suffix == ".py":
        return "python"
    if path.suffix == ".tsx":
        return "typescriptreact"
    if path.suffix == ".ts":
        return "typescript"
    if path.suffix == ".jsx":
        return "javascriptreact"
    return "javascript"


def lsp_root_for(root: Path, path: Path) -> Path:
    if path.suffix in {".ts", ".tsx", ".js", ".jsx"} and (root / "frontend").is_dir():
        try:
            path.resolve().relative_to((root / "frontend").resolve())
            return root / "frontend"
        except ValueError:
            return root
    return root


def lsp_command_for(path: Path) -> list[str]:
    if path.suffix == ".py":
        return ["pyright-langserver", "--stdio"]
    if path.suffix in {".ts", ".tsx", ".js", ".jsx"}:
        return ["typescript-language-server", "--stdio", "--log-level", "1"]
    raise ValueError(f"Unsupported file extension for LSP query: {path.suffix}")


def open_lsp_document(root: Path, path: Path, timeout: float) -> tuple[LspClient, Path]:
    lsp_root = lsp_root_for(root, path)
    client = LspClient(lsp_command_for(path), lsp_root)
    client.request(
        "initialize",
        {
            "processId": os.getpid(),
            "rootUri": file_uri(lsp_root),
            "capabilities": {
                "textDocument": {
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    "definition": {"linkSupport": True},
                    "references": {},
                },
                "workspace": {"symbol": {}},
            },
            "clientInfo": {"name": "agr-agent-lsp", "version": "0"},
        },
        timeout=timeout,
    )
    client.notify("initialized", {})
    client.notify(
        "textDocument/didOpen",
        {
            "textDocument": {
                "uri": file_uri(path),
                "languageId": language_for(path),
                "version": 1,
                "text": path.read_text(),
            }
        },
    )
    time.sleep(min(1.5, max(0.2, timeout / 4)))
    return client, lsp_root


def summarize_symbols(symbols: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def walk(items: Any, parent: str | None = None) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            selection_range = item.get("selectionRange") or item.get("range") or {}
            start = selection_range.get("start") or {}
            if isinstance(name, str):
                result.append(
                    {
                        "name": name,
                        "kind": item.get("kind"),
                        "parent": parent,
                        "line": start.get("line"),
                        "character": start.get("character"),
                    }
                )
            walk(item.get("children"), name if isinstance(name, str) else parent)

    walk(symbols)
    return result


def normalize_locations(locations: Any) -> list[dict[str, Any]]:
    if locations is None:
        return []
    items = locations if isinstance(locations, list) else [locations]
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        target_uri = item.get("targetUri") or item.get("uri")
        target_range = item.get("targetSelectionRange") or item.get("range") or {}
        start = target_range.get("start") or {}
        result.append(
            {
                "uri": target_uri,
                "line": start.get("line"),
                "character": start.get("character"),
            }
        )
    return result


def lsp_document_request(
    *,
    root: Path,
    path: Path,
    method: str,
    params: dict[str, Any],
    timeout: float,
) -> Any:
    client, _lsp_root = open_lsp_document(root, path, timeout)
    try:
        return client.request(method, params, timeout=timeout)
    finally:
        client.close()


def zero_based_position(line: int, character: int, zero_based: bool) -> dict[str, int]:
    if zero_based:
        return {"line": line, "character": character}
    return {"line": max(0, line - 1), "character": max(0, character - 1)}


def classify_pyright_diagnostics(
    diagnostics: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    dependency_resolution: list[dict[str, Any]] = []
    actionable: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        if diagnostic.get("rule") in PYRIGHT_DEPENDENCY_RESOLUTION_RULES:
            dependency_resolution.append(diagnostic)
        else:
            actionable.append(diagnostic)
    return dependency_resolution, actionable


def diagnostic_location(diagnostic: dict[str, Any]) -> str:
    file_name = diagnostic.get("file") or "<unknown>"
    start = (diagnostic.get("range") or {}).get("start") or {}
    line = int(start.get("line") or 0) + 1
    character = int(start.get("character") or 0) + 1
    return f"{file_name}:{line}:{character}"


def summarize_pyright_diagnostic(diagnostic: dict[str, Any]) -> dict[str, Any]:
    start = (diagnostic.get("range") or {}).get("start") or {}
    line = start.get("line")
    character = start.get("character")
    return {
        "file": diagnostic.get("file"),
        "line": int(line) + 1 if line is not None else None,
        "character": int(character) + 1 if character is not None else None,
        "severity": diagnostic.get("severity"),
        "message": diagnostic.get("message"),
        "rule": diagnostic.get("rule"),
    }


def render_pyright_actionable_output(
    actionable_diagnostics: list[dict[str, Any]],
    dependency_resolution_count: int,
) -> str:
    lines: list[str] = []
    if actionable_diagnostics:
        lines.append("Pyright actionable diagnostics:")
        for diagnostic in actionable_diagnostics:
            severity = diagnostic.get("severity") or "diagnostic"
            message = diagnostic.get("message") or ""
            rule = diagnostic.get("rule")
            suffix = f" ({rule})" if rule else ""
            lines.append(f"  {diagnostic_location(diagnostic)} - {severity}: {message}{suffix}")
    else:
        lines.append("Pyright actionable diagnostics: none")

    if dependency_resolution_count:
        lines.append(
            "Dependency-resolution diagnostics classified as baseline noise: "
            f"{dependency_resolution_count}"
        )

    counts = {"error": 0, "warning": 0, "information": 0}
    for diagnostic in actionable_diagnostics:
        severity = diagnostic.get("severity")
        if severity in counts:
            counts[severity] += 1
    lines.append(
        f"{counts['error']} errors, {counts['warning']} warnings, "
        f"{counts['information']} informations"
    )
    return "\n".join(lines) + "\n"


def run_pyright_diagnostics(root: Path, py_files: list[str], timeout: float) -> dict[str, Any]:
    completed = run_command(["pyright", *py_files, "--outputjson"], cwd=root, timeout=timeout)
    command: dict[str, Any] = {
        "name": "pyright",
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return command

    diagnostics = payload.get("generalDiagnostics")
    if not isinstance(diagnostics, list):
        return command

    dependency_resolution, actionable = classify_pyright_diagnostics(
        [diagnostic for diagnostic in diagnostics if isinstance(diagnostic, dict)]
    )
    actionable_error_count = sum(
        1 for diagnostic in actionable if diagnostic.get("severity") == "error"
    )
    if completed.returncode in (0, 1):
        command["returncode"] = 1 if actionable_error_count else 0
    command.update(
        {
            "raw_returncode": completed.returncode,
            "raw_stdout": completed.stdout,
            "stdout": render_pyright_actionable_output(
                actionable,
                len(dependency_resolution),
            ),
            "actionable_diagnostic_count": len(actionable),
            "actionable_error_count": actionable_error_count,
            "dependency_resolution_noise_count": len(dependency_resolution),
            "dependency_resolution_noise": [
                summarize_pyright_diagnostic(diagnostic)
                for diagnostic in dependency_resolution
            ],
        }
    )
    return command


def run_diagnostics(root: Path, files: list[str], timeout: float) -> dict[str, Any]:
    py_files = [name for name in files if Path(name).suffix == ".py"]
    ts_files = [name for name in files if Path(name).suffix in {".ts", ".tsx", ".js", ".jsx"}]
    commands: list[dict[str, Any]] = []

    if py_files and shutil.which("ruff"):
        completed = run_command(["ruff", "check", *py_files], cwd=root, timeout=timeout)
        commands.append(
            {
                "name": "ruff",
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
    if py_files and shutil.which("pyright"):
        commands.append(run_pyright_diagnostics(root, py_files, timeout))
    if ts_files and (root / "frontend" / "package.json").is_file():
        completed = run_command(
            ["npm", "run", "type-check:changed", "--", "--base", "origin/main"],
            cwd=root / "frontend",
            timeout=timeout,
        )
        commands.append(
            {
                "name": "frontend type-check:changed",
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
    return {
        "status": "ok" if all(command["returncode"] == 0 for command in commands) else "failed",
        "files": files,
        "commands": commands,
    }


def emit(data: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(data, indent=2, sort_keys=True))
        return
    if output_format == "env":
        for key, value in data.items():
            env_key = f"AGENT_LSP_{key.upper()}"
            if isinstance(value, (dict, list)):
                env_value = json.dumps(value, sort_keys=True)
            else:
                env_value = "" if value is None else str(value)
            env_value = env_value.replace("\n", " ")
            print(f"{env_key}={env_value}")
        return
    raise ValueError(f"Unsupported output format: {output_format}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Typical use:
  agent_lsp.py status
  agent_lsp.py symbols backend/src/example.py
  agent_lsp.py definition backend/src/example.py 120 17
  agent_lsp.py references frontend/src/example.tsx 42 9
  agent_lsp.py --timeout 30 diagnostics --changed

Commands:
  status       Show cached workspace LSP state and whether warm state exists.
  symbols      Print a compact outline of classes, functions, methods, variables,
               and other document symbols for one Python/TypeScript file.
  definition   Jump from a file position to the symbol definition location.
  references   Find known references for the symbol at a file position.
  diagnostics  Run scoped Ruff/Pyright/frontend changed-file diagnostics.
  cleanup      Remove old per-workspace LSP cache state.
  warm         Refresh workspace LSP state. Usually automatic in Symphony lanes;
               run manually only for local smoke testing or stale-state recovery.

Use rg first for broad text/file discovery. Use this helper when symbol identity
matters: definitions, references, imports/exports, large-file outlines, or
reviewing shared API changes.

In Symphony In Progress and In Review lanes, LSP warmup is automatic through the
lane brief helpers. Run `warm` manually only for local smoke testing or recovery
after a clearly stale or missing LSP state.
""",
    )
    parser.add_argument("--root", default=".", help="Workspace root. Default: current directory.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Command timeout in seconds.")
    parser.add_argument("--format", choices=("json", "env"), default="json", help="Output format.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "status",
        help="Show cached workspace LSP state.",
        description="Show cached workspace LSP state without refreshing it.",
    )
    subparsers.add_parser(
        "warm",
        help="Refresh workspace LSP state; normally automatic in Symphony lanes.",
        description=(
            "Refresh workspace LSP state. In Symphony In Progress and In Review lanes, "
            "the lane helpers run this automatically; run it manually only for local "
            "smoke testing or recovery after stale/missing state."
        ),
    )

    symbols_parser = subparsers.add_parser(
        "symbols",
        help="List document symbols for one Python/TypeScript file.",
    )
    symbols_parser.add_argument("file", help="File path relative to --root.")

    for name in ("definition", "references"):
        request_parser = subparsers.add_parser(
            name,
            help=f"Find {name} for a symbol position in one file.",
            description=(
                f"Find {name} for a symbol position. Line and character are 1-based "
                "by default; use --zero-based only for raw editor/LSP coordinates."
            ),
        )
        request_parser.add_argument("file", help="File path relative to --root.")
        request_parser.add_argument("line", type=int, help="Line number, 1-based by default.")
        request_parser.add_argument(
            "character", type=int, help="Character number, 1-based by default."
        )
        request_parser.add_argument(
            "--zero-based",
            action="store_true",
            help="Treat line and character as zero-based raw LSP coordinates.",
        )

    diagnostics_parser = subparsers.add_parser(
        "diagnostics",
        help="Run scoped diagnostics for changed files or explicit files.",
        description=(
            "Run scoped diagnostics with Ruff/Pyright for Python and the existing "
            "frontend type-check:changed guard for TypeScript. This is a navigation "
            "and review aid, not a replacement for required lane validation."
        ),
    )
    diagnostics_parser.add_argument(
        "--changed",
        action="store_true",
        help="Diagnose changed source files from git diff/staged/untracked source state.",
    )
    diagnostics_parser.add_argument("files", nargs="*", help="Files to diagnose.")

    cleanup_parser = subparsers.add_parser(
        "cleanup",
        help="Remove old per-workspace LSP cache state.",
    )
    cleanup_parser.add_argument("--older-than-hours", type=float, default=8.0)

    args = parser.parse_args()
    root = Path(args.root).resolve()

    if args.command == "warm":
        emit(warm_workspace(root, args.timeout), args.format)
        return 0

    if args.command == "status":
        cache_dir = cache_dir_for(root)
        state_file = cache_dir / "state.json"
        if state_file.is_file():
            data = json.loads(state_file.read_text())
            data["state_file"] = str(state_file)
        else:
            data = {
                "status": "missing",
                "reason": "state_file_missing",
                "workspace_root": str(root),
                "cache_dir": str(cache_dir),
            }
        emit(data, args.format)
        return 0

    if args.command == "cleanup":
        cutoff = time.time() - args.older_than_hours * 3600
        removed = 0
        DEFAULT_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        for child in DEFAULT_CACHE_ROOT.iterdir():
            if not child.is_dir():
                continue
            state_file = child / "state.json"
            mtime = state_file.stat().st_mtime if state_file.exists() else child.stat().st_mtime
            if mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        emit({"status": "ok", "removed": removed, "cache_root": str(DEFAULT_CACHE_ROOT)}, args.format)
        return 0

    if args.command == "diagnostics":
        files = changed_source_files(root) if args.changed else args.files
        emit(run_diagnostics(root, files, args.timeout), args.format)
        return 0

    path = (root / args.file).resolve()
    if not path.is_file():
        raise SystemExit(f"File does not exist: {path}")

    text_document = {"uri": file_uri(path)}
    if args.command == "symbols":
        symbols = lsp_document_request(
            root=root,
            path=path,
            method="textDocument/documentSymbol",
            params={"textDocument": text_document},
            timeout=args.timeout,
        )
        emit(
            {
                "status": "ok",
                "file": str(path),
                "symbols": summarize_symbols(symbols),
            },
            args.format,
        )
        return 0

    position = zero_based_position(args.line, args.character, args.zero_based)
    if args.command == "definition":
        method = "textDocument/definition"
        params = {"textDocument": text_document, "position": position}
    elif args.command == "references":
        method = "textDocument/references"
        params = {
            "textDocument": text_document,
            "position": position,
            "context": {"includeDeclaration": True},
        }
    else:
        raise SystemExit(f"Unsupported command: {args.command}")

    locations = lsp_document_request(
        root=root,
        path=path,
        method=method,
        params=params,
        timeout=args.timeout,
    )
    emit(
        {
            "status": "ok",
            "file": str(path),
            "position": position,
            "locations": normalize_locations(locations),
        },
        args.format,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
