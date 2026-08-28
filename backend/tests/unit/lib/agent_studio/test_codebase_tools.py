"""Tests for Agent Studio read-only codebase inspection helpers."""

import subprocess
import hashlib
import json

import pytest

from src.api import agent_studio as api_module
from src.lib.agent_studio.diagnostic_tools import codebase_tools


def test_read_source_file_reads_requested_line_range(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    file_path = repo_root / "backend" / "src" / "demo.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_STUDIO_CODEBASE_ROOT", str(repo_root))

    result = codebase_tools.read_source_file(
        path="backend/src/demo.py",
        start_line=2,
        end_line=3,
    )

    assert result["status"] == "ok"
    assert result["path"] == "backend/src/demo.py"
    assert result["start_line"] == 2
    assert result["end_line"] == 3
    assert [(line["line_number"], line["text"]) for line in result["lines"]] == [(2, "two"), (3, "three")]
    assert all(line["line_truncated"] is False for line in result["lines"])


def test_read_source_file_rejects_path_traversal(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setenv("AGENT_STUDIO_CODEBASE_ROOT", str(repo_root))

    with pytest.raises(ValueError, match="within the repository root"):
        codebase_tools.read_source_file("../outside.txt")


def test_search_codebase_files_mode_finds_matching_paths(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    monkeypatch.setenv("AGENT_STUDIO_CODEBASE_ROOT", str(repo_root))
    monkeypatch.setattr(codebase_tools.shutil, "which", lambda name: "/usr/bin/rg" if name == "rg" else None)
    monkeypatch.setattr(
        codebase_tools.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=(
                f"{repo_root / 'backend' / 'src' / 'agent_studio.py'}\n"
                f"{repo_root / 'docs' / 'guide.md'}\n"
            ),
            stderr="",
        ),
    )

    result = codebase_tools.search_codebase(
        query="agent_studio",
        search_mode="files",
        limit=10,
    )

    assert result["status"] == "ok"
    assert result["search_mode"] == "files"
    assert result["results"] == [{"path": "backend/src/agent_studio.py"}]


def test_search_codebase_files_mode_normalizes_relative_rg_paths(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    monkeypatch.setenv("AGENT_STUDIO_CODEBASE_ROOT", str(repo_root))
    monkeypatch.setattr(codebase_tools.shutil, "which", lambda name: "/usr/bin/rg" if name == "rg" else None)
    monkeypatch.setattr(
        codebase_tools.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="backend/src/agent_studio.py\n",
            stderr="",
        ),
    )

    result = codebase_tools.search_codebase(
        query="agent_studio",
        search_mode="files",
        limit=10,
    )

    assert result["results"] == [{"path": "backend/src/agent_studio.py"}]


def test_search_codebase_content_mode_finds_matching_lines(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    monkeypatch.setenv("AGENT_STUDIO_CODEBASE_ROOT", str(repo_root))
    monkeypatch.setattr(codebase_tools.shutil, "which", lambda name: "/usr/bin/rg" if name == "rg" else None)
    monkeypatch.setattr(
        codebase_tools.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=(
                '{"type":"match","data":{"path":{"text":"'
                + str(repo_root / "backend" / "src" / "agent_studio.py")
                + '"},"lines":{"text":"tool_name = \\"search_codebase\\"\\n"},"line_number":2}}\n'
            ),
            stderr="",
        ),
    )

    result = codebase_tools.search_codebase(
        query="search_codebase",
        search_mode="content",
        limit=10,
    )

    assert result["status"] == "ok"
    assert result["search_mode"] == "content"
    assert result["results"]
    assert result["results"][0]["path"] == "backend/src/agent_studio.py"
    assert result["results"][0]["line_number"] == 2
    assert "search_codebase" in result["results"][0]["line_text"]


def test_search_codebase_content_mode_normalizes_relative_rg_paths(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    monkeypatch.setenv("AGENT_STUDIO_CODEBASE_ROOT", str(repo_root))
    monkeypatch.setattr(codebase_tools.shutil, "which", lambda name: "/usr/bin/rg" if name == "rg" else None)
    monkeypatch.setattr(
        codebase_tools.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=(
                '{"type":"match","data":{"path":{"text":"backend/src/agent_studio.py"},"lines":{"text":"tool_name = \\"search_codebase\\"\\n"},"line_number":2}}\n'
            ),
            stderr="",
        ),
    )

    result = codebase_tools.search_codebase(
        query="search_codebase",
        search_mode="content",
        limit=10,
    )

    assert result["results"][0]["path"] == "backend/src/agent_studio.py"
    assert result["results"][0]["line_number"] == 2


def test_search_codebase_reports_bounded_result_set_is_incomplete(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setenv("AGENT_STUDIO_CODEBASE_ROOT", str(repo_root))
    monkeypatch.setattr(codebase_tools, "_MAX_SEARCH_RESULTS", 2)
    monkeypatch.setattr(
        codebase_tools,
        "_iter_content_matches",
        lambda **_kwargs: iter(
            {
                "path": f"backend/src/file_{index}.py",
                "line_number": index + 1,
                "line_text": f"match {index}",
            }
            for index in range(3)
        ),
    )

    result = codebase_tools.search_codebase(query="match", limit=2)

    assert result["result_set_count"] == 2
    assert result["result_set_truncated"] is True
    assert result["complete"] is False
    assert result["truncated"] is False
    assert result["next_cursor"] is None
    assert result["next_call"] is None


def test_search_codebase_requires_rg(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setenv("AGENT_STUDIO_CODEBASE_ROOT", str(repo_root))
    monkeypatch.setattr(codebase_tools.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="ripgrep \\(rg\\) is required"):
        codebase_tools.search_codebase(
            query="agent_studio",
            search_mode="files",
            limit=10,
        )


def test_search_codebase_raises_clear_error_when_rg_times_out(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    monkeypatch.setenv("AGENT_STUDIO_CODEBASE_ROOT", str(repo_root))
    monkeypatch.setattr(codebase_tools.shutil, "which", lambda name: "/usr/bin/rg" if name == "rg" else None)

    def _raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["rg"], timeout=30)

    monkeypatch.setattr(codebase_tools.subprocess, "run", _raise_timeout)

    with pytest.raises(RuntimeError, match="timed out"):
        codebase_tools.search_codebase(
            query="agent_studio",
            search_mode="files",
            limit=10,
        )


def test_search_codebase_recovers_one_minified_match_in_bounded_exact_chunks(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    long_line = 'const payload = "' + ('🧬\\"' * 4_000) + '";'
    rg_payload = {"type": "match", "data": {"path": {"text": str(repo_root / "bundle.js")},
                  "lines": {"text": long_line + "\n"}, "line_number": 1}}
    monkeypatch.setenv("AGENT_STUDIO_CODEBASE_ROOT", str(repo_root))
    monkeypatch.setattr(codebase_tools, "_RESULT_MAX_CHARS", 1_200)
    monkeypatch.setattr(codebase_tools, "_LONG_LINE_CHUNK_MAX_CHARS", 900)
    monkeypatch.setattr(codebase_tools.shutil, "which", lambda _name: "/usr/bin/rg")
    monkeypatch.setattr(codebase_tools.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args=args[0], returncode=0,
                                                   stdout=json.dumps(rg_payload) + "\n", stderr=""))
    chunks, cursor = [], None
    while True:
        result = codebase_tools.search_codebase(query="payload", limit=1, cursor=cursor)
        assert len(json.dumps(result, default=str)) <= 1_200
        content = api_module._provider_tool_result_content(
            tool_name="search_codebase",
            tool_input={"query": "payload", "limit": 1, "cursor": cursor},
            tool_result=result,
            session_id="session-1",
            turn_id="turn-1",
        )
        assert json.loads(content).get("status") != "compacted_tool_result"
        chunks.append(result["results"][0]["line_text"])
        assert result["results"][0]["line_sha256"] == hashlib.sha256(long_line.encode()).hexdigest()
        if not result["truncated"]:
            break
        cursor = result["next_cursor"]
    assert "".join(chunks) == long_line


def test_read_source_file_recovers_minified_line_with_executable_continuations(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    long_line = "{" + ('\"🧬\":\"値\",' * 2_000) + "}"
    (repo_root / "minified.json").write_text(long_line + "\nnext\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_STUDIO_CODEBASE_ROOT", str(repo_root))
    monkeypatch.setattr(codebase_tools, "_RESULT_MAX_CHARS", 1_100)
    monkeypatch.setattr(codebase_tools, "_LONG_LINE_CHUNK_MAX_CHARS", 800)
    chunks = []
    arguments = {"path": "minified.json", "start_line": 1, "end_line": 1}
    while True:
        result = codebase_tools.read_source_file(**arguments)
        assert len(json.dumps(result, default=str)) <= 1_100
        content = api_module._provider_tool_result_content(
            tool_name="read_source_file",
            tool_input=arguments,
            tool_result=result,
            session_id="session-1",
            turn_id="turn-1",
        )
        assert json.loads(content).get("status") != "compacted_tool_result"
        chunks.extend(line["text"] for line in result["lines"])
        if not result["truncated"]:
            break
        arguments = result["next_call"]["arguments"]
    assert "".join(chunks) == long_line
