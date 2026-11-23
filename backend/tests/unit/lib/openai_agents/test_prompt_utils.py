"""Unit tests for prompt utility helpers."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from src.lib.openai_agents.models import GeneExtractionResultEnvelope

PROMPT_UTILS_PATH = Path(__file__).resolve().parents[4] / "src/lib/openai_agents/prompt_utils.py"
_spec = spec_from_file_location("prompt_utils_under_test", PROMPT_UTILS_PATH)
assert _spec is not None and _spec.loader is not None
prompt_utils = module_from_spec(_spec)
_spec.loader.exec_module(prompt_utils)

format_document_context_for_prompt = prompt_utils.format_document_context_for_prompt
format_hierarchy_for_prompt = prompt_utils.format_hierarchy_for_prompt
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


def test_inject_structured_output_instruction_adds_domain_envelope_contract():
    instructions = "## First\nLine A\n## Second\nTail"

    result = inject_structured_output_instruction(
        instructions=instructions,
        output_type=GeneExtractionResultEnvelope,
    )

    assert "## DOMAIN ENVELOPE EXTRACTION CONTRACT" in result
    assert "`curatable_objects[]` is the only semantic object list" in result
    assert "Do not emit top-level legacy semantic lists" in result
    assert "under `metadata`" in result
    assert "validation findings" in result
    assert "repair mode" not in result.lower()


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


def _outline(*files):
    """The shape get_document_sections_hierarchical returns: all sections, and each file's own."""
    return {
        "sections": [section for file in files for section in file["sections"]],
        "top_level_sections": [section["name"] for file in files for section in file["sections"]],
        "files": list(files),
    }


def _file(name, role, kind, start, end, sections):
    return {"file_id": f"id-{name}", "name": name, "role": role, "kind": kind,
            "chunk_index_start": start, "chunk_index_end": end, "chunk_count": end - start + 1,
            "sections": sections, "top_level_sections": [section["name"] for section in sections]}


METHODS = {"name": "Materials and Methods", "page_numbers": [2, 4], "chunk_count": 3,
           "subsections": [{"name": "Fly strains", "page_numbers": [3], "chunk_count": 1}]}
SHEET = {"name": "Sheet1", "page_numbers": [], "chunk_count": 19, "subsections": []}


def test_a_one_file_paper_lists_one_file_then_its_exact_section_names():
    result = format_hierarchy_for_prompt(_outline(_file("paper.pdf", "main", "PDF", 0, 41, [METHODS])))

    assert "## Document Structure" in result
    assert "This paper has 1 file. Each file lists its sections and subsections with their exact names" in result
    assert "File 1 of 1: paper.pdf (main paper, PDF, 42 chunks, chunks 0 to 41)" in result
    assert "**Materials and Methods** (p.2-4, 3 chunks)" in result
    assert "  └─ Fly strains (p.3, 1 chunks)" in result
    assert 'read_section("Methods")' not in result
    assert "Top-level sections" not in result


def test_several_files_are_listed_in_tab_order_and_text_files_show_no_pages():
    result = format_hierarchy_for_prompt(_outline(
        _file("paper.pdf", "main", "PDF", 0, 41, [METHODS]),
        _file("Table S1.xlsx", "supplement", "Excel", 42, 60, [SHEET]),
    ))

    assert "This paper has 2 files." in result
    assert result.index("File 1 of 2: paper.pdf") < result.index(
        "File 2 of 2: Table S1.xlsx (supplement, Excel, 19 chunks, chunks 42 to 60)")
    assert "**Sheet1** (19 chunks)" in result
    assert 'add `file="<file name>"`' in result


def test_while_files_are_being_added_the_outline_says_so_after_the_files():
    outline = {**_outline(_file("paper.pdf", "main", "PDF", 0, 41, [METHODS])), "files_being_added": 1}

    result = format_hierarchy_for_prompt(outline)

    # Approved by Chris, 22 Nov 2026 (O4), character for character.
    assert "\nFiles are still being added to this paper; they cannot be read yet.\n" in result
    assert result.index("**Materials and Methods**") < result.index("Files are still being added") < result.index(
        "### How to use this outline:")


def test_one_blank_line_separates_the_last_file_from_the_usage_notes():
    plain = format_hierarchy_for_prompt(_outline(_file("paper.pdf", "main", "PDF", 0, 41, [METHODS])))
    adding = format_hierarchy_for_prompt(
        {**_outline(_file("paper.pdf", "main", "PDF", 0, 41, [METHODS])), "files_being_added": 1})

    assert "  └─ Fly strains (p.3, 1 chunks)\n\n### How to use this outline:" in plain
    assert ("  └─ Fly strains (p.3, 1 chunks)\n\nFiles are still being added to this paper; they cannot "
            "be read yet.\n\n### How to use this outline:") in adding


def test_with_no_file_being_added_the_outline_does_not_mention_it():
    for count in (0, None):
        outline = {**_outline(_file("paper.pdf", "main", "PDF", 0, 41, [METHODS])), "files_being_added": count}
        assert "still being added" not in format_hierarchy_for_prompt(outline)
    assert "still being added" not in format_hierarchy_for_prompt(
        _outline(_file("paper.pdf", "main", "PDF", 0, 41, [METHODS])))


def test_while_abc_converts_the_supplements_the_outline_says_so_after_the_files():
    outline = {**_outline(_file("paper.pdf", "main", "PDF", 0, 41, [METHODS])), "supplements_converting": True}

    result = format_hierarchy_for_prompt(outline)

    # Approved by Chris, 23 Nov 2026 (replacing card 42), character for character.
    assert "\nABC has not finished converting this paper's supplements; they cannot be read yet.\n" in result
    assert result.index("**Materials and Methods**") < result.index("ABC has not finished converting") < result.index(
        "### How to use this outline:")
    assert "  └─ Fly strains (p.3, 1 chunks)\n\nABC has not finished converting this paper's supplements; they cannot " \
           "be read yet.\n\n### How to use this outline:" in result


def test_with_no_abc_wait_the_outline_does_not_mention_it():
    for value in (False, None):
        outline = {**_outline(_file("paper.pdf", "main", "PDF", 0, 41, [METHODS])), "supplements_converting": value}
        assert "not finished converting" not in format_hierarchy_for_prompt(outline)
    assert "not finished converting" not in format_hierarchy_for_prompt(
        _outline(_file("paper.pdf", "main", "PDF", 0, 41, [METHODS])))


def test_format_document_context_for_prompt_prefers_hierarchy():
    hierarchy = _outline(_file("paper.pdf", "main", "PDF", 0, 1, [
        {"name": "Results", "page_numbers": [8], "chunk_count": 2, "subsections": []}]))
    context_text, structure_info = format_document_context_for_prompt(hierarchy=hierarchy)

    assert "## Document Structure" in context_text
    assert structure_info == "hierarchy with 1 sections"


def test_document_context_is_exactly_the_section_list_for_a_paper_with_an_abstract():
    """A paper's Abstract section appears in the section list only, never as injected text."""
    hierarchy = _outline(_file("paper.pdf", "main", "PDF", 0, 5, [
        {"name": "Abstract", "page_numbers": [1], "chunk_count": 2, "subsections": []},
        {"name": "Results", "page_numbers": [3, 5], "chunk_count": 4, "subsections": [
            {"name": "Expression", "page_numbers": [4], "chunk_count": 1},
        ]},
    ]))

    context_text, structure_info = format_document_context_for_prompt(hierarchy=hierarchy)

    assert context_text == format_hierarchy_for_prompt(hierarchy)
    assert "**Abstract** (p.1, 2 chunks)" in context_text
    assert "Paper Abstract" not in context_text
    assert structure_info == "hierarchy with 2 sections"
    assert not hasattr(prompt_utils, "fetch_document_abstract")
    assert not hasattr(prompt_utils, "ABSTRACT_TEMPLATE")


def test_format_document_context_for_prompt_handles_no_structure():
    context_text, structure_info = format_document_context_for_prompt(hierarchy=None)

    assert context_text == ""
    assert structure_info == "no structure"
