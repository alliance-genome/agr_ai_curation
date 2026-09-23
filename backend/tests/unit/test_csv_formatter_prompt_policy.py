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
        "packages/alliance/agents/csv_formatter/prompt.yaml",
    ],
)
def test_csv_formatter_prompt_uses_runtime_tool_contract(relative_path: str):
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
        "save_csv_file",
        "data_json",
        "JSON array string",
    ):
        assert forbidden not in content

    assert "Do not paste CSV content" in content
    assert "\nFormatted CSV output:\n" not in content


def test_csv_prompt_finds_rationale_in_catalog_and_keeps_one_field_per_column():
    content = _load_prompt_content("packages/alliance/agents/csv_formatter/prompt.yaml")

    assert '`catalog_query` "rationale"' in content
    assert "`object.pack.<ObjectType>.rationale`" in content
    assert "`object.payload.rationale`" in content
    assert "even when some or all values are empty" in content
    assert "only when a requested source declares no rationale field" in content
    assert "Map each requested column to one source field" in content
    assert "only when the curator explicitly asks for a fallback or combination" in content
