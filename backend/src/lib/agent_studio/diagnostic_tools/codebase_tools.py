"""Read-only codebase inspection helpers for Agent Studio diagnostic tools."""

from __future__ import annotations

import os
import shutil
import subprocess
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[5]
_MAX_READ_LINES = 400
_MAX_SEARCH_RESULTS = 100
_MAX_FILE_LIST_RESULTS = 200
_RG_SUBPROCESS_TIMEOUT_SECONDS = 30


def get_codebase_root() -> Path:
    """Resolve the repository root available to Agent Studio code inspection."""
    configured = os.getenv("AGENT_STUDIO_CODEBASE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return _DEFAULT_REPO_ROOT


def _resolve_repo_path(path: str) -> Path:
    """Resolve a repository-relative path and reject traversal outside the repo."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path is required")

    root = get_codebase_root()
    candidate = (root / path.strip()).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("path must stay within the repository root") from exc
    return candidate


def _relative_repo_path(path: Path) -> str:
    return str(path.relative_to(get_codebase_root()))


def _normalize_rg_path(root: Path, raw_path: str) -> str:
    """Normalize rg output to a repository-relative path."""
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve(strict=False)
    return str(candidate.relative_to(root))


def _require_rg() -> str:
    """Resolve the rg binary or fail with a clear runtime error."""
    rg_path = shutil.which("rg")
    if not rg_path:
        raise RuntimeError("ripgrep (rg) is required for Agent Studio codebase inspection")
    return rg_path


def _iter_file_matches(root: Path, query: str, path_glob: Optional[str]) -> Iterable[Dict[str, Any]]:
    """Yield file path matches using rg."""
    rg_path = _require_rg()
    command = [rg_path, "--files", str(root)]
    if path_glob:
        command.extend(["-g", path_glob])
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=_RG_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("rg file listing timed out") from exc
    if completed.returncode not in (0, 1):
        raise RuntimeError(completed.stderr.strip() or "rg --files failed")

    lowered = query.lower()
    for raw_line in completed.stdout.splitlines():
        relative = _normalize_rg_path(root, raw_line.strip())
        if lowered in relative.lower():
            yield {"path": relative}


def _iter_content_matches(
    root: Path,
    query: str,
    path_glob: Optional[str],
    per_file_matches: int,
) -> Iterable[Dict[str, Any]]:
    """Yield content matches using rg."""
    rg_path = _require_rg()
    command = [
        rg_path,
        "--json",
        "--line-number",
        "--color",
        "never",
        "--smart-case",
        "--max-count",
        str(per_file_matches),
        "--max-filesize",
        "1M",
    ]
    if path_glob:
        command.extend(["-g", path_glob])
    command.extend([query, str(root)])
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=_RG_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("rg content search timed out") from exc
    if completed.returncode not in (0, 1):
        raise RuntimeError(completed.stderr.strip() or "rg search failed")

    for raw_line in completed.stdout.splitlines():
        if not raw_line.strip():
            continue
        payload = json.loads(raw_line)
        if payload.get("type") != "match":
            continue
        data = payload["data"]
        path_text = data["path"]["text"]
        relative = _normalize_rg_path(root, path_text)
        yield {
            "path": relative,
            "line_number": data["line_number"],
            "line_text": data["lines"]["text"].rstrip("\n"),
        }


def search_codebase(
    query: str,
    search_mode: str = "content",
    path_glob: Optional[str] = None,
    per_file_matches: int = 1,
    limit: int = 20,
) -> Dict[str, Any]:
    """Search the runtime repository by filename or file content."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required")

    if search_mode not in {"content", "files"}:
        raise ValueError("search_mode must be 'content' or 'files'")

    if per_file_matches < 1 or per_file_matches > 20:
        raise ValueError("per_file_matches must be between 1 and 20")

    limit = max(1, min(limit, _MAX_SEARCH_RESULTS if search_mode == "content" else _MAX_FILE_LIST_RESULTS))
    root = get_codebase_root()

    iterator: Iterable[Dict[str, Any]]
    if search_mode == "files":
        iterator = _iter_file_matches(root=root, query=query.strip(), path_glob=path_glob)
    else:
        iterator = _iter_content_matches(
            root=root,
            query=query.strip(),
            path_glob=path_glob,
            per_file_matches=per_file_matches,
        )

    results: List[Dict[str, Any]] = []
    truncated = False
    for match in iterator:
        if len(results) >= limit:
            truncated = True
            break
        results.append(match)

    return {
        "status": "ok",
        "search_mode": search_mode,
        "query": query.strip(),
        "path_glob": path_glob,
        "repo_root": str(root),
        "results": results,
        "result_count": len(results),
        "truncated": truncated,
    }


def read_source_file(
    path: str,
    start_line: int = 1,
    end_line: Optional[int] = None,
) -> Dict[str, Any]:
    """Read a repository file with line-numbered output."""
    target = _resolve_repo_path(path)
    if not target.exists():
        raise ValueError(f"path does not exist: {path}")
    if not target.is_file():
        raise ValueError(f"path is not a file: {path}")

    if start_line < 1:
        raise ValueError("start_line must be >= 1")
    if end_line is not None and end_line < start_line:
        raise ValueError("end_line must be >= start_line")

    requested_end = end_line or (start_line + _MAX_READ_LINES - 1)
    actual_end = min(requested_end, start_line + _MAX_READ_LINES - 1)

    try:
        raw_text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"file is not valid UTF-8 text: {path}") from exc

    lines = raw_text.splitlines()
    selection = lines[start_line - 1:actual_end]
    numbered_lines = [
        {"line_number": start_line + offset, "text": line}
        for offset, line in enumerate(selection)
    ]

    return {
        "status": "ok",
        "path": _relative_repo_path(target),
        "repo_root": str(get_codebase_root()),
        "start_line": start_line,
        "end_line": actual_end,
        "total_lines": len(lines),
        "truncated": actual_end < requested_end,
        "lines": numbered_lines,
    }
