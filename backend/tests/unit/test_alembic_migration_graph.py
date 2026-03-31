"""Regression checks for the Alembic revision graph."""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path


VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"
REVISION_RE = re.compile(r"^revision\s*[:=]\s*(.+)$", re.MULTILINE)
DOWN_REVISION_RE = re.compile(r"^down_revision\s*[:=]\s*(.+)$", re.MULTILINE)


def _load_revision_graph() -> tuple[dict[str, tuple[str, object]], dict[str, set[str]]]:
    revisions: dict[str, tuple[str, object]] = {}
    children: dict[str, set[str]] = defaultdict(set)

    for path in VERSIONS_DIR.glob("*.py"):
        text = path.read_text()
        revision_match = REVISION_RE.search(text)
        down_revision_match = DOWN_REVISION_RE.search(text)
        if revision_match is None:
            continue

        revision = ast.literal_eval(revision_match.group(1).split("=", 1)[-1].strip())
        down_revision = None
        if down_revision_match is not None:
            down_revision = ast.literal_eval(
                down_revision_match.group(1).split("=", 1)[-1].strip()
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

    assert heads == ["q2r3s4t5u6v7"]
