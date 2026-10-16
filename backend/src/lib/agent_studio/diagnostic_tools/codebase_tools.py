"""Read-only codebase inspection helpers for Agent Studio diagnostic tools."""

from __future__ import annotations

import os
import shutil
import subprocess
import json
import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.lib.openai_agents.config import (
    get_codebase_file_list_max_results,
    get_codebase_search_max_results,
    get_codebase_search_timeout_seconds,
)


_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[5]
# Env-configurable (defaults unchanged); see config.py getters and .env.example:
#   CODEBASE_SEARCH_MAX_RESULTS,
#   CODEBASE_FILE_LIST_MAX_RESULTS, CODEBASE_SEARCH_TIMEOUT_SECONDS.
_MAX_SEARCH_RESULTS = get_codebase_search_max_results()
_MAX_FILE_LIST_RESULTS = get_codebase_file_list_max_results()
_RG_SUBPROCESS_TIMEOUT_SECONDS = get_codebase_search_timeout_seconds()
# Inspect application source, not the deployment filesystem. In particular,
# config/connections.yaml, .env, uploads and execution artifacts are not source.
_SOURCE_PREFIXES = (
    "backend/src/", "backend/tests/", "frontend/src/", "docs/", "scripts/",
    "packages/", "config/agents/",
)
_SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".yaml", ".yml", ".json", ".css", ".sh", ".toml"}
_SOURCE_ROOT_FILES = {"README.md", "AGENTS.md", "backend/main.py", "config/README.md"}


def _is_source_path(path: str) -> bool:
    """Use the same source boundary for direct reads and search results."""
    relative = Path(path)
    if any(part.startswith(".") or part.lower() in {
        "node_modules", "__pycache__", "secrets", "credentials", "uploads", "file_outputs",
    } for part in relative.parts):
        return False
    if relative.stem.lower() in {"credentials", "secrets", "connections"}:
        return False
    return path in _SOURCE_ROOT_FILES or (
        path.startswith(_SOURCE_PREFIXES) and relative.suffix.lower() in _SOURCE_SUFFIXES
    )




def _parse_search_cursor(cursor: Optional[str]) -> tuple[int, int]:
    if cursor is None or not str(cursor).strip():
        return 0, 0
    parts = str(cursor).split(":", 1)
    try:
        match_index = int(parts[0])
        line_char = int(parts[1]) if len(parts) == 2 else 0
    except ValueError as exc:
        raise ValueError("cursor must be a search_codebase next_cursor") from exc
    if match_index < 0 or line_char < 0:
        raise ValueError("cursor must be a search_codebase next_cursor")
    return match_index, line_char


def _line_record(match: Dict[str, Any], start: int, end: int) -> Dict[str, Any]:
    text = str(match["line_text"])
    return {
        "path": match["path"], "line_number": match["line_number"],
        "line_text": text[start:end],
        "line_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "line_total_chars": len(text), "line_char_start": start,
        "line_char_end": end, "line_truncated": end < len(text),
    }


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
    if not _is_source_path(str(candidate.relative_to(root))):
        raise ValueError("Only application source and documentation can be inspected; deployment files and private data are unavailable")
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
    # Stable path order keeps cursor-based paging deterministic across rg runs.
    command = [rg_path, "--files", "--sort", "path", "."]
    if path_glob:
        command.extend(["-g", path_glob])
    try:
        completed = subprocess.run(
            command,
            cwd=root,
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
        if _is_source_path(relative) and lowered in relative.lower():
            yield {"path": relative}


class InvalidSearchRegex(ValueError):
    """The ripgrep engine rejected the caller's regular expression."""


def _iter_content_matches(
    root: Path,
    query: str,
    path_glob: Optional[str],
    per_file_matches: int,
    match_mode: str = "regex",
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
        "--sort",
        "path",
        "--max-count",
        str(per_file_matches),
        "--max-filesize",
        "1M",
    ]
    if path_glob:
        command.extend(["-g", path_glob])
    if match_mode == "literal":
        command.append("--fixed-strings")
    command.extend(["--", query, "."])
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=_RG_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("rg content search timed out") from exc
    if completed.returncode not in (0, 1):
        # Use the actual regex engine's diagnostic, not Python's different
        # grammar. Other exit-2 errors (including glob/I/O errors) stay fatal.
        if completed.returncode == 2 and completed.stderr.startswith(
            ("regex parse error:\n", "rg: regex parse error:\n")
        ):
            raise InvalidSearchRegex("Invalid ripgrep regular expression")
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
        if not _is_source_path(relative):
            continue
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
    cursor: Optional[str] = None,
    match_mode: str = "regex",
) -> Dict[str, Any]:
    """Search the runtime repository by filename or file content."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required")

    if search_mode not in {"content", "files"}:
        raise ValueError("search_mode must be 'content' or 'files'")

    if match_mode not in {"regex", "literal"}:
        return {"status": "error", "success": False, "error": "invalid_match_mode",
                "message": "match_mode must be 'regex' or 'literal'."}

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
            match_mode=match_mode,
        )

    max_catalog_results = _MAX_SEARCH_RESULTS if search_mode == "content" else _MAX_FILE_LIST_RESULTS
    matches: List[Dict[str, Any]] = []
    result_set_truncated = False
    try:
        for match in iterator:
            if len(matches) >= max_catalog_results:
                result_set_truncated = True
                break
            matches.append(match)
    except InvalidSearchRegex:
        # Do not echo stderr/query: both can be large or contain private text.
        return {"status": "error", "success": False, "error": "invalid_regex",
                "message": "Invalid ripgrep regular expression. Correct or escape the regex, "
                           "or retry with match_mode='literal' to search exact text such as function_name(."}

    match_index, line_char_start = _parse_search_cursor(cursor)
    if match_index > len(matches) or (match_index == len(matches) and line_char_start):
        raise ValueError("cursor is beyond the bounded search result set")
    results: List[Dict[str, Any]] = []

    def response(next_cursor: Optional[str]) -> Dict[str, Any]:
        next_call = None
        if next_cursor is not None:
            next_call = {"tool": "search_codebase", "arguments": {
                "query": query.strip(), "search_mode": search_mode, "match_mode": match_mode,
                **({"path_glob": path_glob} if path_glob else {}),
                "per_file_matches": per_file_matches, "limit": limit, "cursor": next_cursor,
            }}
        return {"status": "ok", "search_mode": search_mode, "match_mode": match_mode, "query": query.strip(),
                "path_glob": path_glob, "repo_root": str(root), "results": results,
                "result_count": len(results), "result_set_count": len(matches),
                "result_set_truncated": result_set_truncated,
                "complete": next_cursor is None and not result_set_truncated,
                "truncated": next_cursor is not None, "next_cursor": next_cursor,
                "next_call": next_call}

    current_index = match_index
    while current_index < len(matches) and len(results) < limit:
        match = matches[current_index]
        if search_mode == "files":
            results.append(dict(match))
            current_index += 1
            continue

        text = str(match["line_text"])
        start = line_char_start if current_index == match_index else 0
        if start > len(text):
            raise ValueError("cursor line character offset is beyond the matched line")
        results.append(_line_record(match, start, len(text)))
        current_index += 1
        line_char_start = 0
    return response(str(current_index) if current_index < len(matches) else None)


def read_source_file(
    path: str,
    start_line: int = 1,
    end_line: Optional[int] = None,
    line_char_start: int = 0,
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
    if line_char_start < 0:
        raise ValueError("line_char_start must be >= 0")

    try:
        raw_text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"file is not valid UTF-8 text: {path}") from exc

    lines = raw_text.splitlines()
    if start_line > len(lines) + 1:
        raise ValueError("start_line is beyond the end of the file")
    requested_end = min(end_line or len(lines), len(lines))
    numbered_lines: List[Dict[str, Any]] = []
    relative_path = _relative_repo_path(target)

    line_number = start_line
    while line_number <= requested_end:
        text = lines[line_number - 1]
        start = line_char_start if line_number == start_line else 0
        if start > len(text):
            raise ValueError("line_char_start is beyond the selected source line")
        full_record = {"line_number": line_number, "text": text[start:],
                       "line_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                       "line_total_chars": len(text), "line_char_start": start,
                       "line_char_end": len(text), "line_truncated": False}
        numbered_lines.append(full_record)
        line_number += 1
    return {
        "status": "ok", "path": relative_path, "repo_root": str(get_codebase_root()),
        "start_line": start_line,
        "end_line": numbered_lines[-1]["line_number"] if numbered_lines else start_line - 1,
        "total_lines": len(lines), "lines": numbered_lines,
        "truncated": False, "next_call": None,
    }
