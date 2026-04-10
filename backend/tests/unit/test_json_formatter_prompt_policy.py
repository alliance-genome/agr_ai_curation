from pathlib import Path

import pytest
import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_prompt_content(relative_path: str) -> str:
    prompt_path = _repo_root() / relative_path
    data = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    content = data.get("content")
    if content is None:
        raise ValueError(f"Missing 'content' key in {prompt_path}")
    return str(content)


@pytest.mark.parametrize(
    "relative_path",
    [
        "packages/alliance/agents/json_formatter/prompt.yaml",
        "alliance_agents/json_formatter/prompt.yaml",
    ],
)
def test_json_formatter_prompt_uses_runtime_tool_contract(relative_path: str):
    content = _load_prompt_content(relative_path)

    assert "`data_json` (required)" in content
    assert "`filename` (required)" in content
    assert "`pretty` (optional)" in content
    assert "Do not paste JSON into the assistant response" in content
    assert "filename_hint" not in content
    assert "\nFormatted JSON output:\n" not in content


def test_json_formatter_prompt_copies_stay_in_sync():
    package_prompt = _load_prompt_content("packages/alliance/agents/json_formatter/prompt.yaml")
    legacy_prompt = _load_prompt_content("alliance_agents/json_formatter/prompt.yaml")

    assert legacy_prompt == package_prompt
