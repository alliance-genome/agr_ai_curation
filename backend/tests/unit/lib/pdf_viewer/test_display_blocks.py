"""Display blocks for a text file, built from its processed elements (ALL-1460, P9a).

A quote is an exact slice of a stored piece. The blocks must hold the text exactly as
it was cut into pieces, so the text view can find a quote by exact substring (prose)
or exact row-line equality (table rows). The fixture is cut by the real chunker and
compared. Made-up content only.
"""

import asyncio
import copy
import json

import pytest

from src.lib.pdf_viewer.display_blocks import (
    UnsupportedDisplayElementError,
    build_display_blocks,
    file_display_blocks,
)
from src.lib.pipeline import hierarchy_resolution as hierarchy
from src.lib.pipeline.chunk import chunk_parsed_document
from src.lib.pipeline.pdfx_parser import markdown_to_pipeline_elements
from src.lib.pipeline.table_chunking import table_row_display_cells, table_row_search_text
from src.models.strategy import ChunkingStrategy


_MARKDOWN = """# Supplementary Table S1

Strains used in this made-up study. They were kept at **two** temperatures.

## Sheet 1

| Strain | Genotype | Temp |
|---|---|---|
| XS0401 | zeta401 \\| b | 25 C |
| XS0402 |  | 20 C |

- first list item
- second item

```
x = 1
```

## Sheet 2

| Probe | Value |
|---|---|
| P1 | 0.5 |
"""


def _elements():
    return markdown_to_pipeline_elements(_MARKDOWN)


def _footer_element(index: int) -> dict:
    return {
        "index": index,
        "type": "NarrativeText",
        "text": "Page 3 of 9",
        "metadata": {"doc_item_label": "page_footer", "section_path": []},
    }


def test_blocks_keep_kinds_levels_and_section_paths_in_reading_order():
    blocks = build_display_blocks(_elements())

    assert [block.kind for block in blocks] == [
        "heading",
        "paragraph",
        "heading",
        "table",
        "list_item",
        "list_item",
        "code",
        "heading",
        "table",
    ]
    headings = [block for block in blocks if block.kind == "heading"]
    assert [(block.text, block.level) for block in headings] == [
        ("Supplementary Table S1", 1),
        ("Sheet 1", 2),
        ("Sheet 2", 2),
    ]
    assert blocks[3].section_path == ["Supplementary Table S1", "Sheet 1"]
    assert (blocks[3].section_title, blocks[3].parent_section, blocks[3].subsection) == ("Sheet 1", None, None)
    assert all(block.level is None for block in blocks if block.kind != "heading")
    assert all(block.rows is None for block in blocks if block.kind != "table")
    # A table's text is its stored lines, separator included, as the chunker lays it out.
    assert blocks[3].text == (
        "| Strain | Genotype | Temp |\n|---|---|---|\n"
        "| XS0401 | zeta401 \\| b | 25 C |\n| XS0402 | | 20 C |"
    )


def test_table_rows_hold_the_exact_stored_line_cells_and_header_flag():
    blocks = build_display_blocks(_elements())
    table = blocks[3]

    assert [row.line for row in table.rows] == [
        "| Strain | Genotype | Temp |",
        "| XS0401 | zeta401 \\| b | 25 C |",
        "| XS0402 | | 20 C |",
    ]
    # The separator line is not a row; the Markdown header row is a header.
    assert [row.is_header for row in table.rows] == [True, False, False]
    # Escapes are undone per cell; empty cells stay so columns line up.
    assert table.rows[1].cells == ["XS0401", "zeta401 | b", "25 C"]
    assert table.rows[2].cells == ["XS0402", "", "20 C"]


def test_cell_unescape_is_the_one_used_for_pdf_search_text():
    row = "| a \\| b |  | C:\\\\|"

    assert table_row_display_cells(row) == ["a | b", "", "C:\\"]
    assert table_row_search_text(row) == " ".join(
        cell for cell in table_row_display_cells(row) if cell
    )


def test_page_footers_are_left_out_as_the_chunker_does():
    elements = _elements()
    elements.insert(2, _footer_element(len(elements)))

    blocks = build_display_blocks(elements)

    assert all(block.text != "Page 3 of 9" for block in blocks)
    assert len(blocks) == 9


def test_an_unknown_element_type_is_a_named_error():
    elements = _elements()
    elements.append({"index": 99, "type": "Image", "text": "fig", "metadata": {}})

    with pytest.raises(UnsupportedDisplayElementError):
        build_display_blocks(elements)


def _pieces(elements):
    return asyncio.run(
        chunk_parsed_document(
            elements, ChunkingStrategy.get_research_strategy(), "made-up-document"
        )
    )


def _joined(blocks) -> str:
    return "\n\n".join(block.text for block in blocks)


def test_every_stored_piece_is_exact_text_of_the_joined_blocks():
    elements = _elements()
    elements.insert(2, _footer_element(len(elements)))
    pieces = _pieces(elements)
    blocks = build_display_blocks(elements)
    joined = _joined(blocks)
    row_lines = {row.line for block in blocks if block.kind == "table" for row in block.rows}

    assert pieces
    for piece in pieces:
        assert piece.content in joined
    stored_lines = {line for piece in pieces for line in piece.content.split("\n")}
    assert row_lines <= stored_lines


def _sentences(count: int, separator: str) -> str:
    return separator.join(
        f"Sentence {number} describes made-up flies kept at a steady temperature."
        for number in range(count)
    )


def _oversized_markdown(separator: str) -> str:
    return f"# Notes\n\n{_sentences(45, separator)}\n"


def test_an_oversized_element_cut_at_single_spaces_stays_exact():
    elements = markdown_to_pipeline_elements(_oversized_markdown(" "))
    pieces = _pieces(elements)
    joined = _joined(build_display_blocks(elements))

    assert len(pieces) > 1
    assert all(piece.content in joined for piece in pieces)


def test_an_oversized_element_cut_at_other_whitespace_is_the_documented_exception():
    """The chunker rejoins sentences with one space; the block keeps the original."""

    elements = markdown_to_pipeline_elements(_oversized_markdown("  "))
    pieces = _pieces(elements)
    joined = _joined(build_display_blocks(elements))

    assert len(pieces) > 1
    assert "  " in joined
    assert any(piece.content not in joined for piece in pieces)
    # With the whitespace runs made single, every piece is exact again.
    assert all(piece.content in joined.replace("  ", " ") for piece in pieces)


def test_a_table_split_across_pieces_keeps_every_row_line_exact():
    rows = "\n".join(f"| XS{number:04d} | made-up genotype {number} | 25 C |" for number in range(200))
    markdown = f"# Table S2\n\n| Strain | Genotype | Temp |\n|---|---|---|\n{rows}\n"
    elements = markdown_to_pipeline_elements(markdown)
    pieces = _pieces(elements)
    blocks = build_display_blocks(elements)
    [table] = [block for block in blocks if block.kind == "table"]
    stored_lines = [line for piece in pieces for line in piece.content.split("\n")]

    assert len(pieces) > 2
    assert len(table.rows) == 201
    assert all(row.line in stored_lines for row in table.rows)
    assert all(piece.content in _joined(blocks) for piece in pieces)


def test_a_quote_can_cross_two_blocks_inside_one_piece():
    elements = markdown_to_pipeline_elements(
        "# Methods\n\nFlies were raised on made-up food.\n\nThey were kept at 25 C.\n"
    )
    [piece] = _pieces(elements)
    blocks = build_display_blocks(elements)
    quote = "made-up food.\n\nThey were"

    assert quote in piece.content
    assert all(quote not in block.text for block in blocks)
    assert quote in _joined(blocks)


# --- section names equal the stored pieces' ----------------------------------------

_SECTION_FIELDS = ("section_title", "parent_section", "subsection", "section_path")


def _assert_blocks_carry_their_pieces_sections(blocks, pieces):
    """Every non-heading block inside a piece has that piece's section fields.

    Headings are left out: a piece of stacked headings carries its innermost heading's
    fields, as the chunker decides.
    """

    compared = 0
    for piece in pieces:
        for block in blocks:
            if block.kind == "heading" or block.text not in piece.content:
                continue
            assert tuple(getattr(block, field) for field in _SECTION_FIELDS) == tuple(
                getattr(piece, field) for field in _SECTION_FIELDS
            ), (block.text[:30], piece.content[:30])
            compared += 1
    return compared


def _index_like_the_pipeline(markdown):
    """Save elements before hierarchy, resolve, cut pieces; return what is stored."""

    elements = markdown_to_pipeline_elements(markdown)
    saved_json = json.dumps(elements)
    resolved, metadata = asyncio.run(hierarchy.resolve_document_hierarchy(elements))
    return json.loads(saved_json), (metadata.model_dump() if metadata else None), _pieces(resolved)


def test_a_three_level_main_paper_with_a_stored_map_shows_its_pieces_sections(monkeypatch):
    from tests.unit.pipeline.test_hierarchy_piece_sections_pinned import (
        THREE_LEVEL_PAPER,
        fixed_classifier,
    )

    fixed_classifier(monkeypatch)
    saved, stored_map, pieces = _index_like_the_pipeline(THREE_LEVEL_PAPER)

    blocks = file_display_blocks(saved, stored_map)

    assert stored_map is not None
    assert _assert_blocks_carry_their_pieces_sections(blocks, pieces) >= 6
    fly_strains = next(block for block in blocks if block.text.startswith("Flies were raised"))
    assert fly_strains.parent_section == "2 Materials and methods"
    assert fly_strains.subsection == "2.1 Fly strains"
    # Heading levels still follow the file's own headings, four deep.
    assert [block.level for block in blocks if block.kind == "heading"] == [1, 2, 2, 2, 3, 4, 3, 2]


def test_a_main_paper_with_no_stored_map_shows_its_elements_own_sections(monkeypatch):
    """The hierarchy step answered nothing: pieces keep the elements' own fields, and so do blocks."""

    from tests.unit.pipeline.test_hierarchy_piece_sections_pinned import THREE_LEVEL_PAPER

    async def _no_answer(_section_info):
        return ([], None)

    monkeypatch.setattr(hierarchy, "_call_llm_for_hierarchy", _no_answer)
    saved, stored_map, pieces = _index_like_the_pipeline(THREE_LEVEL_PAPER)

    blocks = file_display_blocks(saved, stored_map)

    assert stored_map is None
    assert _assert_blocks_carry_their_pieces_sections(blocks, pieces) >= 6
    stock_table = next(block for block in blocks if block.kind == "table")
    assert stock_table.section_path == [
        "Made-up paper on wing growth", "2 Materials and methods", "2.1 Fly strains", "2.1.1 Stock list",
    ]


def test_a_supplement_sectioned_before_saving_shows_its_pieces_sections():
    """P7c1's shape: sections written onto the elements before the processed JSON is saved,
    and no map stored on the file (agreed with the P7c1 builder)."""

    markdown = (
        "# Supplementary Information\n\n## Supplementary Methods\n\n### Fly husbandry\n\n"
        "Flies were kept at 25 C.\n\n## Table S1\n\n| Stock | Genotype |\n|---|---|\n| BL1 | made-up |\n"
    )
    elements = markdown_to_pipeline_elements(markdown)
    structural_map = hierarchy.section_map(
        [
            hierarchy.SectionItem(
                header="Supplementary Information", parent_section="Supplementary Information",
                subsection=None, is_top_level=True,
            ),
            hierarchy.SectionItem(
                header="Supplementary Methods", parent_section="Supplementary Methods",
                subsection=None, is_top_level=True,
            ),
            hierarchy.SectionItem(
                header="Fly husbandry", parent_section="Supplementary Methods",
                subsection="Fly husbandry", is_top_level=False,
            ),
            hierarchy.SectionItem(
                header="Table S1", parent_section="Table S1", subsection=None, is_top_level=True,
            ),
        ]
    )
    hierarchy.apply_section_hierarchy(elements, structural_map)
    saved = copy.deepcopy(elements)
    pieces = _pieces(elements)

    blocks = file_display_blocks(saved, None)

    assert _assert_blocks_carry_their_pieces_sections(blocks, pieces) >= 2
    husbandry = next(block for block in blocks if block.text.startswith("Flies were kept"))
    assert (husbandry.section_title, husbandry.section_path) == (
        "Supplementary Methods > Fly husbandry", ["Supplementary Methods", "Fly husbandry"],
    )


@pytest.mark.parametrize(
    "stored",
    [
        {},
        {"sections": "not a list"},
        {"sections": [{"header": "Methods"}]},
        {"sections": [{"header": "Methods", "parent_section": "Methods", "is_top_level": "maybe"}]},
    ],
)
def test_a_stored_map_of_an_unknown_shape_is_a_named_error(stored):
    with pytest.raises(hierarchy.StoredSectionMapError):
        file_display_blocks(_elements(), stored)
