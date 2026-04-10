"""Tests for Agent Studio read-only codebase inspection helpers."""

import subprocess

import pytest

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
    assert result["lines"] == [
        {"line_number": 2, "text": "two"},
        {"line_number": 3, "text": "three"},
    ]


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
