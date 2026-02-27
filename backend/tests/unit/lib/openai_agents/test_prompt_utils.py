"""Unit tests for prompt utility helpers."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

PROMPT_UTILS_PATH = Path(__file__).resolve().parents[4] / "src/lib/openai_agents/prompt_utils.py"
_spec = spec_from_file_location("prompt_utils_under_test", PROMPT_UTILS_PATH)
assert _spec is not None and _spec.loader is not None
prompt_utils = module_from_spec(_spec)
_spec.loader.exec_module(prompt_utils)

format_document_context_for_prompt = prompt_utils.format_document_context_for_prompt
format_hierarchy_for_prompt = prompt_utils.format_hierarchy_for_prompt
format_sections_for_prompt = prompt_utils.format_sections_for_prompt
inject_structured_output_instruction = prompt_utils.inject_structured_output_instruction


class DummyEnvelope:
    """Simple dummy output type for testing name extraction."""


def test_inject_structured_output_instruction_returns_original_without_type():
    instructions = "## Section\nBody"

    result = inject_structured_output_instruction(instructions)

    assert result == instructions


def test_inject_structured_output_instruction_prepends_when_requested():
    instructions = "## Intro\nBody"

    result = inject_structured_output_instruction(
        instructions=instructions,
        output_type=DummyEnvelope,
        insert_after_first_section=False,
    )

    assert "DummyEnvelope structured output" in result
    assert result.endswith(instructions)


def test_inject_structured_output_instruction_inserts_before_second_section():
    instructions = "## First\nLine A\nLine B\n## Second\nTail"

    result = inject_structured_output_instruction(
        instructions=instructions,
        output_type_name="TestSchema",
    )

    instruction_idx = result.index("## CRITICAL: ALWAYS PRODUCE STRUCTURED OUTPUT AS VALID JSON")
    second_section_idx = result.index("## Second")

    assert result.index("## First") < instruction_idx
    assert result.index("Line B") < instruction_idx
    assert instruction_idx < second_section_idx
    assert "TestSchema structured output" in result


def test_inject_structured_output_instruction_falls_back_to_prepend_without_second_section():
    instructions = "## Only Section\nBody"

    result = inject_structured_output_instruction(
        instructions=instructions,
        output_type_name="FallbackSchema",
    )

    assert result.endswith(instructions)
    assert result.index("## CRITICAL: ALWAYS PRODUCE STRUCTURED OUTPUT AS VALID JSON") < result.index(
        "## Only Section"
    )


def test_format_hierarchy_for_prompt_returns_empty_when_missing_sections():
    assert format_hierarchy_for_prompt({}) == ""
    assert format_hierarchy_for_prompt({"sections": []}) == ""


def test_format_hierarchy_for_prompt_formats_top_level_and_subsections():
    hierarchy = {
        "sections": [
            {
                "name": "Methods",
                "page_numbers": [2, 4],
                "chunk_count": 3,
                "subsections": [
                    {"name": "Fly Strains", "page_numbers": [3], "chunk_count": 1}
                ],
            }
        ],
        "top_level_sections": ["Methods"],
    }

    result = format_hierarchy_for_prompt(hierarchy)

    assert "## Document Structure" in result
    assert "**Methods** (p.2-4, 3 chunks)" in result
    assert "  └─ Fly Strains (p.3, 1 chunks)" in result
    assert "**Top-level sections (in order):** Methods" in result


def test_format_hierarchy_for_prompt_uses_unknown_when_top_level_missing():
    hierarchy = {
        "sections": [{"name": "Introduction", "page_numbers": [], "chunk_count": 0}],
    }

    result = format_hierarchy_for_prompt(hierarchy)

    assert "**Top-level sections (in order):** Unknown" in result


def test_format_sections_for_prompt_returns_empty_when_no_sections():
    assert format_sections_for_prompt([]) == ""


def test_format_sections_for_prompt_formats_entries_with_defaults():
    sections = [
        {"title": "Introduction", "page_numbers": [1], "chunk_count": 2},
        {"page_numbers": [5, 7]},
    ]

    result = format_sections_for_prompt(sections)

    assert "## Document Sections" in result
    assert "- **Introduction** (p.1, 2 chunks)" in result
    assert "- **Unknown** (p.5-7, 0 chunks)" in result


def test_format_document_context_for_prompt_prefers_hierarchy_and_adds_abstract():
    hierarchy = {
        "sections": [
            {"name": "Results", "page_numbers": [8], "chunk_count": 2, "subsections": []}
        ],
        "top_level_sections": ["Results"],
    }
    sections = [{"title": "Fallback", "page_numbers": [1], "chunk_count": 1}]

    context_text, structure_info = format_document_context_for_prompt(
        hierarchy=hierarchy,
        sections=sections,
        abstract="  A short abstract.  ",
    )

    assert "## Document Structure" in context_text
    assert "## Document Sections" not in context_text
    assert "## Paper Abstract" in context_text
    assert "A short abstract." in context_text
    assert structure_info == "hierarchy with 1 sections + abstract"


def test_format_document_context_for_prompt_uses_sections_when_no_hierarchy():
    context_text, structure_info = format_document_context_for_prompt(
        hierarchy={"sections": []},
        sections=[{"title": "Only Section", "page_numbers": [2], "chunk_count": 1}],
        abstract=None,
    )

    assert "## Document Sections" in context_text
    assert "## Document Structure" not in context_text
    assert structure_info == "1 flat sections"


def test_format_document_context_for_prompt_handles_no_structure_or_abstract():
    context_text, structure_info = format_document_context_for_prompt(
        hierarchy=None,
        sections=[],
        abstract="   ",
    )

    assert context_text == ""
    assert structure_info == "no structure"
