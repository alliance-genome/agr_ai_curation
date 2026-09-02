"""Regression checks for the Alembic revision graph."""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

import pytest


VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _literal_assignment(module: ast.Module, name: str) -> object:
    for node in module.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                if node.value is None:
                    return None
                return ast.literal_eval(node.value)
    raise KeyError(name)


def _load_revision_graph() -> tuple[dict[str, tuple[str, object]], dict[str, set[str]]]:
    revisions: dict[str, tuple[str, object]] = {}
    children: dict[str, set[str]] = defaultdict(set)

    for path in VERSIONS_DIR.glob("*.py"):
        text = path.read_text()
        module = ast.parse(text, filename=str(path))
        try:
            revision = _literal_assignment(module, "revision")
        except KeyError:
            continue
        try:
            down_revision = _literal_assignment(module, "down_revision")
        except KeyError:
            down_revision = None
        assert isinstance(revision, str)
        assert down_revision is None or isinstance(down_revision, str | tuple)
        if isinstance(down_revision, tuple):
            assert all(isinstance(parent, str) for parent in down_revision)

        assert revision not in revisions, (
            f"duplicate Alembic revision {revision!r}: "
            f"{revisions[revision][0]} and {path.name}"
        )
        revisions[revision] = (path.name, down_revision)

        if isinstance(down_revision, tuple):
            for parent in down_revision:
                children[parent].add(revision)
        elif down_revision is not None:
            children[down_revision].add(revision)

    return revisions, children


def test_alembic_revision_graph_has_single_head():
    revisions, children = _load_revision_graph()

    heads = sorted(revision for revision in revisions if revision not in children)

    assert heads == ["h5c6d7e8f9a0"]


def test_alembic_revision_graph_rejects_duplicate_revision_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    for name in ("first.py", "second.py"):
        (tmp_path / name).write_text(
            'revision = "duplicate"\ndown_revision = None\n'
        )
    monkeypatch.setattr(sys.modules[__name__], "VERSIONS_DIR", tmp_path)

    with pytest.raises(
        AssertionError,
        match=r"duplicate Alembic revision 'duplicate': .* and .*",
    ):
        _load_revision_graph()
