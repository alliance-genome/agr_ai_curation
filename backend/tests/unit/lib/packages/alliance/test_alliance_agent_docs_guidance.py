"""Every shipped Alliance agent docs.yaml carries curator-voice usage guidance."""
from pathlib import Path

import pytest
import yaml

from tests.unit.lib.packages import find_repo_root

ALLIANCE_AGENTS_DIR = find_repo_root(Path(__file__)) / "packages" / "alliance" / "agents"

GUIDANCE_KEYS = ("use_when", "avoid_when")


def _shipped_docs_files() -> list[Path]:
    return sorted(
        path
        for path in ALLIANCE_AGENTS_DIR.glob("*/docs.yaml")
        if not path.parent.name.startswith("_")
    )


@pytest.mark.parametrize(
    "docs_path", _shipped_docs_files(), ids=lambda path: path.parent.name
)
def test_alliance_agent_docs_have_usage_guidance(docs_path: Path):
    docs = yaml.safe_load(docs_path.read_text(encoding="utf-8"))
    assert isinstance(docs, dict), f"{docs_path}: docs.yaml must be a mapping"
    for key in GUIDANCE_KEYS:
        values = docs.get(key)
        assert isinstance(values, list) and values, (
            f"{docs_path.parent.name}: '{key}' must be a non-empty list"
        )
        for value in values:
            assert isinstance(value, str) and value.strip(), (
                f"{docs_path.parent.name}: '{key}' entries must be plain sentences"
            )
    limitations = docs.get("limitations")
    assert isinstance(limitations, list) and limitations, (
        f"{docs_path.parent.name}: 'limitations' must remain a non-empty list"
    )


def test_alliance_agent_docs_dir_is_discovered():
    assert _shipped_docs_files(), f"no docs.yaml found under {ALLIANCE_AGENTS_DIR}"
