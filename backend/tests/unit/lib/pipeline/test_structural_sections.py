"""Sections of a supplement from its heading structure, with no model (P7c1).

Elements come from the real ``markdown_to_pipeline_elements`` on Markdown in the
shapes ABC's Office converter writes: a workbook is one top-level title (the file
name) and one second-level heading per sheet, each over one table; most Word
files are one top-level title over plain text. All values are made up.
"""

from __future__ import annotations

from src.lib.pipeline.pdfx_parser import markdown_to_pipeline_elements
from src.lib.pipeline.structural_sections import apply_structural_sections

WORKBOOK = (
    "# Made_up_S1\n\n"
    "## Sheet1\n\n| strain | score |\n| --- | --- |\n| s1 | 1 |\n\n"
    "## Sheet2\n\n| gene | count |\n| --- | --- |\n| g1 | 3 |\n"
)
WORD_WITHOUT_HEADINGS = "# Made_up_S2\n\nMade-up legend text.\n\nA second made-up paragraph.\n"
WORD_WITH_HEADINGS = (
    "# Made_up_S3\n\n## Abstract\n\nMade-up text under a heading called Abstract.\n\n"
    "## Methods\n\n### Strains\n\nMade-up strains.\n\n#### Deeper\n\nMade-up deeper text.\n"
)


def _sections(elements):
    return [(element["text"][:12], element["parent_section"], element["subsection"]) for element in elements]


def test_a_workbook_gives_one_section_per_sheet():
    elements = apply_structural_sections(
        markdown_to_pipeline_elements(WORKBOOK), file_title_is_first_heading=True,
        fallback_section="Made_up_S1.xlsx",
    )
    tables = [element for element in elements if element["type"] == "Table"]
    assert [table["parent_section"] for table in tables] == ["Sheet1", "Sheet2"]
    assert all(table["subsection"] is None and table["is_top_level"] is True for table in tables)


def test_text_under_the_file_title_only_is_one_section_named_after_the_file():
    elements = apply_structural_sections(
        markdown_to_pipeline_elements(WORD_WITHOUT_HEADINGS), file_title_is_first_heading=True,
        fallback_section="Made_up_S2.docx",
    )
    assert {element["parent_section"] for element in elements} == {"Made_up_S2"}


def test_levels_below_the_file_title_give_sections_and_subsections_and_deeper_headings_fold_in():
    elements = apply_structural_sections(
        markdown_to_pipeline_elements(WORD_WITH_HEADINGS), file_title_is_first_heading=True,
        fallback_section="Made_up_S3.docx",
    )
    by_text = {element["text"]: element for element in elements}
    assert by_text["Made-up text under a heading called Abstract."]["parent_section"] == "Abstract"
    strains = by_text["Made-up strains."]
    assert (strains["parent_section"], strains["subsection"], strains["is_top_level"]) == ("Methods", "Strains", False)
    assert strains["section_title"] == "Methods > Strains"
    assert strains["section_path"] == ["Methods", "Strains"]
    deeper = by_text["Made-up deeper text."]
    assert (deeper["parent_section"], deeper["subsection"]) == ("Methods", "Strains")


def test_without_the_title_flag_the_first_level_is_the_section():
    elements = apply_structural_sections(
        markdown_to_pipeline_elements("Made-up text before any heading.\n\n# Results\n\n## Part\n\nMade-up text.\n"),
        file_title_is_first_heading=False, fallback_section="Made_up_S4.pdf",
    )
    by_text = {element["text"]: element for element in elements}
    assert by_text["Made-up text before any heading."]["parent_section"] == "Made_up_S4.pdf"
    assert (by_text["Made-up text."]["parent_section"], by_text["Made-up text."]["subsection"]) == ("Results", "Part")


def test_the_fields_match_the_hierarchy_step_at_the_top_level_and_in_metadata():
    elements = apply_structural_sections(
        markdown_to_pipeline_elements(WORD_WITH_HEADINGS), file_title_is_first_heading=True,
        fallback_section="Made_up_S3.docx",
    )
    for element in elements:
        metadata = element["metadata"]
        for name in ("parent_section", "subsection", "is_top_level", "section_title", "section_path"):
            assert element[name] == metadata[name], name


def test_the_input_is_not_changed():
    elements = markdown_to_pipeline_elements(WORKBOOK)
    before = [dict(element, metadata=dict(element["metadata"])) for element in elements]
    apply_structural_sections(elements, file_title_is_first_heading=True, fallback_section="Made_up_S1.xlsx")
    assert elements == before
