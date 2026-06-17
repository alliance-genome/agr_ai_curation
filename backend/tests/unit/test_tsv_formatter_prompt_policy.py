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
        "packages/alliance/agents/tsv_formatter/prompt.yaml",
    ],
)
def test_tsv_formatter_prompt_uses_runtime_tool_contract(relative_path: str):
    content = _load_prompt_content(relative_path)

    for required in (
        "inspect_output_artifacts",
        "inspect_output_rows",
        "inspect_field_values",
        "build_default_projection_plan",
        "validate_output_projection",
        "preview_output_projection",
        "finalize_and_save",
        "formatter_cannot_complete",
        "source-backed",
        "filename_hint",
        "source_ref",
        "latest `extraction-result:<uuid>`",
        "Do not build replacement row arrays",
    ):
        assert required in content

    for forbidden in (
        "save_tsv_file",
        "data_json",
        "JSON array string",
    ):
        assert forbidden not in content

    assert "Do not paste TSV content" in content
    assert "\nFormatted TSV output:\n" not in content
